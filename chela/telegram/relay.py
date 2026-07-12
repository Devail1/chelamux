"""Outbound relay — post new agent messages to a Telegram topic.

:class:`TelegramRelay` is the ``on_message`` sink for
:class:`~chela.telegram.monitor.TranscriptMonitor`: each parsed
:class:`~chela.telegram.parser.Message` is rendered to Telegram *MarkdownV2*
(:func:`chela.telegram.format.to_markdown_v2`) and sent; if Telegram rejects the
MarkdownV2 (a formatting failure), the same message is re-sent as plain text.

The wire layer is the **direct Bot API** — the same ``sendMessage`` +
4096-character split approach as ``skills/telegram-send/send.py``. No
``python-telegram-bot`` here: PTB arrives with the inbound (Telegram → tmux)
slice; outbound stays dependency-free (stdlib ``urllib`` only). :class:`BotSender`
takes an injectable ``transport`` so the relay/formatting can be unit-tested
against a stub with no live Telegram calls.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from chela.telegram.format import to_markdown_v2, to_plain_text
from chela.telegram.parser import Message

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096

# A sender posts one message body to Telegram and reports whether it was
# accepted: ``send(text, parse_mode) -> ok``. ``parse_mode`` is "MarkdownV2" for
# the formatted attempt or None for the plain-text fallback.
Sender = Callable[[str, "str | None"], bool]

# A transport performs the raw Bot API call: ``transport(method, fields) -> resp``
# (the decoded Telegram JSON, ``{"ok": bool, ...}``). Injectable for tests.
Transport = Callable[[str, dict], dict]

# Tools whose ``tool_use`` is an interactive prompt the human must answer — these
# always relay even when tool-call notifications are hidden, or the operator
# never sees the question. Ported from ccbot's INTERACTIVE_TOOL_NAMES.
INTERACTIVE_TOOL_NAMES = frozenset({"AskUserQuestion", "ExitPlanMode"})


def _hide_tool_event(msg: Message, show_tool_calls: bool) -> bool:
    """True when ``msg`` is a tool notification that should be dropped.

    When ``show_tool_calls`` is False the noisy ``tool_use``/``tool_result``
    events are skipped BEFORE any formatting/sending — EXCEPT the interactive
    prompts in :data:`INTERACTIVE_TOOL_NAMES`, verified by ``tool_name``, which
    must always reach the human. ``text``/``thinking``/``user`` events carry no
    tool name and are never tool events, so they always relay.
    """
    if show_tool_calls:
        return False
    if msg.content_type not in ("tool_use", "tool_result"):
        return False
    return msg.tool_name not in INTERACTIVE_TOOL_NAMES


def split_message(text: str, n: int = MAX_LEN) -> list[str]:
    """Split ``text`` into ≤``n``-char chunks (Telegram's per-message limit)."""
    return [text[i : i + n] for i in range(0, len(text), n)] or [""]


def _urllib_transport(token: str) -> Transport:
    """The default transport: POST form-encoded fields to the Bot API."""

    def transport(method: str, fields: dict) -> dict:
        url = _API.format(token=token, method=method)
        body = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:  # 4xx (e.g. bad MarkdownV2) still carries JSON
            try:
                return json.load(e)
            except (ValueError, OSError):
                return {"ok": False, "description": f"HTTP {e.code}"}
        except urllib.error.URLError as e:
            return {"ok": False, "description": str(e.reason)}

    return transport


class BotSender:
    """Posts message bodies to one chat/topic via the direct Bot API.

    ``send(text, parse_mode)`` splits ``text`` at 4096 chars and posts each
    chunk with ``sendMessage``; it returns ``True`` only if every chunk was
    accepted, so the relay can fall back to plain text on the first rejection.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        topic_id: str | None = None,
        *,
        transport: Transport | None = None,
    ):
        self._chat_id = chat_id
        self._topic_id = topic_id
        self._transport = transport or _urllib_transport(token)

    def send(
        self,
        text: str,
        parse_mode: str | None = None,
        message_thread_id: str | int | None = None,
    ) -> bool:
        # A per-message thread (multi-topic relay) overrides the instance default
        # topic; without one we fall back to the fixed ``topic_id`` (single-topic).
        thread = message_thread_id if message_thread_id is not None else self._topic_id
        for chunk in split_message(text):
            fields = {"chat_id": self._chat_id, "text": chunk}
            if thread:
                fields["message_thread_id"] = thread
            if parse_mode:
                fields["parse_mode"] = parse_mode
            resp = self._transport("sendMessage", fields)
            if not resp.get("ok"):
                log.warning("telegram sendMessage failed: %s", resp.get("description", resp))
                return False
        return True


class TelegramRelay:
    """Renders each new message to MarkdownV2 and posts it, plain-text on failure.

    Use as the monitor's ``on_message`` sink via :meth:`on_message`::

        relay = TelegramRelay(bot_sender.send)
        mon = TranscriptMonitor(on_message=relay.on_message)
    """

    def __init__(self, sender: Sender, *, show_tool_calls: bool = True):
        self._sender = sender
        self._show_tool_calls = show_tool_calls

    def on_message(self, window_id: str, msg: Message) -> None:
        """Relay one parsed message (monitor callback signature)."""
        if _hide_tool_event(msg, self._show_tool_calls):
            return
        if self._sender(to_markdown_v2(msg), "MarkdownV2"):
            return
        # MarkdownV2 was rejected — retry the same content as plain text so a
        # formatting edge case never silently drops a message.
        log.debug("MarkdownV2 rejected for %s; retrying as plain text", window_id)
        self._sender(to_plain_text(msg), None)


class RegistryRelay:
    """Posts each window's messages to ITS bound topic via a registry.

    The multi-topic generalisation of :class:`TelegramRelay`: the monitor emits
    ``(window_id, msg)`` for any of the N polled windows, and this relay resolves
    the target topic per message from a
    :class:`~chela.telegram.bindings.BindingRegistry`
    (``thread_for_window``), posting with that ``message_thread_id``. A window
    with no binding is skipped (never posted). The MarkdownV2→plain-text fallback
    is preserved. Wire as the monitor's ``on_message`` sink::

        relay = RegistryRelay(bot_sender.send, registry)
        mon = TranscriptMonitor(on_message=relay.on_message)

    ``sender`` is ``send(text, parse_mode, message_thread_id) -> ok`` —
    :meth:`BotSender.send` in production, a stub in tests.
    """

    def __init__(self, sender: Sender, registry, *, show_tool_calls: bool = True):
        self._sender = sender
        self._registry = registry
        self._show_tool_calls = show_tool_calls

    def on_message(self, window_id: str, msg: Message) -> None:
        """Relay one parsed message to the window's bound topic (monitor callback)."""
        if _hide_tool_event(msg, self._show_tool_calls):
            return
        thread = self._registry.thread_for_window(window_id)
        if thread is None:
            log.debug("no topic bound for %s; skipping outbound", window_id)
            return
        if self._sender(to_markdown_v2(msg), "MarkdownV2", thread):
            return
        log.debug("MarkdownV2 rejected for %s; retrying as plain text", window_id)
        self._sender(to_plain_text(msg), None, thread)
