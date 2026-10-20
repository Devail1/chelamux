"""CMX-274 — what a dispatched agent's tool calls leave running in its worktree dies when its
turn ends: an orphaned ``pytest -x`` tree (ppid 1, two xdist workers at 100% CPU) outlived the
agent's timed-out Bash call by 1h38m on 10-19.

⛔ No test here signals a real process: a FAKE proc tree and a recording ``kill`` (and
conftest's ``reaper_kills`` behind every default kill path)."""
from __future__ import annotations

import logging
import signal
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, worktree
from tests.test_agent_env_strip import _launch
from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf
from tests.test_worktree import _linked

CONT, TERM, KILL = signal.SIGCONT, signal.SIGTERM, signal.SIGKILL


def _proc(tmp_path: Path, procs: dict[int, tuple[str, str, str, int]]) -> Path:
    """A fake ``/proc``: ``{pid: (comm, state, cwd, ppid)}``."""
    root = tmp_path / "proc"
    for pid, (comm, state, cwd, ppid) in procs.items():
        d = root / str(pid)
        d.mkdir(parents=True)
        (d / "cwd").symlink_to(cwd)
        (d / "cmdline").write_bytes(comm.encode() + b"\0-q\0" + str(pid).encode() + b"\0")
        (d / "stat").write_text(f"{pid} ({comm}) {state} {ppid} 1 1 0\n")
    return root


def _table(tmp_path):
    """The agent's worktree holding (a) its window shell + claude, (b) an orphaned pytest and a
    STOPPED xdist worker, plus (c) a pytest elsewhere."""
    root = tmp_path / "worktrees"
    wt = _linked(root / "CMX-1")
    (wt / "tests").mkdir()
    proc = _proc(tmp_path, {
        9: ("bash", "S", str(wt), 1), 10: ("claude", "S", str(wt), 9),
        20: ("pytest", "R", str(wt), 1), 21: ("python", "T", str(wt / "tests"), 20),
        30: ("pytest", "R", str(tmp_path), 1)})
    return root, wt, proc


def _killer(proc: Path, dies_on_term: set[int], kills: list):
    def kill(pid, sig):
        kills.append((pid, sig))
        if sig == KILL or (sig == TERM and pid in dies_on_term):
            stat = proc / str(pid) / "stat"
            stat.write_text(stat.read_text().replace(") R ", ") Z ").replace(") T ", ") Z "))
    return kill


def test_only_the_orphaned_tree_is_signalled_cont_term_then_kill(tmp_path, caplog):
    """🔴 GUARD — (a) the agent's claude and its shell are spared, (c) outside the worktree is
    never touched, (b) gets CONT (the worker is stopped) then TERM; the worker ignores TERM
    and is KILLed after the grace. Drop the cwd filter ⇒ pid 30 signalled ⇒ RED."""
    root, wt, proc = _table(tmp_path)
    kills, sleeps = [], []
    with caplog.at_level(logging.WARNING):
        got = worktree.reap_strays_in(wt, root, proc_root=proc, kill=_killer(proc, {20}, kills),
                                      grace=1.0, sleep=sleeps.append)
    assert got == [20, 21]
    assert kills == [(20, CONT), (20, TERM), (21, CONT), (21, TERM), (21, KILL)]
    assert sleeps == [0.25] * 4
    assert f"reaping pid 21 (cwd {wt / 'tests'}) left in worktree {wt}: python -q 21" in caplog.text
    assert "pid 21 outlived SIGTERM by 1.0s: SIGKILLed" in caplog.text


def test_the_caller_and_its_ancestors_are_spared_without_a_claude_among_them(tmp_path, monkeypatch):
    """🔴 GUARD — a `chela` CLI run from inside the worktree (e.g. `rework-disputed`) must never
    signal itself or its shell, even when no `claude` sits above it to spare the chain.
    Empty the caller's own ancestor set ⇒ pids 500/501 signalled ⇒ RED."""
    root = tmp_path / "worktrees"
    wt = _linked(root / "CMX-1")
    proc = _proc(tmp_path, {
        501: ("bash", "S", str(wt), 1), 500: ("chela", "S", str(wt), 501),
        20: ("pytest", "R", str(wt), 1)})
    monkeypatch.setattr(worktree.os, "getpid", lambda: 500)
    kills = []
    got = worktree.reap_strays_in(wt, root, proc_root=proc, kill=_killer(proc, {20}, kills),
                                  grace=0.25, sleep=lambda _s: None)
    assert got == [20]
    assert {pid for pid, _sig in kills} == {20}


def test_a_tree_that_dies_on_term_is_never_killed_and_the_wait_ends_early(tmp_path):
    root, wt, proc = _table(tmp_path)
    kills, sleeps = [], []
    worktree.reap_strays_in(wt, root, proc_root=proc, kill=_killer(proc, {20, 21}, kills),
                            grace=5.0, sleep=sleeps.append)
    assert KILL not in [sig for _pid, sig in kills] and sleeps == []


def test_a_vanished_pid_is_skipped_at_either_signal_and_the_next_still_reaped(tmp_path):
    """`except OSError`: ProcessLookupError on CONT drops the pid (not reported), and on KILL
    it is not fatal — the next pid still gets KILLed."""
    root = tmp_path / "worktrees"
    wt = _linked(root / "CMX-1")
    proc = _proc(tmp_path, {p: ("pytest", "R", str(wt), 1) for p in (20, 21, 22)})
    kills = []

    def kill(pid, sig):
        if (pid, sig) in ((20, CONT), (21, KILL)):
            raise ProcessLookupError(pid)
        kills.append((pid, sig))
    assert worktree.reap_strays_in(wt, root, proc_root=proc, kill=kill, grace=0.25,
                                   sleep=lambda s: None) == [21, 22]
    assert (22, KILL) in kills and (20, TERM) not in kills


def test_a_target_that_is_not_a_linked_worktree_reaps_nothing(tmp_path):
    root, wt, proc = _table(tmp_path)
    (wt / ".git").unlink()
    kills = []
    assert worktree.reap_strays_in(wt, root, proc_root=proc, kill=kills.append) == []
    assert kills == []


def test_reap_run_worktree_graceful_picks_the_graceful_reaper(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(worktree, "reap_strays_in", lambda wt, root: calls.append("strays"))
    monkeypatch.setattr(worktree, "reap_processes_in", lambda wt, root: calls.append("kill"))
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text(f"---\nproject_key: CMX\nworkspace:\n  root: {tmp_path}\n---\n")
    worktree.reap_run_worktree(str(tmp_path / "W"), str(wf), graceful=True)
    worktree.reap_run_worktree(str(tmp_path / "W"), str(wf))
    assert calls == ["strays", "kill"]


# --- wiring: the run's end and the respawn reap ------------------------------------------

def test_end_agent_kills_the_window_then_reaps_its_worktree(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda w: calls.append(("kill", w)))
    monkeypatch.setattr(dispatcher, "reap_run_worktree",
                        lambda *a, **k: calls.append(("reap", a, k)))
    dispatcher._end_agent("cmx-1", {"worktree_path": "/wt/1", "workflow_path": "/r/WF.md"})
    dispatcher._end_agent(None, {"worktree_path": None, "workflow_path": "/r/WF.md"})
    assert calls == [("kill", "cmx-1"), ("reap", ("/wt/1", "/r/WF.md"), {"graceful": True})]


def test_task_finished_hand_in_reaps_the_runs_worktree(tmp_path, monkeypatch):
    """The daemon's half of `chela task-finished` ends the agent through `_end_agent`."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path="/wt/t1")
    ended = []
    with patch.object(dispatcher, "_end_agent", side_effect=lambda w, r: ended.append(
            (w, r["worktree_path"]))), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", None)):
        assert dispatcher.mark_awaiting_review("t1")["ok"]
    assert ended == [("test-1", "/wt/t1")]


def test_a_respawn_reaps_the_worktree_but_a_judge_launch_does_not(tmp_path, monkeypatch):
    reaped = []
    monkeypatch.setattr(dispatcher, "reap_strays_in", lambda wt, root: reaped.append((wt, root)))
    _launch(monkeypatch, dispatcher, tmp_path)
    assert reaped == [(tmp_path / "wt", (tmp_path / "wts").resolve())]
    _launch(monkeypatch, dispatcher, tmp_path, judge_window=True)
    assert len(reaped) == 1


def test_an_escalation_reaps_the_runs_strays(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    ended = []
    monkeypatch.setattr(dispatcher, "_end_agent", lambda w, r: ended.append(
        (w, r["worktree_path"])))
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path="/wt/t1")
        row = conn.execute("SELECT * FROM runs WHERE task_id='t1'").fetchone()
        dispatcher._escalate(conn, row, "stuck")
        assert conn.execute("SELECT status FROM runs").fetchone()[0] == "needs_human"
    assert ended == [(None, "/wt/t1")]


def test_a_worktree_that_cannot_be_resolved_reaps_nothing(tmp_path, monkeypatch):
    """`except OSError` on resolve fails CLOSED: unknown scope is never "in scope"."""
    root, wt, proc = _table(tmp_path)

    def boom(self, *a, **k):
        raise PermissionError(self)
    monkeypatch.setattr(Path, "resolve", boom)
    kills = []
    assert worktree.reap_strays_in(wt, root, proc_root=proc, kill=kills.append) == []
    assert kills == []


def test_a_pid_recycled_outside_the_worktree_during_the_grace_is_not_killed(tmp_path):
    root, wt, proc = _table(tmp_path)
    kills = []

    def sleep(_s):                      # pid 21 exits; its pid now belongs to pid 30's twin
        (proc / "21" / "cwd").unlink()
        (proc / "21" / "cwd").symlink_to(tmp_path)
    worktree.reap_strays_in(wt, root, proc_root=proc, kill=_killer(proc, set(), kills),
                            grace=0.25, sleep=sleep)
    assert (20, KILL) in kills and (21, KILL) not in kills


# --- wiring: EVERY `_end_agent` call site reaps (judge round 4: each one swapped for a bare
# `_kill_window` survived). The spy sits on the reap STEP, not on `_end_agent`, so ANY way of
# ending the agent without reaping goes red — not only the one the mutation happened to pick.

def _graceful_reaps(monkeypatch, tmp_path) -> list:
    """Every ``_end_agent`` reap, as (worktree, workflow). Only ``_end_agent`` passes
    ``graceful=True``; the worktree-removal reap (CMX-151) does not, and is not counted."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    reaped: list = []
    monkeypatch.setattr(dispatcher, "reap_run_worktree", lambda wt, wf, graceful=False: (
        reaped.append((wt, wf)) if graceful else None) or [])
    return reaped


def _tick(wf, source, fake, pr_status, read_pr_url=None):
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_url", return_value=read_pr_url), \
         patch.object(dispatcher, "_read_pr_status", return_value=(pr_status, "MERGEABLE")), \
         patch.object(dispatcher, "_strike_merged_tasks", return_value=1), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        return dispatcher.tick(wf.path)


def test_close_run_reaps_the_runs_strays(tmp_path, monkeypatch):
    reaped = _graceful_reaps(monkeypatch, tmp_path)
    with dispatcher._db() as conn:
        _row(conn, status="failed", worktree_path="/wt/closed", workflow_path="/r/WF.md",
             pr_url=None, pr_state=None)
    with patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        assert dispatcher.close_run("abc123", "superseded")["ok"]
    assert dispatcher.resolve_run("abc123")["status"] == "closed"
    assert reaped == [("/wt/closed", "/r/WF.md")]


def test_a_disputed_rework_reaps_the_runs_strays(tmp_path, monkeypatch):
    reaped = _graceful_reaps(monkeypatch, tmp_path)
    with dispatcher._db() as conn:
        _row(conn, status="running", rework_count=1, worktree_path="/wt/disputed",
             workflow_path="/r/WF.md")
    with patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        assert dispatcher.mark_rework_disputed("abc123", "already fixed")["ok"]
    assert dispatcher.resolve_run("abc123")["status"] == "needs_human"
    assert reaped == [("/wt/disputed", "/r/WF.md")]


@pytest.mark.parametrize("status", ["awaiting_review", "running"])
def test_tick_reconciling_a_merged_pr_reaps_the_runs_strays(tmp_path, monkeypatch, status):
    """The merged branch (task still open in the tracker; phase 0 sees the merge)."""
    reaped = _graceful_reaps(monkeypatch, tmp_path)
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status=status, worktree_path="/wt/merged")
    _tick(wf, _Source("abc123"), _FakeTmux(), "merged")
    assert dispatcher.resolve_run("abc123")["status"] == "done"
    assert reaped == [("/wt/merged", str(wf.path))]


def test_tick_reconciling_a_closed_pr_reaps_the_runs_strays(tmp_path, monkeypatch):
    reaped = _graceful_reaps(monkeypatch, tmp_path)
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="awaiting_review",
             worktree_path="/wt/rejected")
    _tick(wf, _Source("abc123"), _FakeTmux(), "closed")
    assert dispatcher.resolve_run("abc123")["status"] == "closed"
    assert reaped == [("/wt/rejected", str(wf.path))]


def test_tick_reconciling_a_review_row_that_left_the_tracker_reaps_its_strays(
        tmp_path, monkeypatch):
    reaped = _graceful_reaps(monkeypatch, tmp_path)
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="awaiting_review",
             worktree_path="/wt/struck")
    _tick(wf, _Source(), _FakeTmux(), "open")
    assert dispatcher.resolve_run("abc123")["status"] == "done"
    assert reaped == [("/wt/struck", str(wf.path))]


def test_tick_reconciling_a_running_row_that_left_the_tracker_merged_reaps_its_strays(
        tmp_path, monkeypatch):
    """The `tracker_gone` + merged-PR branch: a `running` row with NO stored pr_url, so
    phase 0 never refreshes it and the merged branch above cannot take it — only this
    branch's own PR lookup finds the merge."""
    reaped = _graceful_reaps(monkeypatch, tmp_path)
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", worktree_path="/wt/gone",
             pr_url=None, pr_state=None)
    _tick(wf, _Source(), _FakeTmux(), "merged", read_pr_url="https://github.com/o/r/pull/80")
    assert dispatcher.resolve_run("abc123")["status"] == "done"
    assert reaped == [("/wt/gone", str(wf.path))]
