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
    Telegram's 64-byte cap, then ⎋ alone; the full nav row only for the fallback
    shape that has no semantic buttons at all (Slice C2);
  * the ExitPlanMode approve / keep-planning and the permission allow / deny
    buttons — both pairs of option-count-independent Enter / Escape keystrokes;
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
    permission_reply_markup,
    plan_reply_markup,
    SELECT_SETTLE_S,
    scraped_reply_markup,
    select_keystrokes,
    select_keystrokes_relative,
    split_select_keys,
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


def test_submit_is_split_off_so_enter_cannot_race_the_cursor_moves():
    # Live (CMX-32): Down Down Enter sent back-to-back submitted option 2, not 3 —
    # the selector answers Enter against the row it held before the last move. The
    # moves and the submit must go out separately, with SELECT_SETTLE_S between.
    assert split_select_keys(["Down", "Down", "Enter"]) == (["Down", "Down"], ["Enter"])
    # Cursor already on target → nothing to settle, just submit.
    assert split_select_keys(["Enter"]) == ([], ["Enter"])
    # A sequence with no trailing submit is left whole (nothing to race).
    assert split_select_keys(["Up"]) == (["Up"], [])
    assert SELECT_SETTLE_S > 0
    assert select_keystrokes_relative(0, 3) == ["Up", "Up", "Up", "Enter"]


# --------------------------------------------------------------------------
# scraped_reply_markup / nav_only_markup — the pane-triggered keyboards
# --------------------------------------------------------------------------

def test_scraped_markup_is_compact_numeric_selectors_then_esc_only():
    markup = scraped_reply_markup(["main", "dev"])
    # Buttons are bare NUMBERS, not the labels: a caption is the one Telegram
    # surface that hard-truncates to a single line, so a label there is unreadable
    # on a phone. The full labels live in the message body (which wraps); these
    # just select them, 1-based caption ↔ 0-based index.
    assert markup["inline_keyboard"][0] == [
        {"text": "1", "callback_data": "qa:0"},
        {"text": "2", "callback_data": "qa:1"},
    ]
    # … then ⎋ alone (Slice C2): Telegram shows no caret, so ↑ ↓ ⏎ 🔄 would be blind
    # presses and the option buttons already answer the question.
    assert markup["inline_keyboard"][-1] == [
        {"text": "⎋ Esc", "callback_data": "qa:nav:esc"}
    ]


def test_scraped_markup_packs_selectors_a_few_per_row_in_option_order():
    markup = scraped_reply_markup([f"option {i}" for i in range(6)])
    rows = markup["inline_keyboard"][:-1]  # drop the ⎋ row
    assert [[b["text"] for b in row] for row in rows] == [["1", "2", "3", "4"], ["5", "6"]]
    # Order is load-bearing: a tap injects (i − cursor) Down/Up presses against the
    # live selector, so button N must still be option N, with no gaps.
    assert [c for c in _callbacks(markup) if not c.startswith("qa:nav:")] == [
        f"qa:{i}" for i in range(6)
    ]


def test_scraped_markup_callback_data_is_index_only_within_64_bytes():
    markup = scraped_reply_markup(["x" * 400, "short"])
    semantic = [c for c in _callbacks(markup) if not c.startswith("qa:nav:")]
    assert semantic == ["qa:0", "qa:1"]
    for cb in _callbacks(markup):
        assert len(cb.encode()) <= 64
    # A 400-char label can't bloat a caption either — the caption is just "1".
    assert _buttons(markup)[0]["text"] == "1"


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

def test_plan_markup_is_approve_keep_planning_only():
    markup = plan_reply_markup()
    # Two semantic approval buttons, both reusing Slice A's nav plumbing so no
    # inbound handler change is needed: Approve→Enter, Keep planning→Escape.
    # Enter's default proceed option enables auto mode (verified live against
    # Claude Code 2.1.207), so the button says so rather than a bare "Approve".
    assert markup["inline_keyboard"] == [[
        {"text": "✅ Approve (auto mode)", "callback_data": "qa:nav:ent"},
        {"text": "📝 Keep planning", "callback_data": "qa:nav:esc"},
    ]]
    # No nav row (Slice C2): these two buttons already bind both keys a human can
    # press without seeing the pane, and a ⎋ button would just duplicate "Keep
    # planning".
    # These are option-count-independent single keystrokes — no index buttons.
    assert not any(
        c.startswith(QA_CB_PREFIX) and not c.startswith("qa:nav:")
        for c in _callbacks(markup)
    )


def test_permission_markup_is_allow_once_deny_only():
    markup = permission_reply_markup()
    # Slice C2: the gate's option 1 ("Yes") is default-highlighted, so Enter allows
    # it once and Escape denies — option-count-independent, like the plan approval.
    assert markup["inline_keyboard"] == [[
        {"text": "✅ Allow once", "callback_data": "qa:nav:ent"},
        {"text": "❌ Deny", "callback_data": "qa:nav:esc"},
    ]]
    # Deliberately NO one-tap "Yes, and don't ask again" — a mis-tap from a phone
    # must not be able to widen the session's permissions for every later command.
    assert len(_buttons(markup)) == 2


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
