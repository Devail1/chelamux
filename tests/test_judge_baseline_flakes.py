"""🎲 CMX-195 — a judge round must not be burned by a flake, a timeout, or a finished run.

254 rounds over 14 days, 21 of them ``cannot_verify``: one flaky test red on the base before
any mutation, the same "NO experiments" retried back-to-back, a suite KILLED at its timeout
under load reported as "NOT GREEN", and a judge that outlived its (override-merged) run
still asking the operator for "a human look".
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

import test_judge as tj
from chela import dispatcher, judge

TEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'

FLAKY_ONCE = '''\
from pathlib import Path

def test_flaky():
    flag = Path({flag!r})
    if not flag.exists():
        flag.touch()
        assert False, "red the first time only"
'''
ALWAYS_RED = "def test_flaky():\n    assert False, 'red every time'\n"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _project(tmp_path: Path, extra: str) -> Path:
    root = tmp_path / "wt"
    root.mkdir()
    (root / "test_extra.py").write_text(extra)
    return tj._project(root)


def test_a_test_red_once_then_green_is_a_recorded_flake_and_the_round_continues(tmp_path):
    wt = _project(tmp_path, FLAKY_ONCE.format(flag=str(tmp_path / "flag")))
    report = judge.run_experiments(wt, TEST_CMD, {"experiments": [tj._exp()]}, timeout=120)
    assert report.cannot_verify == ""
    assert report.baseline_flakes == ["test_extra.py::test_flaky"]
    assert [o.verdict for o in report.outcomes] == [judge.KILLED]   # the round RAN
    assert len(report.suite_failures) == 1                         # the flake's red run, kept


def test_a_test_red_twice_is_still_red_never_a_flake(tmp_path):
    """⛔ The negative control: re-running once must never let a red base read as green."""
    wt = _project(tmp_path, ALWAYS_RED)
    report = judge.run_experiments(wt, TEST_CMD, {"experiments": [tj._exp()]}, timeout=120)
    assert "NOT GREEN" in report.cannot_verify and "test_extra.py::test_flaky" in report.cannot_verify
    assert report.baseline_flakes == [] and report.outcomes == []
    assert len(report.suite_failures) == 2                         # first run + the re-run


def _red(failed=1, errors=0, passed=5, tail="FAILED t.py::test_a - boom\n"):
    return judge.SuiteResult(True, 1, passed, failed, errors, tail)


@pytest.mark.parametrize("baseline, reruns", [
    (_red(), 1),                                         # every failure named ⇒ re-run once
    (_red(errors=1), 0),                                 # an ERROR is not a named failure
    (_red(failed=2), 0),                                 # more failures than names
    (_red(passed=0), 0),                                 # nothing passed: the suite never ran
    (_red(tail="1 failed\n"), 0),                        # no names at all
])
def test_only_a_fully_named_red_baseline_is_re_run(monkeypatch, tmp_path, baseline, reruns):
    calls: list[tuple] = []
    green = judge.SuiteResult(True, 0, 1, 0, 0, "")
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: (calls.append(a), green)[1])
    flakes: list[str] = []
    out = judge._rerun_failed_once(tmp_path, "pytest -q", 60, baseline, [], flakes)
    assert len(calls) == reruns
    if reruns:
        assert calls == [("pytest -q t.py::test_a", tmp_path, 60)]   # ONLY the failing test
        assert out.green and flakes == ["t.py::test_a"]
    else:
        assert out is baseline and flakes == []


def test_a_rerun_that_ran_fewer_tests_than_failed_is_not_green(monkeypatch, tmp_path):
    empty = judge.SuiteResult(True, 0, 0, 0, 0, "")             # exit 0, nothing ran
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: empty)
    flakes: list[str] = []
    assert not judge._rerun_failed_once(tmp_path, "pytest", 60, _red(), [], flakes).green
    assert flakes == []


# --- ⏳ a timeout is its own unknown, never "NOT GREEN" -----------------------------------


@pytest.mark.parametrize("cmd, says, never", [
    (f'"{sys.executable}" -c "import time; time.sleep(30)"', "TIMEOUT", "NOT GREEN"),
    (f'"{sys.executable}" -c "raise SystemExit(1)"', "NOT GREEN", "TIMEOUT"),   # control: RED
])
def test_a_suite_killed_at_its_timeout_says_TIMEOUT_not_red(tmp_path, monkeypatch, cmd, says,
                                                            never):
    wt = tj._project(tmp_path / "wt")
    runs: list[str] = []
    real = judge.run_suite
    monkeypatch.setattr(judge, "run_suite", lambda c, *a, **k: (runs.append(c), real(c, *a, **k))[1])
    report = judge.run_experiments(wt, cmd, {"experiments": [tj._exp()]}, timeout=2)
    assert says in report.cannot_verify and never not in report.cannot_verify
    if says == "TIMEOUT":
        assert "did not finish in 2s" in report.cannot_verify and "under load" in report.cannot_verify
        assert runs == [cmd]                 # no second full run on the same budget


def test_a_timed_out_baseline_is_not_re_run_plain_under_the_same_budget(tmp_path, monkeypatch):
    killed = judge.SuiteResult(False, -1, 0, 0, 0, "", "did not finish", timed_out=True)
    runs: list[str] = []
    monkeypatch.setattr(judge, "run_suite", lambda c, *a, **k: (runs.append(c), killed)[1])
    monkeypatch.setattr(judge, "_has_pytest_cov", lambda wt: True)
    monkeypatch.setattr(judge.judge_select, "load_cached_coverage", lambda sha: None)
    baseline, selector, _ = judge.run_baseline(tmp_path, "pytest -q", 60, tmp_path)
    assert baseline.timed_out and selector is None and len(runs) == 1


# --- 🔕 a judge that outlives its run records, and does not notify ------------------------


@pytest.mark.parametrize("moved_to, state_written", [
    ("done", False), ("closed", False),
    ("awaiting_review", True),                           # control: a live run IS told
])
def test_a_run_finished_mid_judge_gets_no_cannot_verify_on_its_row(tmp_path, monkeypatch,
                                                                    moved_to, state_written):
    def finishes_mid_round(*a, **k):
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET status=? WHERE task_id='abc123'", (moved_to,))
            conn.commit()
        return judge.Report(cannot_verify="a flake")
    monkeypatch.setattr(judge, "run_experiments", finishes_mid_round)
    result, run, _ = tj._judge_run(tmp_path, tj.REAL_GUARD_TEST, {"experiments": [tj._exp()]})
    assert (run["judge_state"] == judge.J_CANNOT_VERIFY) is state_written
    assert ("moot" in result) is not state_written
    recorded = judge.load_private("abc123")
    assert recorded[-1]["cannot_verify"] == "a flake"    # the round is still on the record
