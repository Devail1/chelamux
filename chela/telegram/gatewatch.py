"""Pane watcher — surface the live-TUI prompts that the transcript can't relay.

Three blocked-agent prompts are rendered only in Claude Code's live TUI, so this
watcher reads each bound window's tmux pane every tick and surfaces them to the
window's topic with a tap-to-answer keyboard:

  * a **permission gate** ("Do you want to proceed?" for a Bash/Edit) — never in
    the transcript at all. Posts ``❓ Permission — <tool>: <command>`` with
    ✅ Allow once / ❌ Deny (:func:`~chela.telegram.interactive.permission_reply_markup`).
  * an **AskUserQuestion** selector — posts the scraped question with one button
    per option (:func:`~chela.telegram.interactive.scraped_reply_markup`; the nav
    row only for the multi-tab / multi-select fallback).
  * an **ExitPlanMode** plan approval — posts the scraped plan with
    ✅ Approve / 📝 Keep planning (:func:`~chela.telegram.interactive.plan_reply_markup`).

**All three are detected from the pane, with no transcript precondition.** Slice
C1 originally gated the permission pane-read on the window having an *unpaired*
``tool_use`` in the transcript, on the theory that the transcript says a call is
pending and the pane then confirms it is blocked. Live testing (2026-07-12) showed
that correlation can never fire: Claude Code appends the gated call's ``tool_use``
to the JSONL only **once the human answers** — exactly as measured for
AskUserQuestion (A2) and ExitPlanMode (B2) — so while a gate is pending there is
*nothing* unpaired, the pane is never read, and the ``❓ Permission`` message never
posts. The gate's identity therefore comes from the **pane** too
(:func:`~chela.telegram.panescan.scrape_gate_identity` reads the "Bash command /
<cmd>" header the dialog is drawn with); a transcript ``tool_use``, if one happens
to be unpaired, is only a fallback.

All three relays are **edge-triggered / de-duped per window** by a scraped-content
signature: the first scrape posts one message, a changed scrape (the selector
finished rendering) **edits it in place** rather than double-posting, and an
unchanged scrape does nothing. When the prompt leaves the pane (answered), the
tracked message is **deleted** — its buttons are dead the moment the prompt is
gone (a stray tap would fire ``Enter`` at whatever the agent is doing next), and
the transcript's ``tool_result`` is the durable record of what was chosen. The
matching ``tool_result`` clears the tracking too, as belt-and-suspenders.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from chela.telegram.interactive import (
    nav_only_markup,
    permission_reply_markup,
    plan_reply_markup,
    scraped_reply_markup,
)
from chela.telegram.panescan import (
    AskUQ,
    ExitPlan,
    Gate,
    detect_askuserquestion,
    detect_exitplanmode,
    detect_permission_gate,
)

log = logging.getLogger(__name__)

# A sender posts one prompt to a topic:
# ``send(text, parse_mode, thread, reply_markup=...) -> ok``. Every prompt body is
# scraped pane text (shell/path characters, TUI glyphs), so all of them go as
# plain text (``parse_mode=None``) — no MarkdownV2 escaping to get wrong. Only
# used when no id-returning ``post`` is wired (plain-sender tests).
Sender = Callable[..., bool]
# A poster posts one prompt and returns its Telegram message_id (or None on
# failure): ``post(text, parse_mode, thread, reply_markup) -> id``. The watcher
# remembers that id so a re-scrape edits the same message and a resolved prompt
# can be deleted.
Poster = Callable[..., "int | None"]
# An editor rewrites a tracked message by id (tolerating "not modified"):
# ``edit(message_id, text, parse_mode, reply_markup) -> ok``.
Editor = Callable[..., bool]
# A deleter removes a tracked message by id: ``delete(message_id) -> ok``.
Deleter = Callable[[int], bool]
# A capture reads a window's visible pane text: ``capture(window_id) -> str``.
Capture = Callable[[str], str]

# Longest command/arg detail we inline before truncating (keeps the line tidy;
# the full command is one /screenshot away).
_MAX_DETAIL = 300

# Longest plan body inlined before truncating (the full plan is one /screenshot
# away; a shorter cap keeps the approval message readable on a phone).
_MAX_PLAN = 2000

# The prompt kinds this watcher tracks, one tracked message each per window.
_GATE = "permission"
_ASKUQ = "askuserquestion"
_PLAN = "exitplanmode"


@dataclass
class _PendingTool:
    """An unpaired ``tool_use`` awaiting its ``tool_result``."""

    tool_name: str | None
    tool_input: dict | None


@dataclass
class _Tracked:
    """The prompt message currently posted for one window + prompt kind.

    ``signature`` is the scraped content it was last rendered from (the
    edge-trigger / de-dup marker); ``message_id`` is the Telegram message to edit
    as the prompt re-renders and to delete once it resolves (None when no
    id-returning poster is wired).
    """

    signature: str
    message_id: int | None


def _clip(text: str, limit: int = _MAX_DETAIL) -> str:
    """One line, truncated with an ellipsis — safe to inline in a relay message."""
    flat = " ".join(str(text).split())
    return flat[: limit - 1] + "…" if len(flat) > limit else flat


def _tool_detail(tool_name: str | None, tool_input: dict | None) -> str | None:
    """The human-facing arg summary for a ``tool_use``, or None if there's none apt.

    Bash → its ``command``; the file tools → their ``file_path``. Anything else
    returns None. This is the *fallback* identity for a gate: the pane is the
    primary source (the gated ``tool_use`` isn't in the transcript yet), so this
    only fires when the dialog header couldn't be scraped and some tool_use
    happened to be unpaired.
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
    return _clip(val) if val else None


def format_gate_message(info: _PendingTool | None, gate: Gate) -> str:
    """Build the enriched relay line for a detected gate.

    Identity comes from the **pane** first (the gate dialog names the tool and the
    command it wants to run — and while the gate is pending that is the only place
    it exists), then from an unpaired transcript ``tool_use`` if the dialog wasn't
    recognisable, and finally — with no identity at all — from the scraped gate
    region itself, so a reworded dialog still relays something actionable.
    """
    tx_tool = info.tool_name if info else None
    tool = gate.tool or tx_tool
    detail = _clip(gate.detail) if gate.detail else None
    if detail is None and tool == tx_tool and info is not None:
        detail = _tool_detail(tx_tool, info.tool_input)
    if tool and detail:
        return f"❓ Permission — {tool}: {detail}"
    if tool:
        return f"❓ Permission — {tool}"
    text = (gate.text or "").strip()
    return f"❓ Permission\n{text}" if text else "❓ Permission"


def _gate_signature(gate: Gate) -> str:
    """A stable key for one gate instance — the de-dup / edge-trigger marker."""
    return "\x00".join((gate.kind, gate.tool or "", gate.detail or "", gate.text))


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


def format_plan_message(plan: ExitPlan) -> str:
    """The plain-text relay body for a detected ExitPlanMode plan approval.

    The scraped plan (the text above the options) under a 📋 header; the
    approve / keep-planning buttons carry the choices, so the options are not
    repeated. A long plan is truncated with a ``/screenshot`` pointer; an empty
    scrape (plan scrolled off the pane) falls back to a generic prompt.
    """
    body = (plan.text or "").strip()
    if not body:
        return "📋 Claude has written up a plan — approve to proceed."
    if len(body) > _MAX_PLAN:
        body = body[:_MAX_PLAN].rstrip() + "\n… (/screenshot for full)"
    return f"📋 Plan review:\n{body}"


def _plan_signature(plan: ExitPlan) -> str:
    """A stable key for one plan-approval instance — the de-dup / edge marker."""
    return plan.text


class PermissionGateWatcher:
    """Polls panes to surface the live-TUI prompts, and answers them from Telegram.

    Call :meth:`poll` once per outbound cycle (after ``monitor.poll``) with the
    bound window ids: each window's pane is captured **once** and run through all
    three detectors, and each newly-detected prompt is relayed exactly once with
    its keyboard. Wire :meth:`observe` into the monitor's ``on_message`` (alongside
    the relay) so an answered prompt's ``tool_result`` also clears its tracking,
    and so an unpaired ``tool_use`` is available as the gate's fallback identity.
    """

    def __init__(
        self,
        sender: Sender,
        registry,
        *,
        capture: Capture,
        detect: Callable[[str], Gate | None] = detect_permission_gate,
        detect_askuq: Callable[[str], AskUQ | None] = detect_askuserquestion,
        detect_plan: Callable[[str], ExitPlan | None] = detect_exitplanmode,
        post: Poster | None = None,
        edit: Editor | None = None,
        delete: Deleter | None = None,
    ):
        self._sender = sender
        self._registry = registry
        self._capture = capture
        self._detect = detect
        self._detect_askuq = detect_askuq
        self._detect_plan = detect_plan
        # Each relay edits its message in place as the prompt settles (a mid-render
        # partial → the full option list is ONE message, not two) and deletes it
        # once answered. Production wires ``post``/``edit``/``delete`` from
        # :class:`BotSender`; when they're absent (plain-sender tests) it degrades
        # to posting via ``sender`` with no id to edit or delete.
        self._post = post
        self._edit = edit
        self._delete = delete
        # window_id -> {tool_use_id: _PendingTool}, insertion-ordered so the most
        # recently added is the last key.
        self._pending: dict[str, dict[str, _PendingTool]] = {}
        # window_id -> {prompt kind: _Tracked} — the edge-trigger markers + the
        # message ids to edit / delete.
        self._prompts: dict[str, dict[str, _Tracked]] = {}

    def observe(self, window_id: str, msg) -> None:
        """Track ``tool_use``/``tool_result`` pairing for one parsed message.

        The ``tool_use`` half is the gate's *fallback* identity (the gated call
        itself is not in the transcript while it is gated, so this is whatever else
        was in flight). The ``tool_result`` half is the belt-and-suspenders resolve
        signal for a pane prompt: an AskUserQuestion / ExitPlanMode result lands at
        answer-time, so it clears (and poofs) that window's tracked prompt even if
        the pane hasn't repainted yet. Non-tool events are ignored.
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
            name = getattr(msg, "tool_name", None)
            if name == "AskUserQuestion":
                self._resolve(window_id, _ASKUQ)
            elif name == "ExitPlanMode":
                self._resolve(window_id, _PLAN)

    def forget(self, window_id: str) -> None:
        """Drop all state for a window (e.g. after it closes)."""
        self._pending.pop(window_id, None)
        self._prompts.pop(window_id, None)

    def poll(self, window_ids) -> None:
        """Read each window's pane once and relay newly-detected prompts."""
        for wid in window_ids:
            try:
                self._poll_window(wid)
            except Exception:
                log.exception("pane-watch poll failed for %s", wid)

    # -- internals ---------------------------------------------------------

    def _poll_window(self, window_id: str) -> None:
        # One capture per window per tick, shared by all three detectors.
        pane = self._capture(window_id)
        uq = self._detect_askuq(pane)
        plan = self._detect_plan(pane)
        # A plan-approval selector's "❯ 1. Yes, and auto-accept edits" row also
        # matches the permission menu's signature, so look for a gate only when
        # neither selector is up — otherwise one pane would relay two prompts.
        gate = None if (uq is not None or plan is not None) else self._detect(pane)

        self._sync(window_id, _ASKUQ, uq, _askuq_signature, format_askuq_message, _askuq_markup)
        self._sync(
            window_id, _PLAN, plan, _plan_signature, format_plan_message,
            lambda _p: plan_reply_markup(),
        )
        self._sync(
            window_id, _GATE, gate, _gate_signature,
            lambda g: format_gate_message(self._latest_pending(window_id), g),
            lambda _g: permission_reply_markup(),
        )

    def _latest_pending(self, window_id: str) -> _PendingTool | None:
        """The most recent unpaired ``tool_use`` for a window, if any."""
        pend = self._pending.get(window_id)
        if not pend:
            return None
        return pend[next(reversed(pend))]

    def _sync(self, window_id, kind, detected, sig_fn, text_fn, markup_fn) -> None:
        """Post / edit / poof the tracked message for one prompt kind.

        The single relay path all three prompts share. ``detected is None`` means
        the prompt is no longer on the pane (answered / dismissed) → resolve it.
        Otherwise: an unchanged scrape is a no-op (edge-triggered — a still-open
        prompt is never re-posted), a changed scrape edits the tracked message in
        place (so a mid-render partial and the settled prompt are ONE message), and
        a first scrape posts it. An edit that fails (the message was deleted) falls
        back to a fresh post.
        """
        if detected is None:
            self._resolve(window_id, kind)
            return

        signature = sig_fn(detected)
        tracked = self._prompts.get(window_id, {}).get(kind)
        if tracked is not None and tracked.signature == signature:
            return

        thread = self._registry.thread_for_window(window_id)
        if thread is None:
            log.debug("%s prompt on %s but no bound topic; skipping", kind, window_id)
            return

        text = text_fn(detected)
        markup = markup_fn(detected)

        if tracked is not None and tracked.message_id is not None and self._edit is not None:
            if self._edit(tracked.message_id, text, None, markup):
                tracked.signature = signature
                return
            tracked.message_id = None  # gone from Telegram → post a fresh one

        message_id = None
        if self._post is not None:
            message_id = self._post(text, None, thread, markup)
        else:
            # No id-returning poster (plain-sender tests) — post via the sender;
            # without an id we can neither edit nor poof the message.
            self._sender(text, None, thread, reply_markup=markup)
        self._prompts.setdefault(window_id, {})[kind] = _Tracked(signature, message_id)

    def _resolve(self, window_id: str, kind: str) -> None:
        """The prompt is answered — drop its marker and poof its message.

        Deleting is the point: the buttons on an answered prompt are not just
        stale, they are *live* — a later tap would fire Enter/Escape at whatever
        the agent is doing by then. The transcript's ``tool_result`` (relayed
        separately) is the record of what was chosen.
        """
        tracked = self._prompts.get(window_id, {}).pop(kind, None)
        if tracked is None or tracked.message_id is None or self._delete is None:
            return
        try:
            self._delete(tracked.message_id)
        except Exception:
            log.exception("failed to delete resolved %s prompt on %s", kind, window_id)
