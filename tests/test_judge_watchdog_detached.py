"""👻⚖️ CMX-429: a judge whose WINDOW is gone is not a judge that died.

Measured 2026-10-01 on PR #569: since CMX-411 the judge agent stops right after launching
`chela judge run --detach`, so its window going away is normal. The watchdog read it as a
dead judge, wrote CANNOT VERIFY, and a second judge was spawned into the SAME worktree while
the first detached run was still mutating it. These tests pin:

* window gone + a live run recorded in `$CHELA_DIR/judge-logs/<task>.json` ⇒ held: no
  CANNOT VERIFY, no reap, no respawn;
* window gone + that run's pid dead (or recycled: other start ticks) ⇒ CANNOT VERIFY, once;
* the wall counts from the run's own start, even when the row's marker was wiped;
* ⭐ a run that finishes publishes its verdict and the row proceeds normally.

Fake pids and json in a temp judge-logs dir; no real judge agents, no tmux.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from chela import dispatcher, judge, workflow
from tests.test_judge import REAL_GUARD_TEST, _exp, _run_row, _wf, _workflow_repo

DEAD_PID = 2 ** 22 + 7          # above the default pid_max: never a live process


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    logs = tmp_path / "judge-logs"
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: logs)
    return logs


def _iso(delta_seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _judging_row(tmp_path, *, spawned_ago=10 * 60, run_started_ago: float | None = None):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), judge_state=judge.J_RUNNING,
                 judge_sha="cafe1234", judge_started_at=_iso(-spawned_ago),
                 judge_run_started_at=None if run_started_ago is None else _iso(-run_started_ago))
    return wf


def _run_status(task="abc123", *, run_started_ago=5 * 60, **over):
    """The json `judge_run` writes about itself — here for a fake run owned by THIS process
    (alive, with its real start ticks) unless ``over`` says otherwise. No lock file."""
    st = {**judge.owner_identity(os.getpid()), "task_id": task,
          "run_started_at": time.time() - run_started_ago, "detached": True,
          "done": 2, "total": 9}
    st.update(over)
    path = judge.judge_status_path(task)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(st))
    return st


def _tick(wf):
    """One watchdog pass with the judge's window GONE from tmux."""
    with dispatcher._db() as conn, \
         patch.object(dispatcher, "_capture_pane", return_value=""), \
         patch.object(dispatcher, "_judge_hit_classifier_outage", return_value=False), \
         patch.object(dispatcher, "_kill_windows_named") as killed, \
         patch.object(dispatcher, "remove_worktree", return_value=True) as removed:
        handed = dispatcher._judge_watchdog(conn, wf, live_windows=set())
        conn.commit()
    run = dispatcher.resolve_run("abc123")
    return handed, run["judge_state"], removed.call_count, killed.call_count, run


# --- window gone, run alive ⇒ held ----------------------------------------------------------

def test_window_gone_but_detached_run_alive_is_held_not_cannot_verify(tmp_path):
    """⛔ PR #569's shape: the agent's window is gone (normal since `--detach`), the run's
    status json names a LIVE owner, and there is no slot lock to fall back on. The watchdog
    must hold: no CANNOT VERIFY, no worktree reap. Seen to go red: `live_judge_run` ignores
    the json (reads only the lock)."""
    wf = _judging_row(tmp_path, run_started_ago=20 * 60)
    _run_status()
    handed, state, removed, _, _ = _tick(wf)
    assert (handed, state, removed) == (0, judge.J_RUNNING, 0)


def test_a_live_run_is_timed_from_its_own_start_when_the_rows_marker_was_wiped(tmp_path):
    """A respawn (or a daemon from before a restart) can leave `judge_run_started_at` NULL
    under a live run. The wall then counts from the start the run recorded about itself
    (5 min ago), never from the agent's spawn 90 min ago — #569's 19:19 wall. Seen to go
    red: drop the json's `run_started_at` fallback."""
    wf = _judging_row(tmp_path, spawned_ago=90 * 60, run_started_ago=None)
    _run_status(run_started_ago=5 * 60)
    assert _tick(wf)[:2] == (0, judge.J_RUNNING)


def test_a_live_run_past_the_wall_from_its_own_start_is_still_reaped(tmp_path):
    """⭐ COUNTERWEIGHT: the hold is bounded — a live run 61 min past its OWN start is stuck,
    and is reaped exactly as before."""
    wf = _judging_row(tmp_path, spawned_ago=90 * 60, run_started_ago=None)
    _run_status(run_started_ago=61 * 60)
    handed, state, removed, _, run = _tick(wf)
    assert (handed, state, removed) == (1, judge.J_CANNOT_VERIFY, 1)
    assert "did not finish" in run["judge_detail"]


# --- window gone, run dead ⇒ CANNOT VERIFY, once --------------------------------------------

def test_window_gone_and_run_pid_dead_is_cannot_verify_once(tmp_path):
    """No live owner and no verdict ⇒ the run really is lost: CANNOT VERIFY (not counted
    against the retry budget — the window vanished), worktree reaped, and only ONCE — a
    second tick finds nothing left to hand over."""
    wf = _judging_row(tmp_path, run_started_ago=20 * 60)
    _run_status(pid=DEAD_PID, start_ticks=1, started=1.0)
    handed, state, removed, _, run = _tick(wf)
    assert (handed, state, removed) == (1, judge.J_CANNOT_VERIFY, 1)
    assert "window disappeared" in run["judge_detail"]
    assert run["judge_no_verdict"] == 1
    assert _tick(wf)[:3] == (0, judge.J_CANNOT_VERIFY, 0)


def test_a_recycled_pid_with_other_start_ticks_is_not_a_live_run(tmp_path):
    """The pid exists (it is this process) but its `/proc` start ticks differ from the ones
    the run recorded: a recycled pid, not the run. CANNOT VERIFY. Seen to go red: identity
    by bare pid existence."""
    from chela import sessions

    ticks = sessions.proc_start_ticks(os.getpid())
    if ticks is None:
        pytest.skip("no /proc on this host")
    wf = _judging_row(tmp_path, run_started_ago=20 * 60)
    _run_status(start_ticks=ticks - 1)
    assert _tick(wf)[:3] == (1, judge.J_CANNOT_VERIFY, 1)


# --- spawn refused while a run is alive -----------------------------------------------------

def _spawn(tmp_path, wf):
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)) as wt, \
             patch.object(dispatcher, "_refresh_judge_worktree", return_value=""), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars", return_value={}), \
             patch.object(dispatcher, "_launch_agent", return_value=None) as launch:
            ok = dispatcher._spawn_judge(wf, row, "beef5678", conn)
    return ok, wt.call_count + launch.call_count, dispatcher.resolve_run("abc123")


def test_spawn_is_refused_while_a_detached_run_is_alive(tmp_path):
    """⛔ Never a second judge into a worktree a live run is mutating — #569's 18:17:39
    respawn. The row reads `cannot_verify` (as the old watchdog left it) and a new head is
    pending, but the run's json names a live owner: nothing is checked out, no agent is
    launched, and the sha is NOT burned. Seen to go red: drop the check in `_spawn_judge`."""
    wf = _judging_row(tmp_path, run_started_ago=20 * 60)
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET judge_state=? WHERE task_id='abc123'",
                     (judge.J_CANNOT_VERIFY,))
        conn.commit()
    _run_status()
    ok, touched, run = _spawn(tmp_path, wf)
    assert (ok, touched) == (False, 0)
    assert (run["judge_sha"], run["judge_state"]) == ("cafe1234", judge.J_CANNOT_VERIFY)


def test_spawn_proceeds_once_the_run_is_gone(tmp_path):
    """⭐ COUNTERWEIGHT: a status left by a dead run refuses nothing."""
    wf = _judging_row(tmp_path)
    _run_status(pid=DEAD_PID, start_ticks=1, started=1.0)
    ok, touched, run = _spawn(tmp_path, wf)
    assert ok is True and touched == 2
    assert (run["judge_sha"], run["judge_state"]) == ("beef5678", judge.J_RUNNING)


def test_detach_is_refused_by_a_live_run_status_alone(tmp_path, monkeypatch):
    """`chela judge run --detach` refuses a second run on a task whose run status names a
    live owner, even with no slot lock (a run claimed under another layout, or before a
    restart). Seen to go red: `detach_judge_run` reads only the lock."""
    repo = _workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123")
    _run_status()
    spawned = []
    monkeypatch.setattr(judge, "spawn_detached", lambda *a: spawned.append(a) or 1)
    started = judge.detach_judge_run("abc123", tmp_path / "experiments.json")
    assert started["ok"] is False and "already running" in started["error"]
    assert spawned == []


# --- ⭐ the case that must be ACCEPTED ------------------------------------------------------

def test_a_detached_run_whose_window_is_gone_publishes_its_verdict(tmp_path, monkeypatch):
    """⭐ End to end on a REAL `judge_run`: mid-battery the agent's window is gone and the
    row's start marker has been wiped (#569's respawn did exactly that). The watchdog ticks
    and must hold, a spawn attempt must be refused, and the run must then publish its
    verdict onto the row — which the next watchdog tick leaves alone."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_state=judge.J_RUNNING, judge_sha="cafe1234",
                 judge_started_at=_iso(-30 * 60))
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    wf = workflow.load_workflow(repo / "WORKFLOW.md")
    real_run_experiments = judge.run_experiments
    mid = {}

    def _battery(*a, **kw):
        report = real_run_experiments(*a, **kw)
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET judge_run_started_at=NULL WHERE task_id=?", (task_id,))
            conn.commit()
        mid["tick"] = _tick(wf)[:3]
        mid["spawn"] = _spawn(tmp_path, wf)[:2]
        return report

    monkeypatch.setattr(judge, "run_experiments", _battery)
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=True, detached=True)

    assert mid["tick"] == (0, judge.J_RUNNING, 0)           # held, not reaped
    assert mid["spawn"] == (False, 0)                       # no second judge
    assert result["ok"] is True and result["state"] == judge.J_CLEAN
    assert dispatcher.resolve_run(task_id)["judge_state"] == judge.J_CLEAN   # ⭐ published
    assert not judge.judge_status_path(task_id).exists()   # the run cleared its own record
    assert _tick(wf)[:2] == (0, judge.J_CLEAN)              # nothing left to watch
