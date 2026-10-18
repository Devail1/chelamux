"""🔁⚖️ CMX-263: a daemon restart during a judge LAUNCH must not cost a judge round.

Measured 2026-10-18: `pm2 restart chela-daemon` landed between a judge's `before_run` hook and
its window. The new daemon's watchdog then wrote "the judge's window disappeared" CANNOT
VERIFY. These tests pin:

* a shutdown signal mid-launch lets that launch finish, and the tick launches nothing new;
* a launch the shutdown killed (its hook took the signal) records no CANNOT VERIFY;
* a launch interrupted HARD (SIGKILL: the launcher's pid is dead) is not reaped by the
  watchdog and is relaunched by the next daemon as the SAME round (no retry spent);
* a launch still in flight in another live process is left alone;
* negative controls: a `running` row with no launch marker is reaped as before.

Fake launcher, fake pids; no real judge agents, no tmux.
"""
from __future__ import annotations

import json
import os
import signal
import time
from unittest.mock import patch

import pytest

from chela import dispatcher, judge, main, ticklock
from tests.test_judge import _run_row, _tick, _wf

DEAD_PID = 2 ** 22 + 7          # above the default pid_max: never a live process
DEAD_OWNER = {"pid": DEAD_PID, "start_ticks": 1, "started": 1.0}
SPAWN_JUDGE = dispatcher._spawn_judge   # the real one; `_tick` patches the module attribute


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")
    monkeypatch.setenv("CHELA_JUDGE_MAX_CONCURRENT", "1")
    monkeypatch.setattr(dispatcher, "_shutdown_check", lambda: False)
    saved = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    yield
    for s, handler in saved.items():
        signal.signal(s, handler)


def _row(tmp_path, task_id="abc123", **over):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, task_id, workflow_path=str(wf.path),
                 branch_name=f"test-{task_id}", **over)
    return wf


def _interrupted_row(tmp_path, owner=DEAD_OWNER, judge_sha="cafe1234", **over):
    """A `running` judge row whose launch never finished, as a SIGKILLed daemon leaves it."""
    return _row(tmp_path, judge_state=judge.J_RUNNING, judge_sha=judge_sha,
                judge_started_at=dispatcher._now(), judge_cannot_verify_tries=1,
                judge_launch_owner=owner if isinstance(owner, str) else json.dumps(owner),
                **over)


def _real_spawn(launch):
    """The REAL `_spawn_judge`, with only its git/tmux edges faked and `launch` as the agent."""
    def spawn(wf, row, sha, conn, task=None):
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "_refresh_judge_worktree", return_value=""), \
             patch.object(judge, "_changed_files", return_value=None), \
             patch.object(judge, "docs_only_diff", return_value=None), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars", return_value={}), \
             patch.object(dispatcher, "_launch_agent", side_effect=lambda *a, **k: launch(row)):
            return SPAWN_JUDGE(wf, row, sha, conn, task)
    return spawn


def _run(task_id="abc123"):
    return dispatcher.resolve_run(task_id)


def _watchdog(wf):
    with dispatcher._db() as conn, \
         patch.object(dispatcher, "_capture_pane", return_value=""), \
         patch.object(dispatcher, "_judge_hit_classifier_outage", return_value=False), \
         patch.object(dispatcher, "_kill_windows_named"), \
         patch.object(dispatcher, "remove_worktree", return_value=True):
        return dispatcher._judge_watchdog(conn, wf, live_windows=set())


# --- graceful shutdown: the launch in progress finishes, nothing new starts -----------------

def test_a_signal_mid_launch_lets_it_finish_and_launches_nothing_new(tmp_path):
    """The fake launcher is mid-launch when SIGTERM lands (what `pm2 restart` sends). The
    handler only sets a flag, so the launch completes: the row is `running` with its marker
    cleared. The second candidate is NOT launched. Seen to go red: drop the trigger's
    `shutting_down()` break (both launch)."""
    wf = _row(tmp_path, "abc123")
    _row(tmp_path, "def456")
    stop = main.GracefulShutdown("test").install()
    dispatcher.set_shutdown_check(lambda: stop.stopping)
    launched = []

    def launch(row):
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)                   # the handler runs here; the launch carries on
        launched.append(row["task_id"])

    with patch.object(dispatcher, "judge_max_concurrent", return_value=5):
        summary = _tick(wf, _real_spawn(launch), open_ids=("abc123", "def456"))
    assert stop.stopping is True
    assert launched == ["abc123"]
    assert summary["judged"] == 1
    run = _run("abc123")
    assert (run["judge_state"], run["judge_launch_owner"]) == (judge.J_RUNNING, None)
    assert _run("def456")["judge_state"] is None


def test_no_shutdown_launches_every_candidate(tmp_path):
    """⭐ COUNTERWEIGHT to the test above: without a signal both candidates launch."""
    wf = _row(tmp_path, "abc123")
    _row(tmp_path, "def456")
    launched = []
    with patch.object(dispatcher, "judge_max_concurrent", return_value=5):
        _tick(wf, _real_spawn(lambda row: launched.append(row["task_id"])),
              open_ids=("abc123", "def456"))
    assert launched == ["abc123", "def456"]


def test_shutdown_skips_the_rework_respawn_and_the_claim(tmp_path):
    """A restart waits for nothing new: no rework re-spawn, no fresh claim."""
    wf = _row(tmp_path, "abc123", status="changes_requested", rework_count=0)
    dispatcher.set_shutdown_check(lambda: True)
    from chela.sources import Task

    task = Task(id="new1", title="t", file="TODO.md", line_number=2, raw="- [ ] t")
    with patch.object(dispatcher, "_respawn_rework", return_value=False) as rework, \
         patch.object(dispatcher, "_spawn", return_value=True) as spawn, \
         patch.object(dispatcher, "_claim_order", return_value=[task]):
        _tick_raw(wf)
    assert (rework.call_count, spawn.call_count) == (0, 0)
    dispatcher.set_shutdown_check(lambda: False)
    with patch.object(dispatcher, "_respawn_rework", return_value=False) as rework, \
         patch.object(dispatcher, "_spawn", return_value=True) as spawn, \
         patch.object(dispatcher, "_claim_order", return_value=[task]):
        _tick_raw(wf)
    assert rework.call_count == 1 and spawn.call_count == 1


def _tick_raw(wf):
    """`test_judge._tick` without its own `_respawn_rework`/`_claim_order` patches."""
    from chela.workflow import WorkflowStatus
    from tests.test_judge import _EmptySource

    class R:
        returncode, stdout, stderr = 0, "", ""

    with patch.object(dispatcher, "load_workflow_cached",
                      return_value=WorkflowStatus(path=wf.path, workflow=wf, error=None)), \
         patch.object(dispatcher, "get_source", return_value=_EmptySource()), \
         patch.object(dispatcher.subprocess, "run", return_value=R()), \
         patch.object(dispatcher, "_spawn_judge", return_value=False), \
         patch.object(dispatcher, "remove_worktree", return_value=True), \
         patch.object(dispatcher, "_failing_log_tail", return_value=""):
        return dispatcher.tick(wf.path)


def test_a_launch_killed_by_the_shutdown_records_no_cannot_verify(tmp_path):
    """The restart's signal killed the `before_run` hook (CMX-255 at 17:41:00): the launch
    raised while shutting down. No CANNOT VERIFY; the marker stays for the next daemon.
    Seen to go red: drop the `shutting_down()` arm in `_spawn_judge`'s except."""
    wf = _row(tmp_path)
    dispatcher.set_shutdown_check(lambda: True)

    def launch(row):
        raise RuntimeError("before_run hook died of SIGINT")

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        assert _real_spawn(launch)(wf, row, "cafe1234", conn) is False
    run = _run()
    assert run["judge_state"] == judge.J_RUNNING
    assert json.loads(run["judge_launch_owner"])["pid"] == os.getpid()


def test_a_launch_failure_outside_shutdown_is_still_cannot_verify(tmp_path):
    """⭐ NEGATIVE CONTROL: a launch failure with no restart under way is what it always was."""
    wf = _row(tmp_path)

    def launch(row):
        raise RuntimeError("tmux said no")

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        assert _real_spawn(launch)(wf, row, "cafe1234", conn) is False
    run = _run()
    assert run["judge_state"] == judge.J_CANNOT_VERIFY
    assert "failed to launch" in run["judge_detail"]
    assert run["judge_launch_owner"] is None


# --- restart recovery: an interrupted launch is relaunched, never reaped --------------------

def test_a_sigkilled_launch_is_not_reaped_as_window_disappeared(tmp_path):
    """The incident's second half: the launcher is dead, the window never came. The watchdog
    must not write "window disappeared". Seen to go red: drop the watchdog's marker skip."""
    wf = _interrupted_row(tmp_path)
    assert _watchdog(wf) == 0
    assert _run()["judge_state"] == judge.J_RUNNING


def test_negative_control_a_running_row_without_a_marker_is_still_reaped(tmp_path):
    """⭐ NEGATIVE CONTROL: no launch marker ⇒ the launch had finished; a missing window with
    no live run is a lost judge, exactly as before CMX-263."""
    wf = _interrupted_row(tmp_path, owner="null")
    assert _watchdog(wf) == 1
    run = _run()
    assert run["judge_state"] == judge.J_CANNOT_VERIFY
    assert "window disappeared" in run["judge_detail"]


def test_an_unreadable_marker_is_not_an_interrupted_launch(tmp_path):
    """A marker that does not parse names no launcher: the watchdog handles the row as before
    and the trigger does not relaunch it."""
    wf = _interrupted_row(tmp_path, owner="{not json")
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert dispatcher._judge_launch_interrupted(row) is False
    assert _watchdog(wf) == 1


def test_the_next_daemon_relaunches_a_sigkilled_launch_as_the_same_round(tmp_path):
    """The new daemon's trigger relaunches it: same sha, the retry count NOT bumped (1 stays
    1), no CANNOT VERIFY, the marker cleared once the launch is through. With one judge slot,
    the interrupted row must not hold that slot against its own relaunch. Seen to go red: the
    candidates query without the `running`+marker arm, or `judging` counting the row."""
    wf = _interrupted_row(tmp_path)
    launched = []
    summary = _tick(wf, _real_spawn(lambda row: launched.append(row["judge_sha"])))
    assert summary["judged"] == 1 and launched == ["cafe1234"]
    run = _run()
    assert (run["judge_state"], run["judge_sha"], run["judge_cannot_verify_tries"],
            run["judge_launch_owner"]) == (judge.J_RUNNING, "cafe1234", 1, None)


def test_an_interrupted_launch_left_by_this_process_is_relaunched(tmp_path):
    """The shutdown-killed launch above leaves THIS pid on the marker. A tick never runs the
    trigger mid-launch, so its own pid is an interrupted launch too."""
    wf = _interrupted_row(tmp_path, owner=judge.owner_identity(os.getpid()))
    launched = []
    assert _tick(wf, _real_spawn(lambda row: launched.append(1)))["judged"] == 1
    assert launched == [1]


def test_a_launch_in_flight_in_another_live_process_is_left_alone(tmp_path):
    """Another live process (a `chela dispatch` beside the daemon) is still launching: no
    relaunch, no reap, and it holds the judge slot."""
    wf = _interrupted_row(tmp_path, owner=judge.owner_identity(os.getppid()))
    _row(tmp_path, "def456")
    spawned = []
    _tick(wf, _real_spawn(lambda row: spawned.append(row["task_id"])),
          open_ids=("abc123", "def456"))
    assert spawned == []                 # one slot, and the live launch holds it
    with patch.object(dispatcher, "judge_max_concurrent", return_value=5):
        _tick(wf, _real_spawn(lambda row: spawned.append(row["task_id"])),
              open_ids=("abc123", "def456"))
    assert spawned == ["def456"]         # a free slot still never relaunches abc123
    _watchdog(wf)                        # (def456's fake launch made no window: reaped)
    assert _run()["judge_state"] == judge.J_RUNNING


def test_set_judge_state_clears_the_launch_marker(tmp_path):
    _interrupted_row(tmp_path)
    dispatcher.set_judge_state("abc123", judge.J_CLEAN, "ok")
    assert _run()["judge_launch_owner"] is None
    _interrupted_row(tmp_path, task_id="def456")
    dispatcher.set_judge_state("def456", judge.J_CLEAN, "ok", sha="cafe1234")
    assert _run("def456")["judge_launch_owner"] is None


# --- the daemon wires its signal flag into the dispatcher -----------------------------------

def test_the_daemon_wires_its_shutdown_flag_into_the_dispatcher(tmp_path, monkeypatch):
    """`chela run` must hand the dispatcher its GracefulShutdown flag, or a mid-tick signal
    starts the next launch anyway. Seen to go red: delete either `set_shutdown_check` call."""
    seen = []

    def begin_tick(wait):
        seen.append(dispatcher.shutting_down())
        os.kill(os.getpid(), signal.SIGTERM)
        time.sleep(0.05)
        seen.append(dispatcher.shutting_down())
        return None

    monkeypatch.setattr(ticklock, "begin_tick", begin_tick)
    with patch.object(main.scheduler, "init"), \
         patch.object(main.event_log, "new_boot", return_value="b"), \
         patch.object(main.event_log, "append"), \
         patch.object(main.capabilities, "effective", return_value={}), \
         patch.object(main.capabilities, "announce"), \
         patch.object(main.capabilities, "publish"), \
         patch.object(main.capabilities, "clear"), \
         patch.object(main.agent_manager, "start_background_refresh"):
        main.cmd_run(None)
    assert seen == [False, True]
    # ...and unbinds it on the way out: nothing in this process stays "shutting down".
    assert dispatcher.shutting_down() is False


# --- review follow-ups: bounded skip, stale heads, no marker before the launch --------------

def test_a_running_judge_on_an_older_head_is_still_re_judged(tmp_path):
    """A `running` row with NO launch marker whose head moved stays a candidate, exactly as
    before CMX-263 (`_spawn_judge` itself refuses while that run is alive) when a second judge
    slot is free. Seen to go red:
    the in-flight skip without its marker check."""
    wf = _interrupted_row(tmp_path, owner="null", judge_sha="0ld5ha")
    launched = []
    with patch.object(dispatcher, "judge_max_concurrent", return_value=5):
        _tick(wf, _real_spawn(lambda row: launched.append(1)),
              windows=(judge.judge_window_name("test-abc123"),))
    assert launched == [1]
    assert _run()["judge_sha"] == "cafe1234"


def test_an_unfinished_launch_past_the_wall_is_reaped(tmp_path):
    """The watchdog's hold on an unfinished launch is bounded: past JUDGE_TIMEOUT_SECONDS from
    its spawn (a hung launcher, or a relaunch that never came) it is reaped as before."""
    wf = _interrupted_row(tmp_path, owner=judge.owner_identity(os.getppid()))
    old = (dispatcher._parse_ts(dispatcher._now())
           - dispatcher.timedelta(seconds=dispatcher.JUDGE_TIMEOUT_SECONDS + 5)).isoformat()
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET judge_started_at=? WHERE task_id='abc123'", (old,))
        conn.commit()
    assert _watchdog(wf) == 1
    run = _run()
    assert (run["judge_state"], run["judge_launch_owner"]) == (judge.J_CANNOT_VERIFY, None)
    # The reaped row is an ordinary unknown again: the trigger retries it.
    launched = []
    _tick(wf, _real_spawn(lambda row: launched.append(1)))
    assert launched == [1]


def test_no_marker_is_written_before_the_launch_itself(tmp_path):
    """A failure in the pre-launch steps (here the risk classifier) is not an interrupted
    launch: no marker, so the trigger does not relaunch it every tick."""
    wf = _row(tmp_path)
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "_refresh_judge_worktree", return_value=""), \
             patch.object(judge, "_changed_files", side_effect=RuntimeError("corrupt")), \
             pytest.raises(RuntimeError):
            SPAWN_JUDGE(wf, row, "cafe1234", conn)
    assert _run()["judge_launch_owner"] is None
