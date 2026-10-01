"""⏱️⚖️ CMX-411: `chela judge run --detach` — the battery outlives the judge agent's Bash tool.

Measured on #556 (cmx-406), 2026-09-30: the agent's Bash tool moved `chela judge run` to the
background at 10 min and KILLED it at its 30-min background limit; the agent re-ran the
whole battery from zero and the daemon's 60-min judge wall fired first. These tests pin the
four things that fix it:

* the run is NOT a descendant of the agent's shell — its own session and process group;
* a second run on the same task while one is live is REFUSED, never restarted from zero;
* the watchdog's wall starts at the RUN's own start marker, not the agent's spawn;
* ⭐ a run that takes longer than 30 minutes (simulated clock) still publishes its verdict.
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, judge, runtime_truth, workflow
from tests.test_judge import (
    REAL_GUARD_TEST,
    TEST_CMD,
    _project,
    _exp,
    _judge_worktree_path,
    _run_row,
    _wf,
    _workflow_repo,
)


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _iso(delta_seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


# --- the run is not a descendant of the agent's shell --------------------------------------

def test_a_detached_run_has_its_own_session_and_process_group(tmp_path):
    """⛔ The whole fix. The Bash tool kills what runs in the agent shell's process group;
    a detached run must lead a NEW session, so that kill (and the window's SIGHUP) never
    reaches it. Seen to go red: `start_new_session=False` in `judge.spawn_detached`."""
    log_path = tmp_path / "logs" / "t.log"
    pid = judge.spawn_detached(
        [sys.executable, "-c", "import time; print('up', flush=True); time.sleep(30)"],
        log_path,
    )
    try:
        assert os.getsid(pid) == pid                  # it LEADS its own session…
        assert os.getpgid(pid) == pid                 # …and its own process group
        assert os.getsid(pid) != os.getsid(0)         # ⛔ not the caller's session
        assert os.getpgid(pid) != os.getpgid(0)       # ⛔ not the caller's group
        deadline = time.time() + 10
        while time.time() < deadline and "up" not in log_path.read_text():
            time.sleep(0.05)
        assert "up" in log_path.read_text()           # its output lands in its OWN log
    finally:
        os.killpg(pid, signal.SIGKILL)
        os.waitpid(pid, 0)


def test_detach_launches_the_same_run_as_a_marked_child_and_returns_at_once(
    tmp_path, monkeypatch,
):
    """`--detach` re-execs `chela judge run` WITHOUT `--detach` (no fork bomb) and WITH the
    child marker, then returns immediately with the pid and the log path."""
    repo = _workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    launched = {}

    def _spawn(argv, log_path):
        launched.update(argv=argv, log=log_path)
        return 4242

    monkeypatch.setattr(judge, "spawn_detached", _spawn)
    started = judge.detach_judge_run("abc123", exp_file)

    assert started["ok"] is True and started["pid"] == 4242
    argv = launched["argv"]
    assert argv[:5] == [sys.executable, "-m", "chela.main", "judge", "run"]
    assert "--detached-child" in argv and "--detach" not in argv
    assert argv[argv.index("--experiments") + 1] == str(exp_file.resolve())
    assert launched["log"] == judge.judge_log_path("abc123")
    assert started["log"] == str(judge.judge_log_path("abc123"))


def test_the_judge_prompt_tells_the_agent_to_detach(tmp_path):
    """Wiring: the agent is told to run `--detach` — the command it is handed is the one
    that leaves its shell. Seen to go red: drop `--detach` from `_judge_vars`' judge_cmd."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        vars_ = dispatcher._judge_vars(wf, row, tmp_path / "wt", "cafe1234")
    assert vars_["judge_cmd"].endswith(" --detach")
    assert vars_["judge_log"] == str(judge.judge_log_path("abc123"))
    rendered = workflow.render_prompt(dispatcher.JUDGE_PROMPT, vars_)
    assert vars_["judge_cmd"] in rendered


# --- a second concurrent run on the same task is refused -----------------------------------

def _live_lock(worktree, *, pid=None, detached=False):
    from chela import sessions

    pid = os.getpid() if pid is None else pid
    lock = worktree.parent / f".{worktree.name}.judgelock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": pid, "started": sessions.proc_started(pid),
                                "task_id": "abc123", "detached": detached}))
    return lock


def test_a_second_detach_is_refused_while_a_run_is_live(tmp_path, monkeypatch):
    """⛔ Never a from-zero restart: with a live run holding the slot, `--detach` refuses
    and launches NOTHING. Seen to go red: ignore the live lock in `detach_judge_run`."""
    repo = _workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123")
    _live_lock(_judge_worktree_path(tmp_path, "abc123"))
    spawned = []
    monkeypatch.setattr(judge, "spawn_detached", lambda *a: spawned.append(a) or 1)

    started = judge.detach_judge_run("abc123", tmp_path / "experiments.json")

    assert started["ok"] is False
    assert "already running" in started["error"]
    assert spawned == []


def test_a_detach_with_no_live_run_is_not_refused(tmp_path, monkeypatch):
    """⭐ COUNTERWEIGHT — an always-refuse bug passes the test above trivially. A lock whose
    owner is gone is no claim at all."""
    import subprocess

    repo = _workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123")
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    lock = _judge_worktree_path(tmp_path, "abc123")
    lock = lock.parent / f".{lock.name}.judgelock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": dead.pid, "started": 1.0, "task_id": "abc123"}))
    monkeypatch.setattr(judge, "spawn_detached", lambda *a: 99)

    assert judge.detach_judge_run("abc123", tmp_path / "experiments.json")["ok"] is True


def test_a_claim_is_refused_while_another_claimer_is_mid_write(tmp_path):
    """The claim is atomic (`O_EXCL`): a lock file another child created a moment ago and
    has not written yet is a claim in progress, not a stale one to take over — else two
    `--detach` children started together could both run."""
    wt = tmp_path / "wts" / "judge-abc123"
    lock = wt.parent / f".{wt.name}.judgelock"
    lock.parent.mkdir(parents=True)
    lock.write_text("")                              # created, not yet written

    assert "claiming" in (judge._claim_judge_slot(wt, "abc123") or "")
    assert lock.read_text() == ""                    # ⛔ not overwritten


# --- the wall measures the RUN, from its own start ----------------------------------------

def _judging_row(tmp_path, *, spawned_ago: float, run_started_ago: float | None):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), judge_state=judge.J_RUNNING,
                 judge_sha="cafe1234", judge_started_at=_iso(-spawned_ago),
                 judge_run_started_at=None if run_started_ago is None else _iso(-run_started_ago))
    return wf


def _watch(wf):
    window = judge.judge_window_name("test-1")
    with dispatcher._db() as conn, \
         patch.object(dispatcher, "_capture_pane", return_value=""), \
         patch.object(dispatcher, "_judge_hit_classifier_outage", return_value=False), \
         patch.object(dispatcher, "_kill_windows_named"), \
         patch.object(dispatcher, "remove_worktree", return_value=True):
        handed = dispatcher._judge_watchdog(conn, wf, live_windows={window})
        conn.commit()
    return handed, dispatcher.resolve_run("abc123")["judge_state"]


def test_the_wall_starts_at_the_runs_own_start_not_the_agents_spawn(tmp_path):
    """The agent was spawned 90 min ago, but its battery started 5 min ago: the run has
    used 5 of its 60 minutes and must NOT be reaped. Seen to go red: time the wall from
    `judge_started_at` (the agent's spawn) instead of `judge_run_started_at`."""
    wf = _judging_row(tmp_path, spawned_ago=90 * 60, run_started_ago=5 * 60)
    assert _watch(wf) == (0, judge.J_RUNNING)


def test_a_run_past_the_wall_from_its_own_start_is_still_reaped(tmp_path):
    """⭐ COUNTERWEIGHT — the wall is MOVED, not removed: a run 61 min past its own start
    is stuck, and it is reaped."""
    wf = _judging_row(tmp_path, spawned_ago=90 * 60, run_started_ago=61 * 60)
    assert _watch(wf) == (1, judge.J_CANNOT_VERIFY)


def test_an_agent_that_never_starts_its_run_is_still_bounded_by_its_spawn(tmp_path):
    """No run marker yet ⇒ the agent's own spawn time bounds it, as before CMX-411."""
    wf = _judging_row(tmp_path, spawned_ago=61 * 60, run_started_ago=None)
    assert _watch(wf) == (1, judge.J_CANNOT_VERIFY)


def test_a_new_judge_spawn_clears_the_previous_runs_start_marker(tmp_path):
    """A re-judge must not inherit the last run's start and be reaped for it."""
    wf = _judging_row(tmp_path, spawned_ago=120 * 60, run_started_ago=100 * 60)
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "_refresh_judge_worktree", return_value=""), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars", return_value={}), \
             patch.object(dispatcher, "_launch_agent", return_value=None):
            assert dispatcher._spawn_judge(wf, row, "beef5678", conn) is True
    assert dispatcher.resolve_run("abc123")["judge_run_started_at"] is None


def _sleeper(tmp_path):
    return judge.spawn_detached([sys.executable, "-c", "import time; time.sleep(30)"],
                                tmp_path / "sleeper.log")


def _reap(pid):
    # `os.kill`, not `os.killpg`: a test may have monkeypatched `os.killpg` (it is the same
    # module object as `judge.os`), and the sleeper is its own group leader anyway.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    os.waitpid(pid, 0)


def test_stop_signals_only_a_live_detached_run_never_a_foreground_one(tmp_path, monkeypatch):
    """A detached run is out of every window's reach, so the timeout reap signals its own
    process group — and ONLY a group `--detach` made. A live FOREIGN process whose claim is
    not marked detached (a manual foreground run) is never signalled, nor is this process.
    Seen to go red: drop the `detached` check in `judge.stop_judge_run`."""
    wt = tmp_path / "wts" / "judge-abc123"
    killed = []
    monkeypatch.setattr(judge.os, "killpg", lambda pid, sig: killed.append((pid, sig)))
    child = _sleeper(tmp_path)
    try:
        _live_lock(wt, pid=child, detached=False)     # live, foreign, but a foreground run
        assert judge.stop_judge_run(wt) is False
        _live_lock(wt, detached=True)                 # detached, but names THIS process
        assert judge.stop_judge_run(wt) is False
        assert killed == []

        _live_lock(wt, pid=child, detached=True)      # ⭐ the one it must stop
        assert judge.stop_judge_run(wt) is True
        assert killed == [(child, signal.SIGTERM)]
    finally:
        _reap(child)


def test_the_watchdogs_timeout_reap_stops_the_detached_run(tmp_path):
    """Wiring: a detached run past the wall is STOPPED by the watchdog's reap, not left
    running against a worktree the reap just deleted. A real detached process holds the
    claim; after the reap it is gone. Seen to go red: remove the `stop_judge_run` call from
    `_judge_watchdog`."""
    wf = _judging_row(tmp_path, spawned_ago=90 * 60, run_started_ago=61 * 60)
    child = _sleeper(tmp_path)
    done = 0
    try:
        _live_lock(judge.judge_worktree_path(wf, "abc123"), pid=child, detached=True)
        assert _watch(wf) == (1, judge.J_CANNOT_VERIFY)
        deadline = time.time() + 10
        while time.time() < deadline:
            done, _ = os.waitpid(child, os.WNOHANG)
            if done:
                break
            time.sleep(0.05)
        assert done == child, "the detached run is still alive after the timeout reap"
    finally:
        if not done:
            _reap(child)


# --- ⭐ the case that must be ACCEPTED ------------------------------------------------------

def test_a_run_longer_than_30_minutes_still_publishes_its_verdict(tmp_path, monkeypatch):
    """⭐ #556's shape, end to end on a REAL judge run: the agent spent 30 min designing,
    then the battery ran for 35 more (simulated clock) — past the Bash tool's 30-min
    background limit, and 65 min after the agent was spawned. The watchdog ticks mid-run
    and must hold; the run must publish its verdict. Seen to go red: time the wall from the
    agent's spawn (the watchdog reaps at 65 min, mid-run), or never stamp the run's start."""
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
    mid_run = {}

    def _slow_battery(*a, **kw):
        report = real_run_experiments(*a, **kw)
        later = (datetime.now(timezone.utc) + timedelta(minutes=35)).isoformat()
        with patch.object(dispatcher, "_now", return_value=later):
            mid_run["handed"], mid_run["state"] = _watch(wf)
        mid_run["marker"] = dispatcher.resolve_run(task_id)["judge_run_started_at"]
        return report

    monkeypatch.setattr(judge, "run_experiments", _slow_battery)
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        result = judge.judge_run(task_id, exp_file, cleanup=True, detached=True)

    assert mid_run["marker"] is not None                     # the run stamped its own start
    assert (mid_run["handed"], mid_run["state"]) == (0, judge.J_RUNNING)   # held, not reaped
    assert result["ok"] is True and result["state"] == judge.J_CLEAN
    assert dispatcher.resolve_run(task_id)["judge_state"] == judge.J_CLEAN  # ⭐ published


# --- chela doctor shows a live run --------------------------------------------------------

@pytest.mark.parametrize("detached", [True, False])
def test_doctor_shows_a_live_run_with_elapsed_time_and_progress(tmp_path, monkeypatch, detached):
    """A live judge run is listed with its elapsed time (from its OWN start) and k/N — the
    status `judge_run` writes as each experiment starts. Real run, real progress file.
    Both ways round: a detached run says so and names its log; a foreground one says
    `foreground` and names none (it has no log of its own — its output is the caller's)."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    logs = tmp_path / "judge-logs"
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: logs)
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp(), _exp(guard="again")]}))
    seen = []
    real_apply = judge._apply_experiments

    def _apply(*a, progress=None, **kw):
        if progress is None:                      # a consistency re-run: not the battery
            return real_apply(*a, **kw)

        def _spy(done, total):
            progress(done, total)
            findings = runtime_truth.audit(runtime_truth.fact("judge.live_runs"))
            seen.append([(f.title, f.detail) for f in findings])
        return real_apply(*a, progress=_spy, **kw)

    monkeypatch.setattr(judge, "_apply_experiments", _apply)
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        judge.judge_run(task_id, exp_file, cleanup=True, detached=detached)

    how = "detached" if detached else "foreground"
    hits = [(t, d) for found in seen for t, d in found
            if f"judge for abc123 running ({how})" in t and "experiment 1/2" in t
            and "elapsed" in t]
    assert hits, seen
    log_detail = f"log {logs / 'abc123.log'}"
    assert all((log_detail in d) is detached for _, d in hits), hits
    # …and gone once the run released its slot.
    assert [f.title for f in runtime_truth.audit(runtime_truth.fact("judge.live_runs"))] == [
        "no judge run in flight"]


@pytest.mark.parametrize("elapsed, total, want", [
    (125, 8, "2m05s elapsed, experiment 3/8"),
    (3 * 3600 + 60, None, "3h01m elapsed, baseline"),
])
def test_doctor_formats_elapsed_and_progress(elapsed, total, want):
    obs = runtime_truth.observed([{"task_id": "T", "pid": 1, "elapsed": elapsed, "done": 3,
                                   "total": total, "detached": True, "log": "/x.log"}])
    [f] = runtime_truth._judge_runs_report(None, obs)
    assert want in f.title


# --- ⚖️ CMX-411 rework round 1: the CLI wiring and the login-expired arm --------------------
#
# The judge corrupted each of these and the suite stayed green: every test above called
# `judge.judge_run(..., detached=True)` / `judge.detach_judge_run(...)` DIRECTLY, so the
# argparse dispatch in `cmd_judge` that turns `--detach` / `--detached-child` into those
# calls was never driven (DEFEAT_SHAPES 411). These drive the real `main.main()`.

def test_cli_detached_child_marks_its_claim_detached(tmp_path, monkeypatch):
    """⛔ The invariant, read off the claim itself: a `--detached-child` run's lock says
    `detached: true` — else `stop_judge_run` refuses to stop it on a timeout and the
    watchdog deletes its worktree out from under a live battery. Real argparse, real
    `judge_run`, the lock read back mid-battery. Seen to go red:
    `detached=getattr(args, "detached_child", False)` → `detached=False` in `cmd_judge`."""
    from chela import main

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    repo = _workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    real_run_experiments = judge.run_experiments
    claims = []

    def _spy(worktree, *a, **kw):
        claims.append(judge._read_judge_lock(judge._judge_lock_path(worktree)))
        return real_run_experiments(worktree, *a, **kw)

    def _run(*flags):
        claims.clear()
        with patch.object(judge, "run_experiments", _spy), \
             patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")), \
             patch.object(sys, "argv", ["chela", "judge", "run", "abc123",
                                        "--experiments", str(exp_file), *flags]):
            main.main()
        assert claims, "the battery never ran"
        return claims[0]

    assert _run("--detached-child")["detached"] is True
    # ⭐ COUNTERWEIGHT — an always-detached claim would let the watchdog signal a manual
    # foreground run it never launched.
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET judge_state=NULL, judge_sha=NULL WHERE task_id='abc123'")
        conn.commit()
    assert _run()["detached"] is False


@pytest.mark.parametrize("extra, want_cleanup", [((), True), (("--no-cleanup",), False)])
def test_cli_detach_takes_the_detach_path_and_never_runs_the_battery_inline(
    tmp_path, monkeypatch, capsys, extra, want_cleanup,
):
    """`chela judge run --detach` spawns the detached child and returns — it must NEVER run
    the battery in the caller's own process (that is the Bash-tool kill all over again).
    Real argparse; the only thing faked is the process spawn. Seen to go red:
    `if getattr(args, "detach", False):` → `if False and …` in `cmd_judge`.

    ⛔ The child's argv is pinned EXACTLY, both ways round on `--no-cleanup`: every flag the
    caller gave must reach the re-exec'd child, since the child — not this process — is the
    one that cleans up (DEFEAT_SHAPES 411b). Seen to go red: `cleanup=not args.no_cleanup`
    → `cleanup=True` on the `detach_judge_run` call in `cmd_judge`."""
    from chela import main

    repo = _workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    spawned = []
    monkeypatch.setattr(judge, "spawn_detached", lambda argv, log: spawned.append(argv) or 4242)
    inline = []
    monkeypatch.setattr(judge, "judge_run", lambda *a, **kw: inline.append(a) or {})

    with patch.object(sys, "argv", ["chela", "judge", "run", "abc123",
                                    "--experiments", str(exp_file), "--detach", *extra]):
        main.main()

    assert inline == []                                   # ⛔ no battery in this process
    assert spawned == [[sys.executable, "-m", "chela.main", "judge", "run", "abc123",
                        "--experiments", str(exp_file.resolve()), "--detached-child",
                        *(() if want_cleanup else ("--no-cleanup",))]]
    out = capsys.readouterr().out
    assert "started DETACHED (pid 4242)" in out
    assert str(judge.judge_log_path("abc123")) in out


@pytest.mark.parametrize("flags, want", [
    ((), {"cleanup": True, "detached": False}),
    (("--no-cleanup",), {"cleanup": False, "detached": False}),
    (("--detached-child",), {"cleanup": True, "detached": True}),
    (("--detached-child", "--no-cleanup"), {"cleanup": False, "detached": True}),
])
def test_cli_run_hands_both_flags_to_judge_run(monkeypatch, flags, want):
    """The OTHER end of the re-exec: the child's `--detached-child` / `--no-cleanup` must
    arrive at `judge_run` as `detached=` / `cleanup=` — all four combinations, so neither
    flag can be dropped or pinned without one row going red. Seen to go red:
    `cleanup=not args.no_cleanup` → `cleanup=True` on the `judge_run` call in `cmd_judge`."""
    from chela import main

    calls = []
    monkeypatch.setattr(judge, "detach_judge_run",
                        lambda *a, **kw: pytest.fail("a non---detach run took the detach path"))
    monkeypatch.setattr(judge, "judge_run",
                        lambda *a, **kw: calls.append((a, kw)) or {"ok": True, "task_id": "t"})
    with patch.object(sys, "argv", ["chela", "judge", "run", "abc123",
                                    "--experiments", "x.json", *flags]):
        main.main()

    assert calls == [(("abc123", "x.json"), want)]


def test_cli_detach_refused_exits_nonzero_and_spawns_nothing(tmp_path, monkeypatch, capsys):
    """A refused `--detach` (a run is already live) is a FAILURE the agent must see — exit
    1, nothing spawned, nothing run inline — not a silent "started"."""
    from chela import main

    repo = _workflow_repo(tmp_path, "abc123", REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, "abc123")
    _live_lock(_judge_worktree_path(tmp_path, "abc123"))
    spawned, inline = [], []
    monkeypatch.setattr(judge, "spawn_detached", lambda *a: spawned.append(a) or 1)
    monkeypatch.setattr(judge, "judge_run", lambda *a, **kw: inline.append(a) or {})

    with patch.object(sys, "argv", ["chela", "judge", "run", "abc123",
                                    "--experiments", "x.json", "--detach"]), \
         pytest.raises(SystemExit) as exit_:
        main.main()

    assert exit_.value.code == 1
    assert spawned == [] and inline == []
    assert "already running" in capsys.readouterr().out


_LOGIN_BANNER = "✽ Sonnet 5\n\nLogin expired · Please run /login\n\n❯ "


def _watch_with_pane(wf, pane):
    window = judge.judge_window_name("test-1")
    with dispatcher._db() as conn, \
         patch.object(dispatcher, "_capture_pane",
                      side_effect=lambda w: pane if w == window else ""), \
         patch.object(dispatcher, "_judge_hit_classifier_outage", return_value=False), \
         patch.object(dispatcher, "_kill_windows_named"), \
         patch.object(dispatcher, "remove_worktree", return_value=True):
        handed = dispatcher._judge_watchdog(conn, wf, live_windows={window})
        conn.commit()
    return handed, dispatcher.resolve_run("abc123")["judge_state"]


def test_an_expired_login_does_not_reap_a_run_that_has_started(tmp_path):
    """Once `chela judge run` has stamped its own start it runs detached and needs nothing
    more from the agent's session: an expired login in that pane is NOT a reason to kill a
    battery 5 minutes into its 60. Seen to go red:
    `alive and run_started is None` → `alive` in `_judge_watchdog`."""
    wf = _judging_row(tmp_path, spawned_ago=10 * 60, run_started_ago=5 * 60)
    assert _watch_with_pane(wf, _LOGIN_BANNER) == (0, judge.J_RUNNING)


def test_an_expired_login_still_reaps_a_judge_whose_run_never_started(tmp_path):
    """⭐ COUNTERWEIGHT — CMX-282 is kept, not removed: before the run's start marker exists
    the agent IS the judge, and a dead login there is reaped on sight."""
    wf = _judging_row(tmp_path, spawned_ago=10 * 60, run_started_ago=None)
    assert _watch_with_pane(wf, _LOGIN_BANNER) == (1, judge.J_CANNOT_VERIFY)
    assert "login expired" in dispatcher.resolve_run("abc123")["judge_detail"]


# --- the rest of the run's own bookkeeping, asserted on its effect ------------------------

def test_progress_reports_every_experiment_start_and_the_finish(tmp_path):
    """`chela doctor`'s k/N is exactly what the battery reports: (0,N) … (N-1,N) as each
    experiment starts, then (N,N) when they are all done — never a stale k past the end."""
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    calls = []
    report = judge.run_experiments(
        root, TEST_CMD, {"experiments": [_exp(), _exp(guard="again")]}, timeout=120,
        progress=lambda done, total: calls.append((done, total)),
    )
    assert len(report.outcomes) == 2                 # the battery really ran both
    assert calls == [(0, 2), (1, 2), (2, 2)]


def _status(logs, task, **over):
    from chela import sessions

    st = {"pid": os.getpid(), "started": sessions.proc_started(os.getpid()), "task_id": task,
          "run_started_at": time.time() - 125, "detached": True, "done": 3, "total": 8}
    st.update(over)
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"{task}.json").write_text(json.dumps(st))


def test_live_runs_reports_elapsed_from_the_runs_own_start_and_skips_dead_owners(
    tmp_path, monkeypatch,
):
    """Elapsed is measured from `run_started_at` (125 s here), and a status left by a
    process that is gone is not reported as a live run."""
    logs = tmp_path / "judge-logs"
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: logs)
    _status(logs, "live")
    _status(logs, "dead", pid=2 ** 22 + 7, started=1.0)

    runs = judge.live_judge_runs()

    assert [r["task_id"] for r in runs] == ["live"]
    assert 124 <= runs[0]["elapsed"] < 135


def test_a_finished_run_clears_only_its_own_status(tmp_path, monkeypatch):
    """A run removes its own status file when it releases the slot — and never another
    live process's (a run that took over a stale slot must not erase its successor's)."""
    logs = tmp_path / "judge-logs"
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: logs)
    _status(logs, "mine")
    _status(logs, "theirs", pid=1)
    judge._clear_run_status("mine")
    judge._clear_run_status("theirs")
    assert not (logs / "mine.json").exists()
    assert (logs / "theirs.json").exists()


@pytest.mark.parametrize("elapsed, level", [
    (dispatcher.JUDGE_TIMEOUT_SECONDS - 1, runtime_truth.OK),
    (dispatcher.JUDGE_TIMEOUT_SECONDS, runtime_truth.WARN),
])
def test_doctor_warns_on_a_run_past_the_wall(elapsed, level):
    """A run still going past the wall is one nothing is bounding — the doctor WARNs (and
    says why); one inside it is OK."""
    obs = runtime_truth.observed([{"task_id": "T", "pid": 1, "elapsed": elapsed, "done": 1,
                                   "total": 2, "detached": True, "log": None}])
    [f] = runtime_truth._judge_runs_report(None, obs)
    assert f.level == level
    assert ("PAST the 60min judge wall" in f.detail) is (level == runtime_truth.WARN)


def test_a_stale_empty_claim_is_taken_over_not_refused_forever(tmp_path):
    """⭐ COUNTERWEIGHT to the mid-write refusal: an EMPTY lock that is not fresh is a
    claimer that crashed before writing — taking it over is the only way the slot ever
    frees. Refusing it would wedge every future judge on this task."""
    wt = tmp_path / "wts" / "judge-abc123"
    lock = wt.parent / f".{wt.name}.judgelock"
    lock.parent.mkdir(parents=True)
    lock.write_text("")
    old = time.time() - 60
    os.utime(lock, (old, old))

    assert judge._claim_judge_slot(wt, "abc123", detached=True) is None
    claim = json.loads(lock.read_text())
    assert claim["pid"] == os.getpid() and claim["detached"] is True


def test_detached_argv_carries_no_cleanup_and_a_safe_log_name():
    """`--no-cleanup` survives the re-exec, and a task id can never steer the log path out
    of `judge-logs/`."""
    assert "--no-cleanup" in judge.detached_argv("t", "x.json", cleanup=False)
    assert "--no-cleanup" not in judge.detached_argv("t", "x.json")
    # The experiments path is handed over ABSOLUTE — a relative one names whatever file the
    # child's cwd happens to hold.
    argv = judge.detached_argv("t", "x.json")
    assert argv[argv.index("--experiments") + 1] == str((Path.cwd() / "x.json").resolve())
    assert judge.judge_log_path("../../etc/x").parent == judge.judge_logs_dir()
