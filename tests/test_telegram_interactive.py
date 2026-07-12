"""Interactive-prompt inline keyboards — the pure data behind phone answering.

No live Telegram and no PTB import: the pane-triggered AskUserQuestion keyboards
(:func:`scraped_reply_markup` / :func:`nav_only_markup`), the pane-triggered
ExitPlanMode keyboard (:func:`plan_reply_markup`), the now-vestigial transcript
seam (:func:`ask_reply_markup`), the callback the inbound handler decodes
(:func:`decode_callback`), and the answer-injection sequences
(:func:`select_keystrokes_relative` / :func:`select_keystrokes`) are all plain
data, so these lock in

  * both interactive prompts are now pane-triggered — ``ask_reply_markup`` no
    longer builds any keyboard (Slice A2 + B2; both transcript records are
    post-answer);
  * one semantic ``qa:<i>`` button per scraped option, index-only within
    Telegram's 64-byte cap, then the nav row; nav-only for the fallback shape;
  * the ExitPlanMode approve / keep-planning buttons (Enter / Escape) + nav row;
  * cursor-relative select-and-submit keystrokes (never a blind Down×i);
  * decode round-trips (select / nav-key / refresh) and rejects junk payloads.
"""
from __future__ import annotations

from chela.telegram.interactive import (
    NAV_ACTIONS,
    NAV_KEYS,
    QA_CB_PREFIX,
    ask_reply_markup,
    decode_callback,
    nav_only_markup,
    plan_reply_markup,
    scraped_reply_markup,
    select_keystrokes,
    select_keystrokes_relative,
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


def _has_semantic(markup):
    return any(not c.startswith("qa:nav:") for c in _callbacks(markup))


# --------------------------------------------------------------------------
# select_keystrokes / select_keystrokes_relative — the answer-injection contract
# --------------------------------------------------------------------------

def test_blind_select_keystrokes_is_downs_then_enter():
    # The fallback used only when the live cursor can't be read from the pane.
    assert select_keystrokes(0) == ["Enter"]
    assert select_keystrokes(1) == ["Down", "Enter"]
    assert select_keystrokes(3) == ["Down", "Down", "Down", "Enter"]


def test_cursor_relative_keystrokes_move_down_up_or_stay():
    # Cursor already on the target → just submit.
    assert select_keystrokes_relative(2, 2) == ["Enter"]
    # Target below the cursor → Down×delta.
    assert select_keystrokes_relative(3, 1) == ["Down", "Down", "Enter"]
    # Target above the cursor (the operator arrowed down first) → Up×delta.
    # Verify case from the TODO: current=1, target=0 → one Up.
    assert select_keystrokes_relative(0, 1) == ["Up", "Enter"]
    assert select_keystrokes_relative(0, 3) == ["Up", "Up", "Up", "Enter"]


# --------------------------------------------------------------------------
# scraped_reply_markup / nav_only_markup — the pane-triggered keyboards
# --------------------------------------------------------------------------

def test_scraped_markup_one_semantic_button_per_option_then_nav():
    markup = scraped_reply_markup(["main", "dev"])
    # One index-only semantic row per scraped option, then the nav row.
    assert markup["inline_keyboard"][0] == [{"text": "main", "callback_data": "qa:0"}]
    assert markup["inline_keyboard"][1] == [{"text": "dev", "callback_data": "qa:1"}]
    assert _callbacks({"inline_keyboard": [markup["inline_keyboard"][-1]]}) == [
        f"qa:nav:{key_id}" for (_l, key_id, _t) in NAV_KEYS
    ]


def test_scraped_markup_callback_data_is_index_only_within_64_bytes():
    markup = scraped_reply_markup(["x" * 400, "short"])
    semantic = [c for c in _callbacks(markup) if not c.startswith("qa:nav:")]
    assert semantic == ["qa:0", "qa:1"]
    for cb in _callbacks(markup):
        assert len(cb.encode()) <= 64
    # The 400-char label is truncated for display, never packed into the payload.
    assert len(_buttons(markup)[0]["text"]) < 400


def test_nav_only_markup_is_just_the_nav_row():
    markup = nav_only_markup()
    assert not _has_semantic(markup)  # nav-fallback row only
    assert _callbacks(markup) == [f"qa:nav:{key_id}" for (_l, key_id, _t) in NAV_KEYS]


def test_nav_row_labels_are_glyph_only_so_they_dont_truncate_on_mobile():
    # Five buttons share one row; a worded caption ("⏎ Enter") truncates to "⏎ E…"
    # on a narrow phone, so every label must be a single short glyph (no words).
    for label, _key_id, _tmux in NAV_KEYS:
        assert " " not in label, f"nav label {label!r} has a word — will truncate"
        assert len(label) <= 2  # a glyph (some emoji are 2 code points)


# --------------------------------------------------------------------------
# ask_reply_markup — AskUserQuestion is now pane-triggered: no transcript keyboard
# --------------------------------------------------------------------------

def test_askuserquestion_transcript_tool_use_gets_no_keyboard():
    # Slice A2: the AskUserQuestion tool_use lands post-answer, so no keyboard is
    # attached from the transcript — the pane watcher builds it live instead.
    assert ask_reply_markup(_ask({
        "questions": [{
            "multiSelect": False,
            "options": [{"label": "main"}, {"label": "dev"}],
        }],
    })) is None


def test_no_keyboard_for_other_tools_or_missing_payload():
    assert ask_reply_markup(Message("assistant", "text", "hi")) is None
    assert ask_reply_markup(
        Message("assistant", "tool_use", "Bash", tool_name="Bash")
    ) is None
    assert ask_reply_markup(_ask(None)) is None
    assert ask_reply_markup(_ask({})) is None


def test_exitplanmode_transcript_tool_use_gets_no_keyboard():
    # Slice B2: like AskUserQuestion, the ExitPlanMode tool_use lands post-answer,
    # so no keyboard is attached from the transcript — the pane watcher builds it
    # live instead (:func:`plan_reply_markup`).
    plan_msg = Message(
        "assistant", "tool_use", "ExitPlanMode",
        tool_name="ExitPlanMode", tool_input={"plan": "do the thing"},
    )
    assert ask_reply_markup(plan_msg) is None


# --------------------------------------------------------------------------
# plan_reply_markup — ExitPlanMode (Slice B2): approve / keep-planning buttons
# --------------------------------------------------------------------------

def test_plan_markup_gets_approve_keep_planning_plus_nav():
    markup = plan_reply_markup()
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
    assert decode_callback("qa:nav:ent") == ("key", ("Enter", "⏎"))
    assert decode_callback("qa:nav:esc") == ("key", ("Escape", "⎋"))


# --------------------------------------------------------------------------
# decode_callback — callback_data → action for the inbound handler
# --------------------------------------------------------------------------

def test_decode_semantic_select():
    assert decode_callback("qa:0") == ("select", 0)
    assert decode_callback("qa:2") == ("select", 2)


def test_decode_nav_keys_and_refresh():
    assert decode_callback("qa:nav:up") == ("key", NAV_ACTIONS["up"])
    assert decode_callback("qa:nav:dn") == ("key", ("Down", "↓"))
    assert decode_callback("qa:nav:ent") == ("key", ("Enter", "⏎"))
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
