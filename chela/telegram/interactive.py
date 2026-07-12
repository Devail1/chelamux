"""Interactive-prompt inline keyboards — answer AskUserQuestion from Telegram.

When an agent calls ``AskUserQuestion`` its ``tool_use`` block carries the
structured prompt (``input.questions[]`` with ``{question, header, multiSelect,
options:[{label, description, preview}]}``). The outbound relay
(:mod:`chela.telegram.relay`) already forwards the question text to the bound
topic; this module turns that same structured payload into an **inline keyboard**
so the human can tap an answer from their phone instead of typing it back.

Everything here is pure data (plain dicts + tmux key names), with **no
``python-telegram-bot`` import**, so the keyboard the urllib-based relay attaches
and the callback the PTB inbound handler decodes share one source of truth and
can be unit-tested without the ``[telegram]`` extra:

* :func:`ask_reply_markup` builds the Bot-API ``reply_markup`` dict the relay
  json-encodes onto the ``sendMessage`` for an AskUserQuestion ``tool_use``.
* :func:`decode_callback` decodes a tapped button's ``callback_data`` back into
  an action for the inbound handler to run (:mod:`chela.telegram.inbound`).
* :func:`select_keystrokes` is the answer-injection contract: the tmux key
  sequence that selects option ``i`` in Claude Code's single-select selector.

**MVP boundary (Slice A):** semantic option buttons are attached ONLY for a
single question with ``multiSelect == False`` and well-formed options. Any other
shape (multiple questions, multi-select, missing/blank option labels) gets the
navigation-fallback row only, so the human drives the selector manually rather
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
NAV_KEYS: list[tuple[str, str, str | None]] = [
    ("↑", "up", "Up"),
    ("↓", "dn", "Down"),
    ("⎋ Esc", "esc", "Escape"),
    ("⏎ Enter", "ent", "Enter"),
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
    """The tmux key sequence that selects option ``index`` and submits it.

    Claude Code's AskUserQuestion selector highlights option 0 on render, so
    ``index`` ``Down`` presses move the highlight to option ``index`` and
    ``Enter`` submits (single question → the selection is the answer). The MVP
    excludes multi-select and free-text, and the auto-appended "Other" row sorts
    *after* the semantic options, so it never offsets these indices.
    """
    return ["Down"] * index + ["Enter"]


def _truncate(text: str, n: int = _BTN_TEXT_MAX) -> str:
    text = text.strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def _nav_row() -> list[dict]:
    """The navigation-fallback row as Bot-API inline buttons."""
    return [
        {"text": label, "callback_data": f"{QA_NAV_PREFIX}{key_id}"}
        for (label, key_id, _tmux) in NAV_KEYS
    ]


def _semantic_rows(questions: list) -> list[list[dict]]:
    """One button per option label for a simple single-select question, else [].

    Returns semantic option buttons ONLY when the prompt is exactly one question,
    ``multiSelect`` is false, and every option is a dict with a non-blank label
    (a blank/missing label is how a free-text-style option shows up — we bail to
    nav-only rather than emit a button that would answer the wrong option).
    """
    if len(questions) != 1:
        return []
    q = questions[0]
    if not isinstance(q, dict) or q.get("multiSelect"):
        return []
    options = q.get("options")
    if not isinstance(options, list) or not options:
        return []
    if not all(
        isinstance(o, dict) and str(o.get("label") or "").strip() for o in options
    ):
        return []
    return [
        [{"text": _truncate(str(opt["label"])), "callback_data": f"{QA_CB_PREFIX}{i}"}]
        for i, opt in enumerate(options)
    ]


def ask_reply_markup(msg) -> dict | None:
    """Bot-API ``reply_markup`` for an AskUserQuestion ``tool_use``, else None.

    ``msg`` is a :class:`~chela.telegram.parser.Message`. Returns ``None`` for
    anything that is not an ``AskUserQuestion`` ``tool_use`` carrying a non-empty
    ``questions`` list — so the relay attaches a keyboard only when there is a
    real prompt to answer, and never crashes if the payload is missing/malformed.
    The nav-fallback row is always included; semantic option buttons are added
    only for the MVP single-select shape (:func:`_semantic_rows`).
    """
    if getattr(msg, "content_type", None) != "tool_use":
        return None
    if getattr(msg, "tool_name", None) != "AskUserQuestion":
        return None
    inp = getattr(msg, "tool_input", None)
    if not isinstance(inp, dict):
        return None
    questions = inp.get("questions")
    if not isinstance(questions, list) or not questions:
        return None
    rows = _semantic_rows(questions)
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
