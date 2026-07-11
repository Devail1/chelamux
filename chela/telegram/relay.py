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

    def send(self, text: str, parse_mode: str | None = None) -> bool:
        for chunk in split_message(text):
            fields = {"chat_id": self._chat_id, "text": chunk}
            if self._topic_id:
                fields["message_thread_id"] = self._topic_id
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

    def __init__(self, sender: Sender):
        self._sender = sender

    def on_message(self, window_id: str, msg: Message) -> None:
        """Relay one parsed message (monitor callback signature)."""
        if self._sender(to_markdown_v2(msg), "MarkdownV2"):
            return
        # MarkdownV2 was rejected — retry the same content as plain text so a
        # formatting edge case never silently drops a message.
        log.debug("MarkdownV2 rejected for %s; retrying as plain text", window_id)
        self._sender(to_plain_text(msg), None)
