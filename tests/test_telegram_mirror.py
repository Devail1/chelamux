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

**CMX-54 — the mirror is the PRIMARY journey now, so the composition rule is INVERTED.**
CMX-52 shipped it as the conditional surface: suppressed whenever "every option is already
one tap away", which — the moment CMX-50's held-gate buttons work — is every gate a hook
covers. Liav then drove the mirror from his phone and chose it (*"the cursor moved, and it
was pretty nice, i like this surface more"*), answering a `multiSelect` from a phone for the
first time. So the old rule was a live regression: **the better the zero-keypress path
worked, the more reliably it hid the surface he had picked.** Now every dialog is mirrored
and the zero-keypress option buttons ride on the SAME message, above the D-pad — the tests
below lock in both halves, and the proof-guard that refuses a button whose question cannot
be identified.
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
    recompose_mirror_markup,
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

# A single-select selector, as a PRE-PLUGIN agent's gate reaches us: the scraper can read
# it, and no hook ever announced it. Since CMX-57 the mirror is the only message a gate
# posts, so this is the shape whose numbered keystroke buttons must ride on the mirror
# itself — they are the only zero-scroll answer that fleet has.
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


def _watcher(bot, panes, *, held=None, pending=None, selected=None, now=None):
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
        selected=selected,
        # A fixed clock would sit permanently inside the mirror's edit floor, so the
        # default here advances past it; the throttle has its own tests below.
        now=clock if now else _ticking(),
    )


def _holds(tool_use_id: str):
    """A daemon holding this gate's PermissionRequest hook open (CMX-50)."""
    from chela.gateanswer import OpenGate

    def held(tuid):
        if tuid != tool_use_id:
            return None
        return OpenGate(tool_use_id=tuid, wid="@1", questions=[], deadline=9e12,
                        budget=90.0)

    return held


def _held_gate():
    """The MULTI_PANE selector, as the hook payload behind it, with its hook held open.

    One multiSelect question — the shape that cannot be answered by keystrokes at all, and
    the one Liav answered from a phone for the first time on 2026-07-14.
    """
    from chela.telegram.hookgate import HookGate, Option, Question

    gate = HookGate(
        tool_use_id="toolu_01", tool="AskUserQuestion", seq=1,
        questions=(Question(
            question="Which fruit do you prefer?", header="Fruit", multi_select=True,
            options=(Option(label="Apple"), Option(label="Banana")),
        ),),
    )
    return {"pending": lambda _wid: gate, "held": _holds("toolu_01")}


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


def test_a_permission_gate_is_BUTTONS_ONLY_no_redundant_numbered_mirror():
    # CMX-92 (reverses the CMX-54 coexistence rule for the permission shape ONLY). A binary
    # permission gate is self-describing: the ❓ card NAMES the command (legible on a lock
    # screen) and the ✅ Allow once / ❌ Deny buttons NAME what they do. A <pre> mirror of
    # "1. Yes / 2. don't ask / 3. No" under them repeats two self-describing buttons and
    # nothing more, so the permission gate no longer posts one.
    bot = _Bot()
    w = _watcher(bot, {"@1": BASH_PANE})
    w.poll(["@1"])

    bodies = [s[1] for s in bot.sent]
    # The card — the context line + the Allow/Deny keyboard — is still posted, in full.
    assert any(b.startswith("❓ Permission — Bash: rm -rf build/") for b in bodies)
    # …and it is the ONLY message: no mirror, so no numbered "1. Yes / 2. … / 3. No" body.
    assert _mirror_sends(bot) == [], "a permission gate must not post a redundant mirror"
    assert not any("don't ask again" in b for b in bodies), "the numbered menu is gone"


def test_an_ASKUSERQUESTION_still_mirrors_with_its_numbered_options_UNCHANGED():
    # The other side of CMX-92: the suppression is scoped to the permission shape alone. An
    # AskUserQuestion's mirror is its ONLY message and the only carrier of its option text,
    # so it must be byte-for-byte what it was — numbered options, on the mirror, drivable.
    bot = _Bot()
    w = _watcher(bot, {"@1": SINGLE_PANE})
    w.poll(["@1"])

    assert len(_mirror_sends(bot)) == 1, "the AskUserQuestion mirror is untouched"
    mirror = _mirror_sends(bot)[0]
    assert "❯ 1. Apple" in mirror[1] and "2. Banana" in mirror[1], "its numbered options stay"
    data = [b["callback_data"] for row in mirror[4]["inline_keyboard"] for b in row]
    assert data[:2] == ["qa:0", "qa:1"] and "m:ent" in data, "the selector + D-pad stay"


def test_a_single_select_selector_is_mirrored_TOO():
    # CMX-54's inversion. The old rule suppressed the mirror here ("every option is already
    # one tap away") — but the pane is the only surface that shows you WHERE YOU ARE, and
    # "the answer is easy" was never a reason to take it away.
    #
    # CMX-57 then took the card away as well: ONE message per gate. So the numbered
    # keystroke buttons this pre-plugin shape is answered with had to move ONTO the mirror,
    # or removing the card would have removed the answer — and nothing about ANSWERING was
    # allowed to change. Same `qa:<i>` callbacks, same cursor-relative injection; a
    # different message.
    bot = _Bot()
    w = _watcher(bot, {"@1": SINGLE_PANE})
    w.poll(["@1"])
    assert len(bot.sent) == 1                  # the mirror, and nothing else
    mirror = _mirror_sends(bot)[0]
    assert "❯ 1. Apple" in mirror[1]
    data = [b["callback_data"] for row in mirror[4]["inline_keyboard"] for b in row]
    assert data[:2] == ["qa:0", "qa:1"], "the scraped selector, on the mirror"
    assert "m:up" in data and "m:ent" in data, "above the D-pad"
    # …and it does NOT claim to be free of the terminal, because it is not.
    assert "no keystrokes" not in mirror[1]


def test_a_hook_HELD_gate_gets_THE_PANE_AND_THE_BUTTONS_ON_ONE_MESSAGE():
    """THE task (CMX-54). A held gate used to SUPPRESS the mirror — so the better CMX-50's
    zero-keypress path worked, the more reliably it hid the surface Liav had just chosen
    ("the cursor moved, and it was pretty nice, i like this surface more"). The two are
    complementary: the pane shows where you are, the buttons answer with no keystrokes. So
    they ride on ONE message — options above the D-pad.
    """
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, **_held_gate())
    w.poll(["@1"])

    mirrors = _mirror_sends(bot)
    assert len(mirrors) == 1, "the mirror must NOT be suppressed by a held gate"
    text, markup = mirrors[0][1], mirrors[0][4]
    assert "❯ 1. Apple" in text, "the cursor is still there to steer"

    rows = markup["inline_keyboard"]
    answers = [b["callback_data"] for row in rows for b in row
               if b["callback_data"].startswith("qa:")]
    keys = [b["callback_data"] for row in rows for b in row
            if b["callback_data"].startswith("m:")]
    # An option button per option, then the ✅ Send (this question is multiSelect)…
    assert answers == ["qa:h:toolu_01:0:0", "qa:h:toolu_01:0:1", "qa:hs:toolu_01:0"]
    # …and the whole D-pad still under them, on the same keyboard.
    assert "m:up" in keys and "m:spc" in keys and "m:ent" in keys
    # The answer buttons come FIRST — they are the zero-keypress path, not an afterthought.
    assert rows[0][0]["callback_data"].startswith("qa:")


def test_the_mirrors_answer_buttons_send_NO_KEYSTROKE():
    # The whole safety property of CMX-50, restated for this surface: an option button on
    # the mirror carries the `qa:h:` scheme (→ the blocked hook), never an `m:` key (→ tmux).
    # A button that injected keystrokes for a multiSelect is CMX-32 with a nicer picture.
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, **_held_gate())
    w.poll(["@1"])

    rows = _mirror_sends(bot)[0][4]["inline_keyboard"]
    for row in rows:
        for button in row:
            data = button["callback_data"]
            assert data.startswith(("qa:h:", "qa:hs:", "m:")), data
            # No legacy `qa:<i>` keystroke selectors, and no `qa:nav:` row.
            assert not data.startswith("qa:nav:")


def test_a_multi_question_gate_says_WHICH_question_the_buttons_answer():
    # The TUI walks the questions one at a time, so a numbered button beside a pane showing
    # question 2 must answer question 2 — and the human must be able to SEE that it does.
    # The proof is the question's own text, on the pane (`focused_question`).
    from chela.telegram.hookgate import HookGate, Option, Question

    gate = HookGate(
        tool_use_id="toolu_01", tool="AskUserQuestion", seq=1,
        questions=(
            Question(question="Which colour do you prefer?", header="Colour",
                     options=(Option(label="Red"),)),
            # The one the pane is actually on.
            Question(question="Which fruit do you prefer?", header="Fruit",
                     options=(Option(label="Apple"), Option(label="Banana"))),
        ),
    )
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, pending=lambda _w: gate, held=_holds("toolu_01"))
    w.poll(["@1"])

    text, markup = _mirror_sends(bot)[0][1], _mirror_sends(bot)[0][4]
    assert "question 2/2" in text
    answers = [b["callback_data"] for row in markup["inline_keyboard"] for b in row
               if b["callback_data"].startswith("qa:")]
    assert answers == ["qa:h:toolu_01:1:0", "qa:h:toolu_01:1:1"], "question 1, not 0"


def test_an_UNPROVABLE_focus_refuses_the_buttons_and_keeps_the_D_PAD():
    # Neither question's text is on the pane (it clipped them, or the TUI reworded the
    # heading) — so which question a "1" would answer cannot be proven. A button that
    # answers the wrong QUESTION is CMX-32 wearing a new keyboard: refuse it, say nothing
    # false, and leave the D-pad, which is always correct because the human can see it.
    from chela.telegram.hookgate import HookGate, Option, Question

    gate = HookGate(
        tool_use_id="toolu_01", tool="AskUserQuestion", seq=1,
        questions=(
            Question(question="Which colour?", options=(Option(label="Red"),)),
            Question(question="Which vegetable?", options=(Option(label="Leek"),)),
        ),
    )
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, pending=lambda _w: gate, held=_holds("toolu_01"))
    w.poll(["@1"])

    markup = _mirror_sends(bot)[0][4]
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert not any(d.startswith("qa:") for d in data), "an unprovable mapping gets no button"
    assert "m:up" in data and "m:ent" in data, "…but the pane is still drivable"


def test_a_toggled_option_shows_its_TICK_on_the_mirror_too():
    # The same question is answerable from two messages (the CMX-49 card and the mirror).
    # If their ☑ ticks disagreed, the human would not know which set they are about to send.
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, **_held_gate(),
                 selected=lambda _tuid, _q: {1})       # "Banana" is toggled on
    w.poll(["@1"])

    captions = {b["callback_data"]: b["text"]
                for row in _mirror_sends(bot)[0][4]["inline_keyboard"] for b in row}
    assert captions["qa:h:toolu_01:0:0"] == "☐ 1"
    assert captions["qa:h:toolu_01:0:1"] == "☑ 2"


def test_a_PRE_PLUGIN_window_still_gets_a_drivable_mirror_with_no_buttons():
    # Hooks are read at agent STARTUP, so an agent launched before the plugin emits none:
    # no payload, no hold, no zero-keypress channel. The mirror is that fleet's ONLY way to
    # answer a multiSelect from a phone, and it must keep working with no hook at all.
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE})              # pending=None, held=None
    w.poll(["@1"])

    markup = _mirror_sends(bot)[0][4]
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]
    assert not any(d.startswith("qa:") for d in data), "there is no hook to answer through"
    assert "m:spc" in data and "m:ent" in data, "…and the D-pad answers it anyway"


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


def test_redrawing_a_TAPPED_MIRROR_keeps_its_D_PAD_under_the_new_ticks():
    """A ☑ tap redraws the tapped message's keyboard from the draft book — which knows only
    about options. On the mirror that keyboard also carries the D-pad, so redrawing it
    verbatim would silently strip the pad off the very message the human is steering with.
    """
    live = mirror_markup("AskUserQuestion", [
        [{"text": "☐ 1", "callback_data": "qa:h:t1:0:0"}],
        [{"text": "✅ Send", "callback_data": "qa:hs:t1:0"}],
    ])["inline_keyboard"]
    # What the draft book hands back after the tap: the options, with the tick moved.
    fresh = {"inline_keyboard": [
        [{"text": "☑ 1", "callback_data": "qa:h:t1:0:0"}],
        [{"text": "✅ Send", "callback_data": "qa:hs:t1:0"}],
    ]}

    redrawn = recompose_mirror_markup(live, fresh)["inline_keyboard"]
    data = [b["callback_data"] for row in redrawn for b in row]
    assert redrawn[0][0]["text"] == "☑ 1", "the tick moved"
    assert "m:up" in data and "m:ent" in data, "and the D-pad survived the redraw"


def test_redrawing_a_PLAIN_CARD_is_left_exactly_as_it_was():
    # A CMX-49 card has no D-pad to preserve; nothing may be invented for it.
    fresh = {"inline_keyboard": [[{"text": "✓ 1", "callback_data": "qa:h:t1:0:0"}]]}
    assert recompose_mirror_markup([], fresh) == fresh
    assert recompose_mirror_markup([], None) is None


def test_the_callback_decodes_to_a_key_or_a_refresh_and_nothing_else():
    assert decode_mirror_callback("m:up") == ("key", ("Up", "↑"))
    assert decode_mirror_callback("m:spc") == ("key", ("Space", "␣ Space"))
    assert decode_mirror_callback("m:ref") == ("refresh", None)
    # 📖 / 🎛️ — a view toggle. It presses NO key: the decode is what guarantees that a tap
    # on it can never reach the terminal a gate is sitting on.
    assert decode_mirror_callback("m:doc") == ("toggle", None)
    # Not ours, or crafted → inert. The handler answers the tap and does nothing.
    assert decode_mirror_callback("qa:nav:ent") is None
    assert decode_mirror_callback("m:") is None
    assert decode_mirror_callback("m:rm -rf /") is None
    assert decode_mirror_callback("") is None


# ── CMX-57 — ONE MESSAGE PER GATE, and the 📖 that keeps the previews reachable ──
#
# Liav, from his phone on 2026-07-14, having driven the CMX-54 mirror live: *"i think i
# prefer the bottom variant with this emoji 🎛️ … it's also the single view where i can do
# multi select"*. But a 2-question gate posted THREE messages — ❓ Question 1/2, ❓ Question
# 2/2, and the mirror — so the one surface he uses was buried under two he does not.
#
# One gate is one message now. The cards' content is NOT deleted, though: the pane (and so
# the mirror) can only ever show ONE preview, the one under the ❯, while the cards showed
# them all TOGETHER — and comparing two previews is what a preview is FOR. So it moves
# behind a 📖 toggle on the same message, which keeps the D-pad and the answer buttons.


def _two_question_gate():
    """A 2-question gate with previews — the exact shape that posted three messages."""
    from chela.telegram.hookgate import HookGate, Option, Question

    gate = HookGate(
        tool_use_id="toolu_02", tool="AskUserQuestion", seq=1,
        questions=(
            Question(
                question="Which fruit do you prefer?", header="Fruit", multi_select=True,
                options=(
                    Option(label="Apple", description="Crisp and tart.",
                           preview="┌───────┐\n│ APPLE │\n└───────┘"),
                    Option(label="Banana", description="Soft and sweet.",
                           preview="┌────────┐\n│ BANANA │\n└────────┘"),
                ),
            ),
            Question(
                question="Which colour suits it?", header="Colour",
                options=(Option(label="Red", description="Warm."),
                         Option(label="Yellow", description="Bright.")),
            ),
        ),
    )
    return {"pending": lambda _wid: gate, "held": _holds("toolu_02")}


def _last_body(bot) -> str:
    """The mirror's body as it stands now — its last edit, or its post if never edited."""
    return bot.edits[-1][1] if bot.edits else _mirror_sends(bot)[0][1]


def _last_keyboard(bot) -> list[list[dict]]:
    markup = bot.edits[-1][3] if bot.edits else _mirror_sends(bot)[0][4]
    return markup["inline_keyboard"]


def _callbacks(rows) -> list[str]:
    return [b["callback_data"] for row in rows for b in row]


def test_a_TWO_QUESTION_GATE_POSTS_EXACTLY_ONE_MESSAGE():
    """THE task. Three messages became one — and the one that survives is the mirror."""
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, **_two_question_gate())
    w.poll(["@1"])

    assert len(bot.sent) == 1, "a gate posts ONE message — not the mirror plus two ❓ cards"
    assert len(_mirror_sends(bot)) == 1, "and the one it posts is the mirror"
    body = bot.sent[0][1]
    assert "Question 1/2" not in body and "Question 2/2" not in body
    assert "❯ 1. Apple" in body, "the body is the live pane"
    # The 📖 is offered, because there IS a payload to expand into.
    assert "m:doc" in _callbacks(bot.sent[0][4]["inline_keyboard"])


def test_a_card_left_over_from_an_earlier_tick_is_POOFED_when_the_mirror_takes_over():
    # The payload can land a tick after the pane does (the log is read per tick). A card
    # posted in that window must not simply be abandoned in the topic.
    bot = _Bot()
    gate = _two_question_gate()
    w = PermissionGateWatcher(
        bot.post, _Registry({"@1": "100"}), capture=_capture({"@1": MULTI_PANE}),
        post=bot.post, edit=bot.edit, delete=bot.delete,
        # A dialog detector that finds nothing on the first tick → the cards are the
        # fallback; on the second the mirror takes over and they must go.
        detect_dialog=_flaky_dialog(),
        now=_ticking(), **gate,
    )
    w.poll(["@1"])
    cards = list(bot.sent)
    assert cards and not _mirror_sends(bot), "no mirror yet → the cards are the fallback"

    w.poll(["@1"])
    assert len(_mirror_sends(bot)) == 1, "the mirror takes over"
    assert bot.deleted == [c[0] for c in cards], "and the cards it replaces are poofed"


def _flaky_dialog():
    """A dialog detector that sees nothing the first time and the real region after."""
    seen = {"n": 0}

    def detect(pane):
        seen["n"] += 1
        return None if seen["n"] == 1 else detect_dialog(pane)

    return detect


def test_the_BOOK_expands_EVERY_option_with_its_DESCRIPTION_and_its_PREVIEW():
    """The whole reason the cards' content could not simply be deleted.

    The TUI draws one preview at a time — the one under the ❯ — so the pane can never put
    two side by side. The payload can, and 📖 is where it does it.
    """
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, **_two_question_gate())
    w.poll(["@1"])
    w.toggle_mirror("@1")                       # 📖

    assert len(bot.sent) == 1, "still ONE message — the expansion is an EDIT, not a post"
    body = _last_body(bot)
    assert "APPLE" in body and "BANANA" in body, "both previews, TOGETHER"
    assert "Crisp and tart." in body and "Soft and sweet." in body
    assert "Question 1/2" in body and "Question 2/2" in body, "every question, not just one"
    assert "Red" in body and "Yellow" in body, "including the one the pane is NOT showing"


def test_the_expansion_KEEPS_the_dpad_AND_the_answer_buttons():
    # "A human who expands to compare must be able to answer without collapsing first."
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, **_two_question_gate())
    w.poll(["@1"])
    w.toggle_mirror("@1")

    data = _callbacks(_last_keyboard(bot))
    assert "qa:h:toolu_02:0:0" in data, "the zero-keypress option buttons survive the 📖"
    assert "qa:hs:toolu_02:0" in data, "and so does ✅ Send"
    assert "m:up" in data and "m:ent" in data and "m:spc" in data, "and the whole D-pad"
    assert "m:doc" in data, "and the toggle back"


def test_the_toggle_COLLAPSES_back_to_the_live_pane():
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, **_two_question_gate())
    w.poll(["@1"])
    w.toggle_mirror("@1")                       # 📖 expand
    w.toggle_mirror("@1")                       # 🎛️ collapse

    assert len(bot.sent) == 1, "one message throughout"
    body = _last_body(bot)
    assert "❯ 1. Apple" in body, "the pane is back"
    assert "APPLE" not in body, "and the expansion is gone with it"


def test_a_MULTISELECT_TICK_SURVIVES_an_expand_collapse_round_trip():
    # Both surfaces read the same draft book, so the ☑ cannot drift — but the toggle must
    # not drop the keyboard that carries it on the way through, either.
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE},
                 selected=lambda _tuid, _q: {1}, **_two_question_gate())
    w.poll(["@1"])
    ticked = [b["text"] for row in _last_keyboard(bot) for b in row if "☑" in b["text"]]
    assert ticked == ["☑ 2"], "Banana is toggled"

    w.toggle_mirror("@1")                       # 📖
    assert [b["text"] for row in _last_keyboard(bot) for b in row if "☑" in b["text"]] \
        == ["☑ 2"], "the tick is still there in the expansion"
    w.toggle_mirror("@1")                       # 🎛️
    assert [b["text"] for row in _last_keyboard(bot) for b in row if "☑" in b["text"]] \
        == ["☑ 2"], "and after collapsing back"


def test_a_PREPLUGIN_window_gets_a_DRIVABLE_MIRROR_and_NO_BOOK():
    # No plugin → no hook events → no payload. There is nothing to expand into, so there is
    # no 📖 — a button that opened an empty page would be a lie. The mirror still drives.
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE})       # no `pending`, no `held`
    w.poll(["@1"])

    assert len(bot.sent) == 1
    data = _callbacks(bot.sent[0][4]["inline_keyboard"])
    assert "m:doc" not in data, "no payload → no 📖"
    assert "m:up" in data and "m:spc" in data, "but every key still works"
    w.toggle_mirror("@1")                       # a crafted/stale tap must be inert, not fatal
    assert "m:doc" not in _callbacks(_last_keyboard(bot))


def test_an_UNCHANGED_render_still_costs_ZERO_api_calls_with_the_book_in_the_keyboard():
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, **_two_question_gate())
    w.poll(["@1"])
    before = bot.calls
    w.poll(["@1"])
    w.poll(["@1"])
    assert bot.calls == before, "an unchanged gate is an unchanged message: no API call"


def test_a_TOGGLE_bypasses_the_edit_throttle():
    # A tap that does not visibly move the message is the bug CMX-52 fixed. The 📖 is a tap.
    bot = _Bot()
    w = _watcher(bot, {"@1": MULTI_PANE}, now=lambda: 1000.0, **_two_question_gate())
    w.poll(["@1"])
    w.toggle_mirror("@1")
    assert bot.edits, "the throttle must not swallow a TAP"


def test_an_expansion_TOO_LONG_for_one_message_says_so_rather_than_lying():
    """The 4096 cap applies to an edit too, and there is no second message to spill into.

    So the previews give way — and when even that is not enough, the body SAYS what is
    missing. A silently truncated expansion is the same lie as the silently dropped preview
    CMX-49 ended.
    """
    from chela.telegram.gatewatch import format_mirror_expansion
    from chela.telegram.hookgate import HookGate, Option, Question

    huge = HookGate(
        tool_use_id="toolu_03", tool="AskUserQuestion", seq=1,
        questions=tuple(
            Question(question=f"Question {i}?", options=(
                Option(label=f"Option {i}", description="d" * 800, preview="P" * 4000),
            ))
            for i in range(4)
        ),
    )
    body = format_mirror_expansion(huge)
    assert len(body) <= 4096, "it has to FIT — an edit Telegram rejects shows nothing at all"
    assert "NOT shown" in body, "and it must say what it could not show"


# The pane that exposed the footer trap. A preview-bearing multiSelect selector: the option
# rows sit BESIDE a preview box, and the footer says "Enter to submit", not "Enter to
# select". `detect_askuserquestion` demands that literal footer; the mirror's own pattern
# deliberately does NOT ("its footer varies by tab"). So the scraper reads this pane as no
# selector at all — and gating the payload on the scraper meant no answer buttons and no 📖
# on the exact shape this whole line of work is about. Caught by rendering it, not by the
# suite, which was green throughout.
PREVIEW_PANE = """\
←  ☐ Fruit  ☐ Colour  ✔ Submit  →

Which fruit do you prefer?

❯ 1. Apple                ┌───────┐
  2. Banana               │ APPLE │
                          └───────┘
                          ✂ 4 lines hidden

Space to toggle · Tab/Arrows to navigate · Enter to submit · Esc to cancel
"""


def test_the_PAYLOAD_is_reachable_even_when_the_SCRAPER_cannot_read_the_pane():
    """The corroboration for a payload is "a selector is on screen" — and the DIALOG
    detector is the one that can answer that reliably, because it parses nothing.

    Asking the option scraper for permission to use the payload re-imports the exact
    fragility CMX-49 existed to escape: it is measured against one wording, and a shape it
    was not measured against silently costs the human every button on the message.
    """
    from chela.telegram.panescan import detect_askuserquestion

    assert detect_askuserquestion(PREVIEW_PANE) is None, "the scraper is blind to this pane"
    assert detect_dialog(PREVIEW_PANE).name == "AskUserQuestion", "the mirror is not"

    bot = _Bot()
    w = _watcher(bot, {"@1": PREVIEW_PANE}, **_two_question_gate())
    w.poll(["@1"])

    assert len(bot.sent) == 1
    data = _callbacks(bot.sent[0][4]["inline_keyboard"])
    assert "qa:h:toolu_02:0:0" in data, "the zero-keypress buttons must still be there"
    assert "m:doc" in data, "and so must the 📖 — the previews are the point of this shape"

    w.toggle_mirror("@1")
    assert "APPLE" in _last_body(bot) and "BANANA" in _last_body(bot)
