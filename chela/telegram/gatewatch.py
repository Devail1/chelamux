"""Pane watcher — surface the live-TUI prompts that the transcript can't relay.

Two blocked-agent prompts are rendered only in Claude Code's live TUI, so this
watcher reads the tmux pane to surface them to the bound topic:

  * a **permission gate** ("Do you want to proceed?" for a Bash/Edit) — never in
    the transcript at all. Blind-scraping to find one would be wasteful and racy,
    so it is **transcript-correlated**: *identity* comes from observing the parsed
    :class:`~chela.telegram.parser.Message` stream (per window, whether the latest
    ``tool_use`` is still *unpaired* and what it was), and only for a window with
    an unpaired ``tool_use`` is the pane read for
    :func:`~chela.telegram.panescan.detect_permission_gate`. A newly-detected gate
    posts ``❓ Permission — <tool>: <command/args>`` (the real command from the
    transcript, falling back to the scraped region).

  * an **AskUserQuestion** selector — its ``tool_use`` was measured to land in the
    transcript only *after* the question is answered, so the unpaired-``tool_use``
    gate would never fire while the question is still answerable. It is therefore
    detected **directly from the pane** every tick (no transcript gate), running
    :func:`~chela.telegram.panescan.detect_askuserquestion` on the same
    :func:`~chela.messenger.capture_pane` read the permission gate uses (one
    capture per window, two detectors). A newly-detected selector posts the
    scraped question text with an inline answer keyboard (semantic ``qa:<i>``
    buttons for a simple single-select, nav row only for the multi-tab fallback).

Both relays are **edge-triggered / de-duped per window**: a permission gate is
tracked by ``tool_use_id`` (cleared on its ``tool_result`` or when the pane stops
showing it); an AskUserQuestion is tracked by a scraped-content signature (cleared
when the selector leaves the pane — answered — with the AskUserQuestion
``tool_result`` as a belt-and-suspenders clear).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from chela.telegram.interactive import nav_only_markup, scraped_reply_markup
from chela.telegram.panescan import (
    AskUQ,
    Gate,
    detect_askuserquestion,
    detect_permission_gate,
)

log = logging.getLogger(__name__)

# A sender posts one line to a topic:
# ``send(text, parse_mode, thread, reply_markup=...)`` — the gate line carries
# shell/path characters and the AskUserQuestion question is scraped pane text, so
# both go as plain text (``parse_mode=None``); no MarkdownV2 escaping to get wrong.
# ``reply_markup`` rides along only for the AskUserQuestion answer keyboard.
Sender = Callable[..., bool]
# A poster posts one AskUserQuestion prompt and returns its Telegram message_id
# (or None on failure): ``post(text, parse_mode, thread, reply_markup) -> id``.
# The watcher remembers that id so a re-scrape edits the same message in place.
Poster = Callable[..., "int | None"]
# An editor rewrites a tracked message by id (tolerating "not modified"):
# ``edit(message_id, text, parse_mode, reply_markup) -> ok``.
Editor = Callable[..., bool]
# A capture reads a window's visible pane text: ``capture(window_id) -> str``.
Capture = Callable[[str], str]

# Longest command/arg detail we inline before truncating (keeps the line tidy;
# the full command is one Bash approval away in the pane).
_MAX_DETAIL = 300


@dataclass
class _PendingTool:
    """An unpaired ``tool_use`` awaiting its ``tool_result`` — identity for a gate."""

    tool_name: str | None
    tool_input: dict | None


def _tool_detail(tool_name: str | None, tool_input: dict | None) -> str | None:
    """The human-facing arg summary for a tool_use, or None if there's nothing apt.

    Bash → its ``command``; the file tools → their ``file_path`` (whitespace
    collapsed to one line and truncated). Anything else returns None so the caller
    falls back to the scraped gate region.
    """
    if not isinstance(tool_input, dict):
        return None
    if tool_name == "Bash":
        val = tool_input.get("command")
    elif tool_name in ("Edit", "MultiEdit", "Write", "Read"):
        val = tool_input.get("file_path")
    elif tool_name == "NotebookEdit":
        val = tool_input.get("notebook_path")
    else:
        val = None
    if not val:
        return None
    flat = " ".join(str(val).split())
    return flat[: _MAX_DETAIL - 1] + "…" if len(flat) > _MAX_DETAIL else flat


def format_gate_message(info: _PendingTool | None, gate: Gate) -> str:
    """Build the enriched relay line for a detected gate.

    Prefers the transcript identity (``❓ Permission — <tool>: <detail>``); if the
    tool is known but has no apt detail, drops the detail; if no transcript
    identity is available at all, falls back to the scraped gate region text.
    """
    tool = info.tool_name if info else None
    detail = _tool_detail(tool, info.tool_input) if info else None
    if tool and detail:
        return f"❓ Permission — {tool}: {detail}"
    if tool:
        return f"❓ Permission — {tool}"
    text = (gate.text or "").strip()
    return f"❓ Permission\n{text}" if text else "❓ Permission"


def format_askuq_message(uq: AskUQ) -> str:
    """The plain-text relay line for a detected AskUserQuestion selector.

    Just the scraped question (the answer buttons carry the options); ``❓`` marks
    it as needing a human. Falls back to a generic prompt if the question scraped
    empty.
    """
    q = (uq.question or "").strip()
    return f"❓ {q}" if q else "❓ Claude is asking a question"


def _askuq_signature(uq: AskUQ) -> str:
    """A stable key for one selector instance — the de-dup / edge-trigger marker."""
    return "\x00".join((uq.question, "|".join(uq.options)))


def _askuq_markup(uq: AskUQ) -> dict:
    """The answer keyboard for a detected selector.

    Semantic ``qa:<i>`` option buttons for a simple single-select; the nav row
    only for the multi-tab / multi-select fallback (never a broken keyboard).
    """
    if uq.multi or not uq.options:
        return nav_only_markup()
    return scraped_reply_markup(uq.options)


class PermissionGateWatcher:
    """Observes the message stream + polls panes to surface the live-TUI prompts.

    Wire :meth:`observe` into the monitor's ``on_message`` (alongside the relay)
    so the watcher tracks per-window unpaired ``tool_use``s (permission-gate
    identity) and clears an answered selector's marker, and call :meth:`poll` once
    per outbound cycle (after ``monitor.poll``) with the same window-id set. Each
    poll captures every window's pane once and runs both detectors: the permission
    gate only when a ``tool_use`` is unpaired, the AskUserQuestion selector always
    (it is never in the transcript while pending). Each newly-detected prompt is
    relayed exactly once.
    """

    def __init__(
        self,
        sender: Sender,
        registry,
        *,
        capture: Capture,
        detect: Callable[[str], Gate | None] = detect_permission_gate,
        detect_askuq: Callable[[str], AskUQ | None] = detect_askuserquestion,
        post: Poster | None = None,
        edit: Editor | None = None,
    ):
        self._sender = sender
        self._registry = registry
        self._capture = capture
        self._detect = detect
        self._detect_askuq = detect_askuq
        # The AskUserQuestion relay edits its message in place as the selector
        # settles (a mid-render partial → the full option list is ONE message, not
        # two). Production wires ``post``/``edit`` from :class:`BotSender`; when
        # they're absent (plain-sender tests) it degrades to posting via ``sender``
        # with no id to edit, so a changed scrape re-posts (the pre-A2 behaviour).
        self._post = post
        self._edit = edit
        # window_id -> {tool_use_id: _PendingTool}, insertion-ordered so the most
        # recently added (the likely-blocked tool) is the last key.
        self._pending: dict[str, dict[str, _PendingTool]] = {}
        # window_id -> tool_use_id we've already relayed a gate for (edge trigger).
        self._relayed: dict[str, str] = {}
        # window_id -> signature of the AskUserQuestion selector we've relayed
        # (edge trigger); cleared when the selector leaves the pane / is answered.
        self._relayed_uq: dict[str, str] = {}
        # window_id -> message_id of the AskUserQuestion prompt we posted, so a
        # re-scrape (more options rendered / cursor moved) edits it in place
        # instead of double-posting. Cleared alongside ``_relayed_uq``.
        self._uq_msg: dict[str, int] = {}

    def observe(self, window_id: str, msg) -> None:
        """Track ``tool_use``/``tool_result`` pairing for one parsed message.

        Mirrors the transcript parser's own pairing (keyed by ``tool_use_id``) but
        retains the tool ``input`` so the gate relay can name the real command.
        An AskUserQuestion ``tool_result`` (which lands at answer-time) clears that
        window's selector marker — the belt-and-suspenders to the pane-gone clear.
        Non-tool events are ignored.
        """
        ct = getattr(msg, "content_type", None)
        uid = getattr(msg, "tool_use_id", None)
        if ct == "tool_use":
            if uid:
                self._pending.setdefault(window_id, {})[uid] = _PendingTool(
                    tool_name=getattr(msg, "tool_name", None),
                    tool_input=getattr(msg, "tool_input", None),
                )
        elif ct == "tool_result":
            pend = self._pending.get(window_id)
            if pend and uid:
                pend.pop(uid, None)
            if getattr(msg, "tool_name", None) == "AskUserQuestion":
                # The question was just answered → drop the tracked message so a
                # genuinely new question posts fresh (never edits the answered one).
                self._relayed_uq.pop(window_id, None)
                self._uq_msg.pop(window_id, None)

    def forget(self, window_id: str) -> None:
        """Drop all state for a window (e.g. after it closes)."""
        self._pending.pop(window_id, None)
        self._relayed.pop(window_id, None)
        self._relayed_uq.pop(window_id, None)
        self._uq_msg.pop(window_id, None)

    def poll(self, window_ids) -> None:
        """Read each window's pane once and relay newly-detected prompts.

        Every bound window's pane is captured each tick — the AskUserQuestion
        selector is never in the transcript while pending, so its detection can't
        be gated on an unpaired ``tool_use`` the way the permission gate is. The
        single capture feeds both detectors.
        """
        for wid in window_ids:
            try:
                self._poll_window(wid)
            except Exception:
                log.exception("pane-watch poll failed for %s", wid)

    # -- internals ---------------------------------------------------------

    def _poll_window(self, window_id: str) -> None:
        # One capture per window per tick, shared by both detectors.
        pane = self._capture(window_id)
        self._poll_askuq(window_id, pane)
        self._poll_gate(window_id, pane)

    def _poll_askuq(self, window_id: str, pane: str) -> None:
        """Relay a newly-detected AskUserQuestion selector (pane-only, edge-triggered).

        The selector is drawn incrementally, so successive scrapes of the SAME
        question differ (option 1 only → all options; cursor moves). Rather than
        post a fresh message per changed scrape (the double-relay bug), the first
        scrape posts ONE message and every later scrape of that window **edits it
        in place** — the partial and the settled selector collapse into a single
        message that fills in. The tracking clears when the selector leaves the
        pane (answered) so the next question posts fresh.
        """
        uq = self._detect_askuq(pane)
        if uq is None:
            # Selector gone (answered / dismissed) — clear so the next relays again.
            self._relayed_uq.pop(window_id, None)
            self._uq_msg.pop(window_id, None)
            return

        signature = _askuq_signature(uq)
        if self._relayed_uq.get(window_id) == signature:
            return  # unchanged scrape — edge-triggered, neither re-post nor edit

        thread = self._registry.thread_for_window(window_id)
        if thread is None:
            log.debug("AskUserQuestion on %s but no bound topic; skipping", window_id)
            return

        text = format_askuq_message(uq)
        markup = _askuq_markup(uq)
        msg_id = self._uq_msg.get(window_id)

        # A changed scrape for a window we already posted for → EDIT that message
        # (collapse partial + full into one). On edit failure (message deleted)
        # drop the id and fall through to a fresh post.
        if msg_id is not None and self._edit is not None:
            if self._edit(msg_id, text, None, markup):
                self._relayed_uq[window_id] = signature
                return
            self._uq_msg.pop(window_id, None)

        if self._post is not None:
            new_id = self._post(text, None, thread, markup)
            if new_id is not None:
                self._uq_msg[window_id] = new_id
        else:
            # No id-returning poster (plain-sender tests) — post via the sender;
            # without an id we can't edit, so a changed scrape re-posts (pre-A2).
            self._sender(text, None, thread, reply_markup=markup)
        self._relayed_uq[window_id] = signature

    def _poll_gate(self, window_id: str, pane: str) -> None:
        """Relay a newly-detected permission gate (transcript-gated, edge-triggered)."""
        pend = self._pending.get(window_id)
        if not pend:
            # No unpaired tool_use → nothing can be blocked; clear any relayed gate
            # (the tool_result has arrived) so a later gate edge-triggers again.
            self._relayed.pop(window_id, None)
            return

        uid = next(reversed(pend))  # latest unpaired tool_use
        info = pend[uid]

        gate = self._detect(pane)
        if gate is None:
            # Pane no longer shows a gate — clear so a fresh one relays again.
            self._relayed.pop(window_id, None)
            return

        if self._relayed.get(window_id) == uid:
            return  # already relayed this gate — edge-triggered, not per-poll

        thread = self._registry.thread_for_window(window_id)
        if thread is None:
            log.debug("permission gate on %s but no bound topic; skipping", window_id)
            return

        body = format_gate_message(info, gate)
        self._sender(body, None, thread)
        self._relayed[window_id] = uid
