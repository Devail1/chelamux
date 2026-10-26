"""CMX-214: ``--pause --all`` freezes judges (SIGSTOP tree), ``--resume`` thaws them. The fake
``os.kill`` logs whether the ledger said "frozen" at that instant: a stop before its record is red.
"""
from __future__ import annotations

import signal
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import agent_manager, dispatcher, epoch, fleet, judge, procguard, sessions, workflow
from tests.test_judge_classifier_outage import projects  # noqa: F401, F811
from tests.test_judge_wall import MIN, _judging_run, _own_runs_db, _st, _watch  # noqa: F401

TREE = {10: 1, 11: 10, 12: 11, 20: 1, 21: 20, 99: 1}     # claude 10, run 20, 99 unrelated


def _judge_identity(monkeypatch, tmp_path, cwds):
    """This tmux server is epoch ``e1``; every workflow's judge worktree is ``wt/judge-<task>``."""
    monkeypatch.setattr(epoch, "current", lambda: "e1")
    monkeypatch.setattr(workflow, "load_workflow_cached",
                        lambda p: SimpleNamespace(workflow=SimpleNamespace(path=Path(p))))
    monkeypatch.setattr(judge, "judge_worktree_path",
                        lambda wf, task: (tmp_path / "wt" / f"judge-{task}").resolve())
    monkeypatch.setattr(sessions, "_proc_cwd", lambda pid: str(cwds[pid]) if pid in cwds else None)


@pytest.fixture
def world(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet.config, "CHELA_DIR", tmp_path)
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")
    calls = []

    def kill(pid, sig):
        if pid == 13:
            raise ProcessLookupError(pid)                   # gone mid-freeze
        calls.append((pid, sig, judge.read_freeze("T1")["intervals"][-1][1] is None))

    runs = [{"task_id": "T1", "status": "awaiting_review", "judge_state": judge.J_RUNNING,
             "judge_window_id": "@7", "judge_started_at": "r1", "judge_window_epoch": "e1",
             "workflow_path": str(tmp_path / "WORKFLOW.md")},
            {"task_id": "T2", "status": "awaiting_review", "judge_state": "clean"},
            {"task_id": "T3", "judge_state": judge.J_RUNNING}]       # no window, no pids
    monkeypatch.setattr(dispatcher, "list_runs", lambda: runs)
    monkeypatch.setattr(procguard, "_ppids", lambda: dict(TREE))
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: {"@7": 10}[wid])
    _judge_identity(monkeypatch, tmp_path, {10: tmp_path / "wt" / "judge-T1"})
    monkeypatch.setattr(judge, "live_judge_run", lambda task: {"T1": {"pid": 20}, "T2": {"pid": 99}}.get(task))
    monkeypatch.setattr(fleet.os, "kill", kill)
    monkeypatch.setattr(fleet.inbox, "_selfcheck_scan", lambda: [])
    return calls


def test_freeze_stops_every_pid_of_the_tree_after_its_record_and_resume_thaws(world):
    state = fleet.pause_all(by="@0")
    assert world == [(p, signal.SIGSTOP, True) for p in (10, 20, 11, 21, 12)]
    assert state["judges"]["T1"]["pids"] == [10, 20, 11, 21, 12] and state["judges"].keys() == {"T1"}
    assert fleet.status_lines()[-1].startswith("  judge T1: suspended ")
    assert fleet.status_lines()[-1].endswith(" (SIGSTOP, 5 process(es))")
    world.clear()
    assert fleet.resume_all()["thawed"] == {"T1": True}
    assert world == [(p, signal.SIGCONT, False) for p in (10, 20, 11, 21, 12)]   # closed first
    assert judge.frozen_since("T1", 0.0) < 5.0
    assert judge.read_freeze("T1")["retry"] == 1                # one model-call retry granted
    rec = {"intervals": [[100.0, 150.0], [200.0, None]]}    # clipped at since; open = to now
    assert judge.frozen_since("x", 120.0, now=230.0, rec=rec) == 60.0


def test_a_pid_gone_mid_freeze_is_skipped_and_reported_not_fully_thawed(world, monkeypatch):
    monkeypatch.setitem(TREE, 13, 12)
    assert fleet.pause_all()["judges"]["T1"]["pids"] == [10, 20, 11, 21, 12]
    monkeypatch.setattr(fleet, "_signal", lambda pids, sig: pids[:-1])
    assert fleet.tick(None)[-1] == "fleet resume: judge T1 NOT fully thawed: check its tree for state T"


@pytest.mark.parametrize("rnd,kept", [("r1", [[100.0, 150.0]]), ("r0", [])])
def test_a_second_freeze_keeps_the_rounds_earlier_intervals(world, rnd, kept):
    """Same round ⇒ the earlier (closed) interval keeps its credit; another round's is dropped."""
    judge.write_freeze("T1", {"round": rnd, "intervals": [[100.0, 150.0]]})
    fleet.pause_all()
    rec = judge.read_freeze("T1")
    assert rec["round"] == "r1" and rec["intervals"][:-1] == kept
    assert rec["intervals"][-1][1] is None


def _freeze(intervals):
    judge.write_freeze("abc123", {"round": dispatcher.resolve_run("abc123")["judge_started_at"],
                                  "intervals": intervals})


def test_a_frozen_judge_past_its_wall_is_not_reaped(tmp_path, monkeypatch):
    wf = _judging_run(tmp_path, monkeypatch, run_started_ago=100 * MIN,
                      status=_st(done=7, since_progress=60 * MIN))
    _freeze([[time.time() - 50 * MIN, None]])
    assert _watch(wf)[:2] == (0, judge.J_RUNNING)


def test_a_ledger_from_another_round_does_not_shield_a_stuck_judge(tmp_path, monkeypatch):
    wf = _judging_run(tmp_path, monkeypatch, run_started_ago=100 * MIN,
                      status=_st(done=7, since_progress=60 * MIN))
    judge.write_freeze("abc123", {"round": "older", "intervals": [[time.time(), None]]})
    assert _watch(wf)[:2] == (1, judge.J_CANNOT_VERIFY)


@pytest.mark.parametrize("frozen_min,reaped", [(50, False), (30, True)])
def test_after_a_thaw_the_wall_excludes_the_frozen_time(tmp_path, monkeypatch, frozen_min, reaped):
    """No status ⇒ the flat 60-min wall. 100 min old, 50 frozen ⇒ 50 counted; 30 ⇒ 70."""
    wf = _judging_run(tmp_path, monkeypatch, run_started_ago=100 * MIN, status=None)
    now = time.time()
    _freeze([[now - 80 * MIN, now - (80 - frozen_min) * MIN]])
    assert _watch(wf)[0] == int(reaped)


def test_after_a_thaw_the_progress_clock_restarts_at_the_thaw(tmp_path, monkeypatch):
    """Past its wall even net of the freeze, but it was thawed 2 min ago: not stalled."""
    wf = _judging_run(tmp_path, monkeypatch, run_started_ago=150 * MIN,
                      status=_st(done=7, since_progress=63 * MIN))
    now = time.time()
    _freeze([[now - 62 * MIN, now - 2 * MIN]])
    assert _watch(wf)[:2] == (0, judge.J_RUNNING)


@pytest.mark.parametrize("credit,ok", [(1.0, True), (0.0, False)])
def test_the_suite_deadline_moves_out_by_this_runs_frozen_time(tmp_path, monkeypatch,
                                                               credit, ok):
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path)
    monkeypatch.setitem(judge._run_ctx, "task", "T9")
    judge.write_freeze("T9", {"intervals": [[time.time(), time.time() + credit]]})
    assert judge.run_suite("sleep 0.6", tmp_path, timeout=0.3).ok is ok


def test_a_reap_wakes_a_stopped_tree_after_the_sigterm(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(judge, "_read_judge_lock", lambda p: {"detached": True, "pid": 20})
    monkeypatch.setattr(judge, "_judge_lock_owner_alive", lambda lock: True)
    monkeypatch.setattr(procguard, "_ppids", lambda: dict(TREE))
    monkeypatch.setattr(judge.os, "killpg", lambda pid, sig: sent.append(("pg", pid, sig)))
    monkeypatch.setattr(judge.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    assert judge.stop_judge_run(tmp_path) is True
    assert sent == [("pg", 20, signal.SIGTERM), (20, signal.SIGTERM), (21, signal.SIGTERM),
                    (20, signal.SIGCONT), (21, signal.SIGCONT)]


def test_judge_run_credits_its_own_tasks_freeze_to_the_suite_deadline(tmp_path, monkeypatch):
    """WIRING, on a real judge run: every suite it runs is handed THIS task's freeze ledger,
    and the registration is gone once the run ends. Seen to go red: drop judge_run's
    `_run_ctx["task"] = task_id` (the judge's battery found that mutant surviving)."""
    from tests.test_judge import REAL_GUARD_TEST, _exp, _run_row, _workflow_repo

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    repo = _workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123", judge_state=judge.J_RUNNING, judge_sha="cafe1234")
    judge.write_freeze("abc123", {"intervals": [[100.0, 142.0]]})
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text('{"experiments": [%s]}' % __import__("json").dumps(_exp()))
    credits, real = [], procguard.run_shell_group

    def spy(cmd, **kw):
        credits.append(kw["frozen"](0.0) if kw.get("frozen") else None)
        return real(cmd, **kw)

    monkeypatch.setattr(procguard, "run_shell_group", spy)
    monkeypatch.setattr(dispatcher, "_post_pr_comment", lambda *a, **k: (True, ""))
    judge.judge_run("abc123", exp_file, cleanup=True)
    assert credits and credits == [42.0] * len(credits)
    assert "task" not in judge._run_ctx


# --- after a thaw, ONE failed model call is retried, never a CANNOT VERIFY -------------------

@pytest.fixture
def thawed(tmp_path, projects, monkeypatch):  # noqa: F811
    """A live, idle judge whose transcript ends on the 09-28 classifier outage (a failed
    model call), with `_send_seed` spied. Returns (wf, sent, co, set_ledger)."""
    from tests import test_judge_classifier_outage as co
    sent, ok = [], [True]
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a: sent.append(a) or ok[0])
    wf = co._wf(tmp_path)
    with dispatcher._db() as conn:
        co._run_row(conn, tmp_path, workflow_path=str(wf.path))
    co._spawn(wf)
    co._write_transcript(projects, wf)

    def ledger(retry, rnd=None):
        rnd = rnd or dispatcher.resolve_run("abc123")["judge_started_at"]
        judge.write_freeze("abc123", {"round": rnd, "intervals": [[1.0, 2.0]], "retry": retry})
    return wf, sent, co, ledger, ok


def test_one_failed_model_call_after_a_thaw_is_retried_then_a_second_reaps(thawed):
    wf, sent, co, ledger, _ = thawed
    ledger(1)
    assert co._watchdog(wf, "idle")[0] == 0                    # retried, not reaped
    assert sent == [(co.JUDGE_WID, dispatcher.JUDGE_RETRY_MESSAGE, "abc123")]
    assert dispatcher.JUDGE_RETRY_MESSAGE.startswith("resume: a fleet pause froze you mid-call")
    assert judge.read_freeze("abc123")["retry"] == 0           # the token is spent
    assert co._state() == (judge.J_RUNNING, 0, 0)              # the round goes on
    assert co._watchdog(wf, "idle")[0] == 1                    # only ONE is forgiven
    assert co._state()[0] == judge.J_CANNOT_VERIFY and len(sent) == 1


@pytest.mark.parametrize("retry,rnd,status", [
    (0, None, "idle"),                 # no token: never thawed, or already spent
    (1, "an-older-round", "idle"),     # another round's ledger grants nothing
    (1, None, "busy"),                 # a busy judge may be retrying on its own
])
def test_no_retry_without_this_rounds_token_on_an_idle_judge(thawed, retry, rnd, status):
    wf, sent, co, ledger, _ = thawed
    ledger(retry, rnd)
    co._watchdog(wf, status)
    assert sent == []
    assert judge.read_freeze("abc123")["retry"] == retry


def test_a_retry_that_cannot_be_sent_reaps_as_before_and_spends_the_token(thawed):
    wf, sent, co, ledger, ok = thawed
    ok[0] = False
    ledger(1)
    assert co._watchdog(wf, "idle")[0] == 1
    assert len(sent) == 1 and co._state()[0] == judge.J_CANNOT_VERIFY
    assert judge.read_freeze("abc123")["retry"] == 0


def test_spending_the_token_keeps_a_pause_that_landed_since(thawed, monkeypatch):
    """The watchdog's `freeze` snapshot is stale by the time it spends the token: a pause
    that opened an interval in between must survive the write."""
    wf, sent, co, ledger, _ = thawed
    ledger(1)
    real = dispatcher._judge_model_call_failed

    def pause_lands(wf, row):
        rec = judge.read_freeze("abc123")
        judge.write_freeze("abc123", {**rec, "intervals": [*rec["intervals"], [3.0, None]]})
        return real(wf, row)

    monkeypatch.setattr(dispatcher, "_judge_model_call_failed", pause_lands)
    co._watchdog(wf, "idle")
    assert judge.read_freeze("abc123")["intervals"][-1] == [3.0, None]


def test_an_api_error_reads_as_a_failed_model_call(tmp_path, projects, monkeypatch):  # noqa: F811
    from tests import test_judge_classifier_outage as co
    wf = co._wf(tmp_path)
    with dispatcher._db() as conn:
        co._run_row(conn, tmp_path, workflow_path=str(wf.path))
    co._spawn(wf)
    row = dispatcher.resolve_run("abc123")
    status = ["idle"]
    monkeypatch.setattr(dispatcher, "_agent_status",
                        lambda wid: status[0] if wid == co.JUDGE_WID else None)
    assert dispatcher._judge_model_call_failed(wf, row) is False      # no transcript yet
    co._write_transcript(projects, wf, co._judge_run_record())
    assert dispatcher._judge_model_call_failed(wf, row) is False      # a successful tail
    err = {"type": "assistant", "isApiErrorMessage": True}
    co._write_transcript(projects, wf, [*co._judge_run_record(), err])
    assert dispatcher._judge_model_call_failed(wf, row) is True
    status[0] = "busy"
    assert dispatcher._judge_model_call_failed(wf, row) is False      # busy: not stuck
    status[0] = "idle"
    co._write_transcript(projects, wf, [*co._judge_run_record(), err,
                                        {"type": "user", "message": {"content": "go on"}}])
    assert dispatcher._judge_model_call_failed(wf, row) is False      # recovered after it
    co._write_transcript(projects, wf, [*co._judge_run_record(),
                                        {**err, "isApiErrorMessage": "yes"}])
    assert dispatcher._judge_model_call_failed(wf, row) is False      # only a literal True
