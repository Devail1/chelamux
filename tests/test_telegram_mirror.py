"""The pane MIRROR — one message, edited after every key, with a real D-pad (CMX-52).

The three hand-built detectors parse a dialog *in order to answer it*, so each only works
on a shape it was measured against, and each answers only the options it chose to bind.
The mirror parses nothing: it re-draws the pane region verbatim and puts every key the TUI
reads under it. Liav, from his phone on 2026-07-14, named three things that were broken
and this file locks in the fix for each:

  (a) **NO FEEDBACK.** Tapping ↑/↓ changed nothing on screen, because the render signature
      did not include the cursor — so the edit never fired and the human was driving a
      selector they could not see. Here the ``❯`` cursor is *in* the signature: move it and
      the message is re-drawn in place (never re-posted).
  (b) **NOT UNIVERSAL.** A checkpoint restore, ``/model`` or Settings relayed as nothing at
      all. Here they mirror and they are drivable, with no detector written for them.
  (c) **A multiSelect was UN-ANSWERABLE.** The nav row has no ``Space``, no ``Tab``, no
      ``←``/``→``. The D-pad has all of them.

Plus the two rules that keep it from becoming a rate-limit gun or a liar: an **unchanged**
pane makes no API call at all, and a dialog that leaves the pane **deletes** its message.
"""
from __future__ import annotations

from chela.telegram.gatewatch import (
    PermissionGateWatcher,
    format_mirror_card,
    mirror_signature,
)
from chela.telegram.interactive import (
    MIRROR_ACTIONS,
    MIRROR_KEYS,
    MIRROR_REFRESH_KEY_ID,
    decode_mirror_callback,
    mirror_markup,
)
from chela.telegram.panescan import detect_dialog

# ── Real panes (Claude Code 2.1.207) ────────────────────────────────────────

# A Bash approval. chela's permission detector DOES see this one — so it is the case that
# proves the mirror COEXISTS with a semantic card rather than replacing it.
BASH_PANE = """\
 Bash command
   rm -rf build/

 Do you want to proceed?
 ❯ 1. Yes
   2. Yes, and don't ask again for rm commands in this project
   3. No, and tell Claude what to do differently (esc)

 Esc to cancel
"""

# A multi-question / multi-select selector: the ``←  ☐ … →`` tab strip. The scraper reads
# this as "unparseable" and offers the nav row — which cannot toggle a checkbox. THE shape
# that has to become drivable.
MULTI_PANE = """\
←  ☐ Fruit  ☐ Color  ✔ Submit  →

Which fruit do you prefer?

❯ 1. Apple
  2. Banana
  3. Type something.

Enter to select · Tab/Arrow keys to navigate · Esc to cancel
"""

# The same selector after one ↓ — the ONLY difference is where the ❯ sits.
MULTI_PANE_CURSOR_MOVED = MULTI_PANE.replace("❯ 1. Apple", "  1. Apple").replace(
    "  2. Banana", "❯ 2. Banana"
)

# A single-select selector: every real option already has a numbered button, so this one
# needs no D-pad and must NOT be mirrored.
SINGLE_PANE = """\
 ☐ Fruit

Which fruit do you prefer?

❯ 1. Apple
  2. Banana
  3. Type something.

Enter to select · ↑/↓ to navigate · Esc to cancel
"""

# Two dialogs chela has NO detector for. Today they reach the phone as nothing at all.
RESTORE_PANE = """\
 Restore the code to a previous checkpoint?

 ❯ 1. 14:02  Add the mirror detector
   2. 13:41  Port the D-pad

 Enter to continue · Esc to cancel
"""

MODEL_PANE = """\
 Select model

 ❯ 1. Opus 4.8
   2. Sonnet 5
   3. Haiku 4.5

 Enter to confirm · Esc to cancel
"""

WORKING_PANE = "● Thinking…\n\n────────────────────────\n> \n"


class _Registry:
    def __init__(self, mapping):
        self._mapping = mapping

    def thread_for_window(self, wid):
        return self._mapping.get(wid)


class _Bot:
    """Records every post / edit / delete, and can be made to fail an edit."""

    def __init__(self, edit_ok=True):
        self.sent: list[tuple] = []
        self.edits: list[tuple] = []
        self.deleted: list[int] = []
        self.edit_ok = edit_ok
        self._next_id = 100

    def post(self, text, parse_mode=None, thread=None, markup=None):
        self._next_id += 1
        self.sent.append((self._next_id, text, parse_mode, thread, markup))
        return self._next_id

    def edit(self, message_id, text, parse_mode=None, markup=None):
        self.edits.append((message_id, text, parse_mode, markup))
        return self.edit_ok

    def delete(self, message_id):
        self.deleted.append(message_id)
        return True

    @property
    def calls(self) -> int:
        return len(self.sent) + len(self.edits) + len(self.deleted)


def _capture(panes):
    def capture(wid):
        return panes.get(wid, "")

    return capture


def _watcher(bot, panes, *, held=None, pending=None, now=None):
    clock = now or (lambda: 0.0)
    return PermissionGateWatcher(
        bot.post,
        _Registry({"@1": "100"}),
        capture=_capture(panes),
        post=bot.post,
        edit=bot.edit,
        delete=bot.delete,
        pending=pending,
        held=held,
        # A fixed clock would sit permanently inside the mirror's edit floor, so the
        # default here advances past it; the throttle has its own tests below.
        now=clock if now else _ticking(),
    )


def _ticking(step: float = 10.0):
    """A monotonic clock that advances a full step per read — never inside the floor."""
    state = {"t": 0.0}

    def now() -> float:
        state["t"] += step
        return state["t"]

    return now


def _mirror_sends(bot) -> list[tuple]:
    """Only the mirror's messages — the ones wearing the ``m:`` D-pad.

    NOT "the HTML ones": a CMX-49 hook card is HTML too (its previews live in a ``<pre>``).
    The keyboard is the thing that is unambiguously the mirror's.
    """
    return [
        s for s in bot.sent
        if s[4] and any(
            b["callback_data"].startswith("m:")
            for row in s[4]["inline_keyboard"] for b in row
        )
    ]


# ── (b) NOT UNIVERSAL — eight shapes, no per-shape parser ───────────────────


def test_every_dialog_shape_mirrors_including_the_ones_with_no_detector():
    # The point of the whole surface: mirroring a pane needs no parser, so a dialog nobody
    # wrote a detector for still arrives — and is still drivable.
    for pane, name in [
        (BASH_PANE, "ToolApproval"),
        (MULTI_PANE, "AskUserQuestion"),
        (SINGLE_PANE, "AskUserQuestion"),
        (RESTORE_PANE, "RestoreCheckpoint"),
        (MODEL_PANE, "Settings"),
    ]:
        dialog = detect_dialog(pane)
        assert dialog is not None, f"{name} must mirror"
        assert dialog.name == name


def test_a_working_pane_mirrors_nothing():
    assert detect_dialog(WORKING_PANE) is None
    assert detect_dialog("") is None


def test_a_dialog_with_no_detector_is_relayed_and_is_drivable():
    # RestoreCheckpoint: chela has no semantic detector for it, so today it relays NOTHING.
    bot = _Bot()
    w = _watcher(bot, {"@1": RESTORE_PANE})
    w.poll(["@1"])

    mirrors = _mirror_sends(bot)
    assert len(mirrors) == 1
    _mid, text, parse_mode, thread, markup = mirrors[0]
    assert parse_mode == "HTML"
    assert thread == "100"
    assert "Restore the code" in text
    # …and every key on it fires a real tmux key.
    keys = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert "m:ent" in keys and "m:esc" in keys


def test_the_mirror_renders_the_pane_verbatim_in_a_pre_block():
    # <pre> is the one Telegram surface that is MONOSPACED and scrolls horizontally rather
    # than wrapping — box drawing and a ❯ cursor only line up there. ccbot posted this
    # region as plain text (proportional font); this is the strictly better surface, and
    # it is the one CMX-49 already proved on a real phone with its previews.
    card = format_mirror_card(detect_dialog(MULTI_PANE))
    assert card.parse_mode == "HTML"
    assert "<pre>" in card.text and "</pre>" in card.text
    assert "❯ 1. Apple" in card.text          # the cursor, verbatim
    assert "☐ Fruit" in card.text             # the checkboxes, verbatim
    # A body Telegram refuses to parse must degrade to the content, never to silence.
    assert card.plain is not None and "<pre>" not in card.plain
    assert "❯ 1. Apple" in card.plain


# ── (a) NO FEEDBACK — the cursor IS the signature ───────────────────────────


def test_the_mirror_shows_WHAT_is_being_approved_not_just_the_prompt():
    """The region must start at the tool HEADER, not at "Do you want to proceed?".

    ccbot's table tries the prompt pattern first, which begins the region *below* the
    command — so a Bash approval mirrored as "Do you want to proceed? / 1. Yes / 2. No",
    asking the human to approve nothing in particular. That is strictly worse than the
    one-line card it sits next to, which does name the command. Caught before shipping;
    this is the guard.
    """
    card = format_mirror_card(detect_dialog(BASH_PANE))
    assert "Bash command" in card.text
    assert "rm -rf build/" in card.text, "the command being approved MUST be on the card"


def test_a_dialog_full_of_html_metacharacters_still_renders():
    """A `<`, a `&` or a `>` in a command must be escaped, or Telegram REJECTS the body.

    An HTML parse failure is not a cosmetic bug here: the message is refused outright, and
    the mirror would vanish for exactly the commands most worth looking at.
    """
    pane = BASH_PANE.replace("rm -rf build/", "grep -r \"<div>\" src/ && echo 'a & b'")
    card = format_mirror_card(detect_dialog(pane))
    assert "&lt;div&gt;" in card.text and "&amp;&amp;" in card.text
    assert "<div>" not in card.text, "a raw < would make Telegram refuse the whole message"
    # The plain-text fallback carries the command UNescaped — it is not HTML.
    assert "<div>" in card.plain


def test_the_cursor_is_part_of_the_render_signature():
    # THE bug. The hook card's signature is keyed on the gate's CONTENT, and content does
    # not change when you press ↑ — so the edit never fired and the button looked dead.
    before = mirror_signature(detect_dialog(MULTI_PANE))
    after = mirror_signature(detect_dialog(MULTI_PANE_CURSOR_MOVED))
    assert before != after, "moving the cursor must change the render"


def test_a_keypress_re_renders_the_SAME_message_in_place():
    # One message, edited — not a second one posted below it.
    panes = {"@1": MULTI_PANE}
    bot = _Bot()
    w = _watcher(bot, panes)
    w.poll(["@1"])
    assert len(_mirror_sends(bot)) == 1
    message_id = _mirror_sends(bot)[0][0]

    panes["@1"] = MULTI_PANE_CURSOR_MOVED     # the ↓ landed
    w.refresh_mirror("@1")

    assert len(_mirror_sends(bot)) == 1, "the mirror must never double-post"
    assert len(bot.edits) == 1
    edited_id, text, parse_mode, _markup = bot.edits[0]
    assert edited_id == message_id
    assert parse_mode == "HTML"
    assert "❯ 2. Banana" in text               # the cursor moved, in the chat


def test_an_unchanged_pane_produces_no_api_call_at_all():
    # The de-dup. A dialog sits on the pane for as long as a human takes to read it; a
    # tick that re-renders it would walk the topic into the flood limit for nothing.
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE})
    w.poll(["@1"])
    calls = bot.calls
    w.poll(["@1"])
    w.poll(["@1"])
    w.refresh_mirror("@1")                     # even a 🔄 on an unchanged pane
    assert bot.calls == calls, "an unchanged render must cost zero API calls"


def test_a_tap_bypasses_the_edit_throttle():
    # The poll-driven edits have a floor (a repainting dialog must not spam Telegram), but
    # a TAP must never be throttled: a keypress that does not visibly move the cursor is
    # exactly the "feels dead" bug this surface exists to kill.
    panes = {"@1": MULTI_PANE}
    frozen = 1000.0
    bot = _Bot()
    w = _watcher(bot, panes, now=lambda: frozen)   # the clock never advances
    w.poll(["@1"])

    panes["@1"] = MULTI_PANE_CURSOR_MOVED
    w.poll(["@1"])
    assert bot.edits == [], "a poll inside the floor is skipped"

    w.refresh_mirror("@1")
    assert len(bot.edits) == 1, "a tap re-renders regardless of the floor"


def test_a_throttled_edit_is_deferred_not_lost():
    # The skipped edit leaves the signature STALE on purpose, so the next poll renders the
    # newest pane. Nothing is dropped — only deferred.
    panes = {"@1": MULTI_PANE}
    clock = {"t": 1000.0}
    bot = _Bot()
    w = _watcher(bot, panes, now=lambda: clock["t"])
    w.poll(["@1"])

    panes["@1"] = MULTI_PANE_CURSOR_MOVED
    w.poll(["@1"])                             # inside the floor → skipped
    assert bot.edits == []

    clock["t"] += 60.0                         # the floor has passed
    w.poll(["@1"])
    assert len(bot.edits) == 1
    assert "❯ 2. Banana" in bot.edits[0][1]


# ── The lifecycle: poof when the dialog goes ────────────────────────────────


def test_the_mirror_is_deleted_when_the_dialog_leaves_the_pane():
    # Its buttons are not merely stale, they are LIVE — a later tap would fire Enter at
    # whatever the agent went on to do.
    panes = {"@1": MULTI_PANE}
    bot = _Bot()
    w = _watcher(bot, panes)
    w.poll(["@1"])
    message_id = _mirror_sends(bot)[0][0]

    panes["@1"] = WORKING_PANE                 # answered — the dialog is gone
    w.poll(["@1"])
    assert message_id in bot.deleted


def test_a_key_that_answers_the_dialog_poofs_the_mirror_on_the_tap_itself():
    # Enter submits; the re-render finds no dialog and deletes the message rather than
    # leaving a live D-pad pointing at a resolved gate.
    panes = {"@1": MULTI_PANE}
    bot = _Bot()
    w = _watcher(bot, panes)
    w.poll(["@1"])
    message_id = _mirror_sends(bot)[0][0]

    panes["@1"] = WORKING_PANE
    w.refresh_mirror("@1")
    assert bot.deleted == [message_id], "the tap poofs the mirror, and only the mirror"


def test_a_failed_edit_is_swallowed_and_never_raises():
    # A 429, a deleted message, a "not modified" — decoration must never wedge the relay.
    panes = {"@1": MULTI_PANE}
    bot = _Bot(edit_ok=False)
    w = _watcher(bot, panes)
    w.poll(["@1"])

    panes["@1"] = MULTI_PANE_CURSOR_MOVED
    w.refresh_mirror("@1")                     # must not raise
    # The edit failed, so the watcher poofs the dead message and posts a fresh one rather
    # than leaving a mirror that silently stopped tracking the pane.
    assert len(_mirror_sends(bot)) == 2


def test_a_capture_that_blows_up_never_reaches_the_tap_handler():
    def boom(_wid):
        raise RuntimeError("tmux is having a day")

    bot = _Bot()
    w = PermissionGateWatcher(
        bot.post, _Registry({"@1": "100"}), capture=boom,
        post=bot.post, edit=bot.edit, delete=bot.delete,
    )
    w.refresh_mirror("@1")                     # swallowed
    assert bot.calls == 0


# ── The composition rule (the mirror vs the semantic cards) ─────────────────


def test_a_permission_gate_keeps_its_one_tap_card_AND_gains_a_mirror():
    # Coexistence, not competition. The card's ✅ Allow once stays one tap; the mirror is
    # what lets you reach option 2 ("don't ask again"), which the card deliberately refuses
    # to bind — today that option is reachable from a phone only by guessing at a selector
    # you cannot see.
    bot = _Bot()
    w = _watcher(bot, {"@1": BASH_PANE})
    w.poll(["@1"])

    bodies = [s[1] for s in bot.sent]
    assert any(b.startswith("❓ Permission — Bash: rm -rf build/") for b in bodies)
    assert len(_mirror_sends(bot)) == 1
    assert "2. Yes, and don't ask again" in _mirror_sends(bot)[0][1]


def test_a_single_select_selector_is_NOT_mirrored():
    # Every real option is already a numbered button that lands on it. A D-pad there would
    # be a second message and a strictly worse way to answer.
    bot = _Bot()
    w = _watcher(bot, {"@1": SINGLE_PANE})
    w.poll(["@1"])
    assert _mirror_sends(bot) == []
    assert len(bot.sent) == 1                  # the semantic card, and only it


def test_a_hook_HELD_gate_is_NOT_mirrored():
    # CMX-50: every option of every question is a button that answers the agent directly,
    # with zero keypresses. There is no cursor to steer, so there is nothing to mirror.
    from chela.gateanswer import OpenGate
    from chela.telegram.hookgate import HookGate, Option, Question

    gate = HookGate(
        tool_use_id="toolu_01", tool="AskUserQuestion", seq=1,
        questions=(Question(
            question="Which fruit?", header="Fruit", multi_select=True,
            options=(Option(label="Apple", description="", preview=""),),
        ),),
    )
    held = OpenGate(
        tool_use_id="toolu_01", wid="@1", questions=[], deadline=9e12, budget=90.0)

    bot = _Bot()
    w = _watcher(
        bot, {"@1": MULTI_PANE},
        pending=lambda _wid: gate, held=lambda _tuid: held,
    )
    w.poll(["@1"])
    assert _mirror_sends(bot) == [], "a one-tap gate needs no D-pad"


def test_an_UNHELD_multiselect_gate_IS_mirrored():
    # The hold expired (or the agent predates the plugin). The card falls back to the nav
    # row — which has no Space, no Tab, no ←/→ — so without the mirror this question is
    # literally un-answerable from a phone. THIS is Liav's complaint (c).
    from chela.telegram.hookgate import HookGate, Option, Question

    gate = HookGate(
        tool_use_id="toolu_01", tool="AskUserQuestion", seq=1,
        questions=(Question(
            question="Which fruit?", header="Fruit", multi_select=True,
            options=(Option(label="Apple", description="", preview=""),),
        ),),
    )
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, pending=lambda _wid: gate, held=lambda _t: None)
    w.poll(["@1"])

    assert len(_mirror_sends(bot)) == 1
    keys = [
        b["callback_data"]
        for row in _mirror_sends(bot)[0][4]["inline_keyboard"]
        for b in row
    ]
    # (c): the three keys the nav row never had.
    assert "m:spc" in keys and "m:tab" in keys and "m:lt" in keys and "m:rt" in keys


# ── The D-pad itself ───────────────────────────────────────────────────────


def test_every_button_resolves_through_the_derived_key_table():
    # CMX-45's rule: MIRROR_ACTIONS is DERIVED from MIRROR_KEYS, so a button and the key it
    # fires cannot drift apart. 🔄 is the one keyless button and is excluded by construction.
    for row in MIRROR_KEYS:
        for label, key_id, tmux_key in row:
            assert label, "every button is captioned"
            if key_id == MIRROR_REFRESH_KEY_ID:
                assert tmux_key is None, "a refresh is not a keypress"
                assert key_id not in MIRROR_ACTIONS
                continue
            assert MIRROR_ACTIONS[key_id] == (tmux_key, label)


def test_the_dpad_carries_every_key_the_tui_reads():
    keys = [
        b["callback_data"] for row in mirror_markup()["inline_keyboard"] for b in row
    ]
    assert keys == [
        "m:spc", "m:up", "m:tab",
        "m:lt", "m:dn", "m:rt",
        "m:esc", "m:ref", "m:ent",
    ]


def test_a_vertical_only_dialog_drops_the_inert_arrows():
    # A RestoreCheckpoint is a plain vertical list — ← and → do nothing there, and an inert
    # button is how a human learns to distrust the whole keyboard. ↑/↓ stay.
    keys = [
        b["callback_data"]
        for row in mirror_markup("RestoreCheckpoint")["inline_keyboard"]
        for b in row
    ]
    assert "m:lt" not in keys and "m:rt" not in keys
    assert "m:up" in keys and "m:dn" in keys


def test_an_unknown_dialog_name_gets_the_FULL_pad():
    # The mirror exists precisely for the dialogs we do not recognise; refusing them keys
    # would defeat it.
    keys = [
        b["callback_data"]
        for row in mirror_markup("SomeDialogFromClaudeCode3")["inline_keyboard"]
        for b in row
    ]
    assert "m:lt" in keys and "m:rt" in keys


def test_the_callback_decodes_to_a_key_or_a_refresh_and_nothing_else():
    assert decode_mirror_callback("m:up") == ("key", ("Up", "↑"))
    assert decode_mirror_callback("m:spc") == ("key", ("Space", "␣ Space"))
    assert decode_mirror_callback("m:ref") == ("refresh", None)
    # Not ours, or crafted → inert. The handler answers the tap and does nothing.
    assert decode_mirror_callback("qa:nav:ent") is None
    assert decode_mirror_callback("m:") is None
    assert decode_mirror_callback("m:rm -rf /") is None
    assert decode_mirror_callback("") is None
