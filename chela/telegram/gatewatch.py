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

**For an AskUserQuestion, the pane is no longer the CONTENT authority — the HOOK
payload is** (CMX-49). The scrape's option regexes were measured against one selector
shape, and a **multi-question** selector (which draws a tab strip) or one whose options
carry a **``preview``** (which re-lays the TUI out side-by-side) parses as *unparseable*:
live, a 3-question preview-bearing question reached the phone as its question text and a
bare nav row — zero options, nothing to tap — while ``hook.pre_tool_use`` already held
every option's ``label``, ``description`` and ``preview`` in the event log. So when the
pane says a selector is up **and** the log holds an unresolved ``AskUserQuestion``
``PreToolUse`` for that window (:func:`~chela.telegram.hookgate.pending_gate`), the body
is rendered from the payload — **one message per question** (the TUI walks them one at a
time, and three questions × previews is a wall of text on a phone) — and the pane is
demoted to what it is uniquely good for: saying the gate is on screen *right now*. With
no hook events for the window (a **pre-plugin** agent — hooks are read at agent startup),
the pane-scraped render below is unchanged and remains the fallback.

**And the answer goes back the same way it came** (CMX-50). While the daemon holds an
agent's ``PermissionRequest`` hook open (:mod:`chela.gateanswer`), a tap is handed straight
back through it: **zero keystrokes**, no ``❯`` cursor to find, no Enter racing an arrow
move. That is what makes the multi-question and ``multiSelect`` shapes answerable at all —
they have no cursor semantics to inject against — and it retires the substrate that
silently answered option 2 when the human tapped 3 (CMX-32). Keystroke injection survives
for exactly one case: a pre-plugin agent, whose gate no hook ever announced.

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

**And a surface you can SEE yourself driving** (CMX-52). The cards above are a *static*
render: they say what the gate asks, but not what it currently looks like — so tapping an
arrow on the nav row changed nothing on screen, and the human was driving a selector they
could not see. Worse, the nav row has no ``Space``, no ``Tab`` and no ``←``/``→``, so a
``multiSelect`` question could not be answered from a phone at all; and the three
detectors above cover three dialog shapes out of eight, so a Bash approval, a checkpoint
restore, ``/model`` or Settings relayed as *nothing*. The **mirror** (``_MIRROR``,
:func:`format_mirror_card`) fixes all three the way ccbot did — by not parsing anything:
it re-draws the pane region verbatim (:func:`~chela.telegram.panescan.detect_dialog`) into
ONE message with a full nine-key D-pad under it, **edits that same message in place after
every keypress** (:meth:`PermissionGateWatcher.refresh_mirror`, called from the tap
handler), and deletes it when the dialog leaves the pane. The ``❯`` cursor is *in the
render signature*, which is precisely why the edit fires and you watch the cursor move in
the chat. It does not replace the hook cards — see the decision written out above
:func:`format_mirror_card` — it is what you steer with while they are what you read.

The same per-tick pane capture also feeds a fifth, non-interactive relay — the
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

import html
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from chela.gateanswer import OpenGate
from chela.telegram.hookgate import HookGate, Question
from chela.telegram.interactive import (
    hook_reply_markup,
    mirror_markup,
    nav_only_markup,
    permission_reply_markup,
    plan_reply_markup,
    scraped_reply_markup,
)
from chela.telegram.panescan import (
    AskUQ,
    Dialog,
    ExitPlan,
    Gate,
    Status,
    detect_askuserquestion,
    detect_dialog,
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
_MIRROR = "mirror"

# Every tap on the mirror's D-pad is a tmux capture AND a Telegram edit, so a fast thumb
# is a rate-limit gun. Three things hold it down, and only the third is a clock:
#
#   1. the render is DE-DUPED on its signature — an unchanged pane makes no API call at
#      all (:meth:`PermissionGateWatcher._sync` returns before it touches Telegram);
#   2. the tap path sleeps :data:`~chela.telegram.interactive.MIRROR_SETTLE_S` before it
#      re-captures, so a human thumb cannot outrun it by much;
#   3. this floor, on the poll-driven edits, which is what a *repainting* pane (a spinner
#      inside a dialog region) would otherwise walk straight into the flood limit.
#
# A throttled edit is **skipped, not lost**: the tracked signature is left stale on
# purpose, so the very next poll renders the newest pane. And a TAP bypasses the floor
# entirely (``force=True``) — a keypress that does not visibly move the cursor is the
# exact bug this whole surface exists to kill, and no rate budget is worth reintroducing
# it.
MIRROR_EDIT_MIN_INTERVAL = 1.0
_MIN_EDIT_INTERVAL: dict[str, float] = {_MIRROR: MIRROR_EDIT_MIN_INTERVAL}

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


@dataclass(frozen=True)
class Card:
    """One Telegram message a prompt renders into — body, parse mode, keyboard.

    A prompt is a *list* of these, not a string, because an AskUserQuestion rendered
    from its hook payload is **one message per question** (see
    :func:`format_hook_askuq_cards`). Every other prompt renders to exactly one card, so
    the list is a generalisation, not a redesign.

    ``plain`` is the same body with no markup at all. A card that carries a ``preview``
    goes out as ``parse_mode="HTML"`` (a ``<pre>`` block scrolls horizontally on a phone
    instead of wrapping, which is what keeps box-drawing ASCII legible), and Telegram
    *rejects* a message whose entities it cannot parse — so a rejected HTML body falls
    back to this rather than to silence. A dropped preview would be exactly the silent
    degrade this whole change exists to end.
    """

    text: str
    parse_mode: str | None = None
    markup: dict | None = None
    plain: str | None = None


@dataclass
class _Tracked:
    """The prompt message(s) currently posted for one window + prompt kind.

    ``signature`` is the content it was last rendered from (the edge-trigger / de-dup
    marker); ``message_ids`` are the Telegram messages to edit as the prompt re-renders
    and to delete once it resolves (empty when no id-returning poster is wired). It is a
    *list* because a multi-question AskUserQuestion posts one message per question.

    ``last_edit`` is the throttle clock for the kinds that have one (:data:`_MIN_EDIT_
    INTERVAL` — the mirror). It advances only on an edit that actually went out.
    """

    signature: str
    message_ids: list[int] = field(default_factory=list)
    last_edit: float = 0.0


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


# ── AskUserQuestion, rendered from the HOOK payload (CMX-49) ─────────────────
#
# The scrape gives us a lossy render of the selector; the hook gives us the selector's
# INPUT. Everything below reads the payload — nothing here is inferred from pixels.

# Per-question budget. Telegram hard-rejects a body over 4096 chars and ``BotSender.post``
# does not split (these messages carry keyboards), so a card is squeezed in this order:
# the LABELS are never dropped (an option nobody can read is an option nobody can pick),
# then descriptions, and the previews give way first — but never SILENTLY (see
# :func:`_preview_block` / :data:`_PREVIEW_CLIPPED`).
_MAX_HOOK_QUESTION = 900
_MAX_HOOK_LABEL = 300
_MAX_HOOK_DESC = 900
# The preview budget, squeezed stage by stage until the card fits. 0 = previews dropped,
# which costs an explicit warning line on the card.
_PREVIEW_STAGES = (2400, 1200, 600, 250, 0)

_PREVIEW_CLIPPED = "… [preview clipped — /screenshot for the full box]"
# The zero-keypress promise, said on the card (CMX-50). A tap goes back through the
# agent's own blocked `PermissionRequest` hook, so no key is sent to the pane at all —
# which is why these buttons are safe for the shapes the keystroke path has to refuse.
_ANSWER_BY_HOOK = "👆 Tap an option — chela hands it straight to the agent, no keystrokes."
_ANSWER_BY_HOOK_MULTI = (
    "👆 Tap to toggle as many as you want, then ✅ Send — chela hands them straight to the "
    "agent, no keystrokes."
)
# And the honest half of it: the agent is FROZEN while it waits, so the wait is bounded.
# On expiry nothing is denied — the picker is simply still there, in the terminal.
_ANSWER_DEADLINE = (
    "⏳ The agent is held for up to {budget:.0f}s. Miss that and nothing is lost — the "
    "question is still on the pane, answerable in the terminal."
)
_PREVIEW_DROPPED = (
    "⚠️ Previews were too long for one message and are NOT shown — /screenshot the pane "
    "to read them."
)
# Said plainly, on the card, whenever chela cannot prove which option a tap would land
# on. A button whose ordinal mapping we cannot prove is CMX-32 (the human taps 3, the
# agent is told 2) and is strictly worse than no button — so for these shapes there are
# none, and the message says why rather than looking merely unhelpful.
_ANSWER_IN_TERMINAL = (
    "⌨️ Answer this one in the terminal (or with the keys below) — chela can't yet map a "
    "tap to the right option for this shape, and a button that picks the wrong one is "
    "worse than no button."
)


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _preview_block(preview: str, cap: int) -> tuple[str, bool]:
    """The ``<pre>`` block for an option's preview, and whether it is COMPLETE.

    ``<pre>`` is the one Telegram surface that scrolls horizontally instead of wrapping,
    so box-drawing ASCII survives a phone screen. An **absent** preview renders nothing —
    never an empty block. A preview that does not fit is clipped *visibly*
    (:data:`_PREVIEW_CLIPPED`); the caller reports a dropped one on the card itself.
    """
    body = (preview or "").strip("\n")
    if not body.strip():
        return "", True
    if cap <= 0:
        return "", False
    if len(body) > cap:
        return f"<pre>{_esc(body[:cap].rstrip())}\n{_esc(_PREVIEW_CLIPPED)}</pre>", False
    return f"<pre>{_esc(body)}</pre>", True


def _hook_card_text(q: Question, index: int, total: int, preview_cap: int,
                    answerable: bool, budget: float | None = None) -> tuple[str, bool]:
    """One question's card body (HTML), and whether every preview it has is shown.

    The heading names the question's place in the run (``Question 2/3``) and its
    ``header``, because on a phone three messages land back to back and "which one is
    this?" is otherwise unanswerable. Options are numbered 1..N **in payload order** —
    the same order the TUI draws them, and the order any selector button must select in.

    ``budget`` (not None) means the agent's hook is **blocked on this question right now**
    and a tap will answer it with no keystrokes at all. The card says both halves of that:
    that tapping answers the agent directly, and that the hold is time-bounded — because
    an agent frozen on a human is the entire risk of this feature, and a human who does not
    know the clock is running cannot make an informed decision about letting it run out.
    """
    parts: list[str] = []
    head = "❓"
    if total > 1:
        head += f" Question {index + 1}/{total}"
    if q.header:
        head += f" · {_esc(_clip(q.header, 80))}" if total > 1 else f" {_esc(_clip(q.header, 80))}"
    question = _clip(q.question, _MAX_HOOK_QUESTION)
    parts.append(head)
    parts.append(_esc(question) if question else _esc("(the asker left the question blank)"))
    if q.multi_select:
        parts.append("(multi-select — more than one option may be chosen)")

    complete = True
    for i, opt in enumerate(q.options, start=1):
        parts.append("")
        parts.append(f"{i}. {_esc(_clip(opt.label, _MAX_HOOK_LABEL))}")
        if opt.description:
            parts.append(_esc(_clip(opt.description, _MAX_HOOK_DESC)))
        block, shown = _preview_block(opt.preview, preview_cap)
        if block:
            parts.append(block)
        complete = complete and shown

    if not complete and preview_cap <= 0:
        parts.append("")
        parts.append(_esc(_PREVIEW_DROPPED))
    if budget is not None:
        parts.append("")
        parts.append(_esc(_ANSWER_BY_HOOK_MULTI if q.multi_select else _ANSWER_BY_HOOK))
        parts.append(_esc(_ANSWER_DEADLINE.format(budget=budget)))
    elif not answerable:
        parts.append("")
        parts.append(_esc(_ANSWER_IN_TERMINAL))
    return "\n".join(parts), complete


def _strip_html(text: str) -> str:
    """The card body with its ``<pre>`` markup removed — the plain-text fallback.

    Only ever applied to text WE built, so this is a fixed reverse of a fixed encoding,
    not a general HTML parser.
    """
    return html.unescape(text.replace("<pre>", "").replace("</pre>", ""))


def _hook_markup(gate: HookGate, index: int, answerable: bool,
                 held: bool) -> tuple[dict, bool]:
    """The keyboard for question ``index``, and whether it can actually ANSWER the gate.

    Three keyboards, in descending order of what we can prove:

    1. **held** — the agent's ``PermissionRequest`` hook is blocked on this very gate, so a
       tap is handed back through it (:func:`~chela.telegram.interactive.hook_reply_markup`):
       zero keypresses, no cursor to race, and it works for **every** shape — multi-question
       and ``multiSelect`` included, which keystrokes cannot answer at all.
    2. **answerable** — no hook is holding it (a pre-plugin agent), but the scrape found a
       cursor and the shape is the one keystroke injection can hit: the legacy numeric
       selector.
    3. neither — the nav row, and a card that says outright to answer this one in the
       terminal. A button whose ordinal mapping cannot be proven is CMX-32 waiting to
       happen, and no button is better than a wrong one.
    """
    if held:
        q = gate.questions[index]
        markup = hook_reply_markup(
            [o.label for o in q.options], gate.tool_use_id, index,
            multi_select=q.multi_select,
        )
        if markup is not None:
            return markup, True
        # Telegram's 64-byte callback_data cap could not fit this gate's id. Degrade
        # loudly (nav row + "answer in the terminal"), never to a keyboard whose buttons
        # Telegram would drop.
        log.warning("gate %s does not fit a callback payload — falling back to the nav row",
                    gate.tool_use_id)
    if answerable:
        return scraped_reply_markup([o.label for o in gate.questions[0].options]), False
    return nav_only_markup(), False


def format_hook_askuq_cards(gate: HookGate, answerable: bool,
                            held: "OpenGate | None" = None) -> list[Card]:
    """An AskUserQuestion rendered from its hook payload — **one card per question**.

    Full fidelity, because full fidelity is *what the log already had*: every question,
    every option's ``label`` **and** ``description`` **and** ``preview``, the ``header``,
    and the ``multiSelect`` flag. Nothing is scraped, so nothing is lost to a TUI layout
    the scraper was never measured against.

    One message per question, because the alternative is a wall of text on a phone (three
    questions × three options × a dozen preview lines) and because the TUI itself walks
    the questions one at a time — so a question per message is also the only shape in
    which the multi-question case is *readable* at all.

    ``held`` (a :class:`chela.gateanswer.OpenGate`) means the agent's own hook is blocked on
    this gate right now, so every question gets real answer buttons and a tap sends **no
    keystroke at all** — the shapes the keystroke path had to refuse are now answerable, and
    the card stops apologising. ``answerable`` is the fallback question (can a *keystroke*
    land on the right row?) for an agent whose gate no hook announced.
    """
    cards: list[Card] = []
    total = len(gate.questions)
    for i, q in enumerate(gate.questions):
        markup, by_hook = _hook_markup(gate, i, answerable, held is not None)
        budget = held.budget if (held is not None and by_hook) else None
        text = ""
        for cap in _PREVIEW_STAGES:
            text, _complete = _hook_card_text(
                q, i, total, cap, answerable or by_hook, budget)
            if len(text) <= _TG_TEXT_LIMIT:
                break
        if len(text) > _TG_TEXT_LIMIT:      # a pathological label/description set
            text = text[: _TG_TEXT_LIMIT - 1] + "…"
        cards.append(Card(text=text, parse_mode="HTML", markup=markup,
                          plain=_strip_html(text)))
    return cards


def hook_askuq_signature(gate: HookGate, answerable: bool,
                         held: "OpenGate | None" = None) -> str:
    """The de-dup / edge-trigger marker for a payload-rendered selector.

    Keyed on the gate's ``tool_use_id`` (its identity) plus everything the cards render,
    so a *different* gate re-posts and an unchanged one is a no-op. The hook's *hold* is in
    here too: a gate that was posted with keystroke buttons and is now being held open by a
    blocked hook must re-render with real answer buttons, not keep the old ones. The hold's
    **budget** is what the card prints (never a live countdown), so an unchanged gate keeps
    an unchanged signature and the message is not edited on every tick.
    """
    body = "\x00".join(
        "\x01".join([
            q.question, q.header, str(q.multi_select),
            *[f"{o.label}\x02{o.description}\x02{o.preview}" for o in q.options],
        ])
        for q in gate.questions
    )
    return f"{gate.tool_use_id}\x00{answerable}\x00{held is not None}\x00{body}"


# ── The MIRROR — the pane itself, with a D-pad under it (CMX-52) ─────────────
#
# **How this composes with the hook cards above — the decision, written down.**
#
# The two surfaces answer two different questions and neither subsumes the other:
#
#   * the HOOK payload is what the gate *says* — every option's label, description and
#     ``preview``, in full, losslessly (CMX-49). The pane truncates and mangles all of
#     that, and ccbot — which had no hooks — could never have had it. It is what makes
#     the question READABLE.
#   * the PANE is what the gate *looks like right now* — where the ``❯`` cursor is, which
#     boxes are ticked, which tab is focused. No hook carries a cursor, because a cursor
#     is not an input, it is a render. It is what makes the question DRIVABLE.
#
# So they coexist, and the rule — :func:`mirror_suppressed`, one predicate, so it is one
# line to change once Liav has driven it — is:
#
#   **The mirror is posted for every dialog EXCEPT one whose every option is already a
#   single tap away.**
#
# Suppressed, because a D-pad there would be strictly worse than the buttons already on
# screen (and a second message, and a second edit on every repaint):
#
#   * a gate whose ``PermissionRequest`` hook the daemon is HOLDING OPEN (CMX-50) — every
#     option is a button, every shape, zero keypresses, no cursor to steer;
#   * a single-select AskUserQuestion with its semantic option buttons attached — every
#     real option is a numbered button.
#
# Posted, because in each of these the human is being asked to answer something they
# cannot fully reach:
#
#   * a **permission gate** and a **plan approval**. Their cards bind only the two
#     option-count-independent keys (Enter / Escape → ✅ Allow once / ❌ Deny, ✅ Approve /
#     📝 Keep planning). Option 2 — "don't ask again", "manually approve edits" — is
#     deliberately left unbound (a mis-tap must not widen a session's permissions for
#     good), so today it is reachable from a phone only by *guessing* at a selector you
#     cannot see. With the mirror you can see it, arrow to it, and press Enter. The
#     one-tap card stays exactly as it is: this adds a way to answer, it removes none.
#   * an AskUserQuestion that fell back to the **nav row** — a multi-tab / ``multiSelect``
#     / unparseable selector that no hook is holding. The nav row has no ``Space``, no
#     ``Tab``, no ``←``/``→``, so this shape was not merely awkward from a phone, it was
#     **unanswerable**. The D-pad has all of them.
#   * **any dialog none of the three detectors recognises** — a checkpoint restore,
#     ``/model``, Settings, a reworded prompt. These relayed as *nothing at all*. This is
#     the mirror's enduring value and the reason it is not a fallback: hooks will never
#     cover a dialog Claude Code has not told anyone about, and the mirror needs no
#     telling.
#
# The hook cards are never replaced, never suppressed, and never lose their previews —
# deleting them to make room would take back the exact thing Liav verified live in CMX-49.

# The mirrored region, budgeted against Telegram's 4096-char cap. A dialog region is
# small (it is one TUI box), so this only ever bites on a pathological one — and then it
# keeps the TAIL, because the cursor, the options and the footer hint all live at the
# bottom and the top of a long dialog is prose you can /screenshot.
_MAX_MIRROR = 3200
_MIRROR_CLIPPED = "… [top of the dialog clipped — /screenshot for all of it]"

# The pattern name → what to call it on the card. An unrecognised name (a shape added to
# the table later) falls back to a generic heading rather than to nothing — the mirror's
# whole promise is that it renders what it cannot name.
_MIRROR_TITLES: dict[str, str] = {
    "AskUserQuestion": "Question",
    "ExitPlanMode": "Plan review",
    "ToolApproval": "Approval",
    "PermissionPrompt": "Permission",
    "BashApproval": "Bash approval",
    "RestoreCheckpoint": "Restore checkpoint",
    "Settings": "Settings",
}
_MIRROR_HEAD = "🎛️ {title} — the live pane. Every key below re-draws it here."


def format_mirror_card(dialog: Dialog) -> Card:
    """The pane region, mirrored verbatim into one message, with the D-pad under it.

    **The formatting is load-bearing, so it is stated rather than assumed.** ccbot posted
    this region as *plain text with no parse mode* (``interactive_ui.py``:225-236), which
    hands box drawing and a ``❯`` cursor to Telegram's proportional font: the rows do not
    line up, and a wide row wraps and shears the dialog in half. chela has a strictly
    better surface and has already proven it on Liav's phone — the ``<pre>`` block CMX-49
    ships previews in, which renders **monospaced** and **scrolls horizontally instead of
    wrapping**. So the mirror goes in a ``<pre>``: the columns align, the cursor sits where
    the terminal put it, and a wide dialog scrolls rather than shatters. ccbot's one
    genuinely useful trick is kept — its ``─────`` rule-shortening
    (:func:`~chela.telegram.panescan._shorten_separators`, applied at extraction), which
    stops a full-width horizontal rule from setting the scroll width for the whole box.

    ``Card.plain`` carries the same region with the markup stripped, so a body Telegram
    refuses to parse degrades to ccbot's exact rendering rather than to silence.
    """
    title = _MIRROR_TITLES.get(dialog.name, "Terminal")
    body = "\n".join(line.rstrip() for line in dialog.text.split("\n")).strip("\n")
    if len(body) > _MAX_MIRROR:
        body = _MIRROR_CLIPPED + "\n" + body[-_MAX_MIRROR:]
    head = _MIRROR_HEAD.format(title=title)
    text = f"{_esc(head)}\n<pre>{_esc(body)}</pre>"
    return Card(
        text=text,
        parse_mode="HTML",
        markup=mirror_markup(dialog.name),
        plain=f"{head}\n{body}",
    )


def mirror_suppressed(uq: "AskUQ | None", *, held: "OpenGate | None",
                      answerable: bool) -> bool:
    """Is this dialog's every option ALREADY one tap away? Then it needs no D-pad.

    The whole composition rule, in one predicate — see the section comment above. Two
    shapes qualify, and only two:

    * ``held`` — a blocked ``PermissionRequest`` hook is waiting on this gate, so every
      option of every question is a button that answers the agent directly (CMX-50);
    * a scraped single-select selector with semantic option buttons attached — every real
      option is a numbered button that lands on it (``answerable``, or a pane-only
      selector that parsed cleanly).

    Everything else is mirrored: a permission gate and a plan approval (whose cards
    deliberately bind only two of their menu's options), a nav-row-only selector (which
    has no ``Space``/``Tab``/``←``/``→`` and is therefore not answerable at all), and every
    dialog no detector recognises (which relays nothing at all today).
    """
    if held is not None:
        return True
    if answerable:                      # a hook-rendered card wearing keystroke buttons
        return True
    return uq is not None and not uq.multi and bool(uq.options)


def mirror_signature(dialog: Dialog) -> str:
    """The de-dup / edge-trigger marker for the mirror — **the pane region itself**.

    The whole region, byte for byte, which means **the ``❯`` cursor is in the signature**.
    That is the entire fix for the complaint that started this task. The hook-payload
    signature (:func:`hook_askuq_signature`) is keyed on the gate's *content*, and content
    does not change when you press ↑ — so the edit never fired, the message never moved,
    and tapping an arrow did visibly nothing. Here, moving the cursor moves the render,
    the signature changes, and the message is re-drawn in place. Do not "optimise" this
    into a hash of the semantic fields: the pixels **are** the semantics for this surface.
    """
    return f"{dialog.name}\x00{dialog.text}"


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
        detect_dialog: Callable[[str], Dialog | None] = detect_dialog,
        post: Poster | None = None,
        edit: Editor | None = None,
        delete: Deleter | None = None,
        status: "StatusRelay | None" = None,
        pending: "Callable[[str], HookGate | None] | None" = None,
        held: "Callable[[str], OpenGate | None] | None" = None,
        mirror: bool = True,
        now: Callable[[], float] = time.monotonic,
    ):
        self._sender = sender
        self._registry = registry
        self._capture = capture
        self._detect = detect
        self._detect_askuq = detect_askuq
        self._detect_plan = detect_plan
        # The universal dialog detector (CMX-52) — the one that parses nothing and
        # therefore relays the shapes the three above have never heard of.
        self._detect_dialog = detect_dialog
        self._mirror = mirror
        self._now = now
        # :meth:`refresh_mirror` is called from the PTB event loop (a D-pad tap) while
        # :meth:`poll` runs in the outbound thread, and both mutate ``_prompts``. One lock
        # over the whole reconcile keeps a tap and a tick from interleaving into a
        # half-updated tracker (two posted mirrors, or an orphaned message id).
        self._lock = threading.RLock()
        # The event log's view of what this window is blocked on
        # (:func:`~chela.telegram.hookgate.pending_gate`) — the CONTENT authority for an
        # AskUserQuestion. ``None`` (the default) means pane-only: that is the correct
        # behaviour for a fleet with no hook events, and it keeps this class free of a
        # hard dependency on the log.
        self._pending_gate = pending
        # Is a blocked `PermissionRequest` hook waiting on this gate right now
        # (:func:`chela.gateanswer.open_gate`)? If so the gate can be answered with ZERO
        # keypresses and every shape gets real buttons. ``None`` (the default) means no
        # answer channel — keystrokes or nothing, exactly as before.
        self._held = held
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
                # Held against a concurrent D-pad tap (:meth:`refresh_mirror`), which
                # runs on the PTB loop and reconciles the same tracker.
                with self._lock:
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
        # One capture per window per tick, shared by every detector.
        pane = self._capture(window_id)
        uq = self._detect_askuq(pane)
        plan = self._detect_plan(pane)
        # A plan-approval selector's "❯ 1. Yes, and auto-accept edits" row also
        # matches the permission menu's signature, so look for a gate only when
        # neither selector is up — otherwise one pane would relay two prompts.
        gate = None if (uq is not None or plan is not None) else self._detect(pane)

        # The pane says a selector is on screen; the LOG says what it actually asks. The
        # payload only ever renders for a window whose pane is showing a selector *right
        # now* — an unresolved `pre_tool_use` on its own could equally mean the agent
        # died at the gate (the false-`DIED` mistake, CMX-35, from the other side).
        hook = self._hook_askuq(window_id) if uq is not None else None
        held = self._held_gate(hook.tool_use_id) if hook is not None else None
        answerable = False
        if hook is not None:
            # Is the agent's own hook blocked on this gate? Then the answer goes back
            # through it and the pane is never touched (CMX-50).
            answerable = self._answerable(hook, uq)
            self._sync(
                window_id, _ASKUQ, hook,
                lambda h: hook_askuq_signature(h, answerable, held),
                lambda h: format_hook_askuq_cards(h, answerable, held),
            )
        else:
            self._sync(
                window_id, _ASKUQ, uq, _askuq_signature,
                lambda u: [Card(format_askuq_message(u), None, _askuq_markup(u))],
            )
        self._sync(
            window_id, _PLAN, plan, _plan_signature,
            lambda p: [Card(format_plan_message(p), None, plan_reply_markup())],
        )
        self._sync(
            window_id, _GATE, gate, _gate_signature,
            lambda g: [Card(
                format_gate_message(self._latest_pending(window_id), g),
                None, permission_reply_markup(),
            )],
        )
        # The mirror — the pane itself, the ❯ cursor included, under a full D-pad. Posted
        # for EVERY dialog shape (the eight, not the three) except one whose every option
        # is already a single tap away (:func:`mirror_suppressed` — the whole composition
        # rule, and the reasoning behind it, are written out above ``format_mirror_card``).
        self._sync_mirror(
            window_id, pane,
            suppressed=mirror_suppressed(uq, held=held, answerable=answerable),
        )
        # The fourth read of the same captured text (no extra tmux call). Last, and
        # in its own try: a decoration must never cost us a gate relay.
        if self._status is not None:
            try:
                self._status.sync(window_id, pane)
            except Exception:
                log.exception("status sync failed for %s", window_id)

    def _sync_mirror(self, window_id: str, pane: str, *, suppressed: bool,
                     force: bool = False) -> None:
        """Reconcile one window's mirror against a freshly captured pane.

        ``suppressed`` poofs the mirror (and posts none) — a gate whose hook is held open
        needs no D-pad. ``force`` skips the edit throttle: it is set for a **tap**, where a
        delayed re-draw reads as a dead button.
        """
        if not self._mirror:
            return
        dialog = None
        if not suppressed:
            try:
                dialog = self._detect_dialog(pane)
            except Exception:
                log.exception("dialog scrape failed for %s", window_id)
                return
        self._sync(
            window_id, _MIRROR, dialog, mirror_signature,
            lambda d: [format_mirror_card(d)], force=force,
        )

    def refresh_mirror(self, window_id: str) -> None:
        """Re-capture the pane and re-draw this window's mirror **in place**. Never raises.

        This is what a D-pad tap calls after its key lands (:mod:`chela.telegram.inbound`),
        and it is the whole feature: one message, edited, so the human watches the ``❯``
        cursor move *in the chat*. It runs on the PTB event loop's worker thread while
        :meth:`poll` runs in the outbound thread, so it takes the same lock.

        Unlike the poll, this does **not** re-consult the hook: if a mirror is on screen
        with a live D-pad under it, a tap must always re-draw it. (Were a hook to take the
        gate over between the tick and the tap, the next tick applies the suppression rule
        and poofs the message — the authority stays in one place.) A dialog that has left
        the pane — the key just answered it — resolves the mirror, which deletes it.

        Failures are swallowed. This is a redraw: it must never propagate into the tap
        handler, where it would surface as a broken button on a gate that was, in fact,
        answered.
        """
        try:
            with self._lock:
                self._sync_mirror(
                    window_id, self._capture(window_id), suppressed=False, force=True,
                )
        except Exception:
            log.exception("mirror refresh failed for %s", window_id)

    def _latest_pending(self, window_id: str) -> _PendingTool | None:
        """The most recent unpaired ``tool_use`` for a window, if any."""
        pend = self._pending.get(window_id)
        if not pend:
            return None
        return pend[next(reversed(pend))]

    def _hook_askuq(self, window_id: str) -> HookGate | None:
        """The window's pending ``AskUserQuestion`` from the event log, if any.

        Only an ``AskUserQuestion``: a pending ``ExitPlanMode`` (the other interactive
        tool) is not what an AskUserQuestion pane is showing, and a **subagent's** hook
        events carry its *parent's* ``session_id`` — so they resolve to the parent's
        window, and matching the tool name is what keeps a subagent's ordinary tool call
        from being dressed up as the gate on the parent's screen. A lookup failure is
        never fatal: the pane render is right there.
        """
        if self._pending_gate is None:
            return None
        try:
            gate = self._pending_gate(window_id)
        except Exception:
            log.exception("hook gate lookup failed for %s", window_id)
            return None
        if gate is None or gate.tool != "AskUserQuestion" or not gate.questions:
            return None
        return gate

    def _held_gate(self, tool_use_id: str) -> "OpenGate | None":
        """The blocked hook waiting on this gate, if there is one. Never fatal.

        An expired hold reads as None (:func:`chela.gateanswer.open_gate` enforces the
        deadline on read), so a gate whose hook has already given up loses its answer
        buttons and the card falls back to saying "answer it in the terminal" — which is
        the truth at that point. A button that would be refused is worse than no button.
        """
        if self._held is None:
            return None
        try:
            return self._held(tool_use_id)
        except Exception:
            log.exception("held-gate lookup failed for %s", tool_use_id)
            return None

    @staticmethod
    def _answerable(gate: HookGate, uq: "AskUQ | None") -> bool:
        """Can a tap's ordinal mapping be PROVEN for this gate? If not: no buttons.

        This is the FALLBACK path — a gate no hook is holding open (a pre-plugin agent).
        Answering it goes through keystroke injection, and that path can
        only land on the right row when the scraper can see the ``❯`` cursor and the rows
        it counted are the options we numbered. That holds for exactly one shape: a
        **single question**, **single select**, whose pane scrape parsed the same number
        of options the payload declares.

        Everything else — a multi-question selector (the TUI walks the questions, so
        option *i* means a different thing depending which one is on screen), a
        ``multiSelect`` question (an answer is a *set*), or a preview-bearing layout the
        scraper cannot read a cursor out of — gets the nav row and a card that says so.
        Synthesising a selector whose mapping we cannot prove is CMX-32 (tap 3 → answer
        2): a button that silently picks the wrong option is worse than no button.
        """
        if len(gate.questions) != 1:
            return False
        q = gate.questions[0]
        return (
            not q.multi_select
            and uq is not None
            and not uq.multi
            and uq.cursor >= 0
            and len(uq.options) == len(q.options) > 0
        )

    def _sync(self, window_id, kind, detected, sig_fn, cards_fn, *,
              force: bool = False) -> None:
        """Post / edit / poof the tracked message(s) for one prompt kind.

        The single relay path every prompt shares. ``detected is None`` means the prompt
        is no longer on the pane (answered / dismissed) → resolve it. Otherwise: an
        unchanged render is a no-op (edge-triggered — a still-open prompt is never
        re-posted, and this is the de-dup that keeps a repainting mirror off the wire), a
        changed one edits the tracked message(s) in place (so a mid-render partial and the
        settled prompt are ONE message), and a first render posts them. An edit that fails
        (the message was deleted) falls back to a fresh post.

        A prompt renders to a *list* of cards — one per question for a payload-rendered
        AskUserQuestion, one for every other prompt. A render whose card COUNT changed
        cannot be edited into the old messages, so those are poofed and the new set
        posted.

        A kind with an edit floor (:data:`_MIN_EDIT_INTERVAL` — the mirror) SKIPS an edit
        that lands inside it, leaving the signature stale so the next poll re-renders the
        newest pane; nothing is dropped, only deferred. ``force`` (a D-pad tap) bypasses
        the floor.
        """
        if detected is None:
            self._resolve(window_id, kind)
            return

        signature = sig_fn(detected)
        tracked = self._prompts.get(window_id, {}).get(kind)
        if tracked is not None and tracked.signature == signature:
            return

        now = self._now()
        interval = _MIN_EDIT_INTERVAL.get(kind, 0.0)
        if (
            tracked is not None
            and not force
            and interval
            and now - tracked.last_edit < interval
        ):
            return  # throttled — the signature stays stale, so the next poll re-renders

        thread = self._registry.thread_for_window(window_id)
        if thread is None:
            log.debug("%s prompt on %s but no bound topic; skipping", kind, window_id)
            return

        cards = cards_fn(detected)

        if (
            tracked is not None
            and tracked.message_ids
            and self._edit is not None
            and len(tracked.message_ids) == len(cards)
        ):
            if all(self._edit_card(mid, card)
                   for mid, card in zip(tracked.message_ids, cards)):
                tracked.signature = signature
                tracked.last_edit = now
                return
        if tracked is not None:
            # Either the card count changed, or a message is gone from Telegram (deleted,
            # too old). Poof whatever survives and post a fresh set — a half-updated
            # prompt on a phone is a prompt that lies about what it is asking.
            self._resolve(window_id, kind)

        message_ids: list[int] = []
        for card in cards:
            if self._post is not None:
                message_id = self._post_card(card, thread)
                if isinstance(message_id, int):
                    message_ids.append(message_id)
            else:
                # No id-returning poster (plain-sender tests) — post via the sender;
                # without an id we can neither edit nor poof the message.
                self._sender(card.text, card.parse_mode, thread, reply_markup=card.markup)
        self._prompts.setdefault(window_id, {})[kind] = _Tracked(
            signature, message_ids, now)

    def _post_card(self, card: Card, thread) -> int | None:
        """Post one card, falling back to plain text if Telegram rejects the markup.

        A ``<pre>``-bearing body is the only formatted thing this watcher sends, and a
        formatting rejection must never cost the human the *content*: a gate that arrives
        with its options missing is the exact silent degrade this change exists to end.
        """
        message_id = self._post(card.text, card.parse_mode, thread, card.markup)
        if message_id is None and card.plain and card.parse_mode:
            log.warning("gate card rejected with parse_mode=%s — re-posting it plain",
                        card.parse_mode)
            message_id = self._post(card.plain, None, thread, card.markup)
        return message_id

    def _edit_card(self, message_id: int, card: Card) -> bool:
        """Edit one tracked message to this card, plain-text fallback and all."""
        if self._edit(message_id, card.text, card.parse_mode, card.markup):
            return True
        if card.plain and card.parse_mode:
            return bool(self._edit(message_id, card.plain, None, card.markup))
        return False

    def _resolve(self, window_id: str, kind: str) -> None:
        """The prompt is answered — drop its marker and poof its message(s).

        Deleting is the point: the buttons on an answered prompt are not just
        stale, they are *live* — a later tap would fire Enter/Escape at whatever
        the agent is doing by then. The transcript's ``tool_result`` (relayed
        separately) is the record of what was chosen.
        """
        tracked = self._prompts.get(window_id, {}).pop(kind, None)
        if tracked is None or self._delete is None:
            return
        for message_id in tracked.message_ids:
            try:
                self._delete(message_id)
            except Exception:
                log.exception("failed to delete resolved %s prompt on %s", kind, window_id)
