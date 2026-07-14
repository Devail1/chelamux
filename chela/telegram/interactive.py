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

# The ZERO-KEYPRESS answer scheme (CMX-50). A tap on one of these does not touch the
# terminal at all: the answer is handed back through the blocked ``PermissionRequest``
# hook (:mod:`chela.gateanswer`), so it cannot race the selector, cannot land on the wrong
# row, and works for the shapes keystrokes can never answer — a multi-question run and a
# ``multiSelect`` question.
#
# ``qa:h:<tool_use_id>:<q>:<o>``  — pick (or, when multiSelect, TOGGLE) option ``o`` of
#                                  question ``q`` of the gate ``tool_use_id``;
# ``qa:hs:<tool_use_id>:<q>``     — commit a multiSelect question's toggled set.
#
# The ``tool_use_id`` is in the payload on purpose. It is the gate's identity, and a tap
# that arrives after that gate resolved must be REFUSED, not applied to whatever is on
# screen by then (CMX-32, from the other direction). Resolving the gate from the topic at
# tap time would do exactly that.
QA_HOOK_PREFIX = "qa:h:"
QA_HOOK_SEND_PREFIX = "qa:hs:"

# Telegram's hard cap on callback_data. A button whose data would exceed it is not built
# at all (:func:`hook_reply_markup` returns None and the card says to answer in the
# terminal) — a keyboard Telegram rejects is a gate that arrives unanswerable, and a
# silently-missing button is the failure this whole line of work exists to end.
CALLBACK_DATA_MAX = 64

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


# ── The MIRROR's D-pad (CMX-52) ──────────────────────────────────────────────
#
# The nav row above has ↑ ↓ ⎋ ⏎ 🔄 and nothing else, which makes a ``multiSelect``
# question literally undrivable from a phone: there is no ``Space`` to toggle an option,
# no ``Tab``, and no ``←``/``→`` to walk between the questions of a multi-question run.
# So the mirror carries ccbot's full nine-key D-pad instead — every key the TUI reads:
#
#     ␣ Space   ↑   ⇥ Tab
#     ←         ↓   →
#     ⎋ Esc     🔄  ⏎ Enter
#
# ``ref`` fires no key: it re-captures the pane and **re-renders the same message**. (The
# old 🔄 posted a fresh screenshot *below* the card, which the human then had to scroll to
# and mentally align against the buttons — the mirror's whole point is that the picture
# and the buttons are one message.)
#
# Ported from ccbot's ``_build_interactive_keyboard`` (handlers/interactive_ui.py:81-141),
# MIT; see the top-level NOTICE.
MIRROR_CB_PREFIX = "m:"

MIRROR_KEYS: list[list[tuple[str, str, str | None]]] = [
    [("␣ Space", "spc", "Space"), ("↑", "up", "Up"), ("⇥ Tab", "tab", "Tab")],
    [("←", "lt", "Left"), ("↓", "dn", "Down"), ("→", "rt", "Right")],
    [("⎋ Esc", "esc", "Escape"), ("🔄", "ref", None), ("⏎ Enter", "ent", "Enter")],
]

# The key_id that re-renders instead of pressing anything.
MIRROR_REFRESH_KEY_ID = "ref"

# key_id → (tmux key name, toast label), DERIVED from the table above so a button and the
# key it fires cannot drift apart (the CMX-45 rule). Keyless buttons (🔄) are excluded —
# a refresh is not a keypress.
MIRROR_ACTIONS: dict[str, tuple[str, str]] = {
    key_id: (tmux_key, label)
    for row in MIRROR_KEYS
    for (label, key_id, tmux_key) in row
    if tmux_key is not None
}

# A dialog with **no horizontal axis** drops ←/→ rather than showing two keys that do
# nothing. ccbot special-cased ``RestoreCheckpoint`` for exactly this reason: it is a
# plain vertical list of checkpoints, so ← and → are inert there — and an inert button is
# how a human learns to distrust the whole keyboard. ↑/↓ stay (the list is navigable) and
# so does Tab. This is a *display* rule keyed on the mirrored dialog's pattern name; it
# never changes what a key does, so :data:`MIRROR_ACTIONS` stays the one key table.
VERTICAL_ONLY_DIALOGS: frozenset[str] = frozenset({"RestoreCheckpoint"})

# After a key is sent, how long to let the TUI repaint before re-capturing the pane for
# the edit-in-place. ccbot used 0.5s in production (bot.py:1594-1697) and the cursor moved
# visibly in the chat; a shorter wait races the repaint and mirrors the PREVIOUS frame,
# which would look exactly like the "nothing happened" bug this replaces.
MIRROR_SETTLE_S = 0.5


def mirror_markup(ui_name: str = "", answer_rows=None) -> dict:
    """The mirrored dialog's keyboard: the ANSWER buttons, then the D-pad under them.

    ``ui_name`` is the mirrored :class:`~chela.telegram.panescan.Dialog`'s pattern name and
    only shapes the *layout* of the pad (see :data:`VERTICAL_ONLY_DIALOGS`); an unknown name
    gets the full pad, which is the right default — a mirror exists precisely for the
    dialogs we do not recognise, and refusing them keys would defeat it.

    ``answer_rows`` (CMX-54) are the zero-keypress ``qa:h:`` option buttons for the question
    the pane is currently on, when the daemon is holding that gate's hook open and the
    mapping is provable (:func:`~chela.telegram.gatewatch.mirror_answer`). They sit **above**
    the pad, on the same message, because the two are complementary and not alternatives:
    the pane is the only surface that shows you *where you are*, and the buttons are the
    only surface that answers with *no keystrokes*. Watch the cursor, or tap the answer —
    the human's choice, in one message.
    """
    vertical_only = ui_name in VERTICAL_ONLY_DIALOGS
    rows: list[list[dict]] = [list(row) for row in (answer_rows or [])]
    for row in MIRROR_KEYS:
        buttons = [
            {"text": label, "callback_data": f"{MIRROR_CB_PREFIX}{key_id}"}
            for (label, key_id, _tmux) in row
            if not (vertical_only and key_id in ("lt", "rt"))
        ]
        if buttons:
            rows.append(buttons)
    return {"inline_keyboard": rows}


def is_dpad_row(row) -> bool:
    """Is this keyboard row part of the mirror's D-pad (rather than an answer button)?"""
    return bool(row) and all(
        str(button.get("callback_data", "")).startswith(MIRROR_CB_PREFIX) for button in row
    )


def recompose_mirror_markup(current_rows, answer_markup: dict | None) -> dict | None:
    """Re-draw a tapped message's answer keyboard **without losing its D-pad**.

    A hook option button is now carried on two surfaces: the CMX-49 card (options only) and
    the mirror (options **above** the D-pad). After a tap, :mod:`chela.telegram.inbound`
    redraws the tapped message's keyboard with the fresh one the draft book returns — the
    ``☑`` ticks, the ``✓`` on the chosen option — and that keyboard knows nothing about a
    D-pad. Redrawing a *mirror* with it verbatim would silently strip the pad off the
    message the human is steering with.

    So the pad is taken from the message being redrawn, not rebuilt from a name we would
    have to re-derive: whichever rows of the live keyboard are D-pad rows are kept, under
    the new answer rows. A message with no pad (a plain card) is unchanged, and a tap that
    yields no keyboard returns ``None`` (nothing to redraw).
    """
    if answer_markup is None:
        return None
    rows = [list(row) for row in answer_markup.get("inline_keyboard") or []]
    rows.extend([list(row) for row in (current_rows or []) if is_dpad_row(row)])
    return {"inline_keyboard": rows} if rows else None


def decode_mirror_callback(data: str) -> tuple[str, Any] | None:
    """Decode an ``m:`` D-pad tap, or None if it isn't ours / isn't a known key.

    * ``("key", (tmux_key, label))`` — press one key, then re-render the mirror;
    * ``("refresh", None)`` — the 🔄 button: press nothing, just re-render.

    The target window is **not** in the payload and never will be: it is re-resolved from
    the message's own topic at tap time (CMX-8). An unknown key_id returns None and the
    handler answers the tap and does nothing, so a stale or crafted payload is inert.
    """
    if not data.startswith(MIRROR_CB_PREFIX):
        return None
    key_id = data[len(MIRROR_CB_PREFIX):]
    if key_id == MIRROR_REFRESH_KEY_ID:
        return ("refresh", None)
    action = MIRROR_ACTIONS.get(key_id)
    return ("key", action) if action is not None else None


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


def _cb_fits(data: str) -> bool:
    return len(data.encode("utf-8")) <= CALLBACK_DATA_MAX


def hook_reply_markup(labels, tool_use_id: str, question_index: int,
                      *, multi_select: bool = False, selected=()) -> dict | None:
    """The ZERO-KEYPRESS answer keyboard for one question of a hook-blocked gate.

    One button per option, captioned with its **1-based number** (the option text lives in
    the message body, the only Telegram surface that wraps rather than truncating — the
    lesson of CMX-32). Unlike :func:`scraped_reply_markup`, button *N* here does not move
    a cursor: it names option *N* of *this* question of *this* ``tool_use_id``, and the
    answer is handed to the agent through its own blocked hook. There is no selector to
    race and no ordinal to get wrong, which is why this keyboard is safe for the shapes
    the keystroke path must refuse.

    A ``multiSelect`` question toggles: each tapped option shows ``☑``, and a ``✅ Send``
    button commits the set (a question that takes *several* answers cannot be answered by
    one tap — that is exactly the shape the old path had no way to express).

    Returns **None** if a button's ``callback_data`` would exceed Telegram's 64-byte cap
    (a pathological ``tool_use_id``). The caller then renders the nav row and says plainly
    that this one has to be answered in the terminal — never a keyboard whose buttons
    Telegram would silently drop.
    """
    chosen = set(selected or ())
    buttons: list[dict] = []
    for i, label in enumerate(labels):
        data = f"{QA_HOOK_PREFIX}{tool_use_id}:{question_index}:{i}"
        if not _cb_fits(data):
            return None
        text = str(i + 1)
        if multi_select:
            text = f"{'☑' if i in chosen else '☐'} {text}"
        elif i in chosen:
            text = f"✓ {text}"
        buttons.append({"text": text, "callback_data": data})
    if not buttons:
        return None

    rows = [
        buttons[i : i + _SELECTORS_PER_ROW]
        for i in range(0, len(buttons), _SELECTORS_PER_ROW)
    ]
    if multi_select:
        send = f"{QA_HOOK_SEND_PREFIX}{tool_use_id}:{question_index}"
        if not _cb_fits(send):
            return None
        rows.append([{"text": "✅ Send", "callback_data": send}])
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


def _decode_hook_pick(rest: str) -> tuple[str, Any] | None:
    """``<tool_use_id>:<q>:<o>`` → ``("pick", (tuid, q, o))``.

    The ``tool_use_id`` is only ever used to *look up* an open gate, and a lookup miss is a
    refusal — so a crafted payload buys nothing. It is still bounded and character-checked
    here, because it also becomes a filename in the rendezvous directory.
    """
    parts = rest.rsplit(":", 2)
    if len(parts) != 3:
        return None
    tuid, q, o = parts
    if not _valid_tuid(tuid):
        return None
    try:
        question_index, option_index = int(q), int(o)
    except ValueError:
        return None
    if not (0 <= question_index <= 50 and 0 <= option_index <= 50):
        return None
    return ("pick", (tuid, question_index, option_index))


def _decode_hook_send(rest: str) -> tuple[str, Any] | None:
    """``<tool_use_id>:<q>`` → ``("send", (tuid, q))`` — commit a multiSelect question."""
    tuid, _, q = rest.rpartition(":")
    if not _valid_tuid(tuid):
        return None
    try:
        question_index = int(q)
    except ValueError:
        return None
    if not 0 <= question_index <= 50:
        return None
    return ("send", (tuid, question_index))


def _valid_tuid(tuid: str) -> bool:
    return bool(tuid) and len(tuid) <= 128 and all(
        c.isalnum() or c in "_-" for c in tuid
    )


def decode_callback(data: str) -> tuple[str, Any] | None:
    """Decode a ``qa:`` callback into an action, or None if not ours / invalid.

    Returns one of:

    * ``("pick", (tool_use_id, question_index, option_index))`` — a ZERO-KEYPRESS answer
      tap (``qa:h:…``): the answer goes back through the agent's own blocked hook, and
      **no key is ever sent to the pane**;
    * ``("send", (tool_use_id, question_index))`` — commit a ``multiSelect`` question
      (``qa:hs:…``);
    * ``("select", index)`` — a keystroke-injected option pick (``qa:<index>``), the
      legacy path kept for a pre-plugin agent whose gate no hook ever announced;
    * ``("key", (tmux_key, label))`` — a navigation key (``qa:nav:<key_id>``);
    * ``("refresh", None)`` — the 🔄 button (``qa:nav:ref``).

    ``None`` for a non-``qa:`` payload, an unknown nav key, a non-numeric index,
    or an out-of-range index — the inbound handler answers the tap (to stop the
    button spinner) and does nothing, so a stale or crafted payload is inert.
    """
    if not data.startswith(QA_CB_PREFIX):
        return None
    if data.startswith(QA_HOOK_PREFIX):
        return _decode_hook_pick(data[len(QA_HOOK_PREFIX):])
    if data.startswith(QA_HOOK_SEND_PREFIX):
        return _decode_hook_send(data[len(QA_HOOK_SEND_PREFIX):])
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
