"""CMX-86 — the orchestrator inbox: drop the redundant notices, add the missing ones.

Every notice lands in the orchestrator's context and is re-billed on every later request,
so a notice must carry information or ask for a decision. Locked in here:

  * an ECHO of the orchestrator's own reopen/retry/close is silent; the same by a human is
    a notice;
  * ``awaiting_review`` is silent by default; a run still waiting with no judge round after
    the threshold gets exactly ONE notice;
  * a dispatch hold that lapses (or that somebody else releases) is ONE notice; the
    orchestrator's own ``--resume`` is none;
  * CI red on the head is ONE notice naming the job and the first failing line;
  * a stuck agent / an unread completion marker is ONE notice; a CONFLICTING PR is one per
    head;
  * no notice repeats on a later tick.

Pure: tmux, ``claude agents --json``, the peer socket and gh are all stubbed.
"""
from __future__ import annotations

import subprocess
import time
from argparse import Namespace

import pytest

from chela import dispatcher, hold, inbox, judge, main, orchestrator

ORCH = "@1"
HUMAN = "@7"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.delenv("CHELA_ACTOR", raising=False)
    monkeypatch.delenv("CHELA_WID", raising=False)
    monkeypatch.delenv("CHELA_INBOX_NOTIFY_AWAITING_REVIEW", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: {ORCH: inbox.BUSY})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {ORCH: "orchestrator"})
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "wid_for_session", lambda sid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "transcript_for_window", lambda wid: None)
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    from chela import messenger
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(False, None))
    monkeypatch.setattr(inbox.messenger, "send_tmux", lambda wid, text: False)
    store = inbox.load()
    store["orchestrator"] = ORCH                    # busy → events queue, readable here
    inbox.save(store)


def _queued(kind: str | None = None) -> list[dict]:
    return [e for e in inbox.load()["queue"] if kind is None or e["kind"] == kind]


def _run(status, **over):
    return {"task_id": "T9", "title": "fix the parser", "status": status,
            "branch_name": "cmx-9", "pr_url": "https://github.com/o/r/pull/9",
            "pr_state": "open", **over}


def _as(monkeypatch, wid: str) -> None:
    """Run the CLI as the window ``wid``."""
    monkeypatch.setattr(orchestrator, "self_wid", lambda: wid)


# --- 1. the orchestrator's own actions are not news ----------------------------------

_ACTIONS = {
    # cmd, args, dispatcher fn, status before → after, the notice a human's action gets
    "reopen": (main.cmd_reopen, Namespace(run="T9", reason=""), "reopen",
               "needs_human", "awaiting_review", "run_reopened"),
    "retry": (main.cmd_retry, Namespace(run="T9", reason=""), "retry",
              "needs_human", "changes_requested", "run_changes_requested"),
    "close": (main.cmd_close, Namespace(run="T9", reason="superseded", requeue=False,
                                        keep_pr=True, close_pr=False, force=False,
                                        remove_worktree=False), "close_run",
              "needs_human", "closed", "run_was_closed"),
}


def _act(monkeypatch, name: str) -> tuple[str, str]:
    cmd, args, fn, before, after, kind = _ACTIONS[name]
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: _run(before))
    monkeypatch.setattr(dispatcher, fn, lambda *a, **k: {
        "ok": True, "task_id": "T9", "branch_name": "cmx-9", "reason": "superseded",
        "from_status": before, "comment_posted": True})
    inbox.tick({}, runs=[_run(before)])              # the run is known at `before`
    cmd(args)
    inbox.tick({}, runs=[_run(after, close_reason="superseded")])
    return after, kind


@pytest.mark.parametrize("name", sorted(_ACTIONS))
def test_the_orchestrators_own_action_does_not_notify_it(name, monkeypatch):
    _as(monkeypatch, ORCH)
    _, kind = _act(monkeypatch, name)
    assert _queued(kind) == [], f"the orchestrator's own {name} came back to it as news"
    assert inbox.load()["expected_edges"] == {}      # the announced edge was consumed


@pytest.mark.parametrize("name", sorted(_ACTIONS))
def test_the_same_action_by_a_human_is_a_notice(name, monkeypatch):
    _as(monkeypatch, HUMAN)
    _, kind = _act(monkeypatch, name)
    assert len(_queued(kind)) == 1, f"a human's {name} must reach the orchestrator"


def test_the_auto_orchestrator_actor_is_the_orchestrator_wherever_it_runs(monkeypatch):
    _as(monkeypatch, None)
    monkeypatch.setattr(orchestrator, "self_peer", lambda: None)
    assert inbox.caller_is_orchestrator() is False
    monkeypatch.setenv("CHELA_ACTOR", "auto-orchestrator")
    assert inbox.caller_is_orchestrator() is True


def test_a_failed_action_retracts_its_announcement(monkeypatch):
    _as(monkeypatch, ORCH)
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: _run("needs_human"))
    monkeypatch.setattr(dispatcher, "reopen", lambda *a, **k: {"ok": False, "error": "nope"})
    with pytest.raises(SystemExit):
        main.cmd_reopen(Namespace(run="T9", reason=""))
    assert inbox.load()["expected_edges"] == {}


def test_an_announced_edge_that_never_happens_expires(monkeypatch):
    expected = {"T9": {"status": "closed", "why": inbox.ORCH_ECHO,
                       "ts": time.time() - inbox.EXPECT_EDGE_TTL_SECONDS - 1}}
    events, _ = inbox.run_events([_run("closed")], {"T9": "needs_human"}, expected=expected)
    assert [e["kind"] for e in events if not e.get("silent")] == ["run_was_closed"]


# --- 2. awaiting_review is silent; a judge that never starts is one notice -------------

def test_awaiting_review_is_silent_by_default_but_still_logged(monkeypatch):
    inbox.tick({}, runs=[_run("running")])
    inbox.tick({}, runs=[_run("awaiting_review")])
    assert _queued() == []
    events, _ = inbox.run_events([_run("awaiting_review")], {"T9": "running"})
    assert [(e["kind"], e.get("silent")) for e in events] == [("run_review", True)]


def test_awaiting_review_notice_can_be_turned_back_on(monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_NOTIFY_AWAITING_REVIEW", "true")
    inbox.tick({}, runs=[_run("running")])
    inbox.tick({}, runs=[_run("awaiting_review")])
    assert len(_queued("run_review")) == 1


def test_judge_not_started_fires_exactly_once_after_the_threshold(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "1200")
    store = inbox._empty()
    run = _run("awaiting_review", pr_head_sha="abc", judge_state="")
    t0 = 1_000_000.0
    assert inbox.judge_wait_events([run], store, t0) == []
    assert inbox.judge_wait_events([run], store, t0 + 1199) == []
    fired = inbox.judge_wait_events([run], store, t0 + 1201)
    assert [e["kind"] for e in fired] == ["run_judge_not_started"]
    assert "chela judge run T9" in fired[0]["summary"]
    assert inbox.judge_wait_events([run], store, t0 + 5000) == []     # no repeat


@pytest.mark.parametrize("over", [
    {"judge_state": judge.J_RUNNING},                                   # a round is running
    {"judge_state": judge.J_CLEAN, "judge_sha": "abc"},                 # settled on this head
    {"judge_retry_after": "2026-10-12T10:00:00+00:00"},                 # outage backoff
])
def test_judge_not_started_is_silent_once_a_judge_round_exists(over, monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    store = inbox._empty()
    run = _run("awaiting_review", pr_head_sha="abc", **over)
    assert inbox.judge_wait_events([run], store, 1.0) == []
    assert inbox.judge_wait_events([run], store, 99_999.0) == []


def test_judge_not_started_through_the_tick_is_one_notice(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    run = _run("awaiting_review", pr_head_sha="abc")
    for _ in range(3):
        inbox.tick({}, runs=[run])
    assert len(_queued("run_judge_not_started")) == 1
    assert _queued("run_review") == []


# --- 3. a dispatch hold that ends without the orchestrator ------------------------------

def test_a_hold_that_lapses_by_its_ttl_is_one_notice():
    held = hold.take(reason="rewriting the queue", ttl_seconds=60, by=ORCH)
    store = inbox._empty()
    assert inbox.hold_events(store, held, None, held.created_at + 1) == []      # live: seen
    ended = inbox.hold_events(store, held, None, held.expires_at + 1)           # lapsed
    assert [e["kind"] for e in ended] == ["dispatch_hold_expired"]
    assert inbox.hold_events(store, held, None, held.expires_at + 2) == []      # said once
    assert inbox.hold_events(store, None, None, held.expires_at + 3) == []      # file gone


def test_a_hold_released_by_somebody_else_is_one_notice_by_the_orchestrator_none():
    held = hold.take(ttl_seconds=600, by=ORCH)
    store = inbox._empty()
    inbox.hold_events(store, held, None, held.created_at + 1)
    hold.release(by="dashboard")
    ended = inbox.hold_events(store, None, hold.last_release(), held.created_at + 2)
    assert [e["kind"] for e in ended] == ["dispatch_hold_released"]
    assert "dashboard" in ended[0]["summary"]

    again = hold.take(ttl_seconds=600, by=ORCH)
    inbox.hold_events(store, again, None, again.created_at + 1)
    hold.release(by=ORCH, orchestrator=True)
    assert inbox.hold_events(store, None, hold.last_release(), again.created_at + 2) == []


def test_the_orchestrators_own_resume_is_recorded_as_its_own(monkeypatch):
    _as(monkeypatch, ORCH)
    hold.take(ttl_seconds=600, by=ORCH)
    main.cmd_dispatch_hold(Namespace(resume=True, pause=False, hold_status=False))
    assert hold.last_release()["orchestrator"] is True


def test_hold_expiry_through_the_tick_is_one_notice(monkeypatch):
    held = hold.take(ttl_seconds=1, by=ORCH)
    inbox.tick({}, runs=[])                          # seen live
    monkeypatch.setattr(inbox.time, "time", lambda: held.expires_at + 5)
    inbox.tick({}, runs=[])
    inbox.tick({}, runs=[])
    assert len(_queued("dispatch_hold_expired")) == 1


# --- 4. CI red on the head --------------------------------------------------------------

_GH_LOG = (
    "test (3.12)\tSet up job\t2026-10-12T10:00:00.0000000Z Current runner version: '2.3'\n"
    "test (3.12)\tRun tests\t2026-10-12T10:01:00.0000000Z E   assert 1 == 2\n"
    "test (3.12)\tRun tests\t2026-10-12T10:01:01.0000000Z FAILED tests/test_wall.py::test_grid"
    " - assert 1 == 2\n"
    "test (3.12)\tRun tests\t2026-10-12T10:01:02.0000000Z ##[error]Process completed with "
    "exit code 1.\n"
)


def test_first_failure_line_names_the_test():
    assert inbox.first_failure_line(_GH_LOG) == \
        "FAILED tests/test_wall.py::test_grid - assert 1 == 2"
    assert inbox.first_failure_line("lint\truff\t2026-10-12T10:00:00Z chela/x.py:3:1: F401 "
                                    "`os` imported but unused\n").startswith("chela/x.py:3:1")
    assert inbox.first_failure_line("") == ""


def test_ci_red_is_one_notice_with_the_job_and_the_failing_test():
    run = _run("awaiting_review", pr_head_sha="deadbee1")
    assert inbox.ci_red(run, sha="deadbee1", failing=("CI / test (3.12)",), run_ids=("42",),
                        log_tail=_GH_LOG, sent_back=True) is True
    assert inbox.ci_red(run, sha="deadbee1", failing=("CI / test (3.12)",), run_ids=("42",),
                        log_tail=_GH_LOG, sent_back=True) is False        # once per head
    [event] = _queued("run_ci_red")
    assert "job CI / test 3.12" in event["summary"]      # parens are sanitized out
    assert "FAILED tests/test_wall.py::test_grid" in event["summary"]
    assert "gh run rerun 42 --failed" in event["summary"]


def test_the_rework_a_ci_red_triggers_is_not_a_second_notice():
    inbox.tick({}, runs=[_run("awaiting_review")])
    inbox.ci_red(_run("awaiting_review"), sha="deadbee1", failing=("test",), run_ids=(),
                 log_tail=_GH_LOG, sent_back=True)
    inbox.tick({}, runs=[_run("changes_requested")])
    assert [e["kind"] for e in _queued()] == ["run_ci_red"]


def test_the_dispatchers_ci_loop_raises_the_notice(tmp_path, monkeypatch):
    from tests.test_dispatcher_ci import _check_run, _FakeGh, _row, _tick, _wf
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test (3.12)", conclusion="FAILURE")],
                   log="FAILED tests/test_wall.py::test_grid\n")
    _tick(wf, fake)
    [event] = _queued("run_ci_red")
    assert "FAILED tests/test_wall.py::test_grid" in event["summary"]
    assert event["payload"]["sent_back"] is True


def test_a_refused_rework_retracts_the_ci_echo(monkeypatch):
    inbox.ci_red(_run("awaiting_review"), sha="s1", failing=("t",), run_ids=(), log_tail="",
                 sent_back=True)
    dispatcher._inbox_forget_ci_edge("T9")
    assert inbox.load()["expected_edges"] == {}


# --- 5. a stuck agent / an unread completion marker ---------------------------------------

def test_a_quiet_running_agent_is_one_notice_per_quiet_spell(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    store = inbox._empty()
    run = _run("running", worktree_path="/wt")
    now = 1_000_000.0
    probe = {"T9": {"activity": now - 1100, "markers": {}}}
    assert inbox.stuck_events([run], store, probe, now) == []
    probe = {"T9": {"activity": now - 1300, "markers": {}}}
    assert [e["kind"] for e in inbox.stuck_events([run], store, probe, now)] == ["run_stuck"]
    assert inbox.stuck_events([run], store, probe, now + 600) == []            # no repeat
    probe = {"T9": {"activity": now + 10, "markers": {}}}                      # woke up…
    assert inbox.stuck_events([run], store, probe, now + 1000) == []
    assert len(inbox.stuck_events([run], store, probe, now + 10 + 1300)) == 1   # …quiet again


def test_a_run_in_review_is_never_stuck(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "0")
    store = inbox._empty()
    probe = {"T9": {"activity": 1.0, "markers": {}}}
    assert inbox.stuck_events([_run("awaiting_review")], store, probe, 99_999.0) == []


def test_an_unread_marker_is_one_notice_with_the_daemon_uptime(monkeypatch):
    monkeypatch.setenv("CHELA_MARKER_UNREAD_ALERT_S", "300")
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "0")
    store = inbox._empty()
    now = 1_000_000.0
    probe = {"T9": {"activity": now - 5000, "markers": {"task-finished": now - 400}}}
    fired = inbox.stuck_events([_run("running")], store, probe, now, uptime=3 * 86400)
    assert [e["kind"] for e in fired] == ["run_marker_unread"]      # not ALSO "stuck"
    assert "daemon up 72h00m" in fired[0]["summary"]
    assert inbox.stuck_events([_run("running")], store, probe, now + 60, uptime=1) == []


def test_stuck_probes_read_the_worktree(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_AUTHOR_DATE": "@1000000000 +0000", "GIT_COMMITTER_DATE": "@1000000000 +0000",
           "PATH": __import__("os").environ["PATH"], "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "-q", str(wt)], check=True, env=env)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, env=env)
    (wt / ".chela-task-finished-request.json").write_text("{}")
    probes = inbox.stuck_probes([_run("running", worktree_path=str(wt))])
    assert probes["T9"]["activity"] == 1_000_000_000.0
    assert set(probes["T9"]["markers"]) == {"task-finished"}


def test_stuck_through_the_tick_is_one_notice(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "60")
    quiet_since = time.time() - 3600
    monkeypatch.setattr(inbox, "stuck_probes", lambda runs: {
        "T9": {"activity": quiet_since, "markers": {}}})
    for _ in range(3):
        inbox.tick({}, runs=[_run("running")])
    assert len(_queued("run_stuck")) == 1


# --- 6. a PR that went CONFLICTING --------------------------------------------------------

def test_conflicting_is_one_notice_per_head():
    store = inbox._empty()
    a = _run("awaiting_review", pr_mergeable="CONFLICTING", pr_head_sha="aaa")
    assert [e["kind"] for e in inbox.conflict_events([a], store)] == ["run_pr_conflicting"]
    assert inbox.conflict_events([a], store) == []                              # same head
    assert inbox.conflict_events([{**a, "pr_mergeable": "UNKNOWN"}], store) == []
    assert inbox.conflict_events([a], store) == []                  # flapped back: same head
    b = {**a, "pr_head_sha": "bbb"}
    assert len(inbox.conflict_events([b], store)) == 1                          # new head
    assert inbox.conflict_events([{**b, "pr_mergeable": "MERGEABLE"}], store) == []


def test_conflicting_through_the_tick_is_one_notice():
    run = _run("awaiting_review", pr_mergeable="CONFLICTING", pr_head_sha="aaa")
    for _ in range(3):
        inbox.tick({}, runs=[run])
    assert len(_queued("run_pr_conflicting")) == 1


def test_a_conflict_notice_rots_once_the_pr_is_rebased():
    event = inbox.conflict_events([_run("awaiting_review", pr_mergeable="CONFLICTING",
                                        pr_head_sha="aaa")], inbox._empty())[0]
    assert inbox.stale_reason(event, [_run("awaiting_review", pr_mergeable="MERGEABLE",
                                           pr_head_sha="aaa")])          # resolved
    assert inbox.stale_reason(event, [_run("awaiting_review", pr_mergeable="CONFLICTING",
                                           pr_head_sha="bbb")])          # head moved
    assert inbox.stale_reason(event, [_run("awaiting_review", pr_mergeable="CONFLICTING",
                                           pr_head_sha="aaa")]) is None
