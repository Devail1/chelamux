"""⏱️ CMX-397 phase 1: `chela task-finished` and the nightly sweep are timed per phase, the
way a judge round is (CMX-196) — measure only, nothing they run or decide changes."""
from __future__ import annotations

import contextlib
import json
import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import dispatcher, judge, judge_timing, suite_guard, sweep, sweep_nightly

GREEN = judge.SuiteResult(True, 0, 5, 0, 0, "5 passed")


class Clock:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


@pytest.fixture
def chela_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(suite_guard.config, "CHELA_DIR", tmp_path)
    monkeypatch.setattr(suite_guard, "_waited", 0.0)
    return tmp_path


def _wait(seconds: float) -> None:
    """A suite-slot wait, as `suite_guard.slot` books it."""
    suite_guard._waited += seconds


# --- the active timer -------------------------------------------------------------------

def test_within_measures_nothing_without_an_active_timer(chela_dir):
    """The judge never activates a timer: the phases the shared code opens are no-ops there."""
    assert judge_timing.active() is None
    with judge_timing.within("baseline") as entry:
        _wait(5)
    assert entry is None
    judge_timing.note(tier="core")          # no timer: nothing to note on, and no crash


def test_nested_phases_are_named_under_the_open_one_with_their_own_slot_wait(chela_dir):
    clock = Clock()
    timer = judge_timing.RoundTimer(clock=clock)
    with judge_timing.activate(timer):
        assert judge_timing.active() is timer
        _wait(7)                                    # the hand-in's own slot wait
        with judge_timing.within("sweep"):
            clock.t += 10
            with judge_timing.within("baseline"):
                clock.t += 60
                _wait(3)
                with judge_timing.within("flake_rerun"):
                    clock.t += 4
            with judge_timing.within("mutations"):
                clock.t += 20
        judge_timing.note(tier="core", checks=["sweep"])
    assert judge_timing.active() is None
    d = timer.as_dict()
    assert d["phases"] == {
        "sweep": {"seconds": 94.0, "slot_wait": 3.0},
        "sweep.baseline": {"seconds": 64.0, "slot_wait": 3.0},
        "sweep.baseline.flake_rerun": {"seconds": 4.0, "slot_wait": 0.0},
        "sweep.mutations": {"seconds": 20.0, "slot_wait": 0.0},
    }
    assert d["slot_wait"] == 10.0
    assert d["total"] == 94.0
    assert timer.meta == {"tier": "core", "checks": ["sweep"]}


def test_activate_resets_and_credits_the_wait_even_when_the_block_raises(chela_dir):
    timer = judge_timing.RoundTimer(clock=Clock())
    with pytest.raises(SystemExit):
        with judge_timing.activate(timer):
            with judge_timing.within("sweep"):
                _wait(12)
                raise SystemExit(1)
    assert judge_timing.active() is None
    d = timer.as_dict()
    assert d["slot_wait"] == 12.0
    assert d["phases"]["sweep"]["slot_wait"] == 12.0


def test_a_judge_round_records_no_hand_in_phase(chela_dir):
    """The shared code's `within` lands nothing on the judge's own RoundTimer."""
    timer = judge_timing.RoundTimer(clock=Clock())
    with timer.phase("baseline"):
        with judge_timing.within("flake_rerun"):
            pass
    assert set(timer.as_dict()["phases"]) == {"baseline"}


# --- records -----------------------------------------------------------------------------

def test_record_hand_in_appends_one_line_per_attempt(chela_dir):
    timer = judge_timing.RoundTimer(clock=Clock())
    timer.meta.update(tier="core")
    assert judge_timing.record_hand_in("CMX-9", timer, "refused", now=0)
    assert judge_timing.record_hand_in("CMX-9", timer, "passed", now=60)
    path = chela_dir / "hand-in-timings" / "CMX-9.jsonl"
    recs = [json.loads(line) for line in path.read_text().splitlines()]
    assert [(r["task_id"], r["at"], r["outcome"], r["tier"]) for r in recs] == [
        ("CMX-9", "1970-01-01T00:00:00Z", "refused", "core"),
        ("CMX-9", "1970-01-01T00:01:00Z", "passed", "core")]
    assert recs[0]["timings"]["phases"] == {}
    assert judge_timing.load_hand_ins() == recs


def test_hand_in_path_sanitises_the_task_id(chela_dir):
    assert judge_timing.hand_in_path("../x y") == chela_dir / "hand-in-timings" / ".._x_y.jsonl"
    assert judge_timing.hand_in_path("") == chela_dir / "hand-in-timings" / "_.jsonl"


def test_an_unwritable_record_is_logged_never_raised(chela_dir):
    (chela_dir / "hand-in-timings").write_text("a file where the directory should be")
    timer = judge_timing.RoundTimer(clock=Clock())
    assert judge_timing.record_hand_in("CMX-9", timer, "passed") is False


def test_a_record_that_cannot_be_built_is_logged_never_raised(chela_dir, monkeypatch):
    timer = judge_timing.RoundTimer(clock=Clock())
    monkeypatch.setattr(timer, "as_dict", lambda **k: (_ for _ in ()).throw(RuntimeError("x")))
    assert judge_timing.record_hand_in("CMX-9", timer, "passed") is False
    assert not (chela_dir / "hand-in-timings").exists()


def test_read_records_skips_torn_and_non_object_lines(tmp_path):
    p = tmp_path / "t.jsonl"
    assert judge_timing.read_records(p) == []
    p.write_text('{"a": 1}\n{torn\n[1, 2]\n{"b": 2}\n')
    assert judge_timing.read_records(p) == [{"a": 1}, {"b": 2}]


# --- the summary -------------------------------------------------------------------------

def _rec(task, at, outcome, total, sweep_s=None, stale=False):
    phases = {"sweep": {"seconds": sweep_s, "slot_wait": 0.0}} if sweep_s is not None else {}
    return {"task_id": task, "at": at, "outcome": outcome, "stale": stale,
            "timings": {"phases": phases, "slot_wait": 1.0, "run": total, "total": total}}


def test_summarize_hand_ins_sums_every_attempt_of_a_task():
    recs = [_rec("A", "2026-11-17T00:00:00Z", "refused", 600, 500),
            _rec("A", "2026-11-17T01:00:00Z", "passed", 900, 800),
            _rec("B", "2026-11-17T02:00:00Z", "passed", 300, 200),
            _rec("C", "2026-11-17T02:00:00Z", "passed", 9999, stale=True),
            {"task_id": "D", "at": "2026-11-17T02:00:00Z", "outcome": "passed"}]
    s = judge_timing.summarize_hand_ins(recs)
    assert s["rounds"] == 3 and s["untimed"] == 1
    assert s["outcomes"] == {"refused": 1, "passed": 2}
    assert s["tasks"] == 2
    assert s["phases"]["task_total"] == {"n": 2, "median": 300.0, "p90": 1500.0, "max": 1500.0}
    assert s["phases"]["sweep"]["n"] == 3
    assert s["phases"]["total"]["median"] == 600.0


def test_summarize_hand_ins_honours_since():
    now = 1_763_400_000.0      # 2025-11-17T17:20:00Z
    recs = [_rec("A", "2025-11-17T17:00:00Z", "passed", 100),
            _rec("A", "2025-11-10T00:00:00Z", "refused", 900),
            _rec("B", "not a stamp", "passed", 50)]
    s = judge_timing.summarize_hand_ins(recs, since=3600, now=now)
    assert s["rounds"] == 1 and s["tasks"] == 1
    assert s["outcomes"] == {"passed": 1}
    assert s["phases"]["task_total"]["median"] == 100.0


def test_summary_lines_name_the_kind_and_the_outcomes():
    recs = [_rec("A", "2026-11-17T00:00:00Z", "refused", 600, 500),
            _rec("A", "2026-11-17T01:00:00Z", "passed", 900, 800)]
    lines = judge_timing.summary_lines(judge_timing.summarize_hand_ins(recs), "over all",
                                       "hand-in timings", "attempt")
    assert lines[0] == "⏱️ hand-in timings over all — 2 timed attempt(s)"
    assert lines[1] == "  outcomes: passed 1, refused 1 — over 1 task(s)"
    assert lines[-1].split()[0] == "task_total"
    assert lines[2] == f"  {'phase':14} {'n':>4} {'median':>9} {'p90':>9} {'max':>9}"
    wide = judge_timing.summary_lines(
        {"rounds": 1, "untimed": 0, "phases": {"self_check.baseline.flake_rerun": {
            "n": 1, "median": 5.0, "p90": 5.0, "max": 5.0}}}, "x")
    assert wide[1] == "  phase" + " " * 26 + "    n    median       p90       max"
    assert wide[2] == "  self_check.baseline.flake_rerun    1        5s        5s        5s"


def test_a_note_never_overwrites_the_records_own_fields(chela_dir):
    timer = judge_timing.RoundTimer(clock=Clock())
    timer.meta.update(task_id="X", outcome="passed", at="never", tier="core")
    judge_timing.record_hand_in("CMX-9", timer, "refused", now=0)
    [rec] = judge_timing.read_records(judge_timing.hand_in_path("CMX-9"))
    assert (rec["task_id"], rec["outcome"], rec["at"], rec["tier"]) == (
        "CMX-9", "refused", "1970-01-01T00:00:00Z", "core")
    # The judge's table is unchanged.
    assert judge_timing.summary_lines({"rounds": 0, "untimed": 0, "phases": {}}, "x") == [
        "⏱️ judge timings x — 0 timed round(s)"]


def test_hand_in_line():
    d = {"phases": {"sweep": {"seconds": 125.0, "slot_wait": 0.0},
                    "sweep.baseline": {"seconds": 65.0, "slot_wait": 30.0}},
         "slot_wait": 42.0, "total": 3700.0}
    assert judge_timing.hand_in_line(d, "passed") == (
        "⏱️ hand-in (passed): total 1h 01m · slot wait 42s · sweep 2m 05s · "
        "sweep.baseline 1m 05s (30s slot wait)")


# --- the shared code opens its phases ----------------------------------------------------

def test_the_self_check_times_its_baseline_and_experiments(chela_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(judge, "provision_suite_env", lambda wt: "")
    monkeypatch.setattr(judge, "ts_unparseable", lambda wt, items: "")
    monkeypatch.setattr(judge, "run_baseline", lambda *a, **k: (_wait(2), (GREEN, None, ""))[1])
    monkeypatch.setattr(judge, "_apply_experiments", lambda *a, **k: (_wait(1), ([], ""))[1])
    timer = judge_timing.RoundTimer()
    with judge_timing.activate(timer), judge_timing.within("self_check"):
        judge._self_check(tmp_path, "true", {"experiments": [{"guard": "g"}, {"guard": "h"}]},
                          timeout=30, select_tests=True,
                          budget=sweep.SweepBudget(900, 12), started=judge._now())
    phases = timer.as_dict()["phases"]
    assert phases["self_check.baseline"]["slot_wait"] == 2.0
    assert phases["self_check.experiments"]["slot_wait"] == 2.0      # one per experiment
    assert phases["self_check"]["slot_wait"] == 4.0


def test_the_sweep_times_its_baseline_and_mutations(chela_dir, monkeypatch, tmp_path):
    mut = SimpleNamespace(key="f.py:1")
    monkeypatch.setattr(sweep, "recover", lambda wt: ("", ""))
    monkeypatch.setattr(sweep, "collect_mutations", lambda wt, base: ([mut], ""))
    monkeypatch.setattr(sweep, "unsupported_files", lambda wt, base: ([], ""))
    monkeypatch.setattr(sweep, "generated_files", lambda wt, base: ([], ""))
    monkeypatch.setattr(sweep, "prioritise", lambda muts: muts)
    monkeypatch.setattr(sweep, "_start_guardian", lambda wt: None)
    monkeypatch.setattr(judge, "run_baseline", lambda *a, **k: (_wait(5), (GREEN, None, ""))[1])
    # CMX-455: the battery stamps each result with its seconds — a real result, not a str.
    monkeypatch.setattr(sweep, "_run_one", lambda *a, **k: (
        _wait(3), (sweep.SweepResult(mut, "killed"), ""))[1])
    timer = judge_timing.RoundTimer()
    with judge_timing.activate(timer), judge_timing.within("sweep"):
        sweep._sweep(tmp_path, "origin/dev", "true", timeout=30, select_tests=True,
                     progress=None, budget=sweep.SweepBudget(900, 12), started=sweep._now(),
                     provision=False)
    phases = timer.as_dict()["phases"]
    assert phases["sweep.baseline"]["slot_wait"] == 5.0
    assert phases["sweep.mutations"]["slot_wait"] == 3.0


def test_a_red_baselines_rerun_is_its_own_phase(chela_dir, monkeypatch, tmp_path):
    red = judge.SuiteResult(True, 1, 4, 1, 0, "FAILED tests/t.py::test_a - boom")
    monkeypatch.setattr(judge, "run_suite",
                        lambda *a, **k: (_wait(2), judge.SuiteResult(True, 0, 1, 0, 0, ""))[1])
    timer = judge_timing.RoundTimer()
    flakes: list[str] = []
    with judge_timing.activate(timer), judge_timing.within("baseline"):
        out = judge._rerun_failed_once(tmp_path, "pytest -q", 30, red, None, flakes,
                                       named=["tests/t.py::test_a"])
    assert out.green and flakes == ["tests/t.py::test_a"]       # behaviour unchanged
    assert timer.as_dict()["phases"]["baseline.flake_rerun"]["slot_wait"] == 2.0


def test_the_standard_hand_in_times_lint_and_suite(chela_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    wt = tmp_path / "wt"
    wt.mkdir()
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\nhand_in:\n  lint_cmd: \"ruff\"\njudge:\n"
                  "  test_cmd: \"pytest\"\n---\nbody\n")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES ('t9', ?, 'x', 'running', 'cmx-9', ?, 'b', '2026-11-17T00:00:00+00:00', "
            "1, 9)", (str(wf), str(wt)))
        conn.commit()
    waits = {"ruff": 1, "pytest": 6}
    monkeypatch.setattr(judge, "run_suite", lambda cmd, *a, **k: (_wait(waits[cmd]), GREEN)[1])
    timer = judge_timing.RoundTimer()
    with judge_timing.activate(timer):
        assert dispatcher.verify_standard_hand_in("t9")["ok"]
    phases = timer.as_dict()["phases"]
    assert phases["lint"]["slot_wait"] == 1.0
    assert phases["suite"]["slot_wait"] == 6.0


# --- `chela task-finished` ---------------------------------------------------------------

@pytest.fixture
def tf_run(chela_dir, monkeypatch, tmp_path):
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", "0")
    monkeypatch.setattr(suite_guard, "_thread_waited", {})
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    wt = tmp_path / "CMX-9"
    wt.mkdir()
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\njudge:\n  test_cmd: \"true\"\n---\nbody\n")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES ('t9', ?, 'x', 'running', 'cmx-9', ?, 'b', '2026-11-17T00:00:00+00:00', "
            "1, 9)", (str(wf), str(wt)))
        conn.commit()
    for name, value in (("check_base_sync", {"ok": True, "since": "abc"}),
                        ("check_changelog_fragments", {"ok": True}),
                        ("check_hand_in_size", {"ok": True}),
                        ("check_review_section", {"ok": True}),
                        ("hand_in_tier", "core"),
                        ("ensure_pushed_head", {"ok": True}),
                        ("request_task_finished", {"ok": True, "task_id": "t9"})):
        monkeypatch.setattr(dispatcher, name, lambda *a, _v=value, **k: _v)

    @contextlib.contextmanager
    def battery(task_id):
        _wait(7)                       # queued for the hand-in's one slot
        yield
    monkeypatch.setattr(dispatcher, "hand_in_battery", battery)
    return wt


SWEPT = {"ok": True, "survivors": [], "mutations": 0, "killed": 0, "cannot_verify": "",
         "recovered": ""}


def _task_finished(*flags: str) -> None:
    from chela import main
    with patch.object(sys, "argv", ["chela", "task-finished", "t9", *flags]):
        main.main()


def _only_record(chela_dir: Path) -> dict:
    recs = judge_timing.read_records(chela_dir / "hand-in-timings" / "t9.jsonl")
    assert len(recs) == 1
    return recs[0]


def test_a_passing_hand_in_records_every_phase(tf_run, chela_dir, monkeypatch, capsys):
    monkeypatch.setattr(dispatcher, "verify_self_check",
                        lambda *a: (_wait(4), {"ok": True, "blocking": 0, "cannot_verify": "",
                                               "outcomes": []})[1])
    monkeypatch.setattr(dispatcher, "verify_sweep", lambda *a: (_wait(20), SWEPT)[1])
    monkeypatch.setattr(dispatcher, "reconcile_base_move", lambda *a: (_wait(2), {"ok": True})[1])
    _task_finished("--self-check-experiments", "e.json")
    rec = _only_record(chela_dir)
    assert rec["outcome"] == "passed"
    assert rec["tier"] == "core" and rec["checks"] == ["self_check", "sweep"]
    phases = rec["timings"]["phases"]
    assert {k: v["slot_wait"] for k, v in phases.items()} == {
        "self_check": 4.0, "sweep": 20.0, "remerge": 2.0}
    assert rec["timings"]["slot_wait"] == 33.0
    out = capsys.readouterr().out
    assert "⏱️ hand-in (passed): total " in out
    assert "slot wait 33s" in out and "sweep 0s (20s slot wait)" in out
    assert "self_check 0s (4s slot wait)" in out and "remerge 0s (2s slot wait)" in out


def test_a_refused_hand_in_is_recorded_too(tf_run, chela_dir, monkeypatch, capsys):
    """A refusal held the slot all the same — the flake-restart cost is exactly this."""
    monkeypatch.setattr(dispatcher, "verify_sweep",
                        lambda *a: (_wait(9), {"ok": False, "error": "red baseline"})[1])
    with pytest.raises(SystemExit) as e:
        _task_finished("--no-new-guards")
    assert e.value.code == 1
    rec = _only_record(chela_dir)
    assert rec["outcome"] == "refused"
    assert rec["checks"] == ["sweep"]
    assert rec["timings"]["phases"]["sweep"]["slot_wait"] == 9.0
    assert rec["timings"]["slot_wait"] == 16.0
    assert "⏱️ hand-in (refused)" in capsys.readouterr().out


def test_a_hand_in_that_never_got_the_slot_is_recorded(tf_run, chela_dir, monkeypatch):
    def battery(task_id):
        _wait(50)
        raise suite_guard.SlotTimeout("no suite slot came free in 9s")
    monkeypatch.setattr(dispatcher, "hand_in_battery", battery)
    with pytest.raises(SystemExit):
        _task_finished("--no-new-guards")
    rec = _only_record(chela_dir)
    assert rec["outcome"] == "slot_timeout"
    assert rec["timings"]["slot_wait"] == 50.0 and rec["timings"]["phases"] == {}


def test_a_crashing_check_is_recorded_as_an_error(tf_run, chela_dir, monkeypatch):
    monkeypatch.setattr(dispatcher, "verify_sweep",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        _task_finished("--no-new-guards")
    assert _only_record(chela_dir)["outcome"] == "error"


def test_a_cheap_refusal_before_the_slot_records_nothing(tf_run, chela_dir, monkeypatch):
    monkeypatch.setattr(dispatcher, "check_review_section",
                        lambda *a: {"ok": False, "error": "no ## Review"})
    with pytest.raises(SystemExit):
        _task_finished("--no-new-guards")
    assert not (chela_dir / "hand-in-timings").exists()


# --- the nightly sweep -------------------------------------------------------------------

def test_record_timings_appends_the_nights_phases(chela_dir):
    wf = SimpleNamespace(path=Path("/repo/WORKFLOW.md"))
    timer = judge_timing.RoundTimer(clock=Clock())
    with judge_timing.activate(timer), judge_timing.within("sweep"):
        _wait(30)
    assert sweep_nightly.record_timings(wf, {"at": 0, "status": "clean"}, timer, chela_dir)
    assert sweep_nightly.timings_path(chela_dir) == \
        chela_dir / "sweep-nightly" / "timings.jsonl"
    [rec] = judge_timing.read_records(sweep_nightly.timings_path(chela_dir))
    assert rec["workflow"] == "/repo/WORKFLOW.md" and rec["status"] == "clean"
    assert rec["at"] == "1970-01-01T00:00:00Z"
    assert rec["timings"]["phases"]["sweep"]["slot_wait"] == 30.0
    assert rec["timings"]["slot_wait"] == 30.0


def test_record_timings_never_raises(chela_dir):
    wf = SimpleNamespace(path=Path("/repo/WORKFLOW.md"))
    timer = judge_timing.RoundTimer(clock=Clock())
    assert sweep_nightly.record_timings(wf, {}, timer, chela_dir) is False   # no "at"


# --- `chela judge timings --hand-in / --nightly` -----------------------------------------

def _judge_timings(*flags: str) -> None:
    from chela import main
    with patch.object(sys, "argv", ["chela", "judge", "timings", *flags]):
        main.main()


@pytest.mark.parametrize("flags, title", [
    ((), "⏱️ judge timings over all recorded rounds — 0 timed round(s)"),
    (("--hand-in",), "⏱️ hand-in timings over all recorded rounds — 1 timed attempt(s)"),
    (("--nightly",), "⏱️ nightly sweep timings over all recorded rounds — 1 timed run(s)"),
])
def test_judge_timings_reads_the_kind_asked_for(chela_dir, monkeypatch, capsys, flags, title):
    monkeypatch.setattr(judge, "load_all_private", lambda: [])
    judge_timing.append_record(judge_timing.hand_in_path("A"),
                               _rec("A", "2026-11-17T00:00:00Z", "passed", 60, 30))
    judge_timing.append_record(sweep_nightly.timings_path(),
                               _rec("n", "2026-11-17T00:00:00Z", "clean", 90, 80))
    _judge_timings(*flags)
    assert capsys.readouterr().out.splitlines()[0] == title


def test_judge_timings_kinds_are_exclusive(chela_dir, capsys):
    with pytest.raises(SystemExit) as e:
        _judge_timings("--hand-in", "--nightly")
    assert e.value.code == 2


def test_judge_timings_hand_in_json(chela_dir, capsys):
    judge_timing.append_record(judge_timing.hand_in_path("A"),
                               _rec("A", "2026-11-17T00:00:00Z", "passed", 60, 30))
    _judge_timings("--hand-in", "--json")
    s = json.loads(capsys.readouterr().out)
    assert s["outcomes"] == {"passed": 1} and s["phases"]["task_total"]["median"] == 60.0


def test_a_nightly_run_records_its_provision_and_sweep_phases(chela_dir, monkeypatch, tmp_path):
    # CMX-455: the night reads `sweep.per_pr_sample` — every key unset here.
    wf = SimpleNamespace(path=tmp_path / "WORKFLOW.md", get=lambda *k, default=None: default)
    monkeypatch.setattr(judge, "judge_enabled", lambda wf: True)
    monkeypatch.setattr(judge, "judge_suite_config",
                        lambda wf: SimpleNamespace(test_cmd="true", suite_timeout_seconds=30,
                                                   select_tests=True))
    monkeypatch.setattr(judge, "provision_suite_env", lambda wt: (_wait(3), "")[1])
    monkeypatch.setattr(sweep_nightly, "blamed_lines", lambda wt, std: ({}, {}))
    monkeypatch.setattr(sweep_nightly, "prune_evidence", lambda d: [])
    monkeypatch.setattr(sweep_nightly, "nightly_budget", lambda wf: sweep.SweepBudget(900, 12))
    report = SimpleNamespace(
        _with=lambda verdict: [], results=[], measured=0, battery_seconds=0.0, per_hour=0.0,
        unsupported=[], baseline_failures=[], baseline_flakes=[], baseline_rerun=None,
        kept="", cannot_verify="the baseline is red")
    monkeypatch.setattr(sweep, "sweep", lambda *a, **k: (_wait(40), report)[1])

    def fake_run(wf, now, chela_dir, filer, result, budget=None):
        sweep_nightly._sweep_and_file(wf, tmp_path, "abc123", "origin/dev", [], chela_dir,
                                      filer, result, [])
    monkeypatch.setattr(sweep_nightly, "_run", fake_run)
    monkeypatch.setattr(sweep_nightly, "notify", lambda result: None)
    monkeypatch.setattr(sweep_nightly, "log_quiet", lambda result: None)
    result = sweep_nightly.run(wf, now=0, chela_dir=chela_dir)
    assert result["status"] == sweep_nightly.CANNOT_VERIFY
    [rec] = judge_timing.read_records(sweep_nightly.timings_path(chela_dir))
    assert rec["status"] == sweep_nightly.CANNOT_VERIFY
    assert {k: v["slot_wait"] for k, v in rec["timings"]["phases"].items()} == {
        "provision": 3.0, "sweep": 40.0}
    assert rec["timings"]["slot_wait"] == 43.0
    assert judge_timing.active() is None


# --- the phase lists, pinned against INDEPENDENT literals --------------------------------
#
# ⛔ Never derive these from `judge_timing.HAND_IN_PHASES` / `NIGHTLY_PHASES`: a test that
# reads the tuple it guards agrees with any corruption of it. Every phase is seeded at once,
# so dropping ANY name from a tuple drops its row from what the user is shown.

HAND_IN = ("lint", "suite",
           "self_check", "self_check.baseline", "self_check.baseline.flake_rerun",
           "self_check.experiments",
           "sweep", "sweep.baseline", "sweep.baseline.flake_rerun", "sweep.mutations",
           "remerge")
NIGHTLY = ("provision", "sweep", "sweep.baseline", "sweep.baseline.flake_rerun",
           "sweep.mutations")
JUDGE = ("setup", "reprovision", "provision", "baseline", "visible", "held_out",
         "consistency")
WHOLE = ("slot_wait", "run", "total")


def _seeded(names, at="2026-11-17T00:00:00Z", **extra):
    """One record holding every phase in ``names`` plus one no table lists (``bogus``)."""
    phases = {n: {"seconds": 60.0 * (i + 1), "slot_wait": 0.0}
              for i, n in enumerate((*names, "bogus"))}
    return {"at": at, **extra,
            "timings": {"phases": phases, "slot_wait": 5.0, "run": 900.0, "total": 999.0}}


def _table_rows(lines: list[str]) -> list[str]:
    header = next(i for i, line in enumerate(lines) if line.split()[:2] == ["phase", "n"])
    return [line.split()[0] for line in lines[header + 1:]]


def test_the_hand_in_summary_shows_every_hand_in_phase_and_nothing_else():
    s = judge_timing.summarize_hand_ins([_seeded(HAND_IN, task_id="A", outcome="passed")])
    assert set(s["phases"]) == {*HAND_IN, *WHOLE, "task_total"}
    rows = _table_rows(judge_timing.summary_lines(s, "x", "hand-in timings", "attempt"))
    assert sorted(rows) == sorted({*HAND_IN, *WHOLE, "task_total"})


def test_the_nightly_summary_shows_every_nightly_phase_and_nothing_else(chela_dir, capsys):
    judge_timing.append_record(sweep_nightly.timings_path(), _seeded(NIGHTLY, status="clean"))
    _judge_timings("--nightly")
    rows = _table_rows(capsys.readouterr().out.splitlines())
    assert sorted(rows) == sorted({*NIGHTLY, *WHOLE})


def test_the_judge_summary_keeps_its_own_phases(chela_dir, monkeypatch, capsys):
    monkeypatch.setattr(judge, "load_all_private", lambda: [_seeded(JUDGE)])
    _judge_timings()
    rows = _table_rows(capsys.readouterr().out.splitlines())
    assert sorted(rows) == sorted({*JUDGE, *WHOLE})


def test_the_hand_in_line_names_every_phase_in_run_order():
    line = judge_timing.hand_in_line(_seeded(HAND_IN)["timings"], "passed")
    named = [part.split()[0] for part in line.split(" · ")[2:]]
    assert named == list(HAND_IN)
    assert "bogus" not in line


def test_a_record_exactly_at_the_since_edge_counts_in_every_figure():
    now = 1_763_400_000.0      # 2025-11-17T17:20:00Z
    rec = _rec("A", "2025-11-17T16:20:00Z", "passed", 100)
    s = judge_timing.summarize_hand_ins([rec], since=3600, now=now)
    assert s["rounds"] == 1 and s["tasks"] == 1 and s["outcomes"] == {"passed": 1}
    assert s["phases"]["task_total"]["n"] == 1


# --- every timings file is private to the user -------------------------------------------

@pytest.fixture
def open_umask():
    """umask 0, so the mode is exactly what the code asked for — a 077 umask would hide a
    0644 (or 0755) behind the mask."""
    old = os.umask(0)
    try:
        yield
    finally:
        os.umask(old)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_append_record_writes_0600_in_a_0700_directory(tmp_path, open_umask):
    path = tmp_path / "new-dir" / "t.jsonl"
    assert judge_timing.append_record(path, {"a": 1})
    assert _mode(path) == 0o600
    assert _mode(path.parent) == 0o700


def test_every_timings_writer_writes_a_private_file(chela_dir, open_umask):
    timer = judge_timing.RoundTimer(clock=Clock())
    assert judge_timing.record_hand_in("CMX-9", timer, "passed", now=0)
    wf = SimpleNamespace(path=Path("/repo/WORKFLOW.md"))
    assert sweep_nightly.record_timings(wf, {"at": 0, "status": "clean"}, timer, chela_dir)
    for path in (judge_timing.hand_in_path("CMX-9"), sweep_nightly.timings_path(chela_dir)):
        assert _mode(path) == 0o600, path
    assert _mode(judge_timing.hand_in_dir()) == 0o700
