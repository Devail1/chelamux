"""⏱️ CMX-196 — every judge round records where its time went.

The fake round below drives the REAL `run_experiments` / `run_suite` / `_apply_each` path
with an injected clock: only the suite process, the slot queue and provisioning are faked,
each advancing the clock by a known amount, so every per-phase number is exact.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from chela import config, dispatcher, judge, judge_timing, procguard, suite_guard, workflow
from chela import main as chela_main
from tests.test_judge import (
    FAKE_GUARD_TEST,
    GLYPH_AFTER,
    GLYPH_BEFORE,
    _project,
    _run_row,
    _workflow_repo,
)

HUE_BEFORE = '    hue = "green" if state == "on" else "grey"'
HUE_AFTER = '    hue = "grey"'

SLOT_WAIT = 5.0          # every suite run queues this long for its slot
BASELINE_RUN = 600.0     # the unmutated suite
MUTATED_RUN = 100.0      # a suite under a mutation
PROVISION = 30.0


@pytest.fixture(autouse=True)
def _own_chela_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-dir")


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def _fake_box(monkeypatch, clock: FakeClock, original: str) -> list[float]:
    """The slot queue, the suite process and provisioning, each costing known clock time.
    A run is RED (KILLED) when the glyph line is gone, GREEN otherwise."""
    runs: list[float] = []

    @contextmanager
    def slot(cwd, on_wait=None, max_wait=None):
        clock.t += SLOT_WAIT
        yield

    @contextmanager
    def bounded(cmd, env=None):
        yield cmd, (lambda rc: "")

    def run_shell_group(cmd, cwd, env, timeout, frozen=None):
        text = (judge.Path(cwd) / "guard.py").read_text()
        took = BASELINE_RUN if text == original else MUTATED_RUN
        clock.t += took
        runs.append(took)
        if GLYPH_BEFORE not in text:
            return subprocess.CompletedProcess(cmd, 1, "1 failed, 1 passed in 1s\n", "")
        return subprocess.CompletedProcess(cmd, 0, "2 passed in 1s\n", "")

    def provision(worktree, timeout=600.0):
        clock.t += PROVISION
        return ""

    monkeypatch.setattr(suite_guard, "slot", slot)
    monkeypatch.setattr(suite_guard, "bounded", bounded)
    monkeypatch.setattr(procguard, "run_shell_group", run_shell_group)
    monkeypatch.setattr(judge, "provision_suite_env", provision)
    return runs


def _experiments() -> dict:
    return {"experiments": [
        {"guard": "the glyph cue", "file": "guard.py", "before": GLYPH_BEFORE,
         "after": GLYPH_AFTER},                                         # visible, KILLED
        {"guard": "the hue", "file": "guard.py", "before": HUE_BEFORE,
         "after": HUE_AFTER},                                           # visible, SURVIVED
        {"guard": "the glyph off-state", "file": "guard.py", "before": GLYPH_BEFORE,
         "after": '    glyph = "-"', "held_out": True},                 # held-out, KILLED
    ]}


def _fake_round(tmp_path, monkeypatch, *, select_tests=False):
    root = _project(tmp_path / "repo")
    clock = FakeClock()
    runs = _fake_box(monkeypatch, clock, (root / "guard.py").read_text())
    timer = judge_timing.RoundTimer(clock=clock)
    report = judge.run_experiments(
        root, "pytest -q", _experiments(), timeout=60, select_tests=select_tests,
        consistency_sample=1, timer=timer,
    )
    return report, timer, runs, clock


# --- ⭐ the fake round: exact per-phase numbers -------------------------------------------

def test_a_fake_round_with_an_injected_clock_records_every_phase_exactly(tmp_path, monkeypatch):
    report, timer, runs, _ = _fake_round(tmp_path, monkeypatch)
    assert [o.verdict for o in report.outcomes] == [judge.KILLED, judge.SURVIVED, judge.KILLED]
    assert report.consistency["sampled"] == 1
    assert runs == [600.0, 100.0, 100.0, 100.0, 100.0]       # baseline, 3 + 1 re-run
    assert report.timings is timer

    t = timer.as_dict()
    assert t["phases"] == {
        "provision": {"seconds": 30.0, "slot_wait": 0.0},
        "baseline": {"seconds": 605.0, "slot_wait": 5.0},
        "visible": {"seconds": 210.0, "slot_wait": 10.0, "count": 2},
        "held_out": {"seconds": 105.0, "slot_wait": 5.0, "count": 1},
        "consistency": {"seconds": 105.0, "slot_wait": 5.0, "count": 1},
    }
    assert t["slot_wait"] == 25.0
    assert t["run"] == 1055.0 and t["total"] == 1055.0      # no setup known ⇒ total = run
    assert t["experiments"] == [
        {"stage": "experiments", "held_out": False, "seconds": 105.0, "slot_wait": 5.0,
         "selected": None, "confirmed_full": False, "verdict": "KILLED"},
        {"stage": "experiments", "held_out": False, "seconds": 105.0, "slot_wait": 5.0,
         "selected": None, "confirmed_full": False, "verdict": "SURVIVED"},
        {"stage": "experiments", "held_out": True, "seconds": 105.0, "slot_wait": 5.0,
         "selected": None, "confirmed_full": False, "verdict": "KILLED"},
        {"stage": "consistency", "held_out": False, "seconds": 105.0, "slot_wait": 5.0,
         "selected": None, "confirmed_full": False, "verdict": "SURVIVED"},
    ]
    assert t["load"] is None


def test_NEGATIVE_CONTROL_a_timer_outside_the_round_measures_nothing(tmp_path, monkeypatch):
    """The same box, but the timer is NOT the one the round ran under: every number the
    fake round asserts must come from the wiring, not from the timer alone."""
    root = _project(tmp_path / "repo")
    clock = FakeClock()
    _fake_box(monkeypatch, clock, (root / "guard.py").read_text())
    bystander = judge_timing.RoundTimer(clock=clock)
    report = judge.run_experiments(root, "pytest -q", _experiments(), timeout=60,
                                   select_tests=False, consistency_sample=1)
    assert report.timings is not bystander
    t = bystander.as_dict()
    assert t["phases"] == {} and t["experiments"] == [] and t["slot_wait"] == 0.0
    # …while the round's own (default) timer, on the real clock, still has every phase.
    assert report.timings is not None
    assert set(report.timings.as_dict()["phases"]) == {
        "provision", "baseline", "visible", "held_out", "consistency"}


def test_the_timer_is_dropped_from_the_run_context_after_the_round(tmp_path, monkeypatch):
    _fake_round(tmp_path, monkeypatch)
    assert "timer" not in judge._run_ctx
    # A later suite run (a self-check, a sweep) is never credited to a finished round.
    clock = FakeClock()
    timer = judge_timing.RoundTimer(clock=clock)
    with timer.phase("baseline"):
        judge.run_suite("pytest -q", tmp_path / "repo", 60)
    assert timer.as_dict()["slot_wait"] == 0.0


def test_the_run_context_timer_is_dropped_even_when_the_round_raises(tmp_path, monkeypatch):
    root = _project(tmp_path / "repo")
    monkeypatch.setattr(judge, "_run_experiments",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        judge.run_experiments(root, "pytest -q", _experiments(), timeout=60)
    assert "timer" not in judge._run_ctx


def test_a_selected_subset_and_its_full_confirmation_are_one_experiment_row(
    tmp_path, monkeypatch,
):
    root = _project(tmp_path / "repo")
    clock = FakeClock()
    runs = _fake_box(monkeypatch, clock, (root / "guard.py").read_text())
    sel = judge.judge_select.Selection(nodes=["test_guard.py::test_x"], expected=7,
                                       why="the tests that import guard.py")

    class Selector:
        def select(self, file):
            return sel

    monkeypatch.setattr(judge, "run_baseline", lambda wt, cmd, timeout, scratch, **k: (
        judge.run_suite(cmd, wt, timeout), Selector(), "per-mutation selection"))
    timer = judge_timing.RoundTimer(clock=clock)
    judge.run_experiments(root, "pytest -q", {"experiments": [_experiments()["experiments"][1]]},
                          timeout=60, consistency_sample=0, timer=timer)
    assert runs == [600.0, 100.0, 100.0]            # baseline, subset, full confirmation
    (row,) = timer.as_dict()["experiments"]
    assert row["selected"] == 7 and row["confirmed_full"] is True
    assert row["seconds"] == 210.0 and row["slot_wait"] == 10.0
    assert row["verdict"] == judge.SURVIVED


# --- the round record --------------------------------------------------------------------

def test_record_private_writes_timings_with_the_rounds_total(tmp_path, monkeypatch):
    report, timer, _, _ = _fake_round(tmp_path, monkeypatch)
    report.total_seconds = 4000.0
    judge.record_private("t1", report, _experiments())
    rec = judge.load_private("t1")[-1]
    assert rec["timings"]["total"] == 4000.0 and rec["timings"]["run"] == 1055.0
    assert rec["timings"]["phases"]["baseline"] == {"seconds": 605.0, "slot_wait": 5.0}


def test_an_untimed_report_records_timings_as_null():
    judge.record_private("t2", judge.Report(cannot_verify="x"), {})
    assert judge.load_private("t2")[-1]["timings"] is None


def test_judge_run_records_setup_and_the_concurrent_load(tmp_path):
    task_id = "tm1"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    launched = (datetime.now(timezone.utc) - timedelta(seconds=300)).isoformat()
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, judge_state=judge.J_RUNNING, judge_started_at=launched)
        _run_row(conn, repo, "busy-agent", status="running", task_number=2,
                 branch_name="b2", window_name="w2")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [
        {"guard": "g", "file": "guard.py", "before": GLYPH_BEFORE, "after": GLYPH_AFTER}]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        judge.judge_run(task_id, exp_file, cleanup=False)
    t = judge.load_private(task_id)[-1]["timings"]
    assert t["load"] == {"agents": 1, "judges": 1}
    assert 300 <= t["phases"]["setup"]["seconds"] < 400
    assert {"provision", "baseline", "visible"} <= set(t["phases"])
    assert t["total"] >= t["phases"]["setup"]["seconds"] + t["run"] - 1


def test_judge_run_times_a_reprovisioned_worktree(tmp_path, monkeypatch):
    task_id = "tm2"
    repo = _workflow_repo(tmp_path, task_id, FAKE_GUARD_TEST)
    wt = judge.judge_worktree_path(workflow.load_workflow(repo / "WORKFLOW.md"), task_id)
    judge.shutil.rmtree(wt)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id)
    monkeypatch.setattr(judge, "_reprovision_worktree", lambda *a: "could not rebuild")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps(_experiments()))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        judge.judge_run(task_id, exp_file, cleanup=False)
    t = judge.load_private(task_id)[-1]["timings"]
    assert "reprovision" in t["phases"] and "baseline" not in t["phases"]
    assert "setup" not in t["phases"]               # no judge_started_at on this row


def test_concurrent_load_counts_agents_and_judges(monkeypatch):
    monkeypatch.setattr(dispatcher, "hold_inflight",
                        lambda: {"agents": [{}, {}, {}], "judges": [{}, {}]})
    assert judge.concurrent_load() == {"agents": 3, "judges": 2}


def test_concurrent_load_that_cannot_be_read_is_unknown_not_zero(monkeypatch, caplog):
    def boom():
        raise RuntimeError("database is locked")
    monkeypatch.setattr(dispatcher, "hold_inflight", boom)
    assert judge.concurrent_load() is None
    assert "could not read the concurrent load" in caplog.text


# --- chela judge show --------------------------------------------------------------------

def test_judge_show_renders_an_old_row_without_timings_and_a_new_one_with_them(
    tmp_path, monkeypatch, capsys,
):
    store = judge.heldout_store_path("cmx-9")
    store.parent.mkdir(parents=True)
    old = {"at": "2026-10-01T00:00:00Z", "task_id": "cmx-9", "state": "blocked",
           "visible": {"total": 1, "survived": 1}, "held_out": {}, "consistency": {}}
    store.write_text(json.dumps(old) + "\n")
    report, timer, _, _ = _fake_round(tmp_path, monkeypatch)
    timer.load = {"agents": 3, "judges": 2}
    judge.record_private("cmx-9", report, _experiments())
    chela_main.cmd_judge_show(argparse.Namespace(run="cmx-9", held_out=False))
    out = capsys.readouterr().out
    assert "⏱️ no timings recorded (a round from before CMX-196)" in out
    assert ("⏱️ total 17m 35s · provision 30s · baseline 10m 05s (5s slot wait) · "
            "visible 2× 3m 30s (10s slot wait) · held-out 1× 1m 45s (5s slot wait) · "
            "consistency 1× 1m 45s (5s slot wait) · slot wait 25s") in out
    assert "load at start: 2 judge(s) · 3 agent(s) running (this judge included)" in out
    assert "exp  2 visible          SURVIVED  full suite, 1m 45s (5s slot wait)" in out
    assert "exp  3 held-out         KILLED    full suite" in out
    assert "exp  4 visible re-run   SURVIVED" in out


def test_round_lines_name_selected_tests_and_omit_a_sub_second_wait():
    lines = judge_timing.round_lines({
        "phases": {}, "slot_wait": 0.4, "total": 60, "experiments": [
            {"stage": "experiments", "held_out": False, "seconds": 61, "slot_wait": 0.4,
             "selected": 12, "confirmed_full": True, "verdict": "SURVIVED"},
            {"stage": "experiments", "held_out": False, "seconds": 9, "slot_wait": 1.0,
             "selected": 3, "confirmed_full": False, "verdict": "KILLED"},
        ]})
    assert lines[0] == "⏱️ total 1m 00s · slot wait 0s"
    assert lines[1] == ("   exp  1 visible          SURVIVED  12 selected test(s) → full suite, "
                        "1m 01s")
    assert lines[2] == "   exp  2 visible          KILLED    3 selected test(s), 9s (1s slot wait)"


# --- chela judge timings -----------------------------------------------------------------

def test_percentile_is_nearest_rank():
    assert judge_timing.percentile([4.0, 1.0, 3.0, 2.0], 50) == 2.0
    assert judge_timing.percentile([float(i) for i in range(1, 11)], 90) == 9.0
    assert judge_timing.percentile([7.0], 90) == 7.0


def _rec(at: str, baseline: float, *, stale=False, timed=True) -> dict:
    rec = {"at": at, "stale": stale}
    if timed:
        rec["timings"] = {"phases": {"baseline": {"seconds": baseline, "slot_wait": 0},
                                     "visible": {"seconds": baseline * 2}},
                          "experiments": [{"seconds": baseline / 2}], "slot_wait": 1.0,
                          "run": baseline * 3, "total": baseline * 4}
    return rec


def test_summarize_takes_median_and_p90_over_the_window_only():
    now = datetime(2026, 10, 28, tzinfo=timezone.utc).timestamp()
    records = [
        _rec("2026-10-27T00:00:00Z", 100), _rec("2026-10-26T00:00:00Z", 300),
        _rec("2026-10-25T00:00:00Z", 200),
        _rec("2026-10-01T00:00:00Z", 9999),                  # outside 7d
        _rec("2026-10-27T00:00:00Z", 8888, stale=True),      # a stale round is no round
        _rec("not a time", 7777),                            # unknown age ⇒ not "recent"
        _rec("2026-10-27T00:00:00Z", 0, timed=False),        # before CMX-196
    ]
    s = judge_timing.summarize(records, since=7 * 86400, now=now)
    assert s["rounds"] == 3 and s["untimed"] == 1
    assert s["phases"]["baseline"] == {"n": 3, "median": 200.0, "p90": 300.0, "max": 300.0}
    assert s["phases"]["visible"]["median"] == 400.0
    assert s["phases"]["experiment"] == {"n": 3, "median": 100.0, "p90": 150.0, "max": 150.0}
    assert s["phases"]["slot_wait"]["n"] == 3
    assert s["phases"]["total"]["max"] == 1200.0
    every = judge_timing.summarize(records, since=None, now=now)
    assert every["rounds"] == 5 and every["phases"]["baseline"]["max"] == 9999.0


def test_summary_lines_rank_the_biggest_phase_first_and_the_whole_round_last():
    s = judge_timing.summarize([_rec("2026-10-27T00:00:00Z", 100)])
    lines = judge_timing.summary_lines(s, "over the last 7d")
    assert lines[0] == "⏱️ judge timings over the last 7d — 1 timed round(s)"
    names = [line.split()[0] for line in lines[2:]]
    assert names == ["visible", "baseline", "slot_wait", "run", "total", "experiment"]
    assert lines[2] == "  visible           1    3m 20s    3m 20s    3m 20s"


def test_summary_lines_say_how_many_rounds_were_untimed():
    s = judge_timing.summarize([_rec("2026-10-27T00:00:00Z", 1, timed=False)])
    assert judge_timing.summary_lines(s, "x") == [
        "⏱️ judge timings x — 0 timed round(s), 1 untimed (before CMX-196)"]


def test_cmd_judge_timings_reads_every_task_and_honours_since(capsys):
    recent = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for task, base in (("a", 100), ("b", 300)):
        path = judge.heldout_store_path(task)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(_rec(recent, base)) + "\n"
                        + json.dumps(_rec("2020-01-01T00:00:00Z", 5)) + "\n")
    chela_main.cmd_judge_timings(argparse.Namespace(since="7d", json=True))
    out = json.loads(capsys.readouterr().out)
    assert out["rounds"] == 2
    assert out["phases"]["baseline"] == {"n": 2, "median": 100.0, "p90": 300.0, "max": 300.0}
    chela_main.cmd_judge_timings(argparse.Namespace(since=None, json=False))
    text = capsys.readouterr().out
    assert "over all recorded rounds — 4 timed round(s)" in text


def test_cmd_judge_timings_refuses_a_bad_duration(capsys):
    with pytest.raises(SystemExit) as e:
        chela_main.cmd_judge_timings(argparse.Namespace(since="soon", json=False))
    assert e.value.code == 2
    assert "not a duration" in capsys.readouterr().err


def test_the_timings_subcommand_is_wired(monkeypatch):
    seen = []
    monkeypatch.setattr(chela_main, "cmd_judge_timings", seen.append)
    monkeypatch.setattr("sys.argv", ["chela", "judge", "timings", "--since", "7d"])
    chela_main.main()
    assert seen and seen[0].since == "7d" and seen[0].json is False


def test_duration_formats_unknown_seconds_minutes_and_hours():
    assert judge_timing.duration(None) == "?"
    assert judge_timing.duration(42.4) == "42s"
    assert judge_timing.duration(125) == "2m 05s"
    assert judge_timing.duration(3725) == "1h 02m"


def test_round_lines_skip_a_malformed_experiment_row():
    lines = judge_timing.round_lines({"total": None, "experiments": ["junk"]})
    assert lines == ["⏱️ total ? · slot wait ?"]


@pytest.mark.parametrize("raised, credited", [
    (suite_guard.SlotTimeout("no suite slot came free in 50s"), 50.0),   # the run WAS the wait
    (PermissionError("the slot dir could not be made"), 0.0),          # never queued at all
])
def test_a_slot_wait_that_times_out_is_still_credited(tmp_path, monkeypatch, raised, credited):
    clock = FakeClock()

    @contextmanager
    def slot(cwd, on_wait=None, max_wait=None):
        clock.t += 50.0             # both fail 50s in; only a SlotTimeout spent it queued
        raise raised
        yield

    monkeypatch.setattr(suite_guard, "slot", slot)
    timer = judge_timing.RoundTimer(clock=clock)
    judge._run_ctx["timer"] = timer
    try:
        with timer.phase("baseline"):
            result = judge.run_suite("pytest -q", tmp_path, 60)
    finally:
        judge._run_ctx.pop("timer", None)
    assert not result.ok and "could not be run" in result.detail
    t = timer.as_dict()
    assert t["slot_wait"] == credited
    assert t["phases"]["baseline"] == {"seconds": 50.0, "slot_wait": credited}
