"""Interactive-prompt inline keyboards — answer prompts from Telegram.

Two agent prompts get a tap-to-answer inline keyboard on their bound topic:

* **AskUserQuestion** — surfaced live from the tmux **pane** (Slice A2), because
  its structured ``tool_use`` record only lands in the transcript once the
  question is *answered*. The pane watcher scrapes the selector
  (:func:`~chela.telegram.panescan.detect_askuserquestion`) and builds the
  keyboard from the scraped option labels: :func:`scraped_reply_markup` for a
  simple single-select, :func:`nav_only_markup` for the multi-tab / multi-select
  fallback. Answering re-reads the live ``❯`` cursor and injects the
  cursor-relative keystrokes (:func:`select_keystrokes_relative`).
* **ExitPlanMode** (Slice B) — surfaced from the transcript ``tool_use`` via
  :func:`ask_reply_markup`. Its choices are harness-rendered TUI, not in the
  transcript, so instead of enumerating options it binds ✅ Approve (auto mode) /
  📝 Keep planning to single, option-count-independent keystrokes (Enter / Escape)
  via the same nav plumbing. Enter's default proceed option enables auto mode, so
  that button says so (see :func:`_plan_rows`); the nav row lets the human arrow
  to "manually approve edits".

Everything here is pure data (plain dicts + tmux key names), with **no
``python-telegram-bot`` import**, so the keyboards the urllib-based relay attaches
and the callback the PTB inbound handler decodes share one source of truth and
can be unit-tested without the ``[telegram]`` extra:

* :func:`ask_reply_markup` builds the ``reply_markup`` for an ExitPlanMode
  ``tool_use``; :func:`scraped_reply_markup` / :func:`nav_only_markup` build the
  pane-triggered AskUserQuestion keyboards.
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

# Telegram truncates over-long button captions; keep them short and add an
# ellipsis so a verbose option label still renders a tidy button.
_BTN_TEXT_MAX = 48

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


def _truncate(text: str, n: int = _BTN_TEXT_MAX) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _nav_row() -> list[dict]:
    """The navigation-fallback row as Bot-API inline buttons."""
    return [
        {"text": label, "callback_data": f"{QA_NAV_PREFIX}{key_id}"}
        for (label, key_id, _tmux) in NAV_KEYS
    ]


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

    The full :func:`_nav_row` is still appended so the operator can arrow ``↓`` to
    option 2 ("manually approve edits") and press ``⏎`` when they want manual
    approval instead of auto mode.
    """
    return [
        [
            {"text": "✅ Approve (auto mode)", "callback_data": f"{QA_NAV_PREFIX}ent"},
            {"text": "📝 Keep planning", "callback_data": f"{QA_NAV_PREFIX}esc"},
        ],
    ]


def scraped_reply_markup(labels) -> dict:
    """Bot-API ``reply_markup`` for a pane-scraped single-select AskUserQuestion.

    One semantic ``qa:<i>`` button per option label (index-only callback, so a
    long option can't blow Telegram's 64-byte cap), then the always-present nav
    row. Built from the labels the pane watcher scraped
    (:func:`~chela.telegram.panescan.detect_askuserquestion`), because the
    structured transcript record only lands *after* the question is answered.
    """
    rows = [
        [{"text": _truncate(str(label)), "callback_data": f"{QA_CB_PREFIX}{i}"}]
        for i, label in enumerate(labels)
    ]
    rows.append(_nav_row())
    return {"inline_keyboard": rows}


def nav_only_markup() -> dict:
    """The nav-fallback row alone — for the multi-tab / multi-select shapes.

    The MVP never hands out semantic option buttons for a multi-question or
    multi-select selector (they would answer the wrong thing), so the operator
    drives the selector by hand with the nav keys.
    """
    return {"inline_keyboard": [_nav_row()]}


def ask_reply_markup(msg) -> dict | None:
    """Bot-API ``reply_markup`` for a transcript-triggered ``tool_use``, else None.

    ``msg`` is a :class:`~chela.telegram.parser.Message`. Only ``ExitPlanMode``
    (Slice B) gets a keyboard here: two approval buttons (:func:`_plan_rows`) plus
    the nav row — option-count-independent keystrokes, so it attaches for any
    ExitPlanMode ``tool_use`` (the harness-rendered choices aren't in the
    transcript, so there is nothing to enumerate).

    ``AskUserQuestion`` no longer gets a keyboard here. Its ``tool_use`` record was
    measured to land in the transcript only once the question is *answered*, so a
    keyboard attached at that point would be useless (the selector is gone) and
    would double-post the already-relayed prompt. Slice A2 surfaces it live from
    the **pane** instead (:func:`~chela.telegram.gatewatch.PermissionGateWatcher`),
    building the keyboard from the scraped options (:func:`scraped_reply_markup` /
    :func:`nav_only_markup`); the relay drops the post-answer transcript ``tool_use``.

    Returns ``None`` for anything else — so the relay attaches a keyboard only when
    there is a real prompt to answer, and never crashes on a missing payload.
    """
    if getattr(msg, "content_type", None) != "tool_use":
        return None
    if getattr(msg, "tool_name", None) != "ExitPlanMode":
        return None
    rows = _plan_rows()
    rows.append(_nav_row())
    return {"inline_keyboard": rows}


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
