"""Pane watcher — surface the live-TUI prompts that the transcript can't relay.

Three blocked-agent prompts are rendered only in Claude Code's live TUI, so this
watcher reads each bound window's tmux pane every tick and surfaces them to the
window's topic with a tap-to-answer keyboard:

  * a **permission gate** ("Do you want to proceed?" for a Bash/Edit) — never in
    the transcript at all. Posts ``❓ Permission — <tool>: <command>`` with
    ✅ Allow once / ❌ Deny (:func:`~chela.telegram.interactive.permission_reply_markup`).
  * an **AskUserQuestion** selector — posts the scraped question with every option
    numbered in full **in the message body** (the descriptions too), and a compact
    numeric selector button per option
    (:func:`~chela.telegram.interactive.scraped_reply_markup`; the nav row only for
    the multi-tab / multi-select fallback). The options used to live *only* in the
    button captions, which Telegram hard-truncates to one line — so on a phone the
    human was handed four half-sentences and asked to decide (CMX-32). The body is
    the only surface that wraps, so that is where the options belong.
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

The same per-tick pane capture also feeds a fourth, non-interactive relay — the
**ephemeral status line** (:class:`StatusRelay`): Claude Code's live working verb
(``✻ Cerebrating… (2m 45s · ↓ 12.0k tokens) · 2 shells``) posted as one message
that edits in place while the agent works and **deletes itself** when the turn
ends, so a phone can tell a *thinking* agent from a *dead* one. It is a TUI render
rather than an event — there is no ``Boondoggling`` hook and there cannot be — so
unlike the three gates, the pane is its correct source permanently.

All three prompt relays are **edge-triggered / de-duped per window** by a scraped-content
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
import time
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
    Status,
    detect_askuserquestion,
    detect_exitplanmode,
    detect_permission_gate,
    detect_status,
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
# A typing indicator: ``typing(thread) -> ok``. Fire-and-forget decoration.
Typing = Callable[..., bool]

# Longest command/arg detail we inline before truncating (keeps the line tidy;
# the full command is one /screenshot away).
_MAX_DETAIL = 300

# Longest plan body inlined before truncating (the full plan is one /screenshot
# away; a shorter cap keeps the approval message readable on a phone).
_MAX_PLAN = 2000

# Telegram rejects a message body over 4096 characters, so an AskUserQuestion body
# (question + every option, in full) is budgeted against that cap: the question is
# clipped first, then the remaining room is split evenly across the options, each
# keeping at least _MIN_OPTION characters. ``_OPTION_PREFIX`` is the room the
# "N. " numbering costs per line.
_TG_TEXT_LIMIT = 4096
_MAX_QUESTION = 700
_MIN_OPTION = 80
_MIN_DESC = 40
_OPTION_PREFIX = 6

# The prompt kinds this watcher tracks, one tracked message each per window.
_GATE = "permission"
_ASKUQ = "askuserquestion"
_PLAN = "exitplanmode"

# Claude Code repaints its status line several times a second (the elapsed timer
# ticks even when the verb doesn't), so the ephemeral status message is edited at
# most once per this many seconds per window — otherwise a single working agent
# would walk the topic straight into Telegram's per-chat flood limit. Ported from
# ccbot's STATUS_EDIT_MIN_INTERVAL, which ran at this value in production. A
# dropped update is free: the next poll sends the *latest* verb, not the stale one.
STATUS_EDIT_MIN_INTERVAL = 4.0

# A settled turn shorter than this, with nothing left running, is not worth a
# permanent message — it poofs. Tunable: see :func:`should_keep`.
STATUS_KEEP_MIN_SECONDS = 30

# The status is one line; a pathological verb is clipped rather than wrapped.
_MAX_STATUS = 200


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
    """The plain-text relay body for a detected AskUserQuestion selector.

    The scraped question, then the option labels **in full, numbered** — because
    the message body is the only Telegram surface that *wraps*. The options used to
    live solely in the button captions, and a button caption is the one surface
    Telegram hard-truncates to a single line: a phone showed four half-sentences
    ("Rotate the group, skip the …") and none of the reasoning, i.e. the human was
    asked to decide with the basis for the decision structurally withheld. So the
    body carries the meaning now and the buttons are bare numeric selectors
    (:func:`~chela.telegram.interactive.scraped_reply_markup`) — the numbering here
    is what they select, so it must stay 1:1 with the scraped option order.

    Options are listed only for the shapes that get semantic buttons; the multi-tab
    / multi-select fallback gets the nav row, so its body stays the question alone.
    Each option's scraped description (the prose Claude Code draws under it — the
    TUI renders one per option, not just for the focused one) is indented beneath
    it, so the *reasoning* the decision hinges on travels with the choice.

    A pathological option is **truncated, never dropped** (a dropped option would be
    unpickable), and the whole body stays inside Telegram's 4096-char cap.
    """
    q = _clip(uq.question, _MAX_QUESTION) if (uq.question or "").strip() else ""
    head = f"❓ {q}" if q else "❓ Claude is asking a question"
    if uq.multi or not uq.options:
        return head

    # Split what's left of the message cap evenly across the options, so one verbose
    # option can't crowd the others out — each keeps a readable slice, label first.
    room = _TG_TEXT_LIMIT - len(head) - 2 * (len(uq.options) + 1)
    per_option = max(_MIN_OPTION, room // len(uq.options))
    descriptions = uq.descriptions or ()
    lines = [head]
    for i, label in enumerate(uq.options, start=1):
        label_text = _clip(label, max(_MIN_OPTION, per_option - _OPTION_PREFIX))
        lines.append(f"\n{i}. {label_text}")
        desc = descriptions[i - 1] if i - 1 < len(descriptions) else ""
        left = per_option - len(label_text) - _OPTION_PREFIX
        if desc and left >= _MIN_DESC:
            lines.append(f"   {_clip(desc, left)}")
    body = "\n".join(lines)
    return body if len(body) <= _TG_TEXT_LIMIT else body[: _TG_TEXT_LIMIT - 1] + "…"


def _askuq_signature(uq: AskUQ) -> str:
    """A stable key for one selector instance — the de-dup / edge-trigger marker.

    Everything the message body renders is in here (question, options **and their
    descriptions**), so a scrape that changes the body — e.g. the descriptions
    landing a repaint after the option rows — edits the message in place instead of
    being swallowed as a no-op.
    """
    return "\x00".join(
        (uq.question, "|".join(uq.options), "|".join(uq.descriptions or ()))
    )


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


def format_status_message(st: Status) -> str:
    """The one-line status body — the verb, plus the shells if the verb omits them.

    Working: ``✻ Cerebrating… (2m 45s · ↓ 12.0k tokens) · 2 shells``.
    Settled: ``✅ Worked for 1m 17s · 1 shell still running`` (the settled line
    names its own shells, so they are not appended twice).

    Plain text: the verb is scraped TUI content and an active message is rewritten
    every few seconds, so there is no MarkdownV2 escaping to get wrong on a hot path.
    """
    text = f"{'✻' if st.active else '✅'} {st.verb}"
    if st.shells and "shell" not in st.verb:
        text += f" · {st.shells} shell{'' if st.shells == 1 else 's'}"
    return _clip(text, _MAX_STATUS)


def should_keep(st: Status) -> bool:
    """Is this **settled** status worth leaving in the topic, rather than poofing?

    The ticking verb is pure liveness and always stops. But the settled line it
    turns into is not uniformly noise, so the message is kept *conditionally* — and
    the whole rule lives here, in one function, so that "always keep" or "always
    poof" stays a one-line change rather than a redesign:

    * **shells still running** → KEEP. Background work that outlived the turn is a
      *warning*, not a receipt — it is the one genuinely actionable thing the status
      line ever says, and deleting it throws away the only part worth having.
    * **a long turn** (≥ :data:`STATUS_KEEP_MIN_SECONDS`) → KEEP. How long the agent
      actually worked is useful context to scroll back to.
    * **otherwise** (a quick turn, nothing left running) → poof. A receipt for six
      seconds of work is exactly the silt this message is designed not to leave.

    Keeping costs nothing extra: it is the SAME tracked message, and "keep" simply
    means stop editing it instead of deleting it. No second message, no extra call.
    """
    if st.shells:
        return True
    return st.seconds is not None and st.seconds >= STATUS_KEEP_MIN_SECONDS


@dataclass
class _StatusMsg:
    """The ephemeral status message currently posted for one window.

    Its own record rather than :class:`_Tracked` because its lifecycle needs two
    things a prompt's does not: ``last_edit`` (the throttle clock) and ``thread``
    (a window that moves to another topic must have its old status poofed, not
    edited in place in a topic it no longer belongs to). ``text`` is the de-dup
    marker — the equivalent of a prompt's ``signature``.
    """

    message_id: int
    thread: object
    text: str
    last_edit: float


class StatusRelay:
    """The live "Claude is working" verb, as a self-deleting Telegram message.

    On a phone, a **thinking** agent and a **dead** one look identical: the relay
    ships finished turns, so a long tool run is indistinguishable from a crash.
    This surfaces Claude Code's own status line as ONE tracked message per window
    that is **posted** on first sight, **edited in place** as the verb changes, and
    stops ticking the moment the turn ends — at which point it either **poofs** or
    settles into its final summary, per :func:`should_keep`. The ticking always
    stops (a topic must not silt up with live-looking corpses); what survives is
    only the settled line that still *says* something — shells still running, or a
    turn long enough to be worth the scrollback.

    Three things make this safe to run every couple of seconds on a whole fleet:

    * **It adds no tmux calls.** :meth:`sync` is handed the pane text
      :class:`PermissionGateWatcher` already captured for the three gate detectors;
      the status is a fourth read of the *same* string.
    * **It throttles and de-dups.** At most one edit per
      :data:`STATUS_EDIT_MIN_INTERVAL` per window, and an unchanged body makes no
      API call at all. Claude repaints the line about once a second; posting that
      through would rate-limit the topic within a minute.
    * **Every failure is swallowed.** This is decoration. A 429, a message the user
      deleted, a "not modified" — none of them may raise, wedge the relay, or delay
      a real agent message (which is also why the status calls opt out of the 429
      sleep-and-retry loop; see :meth:`~chela.telegram.relay.BotSender._call`).

    The ``typing`` indicator (``sendChatAction``) rides the same tick, and is what
    actually makes a phone feel live. ccbot fired it only while the pane said "esc
    to interrupt" — but on 2.1.207 that hint is width-dependent chrome and is
    simply absent on a narrow pane (measured: it appears nowhere on a 110-column
    window), so keying on it would mean the indicator never fires. The presence of
    a status line *is* the "agent is working" signal, so that is the trigger.
    """

    def __init__(
        self,
        registry,
        *,
        post: Poster,
        edit: Editor,
        delete: Deleter,
        typing: Typing | None = None,
        detect: Callable[[str], Status | None] = detect_status,
        now: Callable[[], float] = time.monotonic,
        min_interval: float = STATUS_EDIT_MIN_INTERVAL,
    ):
        self._registry = registry
        self._post = post
        self._edit = edit
        self._delete = delete
        self._typing = typing
        self._detect = detect
        self._now = now
        self._min_interval = min_interval
        self._tracked: dict[str, _StatusMsg] = {}

    def sync(self, window_id: str, pane: str) -> None:
        """Reconcile one window's status message against its freshly captured pane.

        Working → post it (first sight), or edit it (the verb moved) subject to the
        throttle, or do nothing (unchanged text / inside the throttle window).
        **Settled** → stop ticking, and either keep the summary or poof it
        (:meth:`_settle`). No status line at all → poof: there is nothing to settle
        on.
        """
        try:
            st = self._detect(pane)
        except Exception:
            log.exception("status scrape failed for %s", window_id)
            return
        if st is None:
            self.resolve(window_id)
            return

        thread = self._registry.thread_for_window(window_id)
        if thread is None:
            return

        tracked = self._tracked.get(window_id)
        if tracked is not None and tracked.thread != thread:
            # The window moved to another topic — its old message can't be edited
            # into the new one, so poof it and post fresh below.
            self.resolve(window_id)
            tracked = None

        if not st.active:
            self._settle(window_id, st, tracked)
            return

        text = format_status_message(st)
        now = self._now()

        if tracked is None:
            self._act(thread)
            mid = self._swallow(self._post, text, None, thread, None)
            if isinstance(mid, int):
                self._tracked[window_id] = _StatusMsg(mid, thread, text, now)
            return

        if now - tracked.last_edit < self._min_interval:
            return  # throttled — the next poll will carry a fresher verb anyway

        # Keep the "typing…" bubble alive (Telegram expires it after ~5s) even on a
        # tick whose text is unchanged, or a stalled verb would look like a death.
        self._act(thread)
        tracked.last_edit = now
        if text == tracked.text:
            return  # de-dup: identical body → no API call at all

        if self._swallow(self._edit, tracked.message_id, text, None, None):
            tracked.text = text
        else:
            # Gone from Telegram (deleted by the user, too old). Drop the tracking;
            # the next tick posts a fresh one.
            self._tracked.pop(window_id, None)

    def _settle(self, window_id: str, st: Status, tracked: "_StatusMsg | None") -> None:
        """The turn is over — stop ticking, then keep the summary or poof it.

        Either way the window is dropped from tracking, so the message is never
        edited again: the ticking noise stops unconditionally, which is the part of
        the behaviour that is not negotiable. Whether the settled *summary* survives
        is :func:`should_keep`'s call.

        A turn that ended without ever posting a status (short enough to fall between
        two polls) settles into nothing at all: we never post a message just to
        announce that it finished.
        """
        self._tracked.pop(window_id, None)
        if tracked is None:
            return
        if not should_keep(st):
            self._swallow(self._delete, tracked.message_id)
            return
        # One last edit — the ticking verb becomes the settled summary and stays
        # put (the shells-still-running warning is the whole reason to keep it).
        text = format_status_message(st)
        if text != tracked.text:
            self._swallow(self._edit, tracked.message_id, text, None, None)

    def resolve(self, window_id: str) -> None:
        """Delete this window's status message outright. Idempotent.

        Used when there is no settled summary to weigh — the pane shows no status
        line at all, or the window is gone from the polled set entirely.
        """
        tracked = self._tracked.pop(window_id, None)
        if tracked is not None:
            self._swallow(self._delete, tracked.message_id)

    def retain(self, window_ids) -> None:
        """Poof the status of every window that is no longer being polled.

        A window that dies or is unbound simply stops appearing in the polled set,
        so its status message would otherwise hang in the topic forever — the exact
        "dead status message" this whole design exists to prevent. Costs nothing:
        it is a set difference over state we already hold, with no tmux call.
        """
        live = set(window_ids)
        for wid in [w for w in self._tracked if w not in live]:
            self.resolve(wid)

    def forget(self, window_id: str) -> None:
        """Drop (and poof) a window's status — e.g. after its window closes."""
        self.resolve(window_id)

    # -- internals ---------------------------------------------------------

    def _act(self, thread) -> None:
        """Refresh the typing indicator, if one is wired. Never raises."""
        if self._typing is not None:
            self._swallow(self._typing, thread)

    @staticmethod
    def _swallow(fn, *args):
        """Call a Telegram op, swallowing every failure.

        The status line is decoration: it must never propagate an exception into
        the outbound loop, where it would take the *real* relay down with it.
        """
        try:
            return fn(*args)
        except Exception:
            log.debug("status telegram call failed", exc_info=True)
            return None


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
        status: "StatusRelay | None" = None,
    ):
        self._sender = sender
        self._registry = registry
        self._capture = capture
        self._detect = detect
        self._detect_askuq = detect_askuq
        self._detect_plan = detect_plan
        # The ephemeral status line, when enabled: it reads the SAME pane text this
        # watcher already captured (no extra tmux calls) but keeps its own tracked
        # message and its own lifecycle — see :class:`StatusRelay`.
        self._status = status
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
        if self._status is not None:
            self._status.forget(window_id)

    def poll(self, window_ids) -> None:
        """Read each window's pane once and relay newly-detected prompts."""
        window_ids = list(window_ids)
        for wid in window_ids:
            try:
                self._poll_window(wid)
            except Exception:
                log.exception("pane-watch poll failed for %s", wid)
        if self._status is not None:
            # A window that died or was unbound has just vanished from the polled
            # set — poof its status message rather than leave it in the topic.
            try:
                self._status.retain(window_ids)
            except Exception:
                log.exception("status retain failed")

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
        # The fourth read of the same captured text (no extra tmux call). Last, and
        # in its own try: a decoration must never cost us a gate relay.
        if self._status is not None:
            try:
                self._status.sync(window_id, pane)
            except Exception:
                log.exception("status sync failed for %s", window_id)

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
