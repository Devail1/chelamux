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
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from chela.telegram.format import to_markdown_v2, to_plain_text
from chela.telegram.interactive import ask_reply_markup
from chela.telegram.parser import Message

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
# Telegram measures this per-message limit in UTF-16 code units, NOT Python code
# points — see :func:`_utf16_len` / :func:`split_message`.
MAX_LEN = 4096

# Flood control: on an HTTP 429 Telegram returns ``parameters.retry_after`` (whole
# seconds). Rather than drop the chunk we sleep and re-send the SAME payload — a
# duplicate on retry is preferred over a lost agent message. Bound both the honored
# wait and the number of attempts so a stuck topic can't wedge the relay.
_MAX_RETRY_AFTER = 30.0  # cap the honored flood-control wait (seconds)
_MAX_SEND_TRIES = 3      # total attempts per payload before giving up
# When a chunk is split mid ``` fence we append a closer and reopen on the next
# chunk; keep this many UTF-16 units free so the added "```" still fits under the
# limit (a closing "\n```" plus a little slack).
_FENCE = "```"
_FENCE_RESERVE = 8

# A sender posts one message body to Telegram and reports whether it was
# accepted: ``send(text, parse_mode, ...) -> ok``. ``parse_mode`` is "MarkdownV2"
# for the formatted attempt or None for the plain-text fallback; the multi-topic
# relay also passes ``message_thread_id`` and an optional ``reply_markup``.
Sender = Callable[..., bool]

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

    The one class of exceptions that fires regardless of ``show_tool_calls``: the
    ``tool_use`` of a pane-triggered prompt (AskUserQuestion — Slice A2 — and
    ExitPlanMode — Slice B2). Both selectors are surfaced live from the pane (with
    answer / approval buttons) while still pending; each one's transcript
    ``tool_use`` only lands *after* it is resolved, so relaying it here would just
    double-post the already-answered prompt. Their ``tool_result`` still relays as
    the "answered" / "approved" confirmation.
    """
    if msg.content_type == "tool_use" and msg.tool_name in (
        "AskUserQuestion",
        "ExitPlanMode",
    ):
        return True
    if show_tool_calls:
        return False
    if msg.content_type not in ("tool_use", "tool_result"):
        return False
    return msg.tool_name not in INTERACTIVE_TOOL_NAMES


def _utf16_len(s: str) -> int:
    """Length of ``s`` in UTF-16 code units — Telegram's unit for its 4096 limit.

    A non-BMP (astral) character — most emoji — is one Python code point but two
    UTF-16 code units (a surrogate pair), so ``len(s)`` undercounts an emoji-heavy
    body and lets an over-limit message reach the wire and get rejected.
    """
    return len(s.encode("utf-16-le")) // 2


def _truncate_utf16(text: str, n: int = MAX_LEN) -> str:
    """Truncate ``text`` to ≤``n`` UTF-16 code units, never splitting a surrogate.

    The UTF-16-correct replacement for ``text[:n]``: counts astral characters as
    two and stops on a whole-character boundary so the result is always valid.
    """
    if _utf16_len(text) <= n:
        return text
    out: list[str] = []
    total = 0
    for ch in text:
        clen = 2 if ord(ch) > 0xFFFF else 1
        if total + clen > n:
            break
        out.append(ch)
        total += clen
    return "".join(out)


def split_message(text: str, n: int = MAX_LEN) -> list[str]:
    """Split ``text`` into chunks Telegram will accept as one message each.

    Two subtleties over a naive ``text[i:i+n]`` slice:

    * **UTF-16 length.** Telegram measures its limit in UTF-16 code units, not
      Python code points (:func:`_utf16_len`), so an astral character (most
      emoji) counts as two. We accumulate whole characters — never splitting a
      surrogate pair — until the next would exceed the budget.
    * **Fenced code blocks.** A split landing inside a ```` ``` ```` block would
      leave a chunk with an unbalanced fence (invalid MarkdownV2, so the relay
      would drop to its plain-text fallback). When we break mid-fence we close
      the fence on this chunk and reopen it on the next, reserving
      :data:`_FENCE_RESERVE` UTF-16 units so the added closer still fits under
      ``n``. A message that is already under the limit is returned verbatim — the
      fence handling only ever runs when an actual split happens.
    """
    chunks: list[str] = []
    cur: list[str] = []
    cur_len = 0
    fence_open = False  # is a ``` code fence open at the current position?
    at_line_start = True
    run = 0  # consecutive backticks since the line start (for fence detection)

    def emit() -> None:
        nonlocal cur, cur_len
        body = "".join(cur)
        if fence_open:  # close the dangling fence so this chunk is valid on its own
            if body and not body.endswith("\n"):
                body += "\n"
            body += _FENCE
        chunks.append(body)
        cur, cur_len = [], 0

    for ch in text:
        # Reopen the fence at the top of a fresh chunk before adding content.
        if not cur and fence_open:
            reopen = _FENCE + "\n"
            cur.append(reopen)
            cur_len = _utf16_len(reopen)
        limit = n - _FENCE_RESERVE if fence_open else n
        clen = 2 if ord(ch) > 0xFFFF else 1
        if cur and cur_len + clen > limit:
            emit()
            if fence_open:
                reopen = _FENCE + "\n"
                cur.append(reopen)
                cur_len = _utf16_len(reopen)
        cur.append(ch)
        cur_len += clen
        # Track fenced blocks: a line whose first three characters are backticks
        # toggles the fence. Extra backticks (4+) and inline `code` don't toggle.
        if ch == "\n":
            at_line_start, run = True, 0
        elif at_line_start and ch == "`":
            run += 1
            if run == 3:
                fence_open = not fence_open
        else:
            at_line_start, run = False, 0

    if cur or not chunks:
        emit()
    return chunks


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
                # Keep the HTTP status even when the body isn't JSON, so a 429 with
                # an empty/garbled body is still recognised as flood control.
                return {"ok": False, "error_code": e.code, "description": f"HTTP {e.code}"}
        except urllib.error.URLError as e:
            return {"ok": False, "description": str(e.reason)}

    return transport


def _retry_after(resp: dict) -> float | None:
    """Flood-control wait (seconds) for a Telegram 429 response, else None.

    A 429 carries ``{"ok": false, "error_code": 429, "parameters":
    {"retry_after": N}}``. Returns ``N`` (capped by the caller) when present, a
    short default when only the 429 status is known (e.g. a non-JSON body — see
    :func:`_urllib_transport`), and None for any non-429 outcome so the caller
    stops retrying and reports the failure as before.
    """
    if resp.get("ok"):
        return None
    params = resp.get("parameters")
    if isinstance(params, dict):
        ra = params.get("retry_after")
        if isinstance(ra, (int, float)) and ra >= 0:
            return float(ra)
    if resp.get("error_code") == 429:
        return 1.0  # 429 without a retry_after — back off briefly and retry
    return None


class BotSender:
    """Posts message bodies to one chat/topic via the direct Bot API.

    ``send(text, parse_mode)`` splits ``text`` at 4096 chars and posts each
    chunk with ``sendMessage``; it returns ``True`` only if every chunk was
    accepted, so the relay can fall back to plain text on the first rejection.
    A Telegram 429 (flood control) is not a rejection — every send goes through
    :meth:`_call`, which sleeps for the advertised ``retry_after`` (capped) and
    re-sends the same payload a bounded number of times before giving up.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        topic_id: str | None = None,
        *,
        transport: Transport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._chat_id = chat_id
        self._topic_id = topic_id
        self._transport = transport or _urllib_transport(token)
        self._sleep = sleep

    def _call(self, method: str, fields: dict) -> dict:
        """Perform a Bot API call, retrying the SAME payload on a 429.

        On flood control we sleep for the advertised ``retry_after`` (bounded by
        :data:`_MAX_RETRY_AFTER`) and re-send, up to :data:`_MAX_SEND_TRIES`
        attempts. Any non-429 response — success or a real error — is returned
        immediately. Preferring a duplicate on retry over a dropped message.
        """
        resp = self._transport(method, fields)
        for _ in range(_MAX_SEND_TRIES - 1):
            wait = _retry_after(resp)
            if wait is None:
                return resp
            wait = min(wait, _MAX_RETRY_AFTER)
            log.warning(
                "telegram %s flood-controlled; retrying in %.1fs", method, wait
            )
            self._sleep(wait)
            resp = self._transport(method, fields)
        return resp

    def send(
        self,
        text: str,
        parse_mode: str | None = None,
        message_thread_id: str | int | None = None,
        reply_markup: dict | None = None,
    ) -> bool:
        # A per-message thread (multi-topic relay) overrides the instance default
        # topic; without one we fall back to the fixed ``topic_id`` (single-topic).
        thread = message_thread_id if message_thread_id is not None else self._topic_id
        # An inline keyboard (e.g. AskUserQuestion answers) rides on the FIRST
        # chunk only — Telegram attaches it to that one message. The Bot API wants
        # it JSON-encoded in the form field.
        markup_json = json.dumps(reply_markup) if reply_markup else None
        for i, chunk in enumerate(split_message(text)):
            fields = {"chat_id": self._chat_id, "text": chunk}
            if thread:
                fields["message_thread_id"] = thread
            if parse_mode:
                fields["parse_mode"] = parse_mode
            if markup_json and i == 0:
                fields["reply_markup"] = markup_json
            resp = self._call("sendMessage", fields)
            if not resp.get("ok"):
                log.warning("telegram sendMessage failed: %s", resp.get("description", resp))
                return False
        return True

    def post(
        self,
        text: str,
        parse_mode: str | None = None,
        message_thread_id: str | int | None = None,
        reply_markup: dict | None = None,
    ) -> int | None:
        """Post ONE message and return its ``message_id`` (None on failure).

        The edit-in-place sibling of :meth:`send`: the pane watcher's interactive
        prompts (an AskUserQuestion selector + its keyboard) are short, so this
        does NOT split at 4096 chars, and it returns the id so a later scrape can
        :meth:`edit` the same message rather than post a duplicate as the selector
        settles.
        """
        thread = message_thread_id if message_thread_id is not None else self._topic_id
        fields = {"chat_id": self._chat_id, "text": _truncate_utf16(text)}
        if thread:
            fields["message_thread_id"] = thread
        if parse_mode:
            fields["parse_mode"] = parse_mode
        if reply_markup:
            fields["reply_markup"] = json.dumps(reply_markup)
        resp = self._call("sendMessage", fields)
        if not resp.get("ok"):
            log.warning("telegram sendMessage failed: %s", resp.get("description", resp))
            return None
        mid = (resp.get("result") or {}).get("message_id")
        return int(mid) if isinstance(mid, int) else None

    def edit(
        self,
        message_id: int,
        text: str,
        parse_mode: str | None = None,
        reply_markup: dict | None = None,
    ) -> bool:
        """``editMessageText`` an existing message; True once it shows ``text``.

        Tolerates Telegram's "message is not modified" (the content was already
        current) as success — so a re-scrape that produced the same text is a
        no-op, not an error. Any other failure (e.g. the tracked message was
        deleted) returns False so the caller can post a fresh one. A message is
        addressed by chat + id, so no ``message_thread_id`` is needed here.
        """
        fields: dict = {
            "chat_id": self._chat_id,
            "message_id": message_id,
            "text": _truncate_utf16(text),
        }
        if parse_mode:
            fields["parse_mode"] = parse_mode
        if reply_markup:
            fields["reply_markup"] = json.dumps(reply_markup)
        resp = self._call("editMessageText", fields)
        if resp.get("ok"):
            return True
        desc = str(resp.get("description", ""))
        if "not modified" in desc.lower():
            return True
        log.warning("telegram editMessageText failed: %s", desc or resp)
        return False

    def delete(self, message_id: int) -> bool:
        """``deleteMessage`` — poof an interactive prompt once it is answered.

        The pane watcher deletes the question / plan / permission message it posted
        as soon as the prompt leaves the pane: its buttons would otherwise stay
        tappable and fire keystrokes at whatever the agent went on to do. A failure
        (already deleted, too old to delete) is logged and reported, never raised —
        the watcher has already dropped its tracking either way.
        """
        resp = self._call(
            "deleteMessage", {"chat_id": self._chat_id, "message_id": message_id}
        )
        if resp.get("ok"):
            return True
        log.warning("telegram deleteMessage failed: %s", resp.get("description", resp))
        return False


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
        # An AskUserQuestion prompt gets an inline keyboard so the human can tap
        # an answer; every other message has no markup. Pass the kwarg only when
        # present so plain senders (and the test stubs) keep their 2-arg shape.
        markup = ask_reply_markup(msg)
        kw = {"reply_markup": markup} if markup else {}
        if self._sender(to_markdown_v2(msg), "MarkdownV2", **kw):
            return
        # MarkdownV2 was rejected — retry the same content as plain text so a
        # formatting edge case never silently drops a message (keyboard kept).
        log.debug("MarkdownV2 rejected for %s; retrying as plain text", window_id)
        self._sender(to_plain_text(msg), None, **kw)


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
        # AskUserQuestion prompts carry an inline answer keyboard; see the
        # single-topic relay above for why the kwarg is passed only when present.
        markup = ask_reply_markup(msg)
        kw = {"reply_markup": markup} if markup else {}
        if self._sender(to_markdown_v2(msg), "MarkdownV2", thread, **kw):
            return
        log.debug("MarkdownV2 rejected for %s; retrying as plain text", window_id)
        self._sender(to_plain_text(msg), None, thread, **kw)
