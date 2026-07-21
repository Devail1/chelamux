"""Answering a gate from the phone with zero keypresses — and, above all, NOT doing it.

The feature is small; the ways it could hurt a live agent are not, and they are what these
lock in. A hook runs synchronously inside the agent's process, so this endpoint is the one
place in chela where a bug does not merely lose an event — it FREEZES someone's work:

  * the wait is BOUNDED and it FAILS OPEN. When the budget runs out the answer is
    ``None`` → the endpoint returns ``{}`` → the picker is untouched and still answerable
    in the terminal. It is never a deny: a deny would destroy a run because a human was
    slow, and this feature's whole promise is that a run is never worse off for it;
  * the wait budget can never exceed what the hook's declared timeout can deliver;
  * a STALE answer is refused. A tap for a gate that has resolved, expired, or belongs to
    a different question is dropped and reported — never applied to whatever is on screen
    by then (that is CMX-32's silent mis-answer, arriving through a new door);
  * an answers map that is partial, or names a label the asker never offered, is refused;
  * a ``multiSelect`` question round-trips a LIST;
  * an agent nobody is watching (no window, no bound topic) is never held at all;
  * N simultaneous gates cannot exhaust the server — past the bound, they fail open.

Fully programmatic: no tmux, no Claude Code, no daemon.
"""
import threading
import time

import pytest

from chela import config, gateanswer, hooks
from chela.telegram.hookgate import HookGate, parse_questions

QUESTIONS = [
    {
        "question": "Which store?",
        "header": "Store",
        "options": [
            {"label": "SQLite", "description": "one file"},
            {"label": "Postgres", "description": "a server"},
        ],
    },
    {
        "question": "Which extras?",
        "header": "Extras",
        "multiSelect": True,
        "options": [
            {"label": "Metrics"},
            {"label": "Tracing"},
            {"label": "Profiling"},
        ],
    },
]

BODY = {
    "hook_event_name": "PermissionRequest",
    "session_id": "s1",
    "transcript_path": "/home/u/.claude/projects/-repo/s1.jsonl",
    "tool_name": "AskUserQuestion",
    "tool_input": {"questions": QUESTIONS},
}

TUID = "toolu_01ABCdef"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    monkeypatch.delenv("CHELA_GATE_WAIT_S", raising=False)
    monkeypatch.delenv("CHELA_GATE_MAX_WAITS", raising=False)


def _gate(questions=None, tuid=TUID):
    return HookGate(tool_use_id=tuid, tool="AskUserQuestion",
                    questions=parse_questions({"questions": questions or QUESTIONS}),
                    seq=1)


def _answer(body=BODY, *, wid="@3", pending=None, bound=("@3",)):
    """Run the endpoint's decision with the world stubbed out (no tmux, no event log)."""
    return gateanswer.answer_permission_request(
        body,
        wid_for=lambda _s, _t: wid,
        pending=pending if pending is not None else (lambda _w: _gate()),
        bound=lambda: set(bound),
    )


def _await_open(tool_use_id=TUID, timeout=3.0):
    """Wait for the waiter thread to publish its open gate (it is the rendezvous)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        gate = gateanswer.open_gate(tool_use_id)
        if gate is not None:
            return gate
        time.sleep(0.01)
    raise AssertionError("the gate never opened — nothing to answer")


def _in_thread(fn):
    box: dict = {}

    def run():
        box["result"] = fn()

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread, box


# --- the budget ------------------------------------------------------------------

def test_the_wait_can_never_outlive_the_hook_that_would_deliver_it(monkeypatch, caplog):
    """A wait longer than the hook's own timeout is a wait that can never deliver.

    Claude Code kills an http hook that answers later than its declared ``timeout``
    (measured, 2.1.209: declared 10s → blocked 10.2s; 65 → ~66; 130 → ~133 — honoured
    verbatim, no 60s clamp). So a budget above that ceiling would accept a human's tap and
    hand it to a hook nobody is listening to any more — silently.
    """
    monkeypatch.setenv("CHELA_GATE_WAIT_S", str(hooks.GATE_TIMEOUT + 60))
    assert gateanswer.wait_budget() < hooks.GATE_TIMEOUT
    assert any("clamping" in r.message for r in caplog.records)


def test_a_zero_budget_never_holds_an_agent_at_all(monkeypatch):
    monkeypatch.setenv("CHELA_GATE_WAIT_S", "0")
    assert gateanswer.wait_budget() == 0
    assert _answer() is None                     # opted out → the pane keeps the gate


# --- answering -------------------------------------------------------------------

def test_an_answer_inside_the_budget_comes_back_as_an_updated_input(monkeypatch):
    """The whole point: the agent is handed the human's choices, with no keystrokes."""
    monkeypatch.setenv("CHELA_GATE_WAIT_S", "5")
    thread, box = _in_thread(_answer)

    gate = _await_open()
    assert gate.wid == "@3"
    ok, _ = gateanswer.submit_answer(TUID, {
        "Which store?": "Postgres",
        "Which extras?": ["Tracing", "Metrics"],
    })
    assert ok
    thread.join(timeout=5)

    decision = box["result"]["hookSpecificOutput"]
    assert decision["hookEventName"] == "PermissionRequest"
    assert decision["decision"]["behavior"] == "allow"
    updated = decision["decision"]["updatedInput"]
    assert updated["questions"] == QUESTIONS          # the asker's own payload, verbatim
    assert updated["answers"] == {
        "Which store?": "Postgres",                   # single-select → one label
        "Which extras?": ["Tracing", "Metrics"],      # multiSelect  → a LIST
    }


def test_the_budget_expiring_fails_open_and_never_denies(monkeypatch):
    """A slow human must cost an agent NOTHING. ``{}``, not a deny."""
    monkeypatch.setenv("CHELA_GATE_WAIT_S", "0.3")
    started = time.monotonic()

    assert _answer() is None                     # → the endpoint returns {} — fail OPEN

    assert time.monotonic() - started < 3        # it really gave up, and promptly
    # And it cleaned up after itself: no orphan rendezvous to confuse the next tap.
    assert gateanswer.open_gate(TUID) is None


def test_the_gate_is_torn_down_once_it_is_answered(monkeypatch):
    monkeypatch.setenv("CHELA_GATE_WAIT_S", "5")
    thread, _box = _in_thread(_answer)
    _await_open()
    gateanswer.submit_answer(TUID, {"Which store?": "SQLite",
                                    "Which extras?": ["Metrics"]})
    thread.join(timeout=5)

    assert gateanswer.open_gate(TUID) is None
    assert not list(gateanswer.gates_dir().glob("*.json"))


# --- the OTHER answer route: a ⏎ driven into the pane (CMX-54) ---------------------

def test_a_gate_answered_AT_THE_TERMINAL_releases_the_held_hook_at_once(monkeypatch):
    """The mirror is now the primary surface, so a held gate can be answered TWO ways.

    A ⏎ on the mirrored pane answers the TUI directly and never comes through the
    rendezvous — so the hook we are holding would wait out its whole budget for an answer
    that is never coming, keeping one of the (bounded) wait slots occupied. The gate's
    ``PostToolUse`` fires whichever way it was answered, and it is the signal that lets go.
    """
    monkeypatch.setenv("CHELA_GATE_WAIT_S", "30")   # a budget we must NOT sit out
    thread, box = _in_thread(_answer)
    _await_open()
    started = time.monotonic()

    gateanswer.gate_resolved(TUID)                  # ← the PostToolUse for that call

    thread.join(timeout=5)
    assert not thread.is_alive(), "the hook sat out its budget on a gate that was over"
    assert time.monotonic() - started < 3
    # It gave up FAIL-OPEN, and answered nothing: the tool has already been answered at the
    # terminal, and a decision here would be chela answering a question a second time.
    assert box["result"] is None
    assert not list(gateanswer.gates_dir().glob("*.json"))


def test_a_tap_that_arrives_after_the_terminal_answered_is_REFUSED(monkeypatch):
    # The other half of the interleaving: once the gate is gone, a tap cannot be re-aimed at
    # whatever the agent is doing by now (CMX-32, from the other direction).
    monkeypatch.setenv("CHELA_GATE_WAIT_S", "5")
    thread, _box = _in_thread(_answer)
    _await_open()
    gateanswer.gate_resolved(TUID)
    thread.join(timeout=5)

    ok, reason = gateanswer.submit_answer(TUID, {"Which store?": "SQLite",
                                                 "Which extras?": ["Metrics"]})
    assert ok is False and "no longer waiting" in reason


def test_a_post_tool_use_for_SOME_OTHER_call_never_releases_our_gate(monkeypatch):
    # A PostToolUse fires for every tool call an agent makes; only the one bearing THIS
    # gate's tool_use_id may end its wait. Releasing on someone else's would fail the gate
    # open the moment the agent ran anything at all.
    monkeypatch.setenv("CHELA_GATE_WAIT_S", "5")
    thread, box = _in_thread(_answer)
    _await_open()

    gateanswer.gate_resolved("toolu_somethingelse")
    gateanswer.gate_resolved(None)                  # a payload with no id at all
    gateanswer.gate_resolved("../../etc/passwd")    # and a crafted one
    assert gateanswer.open_gate(TUID) is not None, "our gate is still being held"

    gateanswer.submit_answer(TUID, {"Which store?": "SQLite",
                                    "Which extras?": ["Metrics"]})
    thread.join(timeout=5)
    assert box["result"] is not None, "the tap still answers the gate"


# --- refusing --------------------------------------------------------------------

def test_an_answer_for_a_gate_that_is_not_waiting_is_refused_and_reported():
    ok, reason = gateanswer.submit_answer("toolu_gone", {"Which store?": "SQLite"})
    assert ok is False
    assert "no longer waiting" in reason


def test_an_expired_gate_refuses_a_late_tap(monkeypatch):
    """The hook gave up seconds ago and the agent has moved on. The tap must NOT land."""
    gateanswer._write_atomic(gateanswer._gate_path(TUID), {
        "tool_use_id": TUID, "wid": "@3", "questions": QUESTIONS,
        "deadline": time.time() - 1,             # the wait is over
    })
    assert gateanswer.open_gate(TUID) is None
    ok, reason = gateanswer.submit_answer(TUID, {"Which store?": "SQLite",
                                                 "Which extras?": ["Metrics"]})
    assert ok is False and "no longer waiting" in reason


def test_a_label_the_asker_never_offered_is_refused(monkeypatch, caplog):
    monkeypatch.setenv("CHELA_GATE_WAIT_S", "5")
    thread, box = _in_thread(_answer)
    _await_open()

    ok, reason = gateanswer.submit_answer(TUID, {"Which store?": "MongoDB",
                                                 "Which extras?": ["Metrics"]})
    assert ok is False
    assert "does not match" in reason
    assert any("refused an answer" in r.message for r in caplog.records)

    gateanswer.submit_answer(TUID, {"Which store?": "SQLite",
                                    "Which extras?": ["Metrics"]})
    thread.join(timeout=5)
    answers = box["result"]["hookSpecificOutput"]["decision"]["updatedInput"]["answers"]
    assert answers["Which store?"] == "SQLite"   # the bad one never got through


def test_a_partial_answers_map_is_refused():
    """Measured on 2.1.209: Claude Code ACCEPTS a partial map and drops the unanswered
    question on the floor — the agent believes it asked and never re-asks. So a
    half-answered gate would silently discard a question, which is the same harm as
    answering the wrong option. chela holds out for a complete map."""
    assert gateanswer.validate_answers(QUESTIONS, {"Which store?": "SQLite"}) is None


def test_a_single_select_question_takes_exactly_one_label():
    assert gateanswer.validate_answers(QUESTIONS, {
        "Which store?": ["SQLite", "Postgres"],  # two answers to a one-answer question
        "Which extras?": ["Metrics"],
    }) is None
    # …but a one-element list is what a toggle UI naturally produces, so it unwraps.
    assert gateanswer.validate_answers(QUESTIONS, {
        "Which store?": ["SQLite"], "Which extras?": ["Metrics"],
    })["Which store?"] == "SQLite"


def test_a_multiselect_question_keeps_its_list_and_drops_duplicates():
    answers = gateanswer.validate_answers(QUESTIONS, {
        "Which store?": "SQLite",
        "Which extras?": ["Tracing", "Metrics", "Tracing"],
    })
    assert answers["Which extras?"] == ["Tracing", "Metrics"]


# --- who is NOT held -------------------------------------------------------------

def test_a_bash_gate_is_not_held_at_all():
    """Only an AskUserQuestion has an ``answers`` map. A Bash gate keeps the pane path."""
    body = dict(BODY, tool_name="Bash", tool_input={"command": "rm -rf /tmp/x"})
    assert _answer(body) is None


def test_a_session_we_cannot_place_in_a_window_is_never_held():
    assert _answer(wid=None) is None


def test_a_window_with_no_bound_topic_is_never_held():
    """Nobody would see the question — so holding a live agent on it is indefensible."""
    assert _answer(bound=()) is None


def test_a_gate_with_no_pre_tool_use_in_the_log_is_not_held():
    """``PermissionRequest`` carries no ``tool_use_id``: without the PreToolUse that does,
    an answer could not be tied back to this gate at all."""
    assert _answer(pending=lambda _w: None) is None


def test_a_pending_gate_that_asks_SOMETHING_ELSE_is_not_answered(caplog):
    """Two calls in flight and we cannot prove which one is blocked → do not guess."""
    other = [{"question": "Deploy now?", "options": [{"label": "Yes"}, {"label": "No"}]}]
    assert _answer(pending=lambda _w: _gate(other)) is None
    assert any("asks something else" in r.message for r in caplog.records)


# --- the bound -------------------------------------------------------------------

def test_simultaneous_gates_cannot_exhaust_the_server(monkeypatch):
    """A blocked hook holds a request thread. Past the bound, the next gate does not wait
    — it fails open to the pane immediately, which costs the agent nothing."""
    monkeypatch.setenv("CHELA_GATE_MAX_WAITS", "2")
    monkeypatch.setenv("CHELA_GATE_WAIT_S", "2")

    threads = []
    boxes = []
    for i in range(5):
        tuid = f"toolu_{i}"
        thread, box = _in_thread(
            lambda t=tuid: _answer(pending=lambda _w, t=t: _gate(tuid=t)))
        threads.append(thread)
        boxes.append(box)

    time.sleep(0.5)
    holding = list(gateanswer.gates_dir().glob("*.gate.json"))
    assert len(holding) == 2, "the bound must cap what is held, not queue behind it"

    for thread in threads:
        thread.join(timeout=5)
    assert all(box["result"] is None for box in boxes)   # every one of them failed OPEN


# --- through the real endpoint ---------------------------------------------------

def test_the_endpoint_hands_a_tapped_answer_back_to_the_blocked_agent(monkeypatch):
    """End to end over HTTP: the hook POSTs, the human taps, the agent gets the labels.

    The response body IS the answer — this is the contract that used to be "the endpoint
    never decides", changed here deliberately (see ``tests/test_hooks.py``).
    """
    from chela.dashboard import app as dash

    monkeypatch.setenv("CHELA_GATE_WAIT_S", "5")
    monkeypatch.setattr(gateanswer, "_bound_windows", lambda: {"@3"})
    monkeypatch.setattr(hooks, "wid_for_session", lambda *_a, **_kw: "@3")
    monkeypatch.setattr("chela.telegram.hookgate.pending_gate", lambda _w: _gate())

    client = dash.app.test_client()
    thread, box = _in_thread(
        lambda: client.post("/hooks/PermissionRequest", json=BODY).get_json())

    _await_open()
    assert gateanswer.submit_answer(TUID, {"Which store?": "SQLite",
                                           "Which extras?": ["Metrics"]})[0]
    thread.join(timeout=5)

    updated = box["result"]["hookSpecificOutput"]["decision"]["updatedInput"]
    assert updated["answers"] == {"Which store?": "SQLite",
                                  "Which extras?": ["Metrics"]}


def test_a_bug_in_our_own_answering_code_still_fails_open(monkeypatch):
    """A 500 here is a stalled agent. Whatever breaks, the agent gets its {} and runs."""
    from chela.dashboard import app as dash

    def _boom(_body):
        raise RuntimeError("the answer path is broken")

    monkeypatch.setattr(dash.gateanswer, "answer_permission_request", _boom)
    resp = dash.app.test_client().post("/hooks/PermissionRequest", json=BODY)

    assert resp.status_code == 200 and resp.get_json() == {}
