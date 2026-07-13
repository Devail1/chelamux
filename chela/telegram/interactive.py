"""Interactive-prompt inline keyboards — answer prompts from Telegram.

Three agent prompts get a tap-to-answer inline keyboard on their bound topic:

* **AskUserQuestion** — surfaced live from the tmux **pane** (Slice A2), because
  its structured ``tool_use`` record only lands in the transcript once the
  question is *answered*. The pane watcher scrapes the selector
  (:func:`~chela.telegram.panescan.detect_askuserquestion`) and relays the options
  numbered **in the message body**, with :func:`scraped_reply_markup` giving each
  one a compact numeric selector button (or :func:`nav_only_markup` for the
  multi-tab / multi-select fallback). Answering re-reads the live ``❯`` cursor and
  injects the cursor-relative keystrokes (:func:`select_keystrokes_relative`),
  submitting only after they settle (:func:`split_select_keys`).
* **ExitPlanMode** (Slice B2) — also surfaced live from the tmux **pane**, because
  its ``tool_use`` record likewise lands in the transcript only once the plan is
  resolved. The pane watcher scrapes the plan-approval selector
  (:func:`~chela.telegram.panescan.detect_exitplanmode`) and attaches
  :func:`plan_reply_markup`. Its choices are harness-rendered TUI, not in the
  transcript, so instead of enumerating options it binds ✅ Approve (auto mode) /
  📝 Keep planning to single, option-count-independent keystrokes (Enter / Escape)
  via the same nav plumbing. Enter's default proceed option enables auto mode, so
  that button says so (see :func:`_plan_rows`).
* a **permission gate** (Slice C2) — a tool-approval dialog, which is not in the
  transcript at *any* point, so it too is scraped from the pane
  (:func:`~chela.telegram.panescan.detect_permission_gate`) and gets
  :func:`permission_reply_markup`: ✅ Allow once (Enter, the default-highlighted
  "1. Yes") / ❌ Deny (Escape) — both verified live against Claude Code 2.1.207.
  The gate's "always allow" option is deliberately left unbound (see
  :func:`_permission_rows`).

Everything here is pure data (plain dicts + tmux key names), with **no
``python-telegram-bot`` import**, so the keyboards the urllib-based relay attaches
and the callback the PTB inbound handler decodes share one source of truth and
can be unit-tested without the ``[telegram]`` extra:

* :func:`scraped_reply_markup` / :func:`nav_only_markup` build the pane-triggered
  AskUserQuestion keyboards; :func:`plan_reply_markup` and
  :func:`permission_reply_markup` build the ExitPlanMode and permission-gate ones.
  :func:`ask_reply_markup` is the now-vestigial transcript keyboard seam (always
  ``None`` — every prompt moved to the pane).
* :func:`decode_callback` decodes a tapped button's ``callback_data`` back into
  an action for the inbound handler to run (:mod:`chela.telegram.inbound`).
* :func:`select_keystrokes_relative` (and the :func:`select_keystrokes` blind
  fallback) is the answer-injection contract: the tmux key sequence that moves the
  selector to option ``i`` and submits it.

**MVP boundary (Slice A2):** semantic option buttons are attached ONLY for a
single question, single-select selector. Any multi-tab / multi-select shape gets
the navigation-fallback row only, so the human drives the selector manually rather
than being handed a keyboard that would answer the wrong thing.

The navigation row is ported from six-ddc/ccbot's ``_build_interactive_keyboard``
(https://github.com/six-ddc/ccbot, MIT); see the top-level NOTICE for attribution.
"""
from __future__ import annotations

from typing import Any

# callback_data schemes. Semantic option picks are ``qa:<index>`` — the INDEX
# ONLY, never the label, so a long option can't blow Telegram's 64-byte
# callback_data cap. Navigation keys are ``qa:nav:<key_id>`` so both flow through
# the same ``^qa:`` CallbackQueryHandler without colliding with the ``/screenshot``
# control keyboard's ``k:`` scheme.
QA_CB_PREFIX = "qa:"
QA_NAV_PREFIX = "qa:nav:"

# AskUserQuestion option buttons are bare numeric selectors ("1", "2", …), so
# several fit one row at any phone width. The option text they select is in the
# message body, which — unlike a button caption — wraps instead of truncating.
_SELECTORS_PER_ROW = 4

# The navigation-fallback row — always present so the operator can drive the
# selector by hand if a semantic pick misfires (or in the non-MVP shapes above).
# ``(label, key_id, tmux_key)``; ``ref`` is special (screenshot refresh), so it
# carries no tmux key and is handled on its own in :func:`decode_callback`.
# Labels are **glyph-only** (no "Esc"/"Enter" words): five buttons share one row,
# and on a narrow phone Telegram truncates a worded caption ("⏎ Enter" → "⏎ E…").
# The glyphs (⎋ = Escape, ⏎ = Enter) render in full at any width.
NAV_KEYS: list[tuple[str, str, str | None]] = [
    ("↑", "up", "Up"),
    ("↓", "dn", "Down"),
    ("⎋", "esc", "Escape"),
    ("⏎", "ent", "Enter"),
    ("🔄", "ref", None),
]

# key_id → (tmux key name, toast label), so a nav button and the key it fires can
# never drift apart. ``ref`` is excluded (no tmux key — it re-screenshots).
NAV_ACTIONS: dict[str, tuple[str, str]] = {
    key_id: (tmux_key, label)
    for (label, key_id, tmux_key) in NAV_KEYS
    if tmux_key is not None
}


def select_keystrokes(index: int) -> list[str]:
    """Blind fallback: assume the cursor is on option 0 and submit option ``index``.

    Claude Code's AskUserQuestion selector highlights option 0 on render, so
    ``index`` ``Down`` presses move the highlight to option ``index`` and
    ``Enter`` submits. This is the fallback used only when the current cursor
    position can't be read from the pane; the pane-triggered path prefers
    :func:`select_keystrokes_relative`, which never assumes the cursor is at 0
    (the operator may have arrowed the selector before tapping a button).
    """
    return ["Down"] * index + ["Enter"]


def select_keystrokes_relative(target: int, cursor: int) -> list[str]:
    """Move the selector from row ``cursor`` to option ``target`` and submit it.

    The pane-triggered answer contract: instead of assuming the highlight is on
    option 0, the handler re-reads the live ``❯`` cursor ordinal at tap time
    (:func:`~chela.telegram.panescan.detect_askuserquestion`) and this computes the
    signed delta — ``Down``×(target−cursor) when moving down, ``Up``×(cursor−target)
    when moving up — then ``Enter``. Semantic option ``i`` occupies navigable row
    ``i`` (the options are the first rows), so ``target`` is the option index.
    """
    delta = target - cursor
    if delta > 0:
        return ["Down"] * delta + ["Enter"]
    if delta < 0:
        return ["Up"] * (-delta) + ["Enter"]
    return ["Enter"]


def split_select_keys(keys: list[str]) -> tuple[list[str], list[str]]:
    """Split an answer sequence into the cursor MOVES and the trailing submit.

    The submit must not race the moves. Measured live (Claude Code 2.1.207, CMX-32)
    on a real 4-option selector with the cursor on option 1: ``Down Down Enter``
    fired back-to-back submits **option 2**, while the same presses with a ~250ms
    pause before ``Enter`` submit option 3. Both ``Down``s do land — the highlight
    ends up on the right row either way — but the selector commits arrow moves on a
    render tick, so an ``Enter`` arriving in the same input burst is answered
    against the row the selector had *before* the last move. Off by one, silently:
    the human taps 3 and the agent is told 2.

    So the caller sends the moves, waits :data:`SELECT_SETTLE_S`, and only then
    sends the submit. This is why the gap exists — do not collapse it back into one
    send loop.
    """
    if keys and keys[-1] == "Enter":
        return keys[:-1], ["Enter"]
    return list(keys), []


# How long to let the selector settle between the cursor moves and the Enter that
# submits them (see :func:`split_select_keys`). 250ms was enough live; this leaves
# margin for a loaded machine.
SELECT_SETTLE_S = 0.4


def _nav_row() -> list[dict]:
    """The full navigation-fallback row as Bot-API inline buttons.

    Only used where there are **no** semantic buttons (:func:`nav_only_markup`) —
    the multi-tab / multi-select selector the MVP won't hand a keyboard to. There
    the operator really is driving the selector blind and needs every key.
    """
    return [
        {"text": label, "callback_data": f"{QA_NAV_PREFIX}{key_id}"}
        for (label, key_id, _tmux) in NAV_KEYS
    ]


def _esc_row() -> list[dict]:
    """The one-button escape hatch that rides under semantic option buttons.

    Where the keyboard already carries semantic buttons, the ↑ ↓ ⏎ 🔄 keys are
    dead weight: Telegram shows no caret, so arrowing is blind, and ⏎ would submit
    whatever row the invisible cursor happens to sit on. Only ⎋ (dismiss the
    prompt) is meaningful without seeing the pane, so that is all we keep.
    """
    return [{"text": "⎋ Esc", "callback_data": f"{QA_NAV_PREFIX}esc"}]


def _plan_rows() -> list[list[dict]]:
    """Two approval buttons for an ExitPlanMode ``tool_use`` (Slice B).

    Unlike AskUserQuestion, ExitPlanMode's approval CHOICES are harness-rendered
    TUI and are **not** in the transcript — ``input`` carries only ``plan``. So we
    can't enumerate them; instead we bind the two default actions to single,
    option-count-independent keystrokes routed through Slice A's
    ``qa:nav:ent``/``qa:nav:esc`` plumbing (``_on_qa`` → :data:`NAV_ACTIONS` →
    ``send_key``), so no inbound handler change is needed.

    Empirically (Claude Code 2.1.207) the plan-approval dialog is::

        Would you like to proceed?
        ❯ 1. Yes, and use auto mode        <- default-highlighted
          2. Yes, manually approve edits
          3. No, refine …
          4. Tell Claude what to change

    * ``Enter`` accepts option 1 → approves the plan **and switches the session
      into auto mode** (status line flips ``⏸ plan mode on`` → ``⏵⏵ auto mode``).
      That is a real permission change, so the button is labelled to say so — a
      plain "Approve" would hide it.
    * ``Escape`` dismisses the dialog and **stays in plan mode** (does NOT cancel
      the task), so the human keeps refining.

    These two buttons already bind both keys the human can press without seeing
    the pane, so no nav row is appended (Slice C2): ↑ ↓ ⏎ 🔄 would be blind
    presses against an invisible caret, and a ⎋ button would just duplicate
    "Keep planning". Arrowing to option 2 ("manually approve edits") is still
    possible from the ``/screenshot`` control keyboard, which shows the pane.
    """
    return [
        [
            {"text": "✅ Approve (auto mode)", "callback_data": f"{QA_NAV_PREFIX}ent"},
            {"text": "📝 Keep planning", "callback_data": f"{QA_NAV_PREFIX}esc"},
        ],
    ]


def _permission_rows() -> list[list[dict]]:
    """The two approval buttons for a pane-detected permission gate (Slice C2).

    A tool/permission gate is a numbered TUI menu whose option 1 ("Yes") is
    default-highlighted, so — exactly like the plan approval — the two answers are
    option-count-independent single keystrokes routed through the existing
    ``qa:nav:ent`` / ``qa:nav:esc`` plumbing (``_on_qa`` → :data:`NAV_ACTIONS` →
    ``send_key``)::

        Do you want to proceed?
        ❯ 1. Yes                                       <- Enter  → ✅ Allow once
          2. Yes, and don't ask again for rm commands  <- deliberately NOT bound
          3. No, and tell Claude what to do (esc)      <- Escape → ❌ Deny

    Option 2 ("don't ask again" / "allow all edits this session") widens the
    session's permissions for good, so it gets **no one-tap button** — a mis-tap
    from a phone must not be able to disarm the gate for every later command.
    """
    return [
        [
            {"text": "✅ Allow once", "callback_data": f"{QA_NAV_PREFIX}ent"},
            {"text": "❌ Deny", "callback_data": f"{QA_NAV_PREFIX}esc"},
        ],
    ]


def scraped_reply_markup(labels) -> dict:
    """Bot-API ``reply_markup`` for a pane-scraped single-select AskUserQuestion.

    One ``qa:<i>`` button per scraped option, captioned with its **1-based number**
    only — ``1`` ``2`` ``3`` … — packed :data:`_SELECTORS_PER_ROW` to a row, then
    the ⎋ escape hatch (:func:`_esc_row`).

    The captions used to be the option labels themselves, which made the question
    unanswerable on a phone: a button caption is the one Telegram surface that
    hard-truncates to a single line, and it was the *only* carrier of the options,
    so the human got four half-sentences and none of the reasoning. The options now
    live numbered and in full in the message body
    (:func:`~chela.telegram.gatewatch.format_askuq_message`), which wraps; these
    buttons are just the selector for them, so button *N* MUST stay option *N* in
    scraped display order — a tap injects ``i − cursor`` Down/Up presses against
    the live selector, so a reordered, filtered or gapped keyboard would answer the
    wrong question.
    """
    numbered = [
        {"text": str(i + 1), "callback_data": f"{QA_CB_PREFIX}{i}"}
        for i, _label in enumerate(labels)
    ]
    rows = [
        numbered[i : i + _SELECTORS_PER_ROW]
        for i in range(0, len(numbered), _SELECTORS_PER_ROW)
    ]
    rows.append(_esc_row())
    return {"inline_keyboard": rows}


def nav_only_markup() -> dict:
    """The nav-fallback row alone — for the multi-tab / multi-select shapes.

    The MVP never hands out semantic option buttons for a multi-question or
    multi-select selector (they would answer the wrong thing), so the operator
    drives the selector by hand with the nav keys.
    """
    return {"inline_keyboard": [_nav_row()]}


def plan_reply_markup() -> dict:
    """Bot-API ``reply_markup`` for a pane-scraped ExitPlanMode plan approval.

    The two approval buttons (:func:`_plan_rows`) — the same option-count-independent
    keystrokes Slice B built, now attached to the pane-triggered plan relay
    (:func:`~chela.telegram.gatewatch.PermissionGateWatcher`) rather than the
    transcript, because the ExitPlanMode ``tool_use`` only lands once the plan is
    resolved. There are no options to enumerate (the choices are harness-rendered
    TUI, not in the transcript), so this keyboard is independent of the scraped
    plan text and attaches for any detected plan-approval selector.
    """
    return {"inline_keyboard": _plan_rows()}


def permission_reply_markup() -> dict:
    """Bot-API ``reply_markup`` for a pane-detected permission gate (Slice C2).

    The ✅ Allow once / ❌ Deny buttons (:func:`_permission_rows`), attached to the
    gate the pane watcher detected
    (:func:`~chela.telegram.panescan.detect_permission_gate`). Like the plan
    approval, the gate's answers are single option-count-independent keystrokes
    (Enter / Escape), so the keyboard is the same whatever menu the gate rendered.
    """
    return {"inline_keyboard": _permission_rows()}


def ask_reply_markup(msg) -> dict | None:
    """Transcript-triggered keyboard seam — now always ``None`` (both prompts moved).

    ``msg`` is a :class:`~chela.telegram.parser.Message`. Both interactive prompts
    that once attached a keyboard here are now surfaced live from the tmux **pane**
    instead, because each one's ``tool_use`` record was measured to land in the
    transcript only *after* the prompt is resolved — too late for the buttons to
    be answerable:

    * **AskUserQuestion** (Slice A2) → :func:`scraped_reply_markup` /
      :func:`nav_only_markup`, built from the scraped options;
    * **ExitPlanMode** (Slice B2) → :func:`plan_reply_markup`, the approve /
      keep-planning buttons.

    Both are attached by :class:`~chela.telegram.gatewatch.PermissionGateWatcher`
    from the pane, and the relay drops the corresponding post-answer transcript
    ``tool_use``. This function is kept as the relay's extension seam and returns
    ``None`` for every message, so the relay never crashes on a missing payload and
    a future transcript-triggered keyboard has a home.
    """
    return None


def decode_callback(data: str) -> tuple[str, Any] | None:
    """Decode a ``qa:`` callback into an action, or None if not ours / invalid.

    Returns one of:

    * ``("select", index)`` — a semantic option pick (``qa:<index>``);
    * ``("key", (tmux_key, label))`` — a navigation key (``qa:nav:<key_id>``);
    * ``("refresh", None)`` — the 🔄 button (``qa:nav:ref``).

    ``None`` for a non-``qa:`` payload, an unknown nav key, a non-numeric index,
    or an out-of-range index — the inbound handler answers the tap (to stop the
    button spinner) and does nothing, so a stale or crafted payload is inert.
    """
    if not data.startswith(QA_CB_PREFIX):
        return None
    rest = data[len(QA_CB_PREFIX):]
    if rest.startswith("nav:"):
        key_id = rest[len("nav:"):]
        if key_id == "ref":
            return ("refresh", None)
        action = NAV_ACTIONS.get(key_id)
        return ("key", action) if action is not None else None
    try:
        index = int(rest)
    except ValueError:
        return None
    # The buttons we build only ever carry 0..N-1; clamp to a sane ceiling so a
    # crafted payload can't fire an absurd run of Down presses at the terminal.
    if index < 0 or index > 50:
        return None
    return ("select", index)
