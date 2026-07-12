"""AskUserQuestion inline keyboard — the pure data behind phone answering.

No live Telegram and no PTB import: the keyboard the outbound relay attaches
(:func:`ask_reply_markup`), the callback the inbound handler decodes
(:func:`decode_callback`), and the answer-injection sequence
(:func:`select_keystrokes`) are all plain data, so these lock in

  * the MVP boundary — semantic option buttons ONLY for a single single-select
    question with well-formed options; nav-fallback only otherwise;
  * index-only callback_data within Telegram's 64-byte cap;
  * ``Down``×i + Enter as the select-and-submit keystroke sequence;
  * decode round-trips (select / nav-key / refresh) and rejects junk payloads.
"""
from __future__ import annotations

from chela.telegram.interactive import (
    NAV_ACTIONS,
    NAV_KEYS,
    QA_CB_PREFIX,
    ask_reply_markup,
    decode_callback,
    select_keystrokes,
)
from chela.telegram.parser import Message


def _ask(tool_input):
    return Message(
        "assistant", "tool_use", "AskUserQuestion",
        tool_name="AskUserQuestion", tool_input=tool_input,
    )


def _buttons(markup):
    return [b for row in markup["inline_keyboard"] for b in row]


def _callbacks(markup):
    return [b["callback_data"] for b in _buttons(markup)]


# --------------------------------------------------------------------------
# select_keystrokes — the answer-injection contract
# --------------------------------------------------------------------------

def test_select_keystrokes_is_downs_then_enter():
    assert select_keystrokes(0) == ["Enter"]
    assert select_keystrokes(1) == ["Down", "Enter"]
    assert select_keystrokes(3) == ["Down", "Down", "Down", "Enter"]


# --------------------------------------------------------------------------
# ask_reply_markup — MVP happy path: single question, single-select
# --------------------------------------------------------------------------

def test_single_select_gets_one_semantic_button_per_option():
    markup = ask_reply_markup(_ask({
        "questions": [{
            "question": "Pick a base branch",
            "header": "Base",
            "multiSelect": False,
            "options": [
                {"label": "main", "description": "stable"},
                {"label": "dev", "description": "next"},
            ],
        }],
    }))
    assert markup is not None
    # One semantic row per option (index-only callback), then the nav row.
    assert markup["inline_keyboard"][0] == [{"text": "main", "callback_data": "qa:0"}]
    assert markup["inline_keyboard"][1] == [{"text": "dev", "callback_data": "qa:1"}]
    # Nav-fallback row is always present as the last row.
    assert _callbacks({"inline_keyboard": [markup["inline_keyboard"][-1]]}) == [
        f"qa:nav:{key_id}" for (_l, key_id, _t) in NAV_KEYS
    ]


def test_semantic_callback_data_is_index_only_within_64_bytes():
    markup = ask_reply_markup(_ask({
        "questions": [{
            "multiSelect": False,
            "options": [
                {"label": "x" * 400},  # a huge label must never reach callback_data
                {"label": "short"},
            ],
        }],
    }))
    semantic = [c for c in _callbacks(markup) if not c.startswith("qa:nav:")]
    assert semantic == ["qa:0", "qa:1"]
    for cb in _callbacks(markup):
        assert len(cb.encode()) <= 64
    # The 400-char label is truncated for display, never packed into the payload.
    assert len(_buttons(markup)[0]["text"]) < 400


# --------------------------------------------------------------------------
# ask_reply_markup — MVP boundary: nav-fallback ONLY (no semantic buttons)
# --------------------------------------------------------------------------

def _has_semantic(markup):
    return any(not c.startswith("qa:nav:") for c in _callbacks(markup))


def test_multiselect_gets_nav_only():
    markup = ask_reply_markup(_ask({
        "questions": [{
            "multiSelect": True,
            "options": [{"label": "a"}, {"label": "b"}],
        }],
    }))
    assert markup is not None
    assert not _has_semantic(markup)  # nav-fallback row only


def test_multiple_questions_get_nav_only():
    markup = ask_reply_markup(_ask({
        "questions": [
            {"multiSelect": False, "options": [{"label": "a"}]},
            {"multiSelect": False, "options": [{"label": "b"}]},
        ],
    }))
    assert markup is not None
    assert not _has_semantic(markup)


def test_blank_or_freetext_option_label_gets_nav_only():
    # A blank/missing label is how a free-text-style option surfaces — bail to
    # nav-only rather than emit a button that would answer the wrong option.
    markup = ask_reply_markup(_ask({
        "questions": [{
            "multiSelect": False,
            "options": [{"label": "real"}, {"label": "   "}],
        }],
    }))
    assert markup is not None
    assert not _has_semantic(markup)


def test_no_options_gets_nav_only():
    markup = ask_reply_markup(_ask({
        "questions": [{"multiSelect": False, "options": []}],
    }))
    assert markup is not None
    assert not _has_semantic(markup)


# --------------------------------------------------------------------------
# ask_reply_markup — not an AskUserQuestion prompt: no keyboard at all
# --------------------------------------------------------------------------

def test_no_keyboard_for_other_tools_or_missing_payload():
    assert ask_reply_markup(Message("assistant", "text", "hi")) is None
    assert ask_reply_markup(
        Message("assistant", "tool_use", "Bash", tool_name="Bash")
    ) is None
    # AskUserQuestion but no structured payload (nothing to build from).
    assert ask_reply_markup(_ask(None)) is None
    assert ask_reply_markup(_ask({})) is None
    assert ask_reply_markup(_ask({"questions": []})) is None


# --------------------------------------------------------------------------
# ask_reply_markup — ExitPlanMode (Slice B): approve / keep-planning buttons
# --------------------------------------------------------------------------

_UNSET = object()


def _plan(tool_input=_UNSET):
    return Message(
        "assistant", "tool_use", "ExitPlanMode",
        tool_name="ExitPlanMode",
        tool_input={"plan": "do the thing"} if tool_input is _UNSET else tool_input,
    )


def test_exitplanmode_gets_approve_keep_planning_plus_nav():
    markup = ask_reply_markup(_plan())
    assert markup is not None
    # Two semantic approval buttons, both reusing Slice A's nav plumbing so no
    # inbound handler change is needed: Approve→Enter, Keep planning→Escape.
    # Enter's default proceed option enables auto mode (verified live against
    # Claude Code 2.1.207), so the button says so rather than a bare "Approve".
    assert markup["inline_keyboard"][0] == [
        {"text": "✅ Approve (auto mode)", "callback_data": "qa:nav:ent"},
        {"text": "📝 Keep planning", "callback_data": "qa:nav:esc"},
    ]
    # The full nav-fallback row is still appended (arrow to a specific variant).
    assert markup["inline_keyboard"][-1] == [
        {"text": label, "callback_data": f"qa:nav:{key_id}"}
        for (label, key_id, _t) in NAV_KEYS
    ]
    # These are option-count-independent single keystrokes — no index buttons.
    assert not any(
        c.startswith(QA_CB_PREFIX) and not c.startswith("qa:nav:")
        for c in _callbacks(markup)
    )


def test_exitplanmode_approval_keys_decode_to_enter_and_escape():
    # The two approval buttons must round-trip through the existing decoder so the
    # inbound handler fires the right tmux key with no new callback scheme.
    assert decode_callback("qa:nav:ent") == ("key", ("Enter", "⏎ Enter"))
    assert decode_callback("qa:nav:esc") == ("key", ("Escape", "⎋ Esc"))


def test_exitplanmode_keyboard_attaches_even_without_plan_payload():
    # The buttons don't depend on the plan text, so an empty/missing input still
    # gets the keyboard (additive; never blocks relaying the plan).
    assert ask_reply_markup(_plan({})) is not None
    assert ask_reply_markup(_plan(None)) is not None


# --------------------------------------------------------------------------
# decode_callback — callback_data → action for the inbound handler
# --------------------------------------------------------------------------

def test_decode_semantic_select():
    assert decode_callback("qa:0") == ("select", 0)
    assert decode_callback("qa:2") == ("select", 2)


def test_decode_nav_keys_and_refresh():
    assert decode_callback("qa:nav:up") == ("key", NAV_ACTIONS["up"])
    assert decode_callback("qa:nav:dn") == ("key", ("Down", "↓"))
    assert decode_callback("qa:nav:ent") == ("key", ("Enter", "⏎ Enter"))
    assert decode_callback("qa:nav:ref") == ("refresh", None)


def test_decode_rejects_junk_and_foreign_payloads():
    assert decode_callback("k:up") is None          # /screenshot keyboard, not ours
    assert decode_callback("qa:nav:bogus") is None   # unknown nav key
    assert decode_callback("qa:notanint") is None    # non-numeric index
    assert decode_callback("qa:-1") is None          # out of range
    assert decode_callback("qa:9999") is None        # absurd run guard
    assert decode_callback("") is None


def test_callback_scheme_prefix_is_stable():
    # The relay builds ``qa:<i>`` and the handler decodes it — one constant.
    assert QA_CB_PREFIX == "qa:"
