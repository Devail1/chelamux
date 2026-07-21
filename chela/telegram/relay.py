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
from typing import Callable, NamedTuple

from chela.telegram.format import to_markdown_v2, to_plain_text, unescape_markdown_v2
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

# MarkdownV2 entity markers. A chunk boundary must never land between an opener
# and its closer, so :func:`split_message` closes whatever is open at the end of a
# chunk and reopens it at the top of the next — for the ``` fence AND for every
# inline entity. Longest first: ``__`` must win over ``_``.
_FENCE = "```"
_INLINE_MARKERS = ("||", "__", "*", "_", "~", "`")
# Prefer to break a chunk here, best first — a paragraph break can never sit
# inside an inline entity, and a mid-word break is the one that used to sever one.
_BREAK_BLANK, _BREAK_LINE, _BREAK_SPACE, _BREAK_ANY = 3, 2, 1, 0

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


class _Unit(NamedTuple):
    """One indivisible piece of a MarkdownV2 body, as :func:`_scan` sees it.

    ``s`` is the literal text (a character, a ``\\x`` escape pair, or an entity
    marker). ``marker`` is the entity marker when this unit is one, ``close``
    whether it closes the currently-open one, and ``link`` whether the unit sits
    inside a ``[label](url)`` construct — a link cannot be closed and reopened
    across a chunk, so we never break inside one.
    """

    s: str
    marker: str | None
    close: bool
    link: bool


def _scan(text: str) -> list[_Unit]:
    """Tokenize an already-rendered MarkdownV2 body into entity-aware units.

    This parses **what is actually emitted** (by ``render_markdown`` /
    ``telegramify-markdown``), not the source Markdown: a ``\\*`` escape is an
    ordinary character, never a bold opener. Inside a ``` fence or an inline
    `` ` `` code span nothing but the matching closer is a marker, which is what
    keeps a literal asterisk in a code sample from opening an entity.
    """
    units: list[_Unit] = []
    stack: list[str] = []
    at_line_start = True
    in_link = False
    i, end = 0, len(text)
    while i < end:
        ch = text[i]
        if ch == "\\" and i + 1 < end:  # escaped char — atomic, never a marker
            units.append(_Unit(text[i : i + 2], None, False, in_link))
            i += 2
            at_line_start = False
            continue
        in_code = bool(stack) and stack[-1] in (_FENCE, "`")
        if in_code:
            marker = stack[-1]
            # A fence closes only at a line start; an inline span at any backtick.
            if text.startswith(marker, i) and (marker == "`" or at_line_start):
                stack.pop()
                units.append(_Unit(marker, marker, True, in_link))
                i += len(marker)
                at_line_start = False
                continue
            units.append(_Unit(ch, None, False, in_link))
            at_line_start = ch == "\n"
            i += 1
            continue
        if at_line_start and text.startswith(_FENCE, i):
            stack.append(_FENCE)
            units.append(_Unit(_FENCE, _FENCE, False, in_link))
            i += len(_FENCE)
            at_line_start = False
            continue
        marker = next((m for m in _INLINE_MARKERS if text.startswith(m, i)), None)
        if marker:
            closing = bool(stack) and stack[-1] == marker
            if closing:
                stack.pop()
            else:
                stack.append(marker)
            units.append(_Unit(marker, marker, closing, in_link))
            i += len(marker)
            at_line_start = False
            continue
        if ch == "[":
            in_link = True
        units.append(_Unit(ch, None, False, in_link))
        if ch == ")" and in_link:
            in_link = False
        at_line_start = ch == "\n"
        i += 1
    return units


def _advance(stack: list[str], u: _Unit) -> list[str]:
    """The open-entity stack after ``u`` (a new list only when it changes)."""
    if u.marker is None:
        return stack
    return stack[:-1] if u.close else stack + [u.marker]


def _closer_len(stack: list[str]) -> int:
    """UTF-16 units needed to close everything in ``stack`` at a chunk boundary.

    The closers we inject count against Telegram's limit like any other text, so
    the budget check has to reserve them — a fence closer is ``\\n``+``` ``` ```.
    """
    return sum(len(m) + 1 if m == _FENCE else len(m) for m in stack)


def _reopen(stack: list[str]) -> str:
    """The markers to re-emit at the top of a continuation chunk, outermost first."""
    return "".join(_FENCE + "\n" if m == _FENCE else m for m in stack)


def _close(body: str, stack: list[str]) -> str:
    """Close every entity left open in ``body``, innermost first."""
    for m in reversed(stack):
        if m == _FENCE:
            if body and not body.endswith("\n"):
                body += "\n"
        body += m
    return body


def split_message(text: str, n: int = MAX_LEN) -> list[str]:
    """Split ``text`` into chunks Telegram will accept as one message each.

    ``text`` is a *rendered MarkdownV2* body, and every chunk is posted with
    ``parse_mode=MarkdownV2`` — so each one must be valid MarkdownV2 **on its
    own**. Three subtleties over a naive ``text[i:i+n]`` slice:

    * **UTF-16 length.** Telegram measures its limit in UTF-16 code units, not
      Python code points (:func:`_utf16_len`), so an astral character (most
      emoji) counts as two. We accumulate whole characters — never splitting a
      surrogate pair — until the next would exceed the budget.
    * **Entity-safe boundaries.** We prefer to break at a blank line, then a
      newline, then a space (never mid-token), as long as the resulting chunk is
      at least half full. A paragraph break cannot sit inside an inline entity,
      so this alone avoids most severed entities.
    * **Balanced entities.** When a break still lands inside an open entity —
      ``*bold*``, ``_italic_``, ``__underline__``, ``~strike~``, ``||spoiler||``,
      `` `code` `` or a ``` fence — we close it at the end of the chunk and
      reopen it at the top of the next, reserving room for the closers so the
      decorated chunk still fits under ``n``. An unterminated entity is exactly
      what Telegram rejects with "Can't find end of Bold entity", which used to
      downgrade the whole message to raw, unformatted Markdown.

    A message already under the limit is returned verbatim — none of this runs
    unless there is an actual split.
    """
    if _utf16_len(text) <= n:
        return [text]

    units = _scan(text)
    chunks: list[str] = []
    stack: list[str] = []
    i = 0
    while i < len(units):
        prefix = _reopen(stack)
        cur_len = _utf16_len(prefix)
        cur_stack = stack
        # Best break seen so far per priority, plus the last legal boundary of
        # any kind: (unit index, open-entity stack there, chunk length there).
        best: dict[int, tuple[int, list[str], int]] = {}
        last: tuple[int, list[str], int] | None = None
        k = i
        while k < len(units):
            u = units[k]
            nxt = _advance(cur_stack, u)
            ulen = _utf16_len(u.s)
            # Always take at least one unit, or an oversized unit would loop.
            if k > i and cur_len + ulen + _closer_len(nxt) > n:
                break
            cur_len += ulen
            cur_stack = nxt
            k += 1
            if k < len(units) and not (u.link and units[k].link):
                if u.s == "\n":
                    blank = k - 2 >= i and units[k - 2].s == "\n"
                    prio = _BREAK_BLANK if blank else _BREAK_LINE
                elif u.s == " ":
                    prio = _BREAK_SPACE
                else:
                    prio = _BREAK_ANY
                last = best[prio] = (k, cur_stack, cur_len)

        if k >= len(units):
            end, end_stack = len(units), cur_stack
        else:
            # A preferred boundary only wins if it doesn't waste half the chunk;
            # otherwise take the fullest legal one (a hard break at ``k`` is the
            # last resort — it can only happen inside an over-long link).
            pick = next(
                (
                    best[p]
                    for p in (_BREAK_BLANK, _BREAK_LINE, _BREAK_SPACE)
                    if p in best and best[p][2] >= n // 2
                ),
                last or (k, cur_stack, cur_len),
            )
            end, end_stack = pick[0], pick[1]

        body = prefix + "".join(u.s for u in units[i:end])
        chunks.append(_close(body, end_stack))
        stack = end_stack
        i = end
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

    ``send(text, parse_mode)`` splits ``text`` at 4096 UTF-16 units
    (:func:`split_message`) and posts each chunk with ``sendMessage``. A chunk
    Telegram rejects is re-sent **as that chunk alone**, unformatted — the
    fallback is per-chunk, so one bad chunk can no longer downgrade a whole
    six-chunk report to raw Markdown. ``True`` means every chunk got delivered
    one way or the other; ``False`` (a chunk that failed even as plain text)
    leaves the relay's whole-message plain-text retry as the last resort.
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

    def _call(self, method: str, fields: dict, *, retry_flood: bool = True) -> dict:
        """Perform a Bot API call, retrying the SAME payload on a 429.

        On flood control we sleep for the advertised ``retry_after`` (bounded by
        :data:`_MAX_RETRY_AFTER`) and re-send, up to :data:`_MAX_SEND_TRIES`
        attempts. Any non-429 response — success or a real error — is returned
        immediately. Preferring a duplicate on retry over a dropped message.

        ``retry_flood=False`` returns the 429 **immediately instead of sleeping**.
        That is the right trade for the *ephemeral status line* and only for it:
        this loop runs in the outbound thread, so honouring a flood-control wait
        for a decoration would stall every real agent message queued behind it for
        up to ``_MAX_RETRY_AFTER * (_MAX_SEND_TRIES - 1)`` seconds. A dropped status
        update costs nothing — the next poll re-sends the *latest* verb anyway,
        which is strictly better than re-sending a stale one — whereas a delayed
        assistant message is the relay failing at its job. The retry loop is not
        bypassed for anything else.
        """
        resp = self._transport(method, fields)
        if not retry_flood:
            return resp
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
            if resp.get("ok"):
                continue
            log.warning("telegram sendMessage failed: %s", resp.get("description", resp))
            if not parse_mode:
                return False
            # A rejected chunk is downgraded ON ITS OWN — one bad chunk must not
            # strip the formatting from the other five (which is what returning
            # False here did: the relay re-sent the WHOLE message as plain text).
            # ``_call`` has already exhausted the 429 retries, so this is a real
            # rejection — a formatting one, since the payload is otherwise fine.
            fields["text"] = unescape_markdown_v2(chunk)
            fields.pop("parse_mode")
            resp = self._call("sendMessage", fields)
            if not resp.get("ok"):
                log.warning(
                    "telegram plain-text fallback failed: %s",
                    resp.get("description", resp),
                )
                return False
        return True

    def post(
        self,
        text: str,
        parse_mode: str | None = None,
        message_thread_id: str | int | None = None,
        reply_markup: dict | None = None,
        retry_flood: bool = True,
    ) -> int | None:
        """Post ONE message and return its ``message_id`` (None on failure).

        The edit-in-place sibling of :meth:`send`: the pane watcher's interactive
        prompts (an AskUserQuestion selector + its keyboard) are short, so this
        does NOT split at 4096 chars, and it returns the id so a later scrape can
        :meth:`edit` the same message rather than post a duplicate as the selector
        settles. ``retry_flood=False`` (the status line) never sleeps on a 429 —
        see :meth:`_call`.
        """
        thread = message_thread_id if message_thread_id is not None else self._topic_id
        fields = {"chat_id": self._chat_id, "text": _truncate_utf16(text)}
        if thread:
            fields["message_thread_id"] = thread
        if parse_mode:
            fields["parse_mode"] = parse_mode
        if reply_markup:
            fields["reply_markup"] = json.dumps(reply_markup)
        resp = self._call("sendMessage", fields, retry_flood=retry_flood)
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
        retry_flood: bool = True,
    ) -> bool:
        """``editMessageText`` an existing message; True once it shows ``text``.

        Tolerates Telegram's "message is not modified" (the content was already
        current) as success — so a re-scrape that produced the same text is a
        no-op, not an error. Any other failure (e.g. the tracked message was
        deleted) returns False so the caller can post a fresh one. A message is
        addressed by chat + id, so no ``message_thread_id`` is needed here.
        ``retry_flood=False`` (the status line) never sleeps on a 429 — see
        :meth:`_call`.
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
        resp = self._call("editMessageText", fields, retry_flood=retry_flood)
        if resp.get("ok"):
            return True
        desc = str(resp.get("description", ""))
        if "not modified" in desc.lower():
            return True
        log.warning("telegram editMessageText failed: %s", desc or resp)
        return False

    def delete(self, message_id: int, retry_flood: bool = True) -> bool:
        """``deleteMessage`` — poof an interactive prompt once it is answered.

        The pane watcher deletes the question / plan / permission message it posted
        as soon as the prompt leaves the pane: its buttons would otherwise stay
        tappable and fire keystrokes at whatever the agent went on to do. The
        ephemeral status line is deleted for a different reason — the turn ended,
        so the message is a corpse and a topic must not accumulate them. A failure
        (already deleted, too old to delete) is logged and reported, never raised —
        the watcher has already dropped its tracking either way.
        """
        resp = self._call(
            "deleteMessage",
            {"chat_id": self._chat_id, "message_id": message_id},
            retry_flood=retry_flood,
        )
        if resp.get("ok"):
            return True
        log.warning("telegram deleteMessage failed: %s", resp.get("description", resp))
        return False

    def chat_action(
        self, action: str = "typing", message_thread_id: str | int | None = None
    ) -> bool:
        """``sendChatAction`` — the "typing…" indicator, while an agent is working.

        This is what makes a phone feel *live*: a thinking agent and a dead one are
        otherwise indistinguishable until the turn lands. Telegram expires the
        indicator after ~5s, so the status watcher re-sends it on each throttled
        tick. Fire-and-forget by design: never retried on flood control (it is pure
        decoration — see :meth:`_call`) and a failure is reported, never raised.
        """
        thread = message_thread_id if message_thread_id is not None else self._topic_id
        fields = {"chat_id": self._chat_id, "action": action}
        if thread:
            fields["message_thread_id"] = thread
        resp = self._call("sendChatAction", fields, retry_flood=False)
        if resp.get("ok"):
            return True
        log.debug("telegram sendChatAction failed: %s", resp.get("description", resp))
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
