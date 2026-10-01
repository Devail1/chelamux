"""⏳⚖️ CMX-431 — the judge wall scales with the battery, and an advancing run is never lost.

Measured 2026-10-01 on PR #580 (risk HIGH, 12 experiments): the battery finished all 12 at
about 60 min — two SURVIVED held-out experiments each needed an 8-minute full-suite
confirmation — and the daemon reaped it at 62 min as "stuck, not thinking" while it was
publishing a real BLOCK. Here:

* the wall is ``base + total × per_experiment + confirmations × full-suite budget``, never
  below the flat 60 min and never above the hard ceiling;
* past it, only a run whose progress STOPPED is "stuck, not thinking";
* a run that has completed every experiment gets a grace period to publish;
* ⭐ a small low-risk battery behaves exactly as before.

⛔ Simulated clock and fake progress json throughout the watchdog half — no real judges.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from chela import dispatcher, judge, runtime_truth
from tests.test_judge import REAL_GUARD_TEST, _exp, _run_row, _wf, _workflow_repo
from tests import test_judge_select as sel

MIN = 60.0
NOW = 1_800_000_000.0           # a fixed simulated "now" for the pure decision


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _st(*, total=12, done=5, since_progress=30.0, baseline=300.0, confirmations=0, **over):
    """A run status as `judge_run` writes it, progress last advanced ``since_progress`` ago."""
    st = {"total": total, "done": done, "baseline_seconds": baseline,
          "confirmations": confirmations,
          "progress_at": None if since_progress is None else NOW - since_progress}
    st.update(over)
    return st


# --- the wall itself --------------------------------------------------------------------------

def test_the_wall_is_sized_from_the_battery_and_its_confirmations():
    """PR #580's shape: 12 experiments, 2 full-suite confirmations, a 330 s baseline
    ⇒ 600 + 12×360 + 2×(330×1.5) = 5910 s — not the flat 3600 that reaped it."""
    assert dispatcher.judge_wall_seconds(_st(baseline=330.0, confirmations=2)) == 5910.0


def test_a_small_battery_keeps_the_flat_60min_wall():
    """⭐ The case that must be ACCEPTED: 3 low-risk experiments size to 28 min, but the
    wall is never SHORTER than it was before — still exactly 60 min."""
    assert dispatcher.JUDGE_TIMEOUT_SECONDS == 3600       # the floor itself, pinned
    assert dispatcher.judge_wall_seconds(_st(total=3, done=1)) == dispatcher.JUDGE_TIMEOUT_SECONDS
    assert dispatcher.judge_wall_seconds(None) == dispatcher.JUDGE_TIMEOUT_SECONDS


def test_the_wall_never_exceeds_the_hard_ceiling():
    huge = _st(total=500, confirmations=100)
    assert dispatcher.judge_wall_seconds(huge) == 3 * 3600


# --- the decision: advancing, stuck, completed, small ----------------------------------------

def test_an_advancing_run_past_60min_is_not_lost():
    """GUARD: 70 min in, 11/12 done, progress 1 min ago — inside its scaled wall. A flat
    60-min wall would reap it here. Seen to go red: the wall corrupted back to flat."""
    assert dispatcher.judge_overdue(70 * MIN, _st(done=11, since_progress=60), NOW) is None


def test_an_advancing_run_past_even_its_scaled_wall_is_not_lost():
    """GUARD: past the scaled wall (82 min here), progress still advanced 2 min ago — the
    watchdog does not kill a moving run. Seen to go red: stall check ignores `progress_at`."""
    assert dispatcher.judge_overdue(100 * MIN, _st(done=11, since_progress=120), NOW) is None


def test_a_run_with_no_progress_past_its_wall_is_stuck():
    """GUARD: past its wall, progress has not moved for 10 min (> the 6-min per-experiment
    budget and the 7.5-min full-suite budget) — it IS stuck, and says so."""
    reason = dispatcher.judge_overdue(100 * MIN, _st(done=7, since_progress=10 * MIN), NOW)
    assert reason is not None and "stuck, not thinking" in reason
    assert "experiment 7/12" in reason and "10min" in reason


def test_a_long_full_suite_confirmation_is_not_a_stall():
    """An 8.5-min confirmation emits no progress while the suite runs. With an 8-min
    baseline the stall window is 12 min, so 9 min of silence is not stuck."""
    st = _st(done=7, since_progress=9 * MIN, baseline=8 * MIN, confirmations=1)
    assert dispatcher.judge_overdue(110 * MIN, st, NOW) is None


def test_a_completed_run_gets_the_grace_and_is_not_reaped():
    """GUARD: every experiment done, the run is publishing (no progress for 10 min), 5 min
    past its 82-min wall — inside the 15-min grace, so it is NOT reaped. Seen to go red:
    the grace dropped."""
    st = _st(done=12, since_progress=10 * MIN)
    wall = dispatcher.judge_wall_seconds(st)
    assert dispatcher.judge_overdue(wall + 5 * MIN, st, NOW) is None


def test_a_completed_run_past_its_grace_with_no_progress_is_reaped():
    """⭐ COUNTERWEIGHT: the grace is bounded — past wall + grace, still silent, reaped."""
    st = _st(done=12, since_progress=20 * MIN)
    wall = dispatcher.judge_wall_seconds(st)
    reason = dispatcher.judge_overdue(wall + 16 * MIN, st, NOW)
    assert reason is not None and "all 12 experiments done" in reason


def test_stuck_is_never_said_of_an_advancing_run_at_the_ceiling():
    """At the hard ceiling an advancing run is reaped as OUT OF BUDGET, never "stuck"."""
    reason = dispatcher.judge_overdue(3 * 3600 + 60, _st(done=11, since_progress=30), NOW)
    assert reason is not None and "hard ceiling" in reason
    assert "stuck, not thinking" not in reason


def test_a_small_low_risk_battery_behaves_as_today():
    """⭐ ACCEPTED: a 3-experiment run at 50 min is left alone, exactly as under the flat
    wall; at 61 min with no progress record it is reaped with today's very words."""
    assert dispatcher.judge_overdue(50 * MIN, _st(total=3, done=1, since_progress=None),
                                    NOW) is None
    assert dispatcher.judge_overdue(61 * MIN, _st(total=3, done=1, since_progress=None),
                                    NOW) == ("the judge did not finish in 60min — it is "
                                             "stuck, not thinking")


# --- the watchdog, end to end on a fake progress json ----------------------------------------

def _iso(delta_seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=delta_seconds)).isoformat()


def _judging_run(tmp_path, monkeypatch, *, run_started_ago: float, status: dict | None):
    logs = tmp_path / "judge-logs"
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: logs)
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), judge_state=judge.J_RUNNING,
                 judge_sha="cafe1234", judge_started_at=_iso(-run_started_ago - 60),
                 judge_run_started_at=_iso(-run_started_ago))
    if status is not None:
        logs.mkdir(parents=True, exist_ok=True)
        now = time.time()
        st = {**judge.owner_identity(os.getpid()), "task_id": "abc123",
              "run_started_at": now - run_started_ago, "detached": True, **status}
        if st.get("progress_at") is not None:
            st["progress_at"] = now - (NOW - st["progress_at"])   # re-anchor to the real clock
        (logs / "abc123.json").write_text(json.dumps(st))
    return wf


def _watch(wf):
    window = judge.judge_window_name("test-1")
    with dispatcher._db() as conn, \
         patch.object(dispatcher, "_capture_pane", return_value=""), \
         patch.object(dispatcher, "_judge_hit_classifier_outage", return_value=False), \
         patch.object(dispatcher, "_kill_windows_named"), \
         patch.object(dispatcher, "remove_worktree", return_value=True), \
         patch.object(judge, "stop_judge_run", return_value=False):
        handed = dispatcher._judge_watchdog(conn, wf, live_windows={window})
        conn.commit()
    row = dispatcher.resolve_run("abc123")
    return handed, row["judge_state"], row["judge_detail"]


def test_the_watchdog_keeps_an_advancing_run_past_60min(tmp_path, monkeypatch):
    """GUARD (wiring): the watchdog really reads the run's status json. 70 min in, progress
    a minute ago ⇒ kept. Seen to go red: the watchdog stops consulting `_judge_run_status`."""
    wf = _judging_run(tmp_path, monkeypatch, run_started_ago=70 * MIN,
                      status=_st(done=11, since_progress=60))
    assert _watch(wf)[:2] == (0, judge.J_RUNNING)


def test_the_watchdog_reaps_a_stalled_run_as_stuck(tmp_path, monkeypatch):
    wf = _judging_run(tmp_path, monkeypatch, run_started_ago=100 * MIN,
                      status=_st(done=7, since_progress=15 * MIN))
    handed, state, detail = _watch(wf)
    assert (handed, state) == (1, judge.J_CANNOT_VERIFY)
    assert "progress has not advanced" in detail and "stuck, not thinking" in detail


def test_the_watchdog_gives_a_completed_run_its_grace(tmp_path, monkeypatch):
    """PR #580 itself: all 12 done, 2 confirmations, reaped at 62 min. Now it is kept."""
    wf = _judging_run(tmp_path, monkeypatch, run_started_ago=62 * MIN,
                      status=_st(done=12, since_progress=2 * MIN, confirmations=2))
    assert _watch(wf)[:2] == (0, judge.J_RUNNING)


def test_a_status_left_by_a_dead_run_is_not_progress(tmp_path, monkeypatch):
    """⭐ COUNTERWEIGHT: a status whose owner is gone cannot hold the wall open — a 70-min
    run is reaped at the flat wall, as before."""
    wf = _judging_run(tmp_path, monkeypatch, run_started_ago=70 * MIN,
                      status={**_st(done=11, since_progress=60), "pid": 2 ** 22 + 7,
                              "started": 1.0, "start_ticks": 1})
    handed, state, detail = _watch(wf)
    assert (handed, state) == (1, judge.J_CANNOT_VERIFY)
    assert detail == "the judge did not finish in 60min — it is stuck, not thinking"


def test_doctor_does_not_warn_about_an_advancing_run_past_60min():
    """`chela doctor` applies the same rule: an advancing run past 60 min is not overdue."""
    r = {"task_id": "T", "pid": 1, "elapsed": 70 * MIN, "detached": True, "log": None,
         **_st(done=11, since_progress=None), "progress_at": time.time() - 30}
    [f] = runtime_truth._judge_runs_report(None, runtime_truth.observed([r]))
    assert f.level == runtime_truth.OK


# --- the run really publishes what the watchdog reads ----------------------------------------

def test_the_battery_heartbeats_baseline_confirmations_and_consistency(tmp_path):
    """GUARD (wiring): the baseline reports its measured duration, a subset survivor's
    full-suite confirmation is announced before it runs, and the consistency stage too.
    Seen to go red: the `"confirm"` heartbeat deleted from `_measure`."""
    root = sel._repo(tmp_path / "repo")
    events: list[tuple[str, dict]] = []
    report = judge.run_experiments(
        root, sel.TEST_CMD,
        {"experiments": [sel._exp("pkg/widget.py", "return 1", "return 2")]},
        timeout=120, consistency_sample=1,
        heartbeat=lambda event, **info: events.append((event, info)),
    )
    [o] = report.outcomes
    assert o.confirmed_full, o.verdict
    names = [e for e, _ in events]
    assert names[0] == "baseline" and events[0][1]["seconds"] > 0
    assert names.count("confirm") == 2          # the battery's, then the re-run's
    assert "consistency" in names
    assert names.index("consistency") > names.index("confirm")


def test_judge_run_writes_progress_time_and_baseline_to_its_status(tmp_path, monkeypatch):
    """GUARD (wiring): what a REAL `judge_run` writes as each experiment starts carries
    `progress_at` and the baseline's duration — what the watchdog sizes the wall from.
    Seen to go red: `judge_run` stops passing its heartbeat to the battery."""
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    logs = tmp_path / "judge-logs"
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: logs)
    task_id = "abc123"
    repo = _workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp(), _exp(guard="again")]}))
    seen: list[dict] = []
    real_apply = judge._apply_experiments

    def _apply(*a, progress=None, **kw):
        if progress is None:                      # a consistency re-run: not the battery
            return real_apply(*a, **kw)

        def _spy(done, total):
            progress(done, total)
            seen.append(json.loads((logs / f"{task_id}.json").read_text()))
        return real_apply(*a, progress=_spy, **kw)

    monkeypatch.setattr(judge, "_apply_experiments", _apply)
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        judge.judge_run(task_id, exp_file, cleanup=True, detached=True)

    assert seen, "the battery never reported progress"
    assert all(isinstance(s.get("baseline_seconds"), float) and s["baseline_seconds"] > 0
               for s in seen), seen
    assert all(s["progress_at"] >= s["run_started_at"] for s in seen), seen
    assert seen[-1]["phase"] == "finishing" and seen[0]["phase"] == "battery"
