"""CMX-384 — a classifier that gave NO VERDICT is an outage, not a denial.

A real denial keeps CMX-155's 🔒 notice. A no-verdict records a silent
``classifier_unavailable`` event (no "blocked" wording, no bang-command advice), and an
agent whose turn stopped on it is re-prompted to resume once the classifier answers again.
tmux, statuses, the peer socket and the runs DB are stubbed; the event log is per-test."""
from __future__ import annotations

import pytest

from chela import dispatcher, event_log, hooks, inbox, messenger, notify

ORCH = "@1"
AGENT = "@41"
OTHER = "@50"

# the two shapes the harness hands the PermissionDenied hook, as `reason`
REAL_DENIAL = ("Permission for this action was denied by the Claude Code auto mode "
               "classifier. Reason: Deletes files outside the task")
NO_VERDICT = ("The server-side auto mode classifier gave no verdict (error). This is a "
              "transient failure of the check, not a judgment about the action. You may try "
              "the action again once.")


@pytest.fixture
def statuses(monkeypatch):
    table = {ORCH: inbox.BUSY, AGENT: inbox.BUSY}
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: dict(table))
    return table


@pytest.fixture
def sends(monkeypatch):
    calls: list[tuple] = []

    def peer(wid, frm, text):
        calls.append(("peer", wid, frm, text))
        return messenger.PeerSendResult(True, "sent")

    monkeypatch.setattr(inbox.messenger, "send_peer", peer)
    monkeypatch.setattr(inbox.messenger, "send_tmux",
                        lambda wid, text: calls.append(("tmux", wid, text)) or True)
    return calls


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch, statuses, sends):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(inbox.epoch, "current", lambda: "e1")
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator", AGENT: "cmx-9", OTHER: "cmx-10"})
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "wid_for_session", lambda sid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "transcript_for_window", lambda wid: None)
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    monkeypatch.setattr(inbox, "stuck_probes", lambda runs: {})
    store = inbox.load()
    store["orchestrator"] = ORCH                    # busy → events queue, readable here
    inbox.save(store)


def _run(status="running", **over):
    return {"task_id": "CMX-9", "title": "fix the parser", "status": status,
            "branch_name": "cmx-9", "window_name": "cmx-9", "window_id": AGENT,
            "window_epoch": "e1", **over}


def _deny(wid, reason, command="ls build", tool_use_id=None):
    body = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": command},
            "reason": reason}
    if tool_use_id:
        body["tool_use_id"] = tool_use_id
    return event_log.append(notify.DENIED_TYPE, hooks.summarize("PermissionDenied", body),
                            hooks.clip_payload(body), wid=wid, epoch="e1")


def _ran(wid, tool="Bash"):
    body = {"session_id": "s", "tool_name": tool, "tool_input": {"command": "true"}}
    return event_log.append(inbox.POST_TOOL_TYPE, hooks.summarize("PostToolUse", body),
                            hooks.clip_payload(body), wid=wid, epoch="e1")


def _logged(kind):
    return event_log.read(types=[kind])["events"]


def _resume_sends(sends):
    return [c for c in sends if c[-1] == inbox.RESUME_PROMPT]


# --- the two message shapes, end to end through tick() ------------------------------------

def test_a_real_denial_keeps_the_blocked_notice():
    """🔴 GUARD (negative control): a real denial still queues the 🔒 notice. Classify it as
    transient and this goes red."""
    runs = [_run()]
    inbox.tick({}, runs=runs)                       # arms the cursor
    _deny(AGENT, REAL_DENIAL, command="rm -rf build")
    inbox.tick({}, runs=runs)
    [event] = _logged(inbox.DENIED_KIND)            # queued, then delivered to the orchestrator
    assert event["summary"] == (
        "🔒 CMX-9 · @41 was blocked by its permission classifier: Bash rm -rf build — the "
        "agent is stopped on it: hand the human a bang-prefixed command to run, or redirect "
        "the agent.")
    assert _logged("classifier_unavailable") == []


def test_a_no_verdict_records_an_outage_and_no_blocked_notice():
    """🔴 GUARD: a no-verdict queues nothing for the orchestrator and logs ONE
    ``classifier_unavailable`` event worded as an outage, with no bang-command advice."""
    runs = [_run()]
    inbox.tick({}, runs=runs)
    _deny(AGENT, NO_VERDICT, command="git status")
    inbox.tick({}, runs=runs)
    assert inbox.load()["queue"] == [] and _logged(inbox.DENIED_KIND) == []
    [event] = _logged("classifier_unavailable")
    assert event["summary"] == (
        "⏳ classifier unavailable — agents retrying: CMX-9 · @41 got no verdict on Bash git "
        "status. An outage, not a denial: nothing was refused.")
    assert "bang" not in event["summary"] and "blocked" not in event["summary"]
    assert event["wid"] == AGENT
    assert event["payload"]["task_id"] == "CMX-9"
    assert event["payload"]["reason"].startswith("The server-side auto mode classifier gave")
    assert set(inbox.load()["classifier_out"]) == {"CMX-9"}


def test_a_no_verdict_leaves_the_denial_cooldown_alone():
    """A real denial right after a no-verdict still gets its notice."""
    out, store = _events([_deny(AGENT, NO_VERDICT), _deny(AGENT, REAL_DENIAL)])
    assert [e["kind"] for e in out] == ["classifier_unavailable", inbox.DENIED_KIND]
    assert store["denied_seen"] == {"CMX-9": 1000.0}


def test_only_the_first_no_verdict_of_an_outage_is_recorded():
    out, store = _events([_deny(AGENT, NO_VERDICT), _deny(AGENT, NO_VERDICT)])
    assert [e["kind"] for e in out] == ["classifier_unavailable"]
    entry = store["classifier_out"]["CMX-9"]
    assert entry["wid"] == AGENT and entry["recovered"] is None
    assert entry["since"] <= entry["last"]


def test_a_no_verdict_from_the_orchestrator_or_an_idle_window_is_not_tracked():
    out, store = _events([_deny(ORCH, NO_VERDICT), _deny(OTHER, NO_VERDICT)])
    assert out == [] and store["classifier_out"] == {}


def test_a_copy_of_one_no_verdict_is_deduped_by_its_tool_use_id():
    out, store = _events([_deny(AGENT, NO_VERDICT, tool_use_id="tu-1")])
    store["classifier_out"]["CMX-9"]["recovered"] = True
    out, store = _events([_deny(AGENT, NO_VERDICT, tool_use_id="tu-1")], store=store)
    assert out == [] and store["classifier_out"]["CMX-9"]["recovered"] is True


def _events(records, runs=None, store=None, now=1000.0):
    store = {**inbox._empty(), "orchestrator": ORCH} if store is None else store
    return inbox.denied_events(records, [_run()] if runs is None else runs, store, now,
                               now_epoch="e1"), store


# --- telling the two apart: stable phrases, not full strings -----------------------------

@pytest.mark.parametrize("reason, outcome", [
    (REAL_DENIAL, "denied"),
    (NO_VERDICT, "no_verdict"),
    ("the auto mode classifier GAVE NO VERDICT (error)", "no_verdict"),
    ("a transient failure of the check", "no_verdict"),
    ("classifier gave\n  no verdict", "no_verdict"),            # whitespace collapsed
    ("it gave no verdicts", "denied"),                          # \b after the phrase
    ("forgave no verdict", "denied"),                           # \b before the phrase
    ("a transient failure of the checker", "denied"),
    ("Permission was denied by the classifier. It gave no verdict", "denied"),  # denial wins
    ("Deletes files outside the task", "denied"),
    ("", "denied"),
    (None, "denied"),
])
def test_the_outcome_is_matched_on_stable_phrases(reason, outcome):
    """🔴 GUARD: positive and negative fixtures for both phrases; an unknown is a denial."""
    assert notify.denial_outcome({"payload": {"reason": reason}}) == outcome


def test_a_record_with_no_payload_is_a_denial():
    assert notify.denial_outcome({}) == "denied"


# --- the phone push (CMX-25) -------------------------------------------------------------

def test_the_phone_push_skips_a_no_verdict_but_not_a_denial(monkeypatch):
    """🔴 GUARD: the push says "blocked by its permission classifier" too, so a no-verdict
    must not reach it."""
    monkeypatch.setattr(notify, "NOTIFY_URL", "http://ntfy.invalid/topic")
    pushed: list[str] = []
    monkeypatch.setattr(notify, "send", lambda text, title=None: pushed.append(text))
    monkeypatch.setattr(notify.discovery, "get_all_windows", lambda: {"cmx-9": AGENT})

    class Owner:
        def acquire(self):
            return True

    watch = notify.DeniedWatch()
    watch.check(owner=Owner())
    _deny(AGENT, NO_VERDICT, command="git status")
    assert watch.check(owner=Owner()) == []
    _deny(AGENT, REAL_DENIAL, command="rm -rf build")
    assert watch.check(owner=Owner()) == ["@41 cmx-9: blocked by its permission classifier: "
                                          "rm -rf build"]
    assert len(pushed) == 1


# --- the agent's turn stopped: re-prompt once the classifier answers ---------------------

def _outage_then(statuses, *after, status=inbox.IDLE):
    runs = [_run()]
    inbox.tick({}, runs=runs)
    _deny(AGENT, NO_VERDICT)
    inbox.tick({}, runs=runs)
    statuses[AGENT] = status
    for record in after:
        record()
    inbox.tick({}, runs=runs)
    return runs


def test_an_idle_agent_is_re_prompted_once_the_classifier_answers(statuses, sends):
    """🔴 GUARD: another window's Bash call ran → the classifier is back → the idle agent
    is told to resume, over the peer socket, exactly once."""
    runs = _outage_then(statuses, lambda: _ran(OTHER))
    assert sends == [("peer", AGENT, "chela-inbox", inbox.RESUME_PROMPT)]
    [event] = _logged("classifier_resumed")
    assert event["wid"] == AGENT
    assert event["payload"]["task_id"] == "CMX-9" and event["payload"]["delivered"] is True
    assert event["summary"] == ("▶ classifier is back: CMX-9 · @41 stopped on a no-verdict "
                                "and was re-prompted to resume")
    assert inbox.load()["classifier_out"] == {}
    inbox.tick({}, runs=runs)
    assert len(sends) == 1


def test_the_resume_prompt_says_resume_and_retry(statuses, sends):
    assert inbox.RESUME_PROMPT == (
        "chela: the permission classifier is back. The no-verdict you got was an outage, not "
        "a denial, so resume your task where it stopped and retry that call unchanged.")


def test_a_real_denial_elsewhere_is_a_verdict_too(statuses, sends):
    _outage_then(statuses, lambda: _deny(OTHER, REAL_DENIAL))
    assert _resume_sends(sends) == [("peer", AGENT, "chela-inbox", inbox.RESUME_PROMPT)]


def test_no_prompt_while_the_classifier_is_still_out(statuses, sends):
    """🔴 GUARD: idle, but nothing proves the classifier answers → no prompt yet."""
    _outage_then(statuses)
    assert sends == []
    assert inbox.load()["classifier_out"]["CMX-9"]["recovered"] is None


def test_a_tool_that_skips_the_classifier_proves_nothing(statuses, sends):
    """🔴 GUARD: a Read that ran elsewhere is no verdict."""
    _outage_then(statuses, lambda: _ran(OTHER, tool="Read"))
    assert sends == []
    assert inbox.load()["classifier_out"]["CMX-9"]["recovered"] is None


def test_a_record_with_no_tool_name_proves_nothing():
    _, store = _events([_deny(AGENT, NO_VERDICT)])
    inbox._note_tool_ran(store["classifier_out"], {"wid": OTHER, "payload": {}})
    assert store["classifier_out"]["CMX-9"]["recovered"] is None


def test_a_new_no_verdict_after_recovery_reopens_the_outage(statuses, sends):
    _outage_then(statuses, lambda: _ran(OTHER), lambda: _deny(AGENT, NO_VERDICT),
                 status=inbox.BUSY)
    assert sends == []
    assert inbox.load()["classifier_out"]["CMX-9"]["recovered"] is None


def test_an_agent_that_moved_on_by_itself_is_not_prompted(statuses, sends):
    """🔴 GUARD: its own classifier-gated call ran → it retried on its own → drop it."""
    _outage_then(statuses, lambda: _ran(AGENT))
    assert sends == [] and inbox.load()["classifier_out"] == {}


def test_the_agents_own_read_does_not_end_its_outage(statuses, sends):
    """🔴 GUARD: a parallel Read finishing after the no-verdict proves nothing; the stopped
    agent is still prompted once the classifier answers elsewhere."""
    runs = _outage_then(statuses, lambda: _ran(AGENT, tool="Read"))
    assert inbox.load()["classifier_out"]["CMX-9"]["recovered"] is None
    _ran(OTHER)
    inbox.tick({}, runs=runs)
    assert sends == [("peer", AGENT, "chela-inbox", inbox.RESUME_PROMPT)]


def test_tool_calls_are_read_only_while_an_outage_is_open():
    """🔴 GUARD: PostToolUse is most of the log; it is read only while it can matter."""
    store = {"denied_cursor": {"seq": event_log.tip()["seq"], "boot": event_log.tip()["boot_id"],
                               "at": 0.0}, "classifier_out": {}}
    _ran(OTHER)
    assert inbox.read_denials(store, 1.0)[0] == []
    store["classifier_out"] = {"CMX-9": {"wid": AGENT, "last": 0.0}}
    [record] = inbox.read_denials(store, 1.0)[0]
    assert record["type"] == "hook.post_tool_use"


def test_a_direct_no_verdict_post_is_logged_not_queued(monkeypatch):
    """🔴 GUARD: the Mod's direct post (file_denial) never queues the silent outage record."""
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [_run()])
    events = inbox.file_denial({"wid": AGENT, "session_id": "s", "tool_name": "Bash",
                                "tool_input": {"command": "git status"},
                                "tool_use_id": "tu-9", "reason": NO_VERDICT}, now=1000.0)
    assert [e["kind"] for e in events] == ["classifier_unavailable"]
    assert inbox.load()["queue"] == []
    assert [e["type"] for e in _logged("classifier_unavailable")] == ["classifier_unavailable"]


def test_a_failed_resume_is_tried_again_next_tick(statuses, sends, monkeypatch):
    """🔴 GUARD: a send that failed puts the outage back; the next idle tick re-sends."""
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(False, None))
    runs = _outage_then(statuses, lambda: _ran(OTHER))
    assert inbox.load()["classifier_out"]["CMX-9"]["recovered"] is True
    assert sends == []                                  # never a pane paste (CMX-482)

    def peer(wid, frm, text):
        sends.append(("peer", wid, frm, text))
        return messenger.PeerSendResult(True, "sent")
    monkeypatch.setattr(inbox.messenger, "send_peer", peer)
    inbox.tick({}, runs=runs)
    assert sends == [("peer", AGENT, "chela-inbox", inbox.RESUME_PROMPT)]
    assert inbox.load()["classifier_out"] == {}


def test_a_real_denial_on_the_agents_own_window_ends_its_outage(statuses, sends):
    _outage_then(statuses, lambda: _deny(AGENT, REAL_DENIAL))
    assert _resume_sends(sends) == [] and inbox.load()["classifier_out"] == {}


@pytest.mark.parametrize("status", [inbox.BUSY, inbox.WAITING])
def test_a_working_agent_is_kept_and_prompted_only_once_it_goes_idle(statuses, sends, status):
    """🔴 GUARD: busy/waiting at recovery → kept, not prompted; idle later → prompted."""
    runs = _outage_then(statuses, lambda: _ran(OTHER), status=status)
    assert sends == []
    assert inbox.load()["classifier_out"]["CMX-9"]["recovered"] is True
    statuses[AGENT] = inbox.IDLE
    inbox.tick({}, runs=runs)
    assert sends == [("peer", AGENT, "chela-inbox", inbox.RESUME_PROMPT)]


def test_a_gone_window_is_dropped_without_a_prompt(statuses, sends):
    del statuses[AGENT]
    _outage_then(statuses, lambda: _ran(OTHER), status=None)
    assert sends == [] and inbox.load()["classifier_out"] == {}


# --- classifier_resumes: what drops an entry ---------------------------------------------

def _store(**entry):
    return {"classifier_out": {"CMX-9": {"wid": AGENT, "since": 900.0, "last": 900.0,
                                         "recovered": True, **entry}}}


def test_a_settled_run_drops_its_outage():
    store = _store()
    assert inbox.classifier_resumes(store, {AGENT: inbox.IDLE}, [_run("awaiting_review")],
                                    1000.0, now_epoch="e1") == []
    assert store["classifier_out"] == {}


def test_a_run_now_in_another_window_drops_its_outage():
    store = _store()
    assert inbox.classifier_resumes(store, {AGENT: inbox.IDLE}, [_run(window_id=OTHER)],
                                    1000.0, now_epoch="e1") == []
    assert store["classifier_out"] == {}


def test_an_outage_older_than_an_hour_is_left_to_the_stuck_alarm():
    """🔴 GUARD: 3600 s, literal; at exactly 3600 it is still tracked."""
    store = _store(recovered=None)
    inbox.classifier_resumes(store, {AGENT: inbox.IDLE}, [_run()], 900.0 + 3600,
                             now_epoch="e1")
    assert "CMX-9" in store["classifier_out"]
    inbox.classifier_resumes(store, {AGENT: inbox.IDLE}, [_run()], 900.0 + 3601,
                             now_epoch="e1")
    assert store["classifier_out"] == {}


def test_a_claimed_run_is_still_tracked():
    store = _store()
    [event] = inbox.classifier_resumes(store, {AGENT: inbox.IDLE}, [_run("claimed")], 1000.0,
                                       now_epoch="e1")
    assert event["kind"] == "classifier_resumed" and event.get("silent") is True
    assert event["payload"]["since"] == 900.0


# --- send_resumes: the transport ---------------------------------------------------------

def _resumed():
    return inbox._event(inbox.RESUMED_KIND, "▶ x · @41 was re-prompted to resume",
                        {"task_id": "CMX-9"}, wid=AGENT, silent=True)


def test_no_socket_never_falls_back_to_the_pane(monkeypatch, sends):
    """🔴 GUARD (CMX-482 negative control): socket unreachable → NOT delivered, and never
    typed into the pane (the old path pasted the same text with send_tmux)."""
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(False, None))
    event = _resumed()
    inbox.send_resumes([event])
    assert sends == []
    assert event["payload"]["delivered"] is False
    assert event["payload"]["outcome"] == "unreachable"
    assert event["summary"] == "▶ x · @41 could not be re-prompted (unreachable)"
    [held] = _logged("peer_unreachable")
    assert held["payload"] == {"target": AGENT, "via": "usage-outage resume"}


@pytest.mark.parametrize("status", ["held", "denied", "expired"])
def test_an_adverse_receipt_never_routes_around_through_the_pane(monkeypatch, sends, status):
    """🔴 GUARD: a gate the agent chose is not bypassed by a paste."""
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(True, status))
    event = _resumed()
    inbox.send_resumes([event])
    assert sends == []
    assert event["payload"]["delivered"] is False
    assert event["summary"] == f"▶ x · @41 could not be re-prompted ({status})"
    [logged] = _logged("classifier_resumed")
    assert logged["payload"]["delivered"] is False
    assert _logged("peer_unreachable") == []
