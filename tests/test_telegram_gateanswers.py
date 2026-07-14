"""A tap on the phone answers the agent — and sends NOT ONE KEYSTROKE at the pane.

The keystroke substrate is what these retire. It could only ever answer one shape (single
question, single select, cursor visible), and the one time it guessed it guessed wrong:
CMX-32, the human taps option 3 and the agent is told option 2, silently. A multi-question
run and a ``multiSelect`` question have no cursor semantics to inject against at all.

So what is asserted here:

  * a single-select tap answers immediately, and the answer goes through the GATE (the
    blocked hook), never through ``send_tmux``/``send_key`` — the whole feature is that
    the terminal is not touched;
  * a multi-question gate accumulates and delivers ONE complete map, only once every
    question has an answer (a partial map is silently dropped by Claude Code — see
    :func:`chela.gateanswer.validate_answers`);
  * a ``multiSelect`` question toggles and commits a LIST;
  * a tap for a gate that is no longer waiting is REFUSED, and says so — it is never
    re-aimed at whatever is on screen now;
  * the buttons carry the gate's ``tool_use_id``, and a payload that would blow Telegram's
    64-byte callback cap produces NO keyboard rather than one whose buttons get dropped.
"""
import pytest

from chela.gateanswer import OpenGate
from chela.telegram import interactive
from chela.telegram.gateanswers import Drafts

QUESTIONS = [
    {
        "question": "Which store?",
        "options": [{"label": "SQLite"}, {"label": "Postgres"}],
    },
    {
        "question": "Which extras?",
        "multiSelect": True,
        "options": [{"label": "Metrics"}, {"label": "Tracing"}, {"label": "Profiling"}],
    },
]

TUID = "toolu_01ABC"


class _Gates:
    """The rendezvous, stubbed: what is open, and what got submitted."""

    def __init__(self, questions=QUESTIONS, open_=True, ok=True):
        self.questions = questions
        self.open = open_
        self.ok = ok
        self.submitted: list[tuple[str, dict]] = []

    def open_gate(self, tool_use_id):
        if not self.open or tool_use_id != TUID:
            return None
        return OpenGate(tool_use_id=TUID, wid="@3", questions=self.questions,
                        deadline=9e18, budget=90.0)

    def submit(self, tool_use_id, answers):
        self.submitted.append((tool_use_id, answers))
        return (self.ok, "answered" if self.ok else "could not deliver the answer")


@pytest.fixture(autouse=True)
def _no_keystrokes(monkeypatch):
    """The load-bearing assertion of this whole file: the answer path must not touch tmux.

    Every tmux write chela owns is booby-trapped for the duration of these tests. If any
    of them fires, the feature has quietly regressed into the substrate it exists to
    replace — and a green test that only checked the *answer* would never have noticed.
    """
    from chela import messenger

    def _boom(*_a, **_kw):
        raise AssertionError("the zero-keypress path sent a keystroke to the pane")

    for name in ("send_tmux", "send_key", "send_escape", "send_keys"):
        if hasattr(messenger, name):
            monkeypatch.setattr(messenger, name, _boom)


def _drafts(gates):
    return Drafts(open_gate=gates.open_gate, submit=gates.submit)


# --- the answer ------------------------------------------------------------------

def test_a_single_question_is_answered_by_one_tap_and_no_keystroke():
    gates = _Gates(questions=[QUESTIONS[0]])
    drafts = _drafts(gates)

    tap = drafts.pick(TUID, 0, 1)                    # option 2: "Postgres"

    assert tap.ok and tap.done
    assert gates.submitted == [(TUID, {"Which store?": "Postgres"})]
    assert "no keystrokes" in tap.toast


def test_a_multi_question_gate_delivers_ONE_complete_map_and_not_before():
    gates = _Gates()
    drafts = _drafts(gates)

    first = drafts.pick(TUID, 0, 0)                  # "SQLite"
    assert first.ok and not first.done
    assert gates.submitted == [], "a partial map would be silently dropped by the agent"
    assert "1 question still to answer" in first.toast

    drafts.pick(TUID, 1, 1)                          # toggle "Tracing" — not committed yet
    assert gates.submitted == []

    done = drafts.send(TUID, 1)                      # ✅ Send commits the multiSelect

    assert done.ok and done.done
    assert gates.submitted == [(TUID, {
        "Which store?": "SQLite",                    # single-select → one label
        "Which extras?": ["Tracing"],                # multiSelect  → a list
    })]


def test_a_multiselect_question_toggles_on_and_off():
    gates = _Gates(questions=[QUESTIONS[1]])
    drafts = _drafts(gates)

    on = drafts.pick(TUID, 0, 0)                     # ☑ Metrics
    assert on.toast.startswith("☑")
    drafts.pick(TUID, 0, 2)                          # ☑ Profiling
    off = drafts.pick(TUID, 0, 0)                    # ☐ Metrics again
    assert off.toast.startswith("☐")
    assert not gates.submitted                       # a toggle is not an answer

    drafts.send(TUID, 0)
    assert gates.submitted == [(TUID, {"Which extras?": ["Profiling"]})]


def test_send_with_nothing_selected_asks_for_a_pick_rather_than_answering():
    gates = _Gates(questions=[QUESTIONS[1]])
    drafts = _drafts(gates)

    tap = drafts.send(TUID, 0)

    assert tap.ok is False and not gates.submitted


# --- refusing --------------------------------------------------------------------

def test_a_tap_for_a_gate_that_is_no_longer_waiting_is_refused():
    """The agent moved on. The tap must not be re-aimed at whatever is on screen now."""
    gates = _Gates(open_=False)
    drafts = _drafts(gates)

    tap = drafts.pick(TUID, 0, 0)

    assert tap.ok is False
    assert "terminal" in tap.toast                   # and it SAYS so — never silence
    assert gates.submitted == []


def test_a_gate_that_closes_mid_draft_refuses_the_final_tap():
    gates = _Gates()
    drafts = _drafts(gates)
    drafts.pick(TUID, 0, 0)                          # answered question 1…
    gates.open = False                               # …and then the agent gave up waiting

    tap = drafts.pick(TUID, 1, 0)

    assert tap.ok is False and gates.submitted == []


def test_an_option_the_asker_never_offered_is_refused():
    gates = _Gates()
    drafts = _drafts(gates)

    assert drafts.pick(TUID, 0, 9).ok is False       # no such option
    assert drafts.pick(TUID, 7, 0).ok is False       # no such question
    assert gates.submitted == []


def test_a_failed_delivery_tells_the_human_to_use_the_terminal():
    gates = _Gates(questions=[QUESTIONS[0]], ok=False)
    drafts = _drafts(gates)

    tap = drafts.pick(TUID, 0, 0)

    assert tap.ok is False and not tap.done
    assert "terminal" in tap.toast


# --- the keyboard ----------------------------------------------------------------

def test_the_buttons_name_the_gate_the_question_and_the_option():
    markup = interactive.hook_reply_markup(["SQLite", "Postgres"], TUID, 0)
    data = [b["callback_data"] for row in markup["inline_keyboard"] for b in row]

    assert data == [f"qa:h:{TUID}:0:0", f"qa:h:{TUID}:0:1"]
    assert interactive.decode_callback(data[1]) == ("pick", (TUID, 0, 1))


def test_a_multiselect_keyboard_toggles_and_carries_a_send_button():
    markup = interactive.hook_reply_markup(
        ["Metrics", "Tracing"], TUID, 2, multi_select=True, selected={1})
    rows = markup["inline_keyboard"]

    assert [b["text"] for b in rows[0]] == ["☐ 1", "☑ 2"]
    assert rows[-1][0]["callback_data"] == f"qa:hs:{TUID}:2"
    assert interactive.decode_callback(rows[-1][0]["callback_data"]) == ("send", (TUID, 2))


def test_a_gate_id_too_long_for_a_callback_gets_NO_keyboard():
    """Telegram silently rejects a 64-byte-plus callback_data. A keyboard whose buttons
    would be dropped is a gate that arrives unanswerable — so we build none, and the card
    says to answer it in the terminal instead."""
    assert interactive.hook_reply_markup(["a", "b"], "t" * 120, 0) is None


def test_a_crafted_or_stale_callback_payload_is_inert():
    for data in ("qa:h:../../etc:0:0", "qa:h::0:0", "qa:h:t:x:0", "qa:hs:t:nope"):
        assert interactive.decode_callback(data) is None
