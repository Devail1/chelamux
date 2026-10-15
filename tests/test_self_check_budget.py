"""⏳ CMX-141 — the self-check has a budget; what it never reached is UNRUN, never a pass."""
from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from chela import dispatcher, judge, main, sweep
from tests.test_judge import REAL_GUARD_TEST, TEST_CMD, _exp, _project
from tests.test_judge_sweep import _run_row



@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


GREEN = judge.SuiteResult(True, 0, 3, 0, 0, "3 passed")


def _stubbed(monkeypatch, step: float = 0.0):
    """No real suite; a SENTINEL selector; each experiment KILLED, advancing a fake clock."""
    clock, calls, selector = {"t": 0.0}, [], object()
    monkeypatch.setattr(judge, "_now", lambda: clock["t"])
    monkeypatch.setattr(judge, "provision_suite_env", lambda wt: "")
    monkeypatch.setattr(judge, "run_baseline", lambda *a, **k: (GREEN, selector, "on"))

    def fake_apply(worktree, test_cmd, items, baseline, timeout, sel=None):
        calls.append(sel)
        clock["t"] += step
        return [judge.Outcome(judge.Experiment.parse(items[0])[0], judge.KILLED, "red")], ""

    monkeypatch.setattr(judge, "_apply_experiments", fake_apply)
    return calls, selector


def _exps(n: int) -> dict:
    return {"experiments": [_exp(guard=f"g{i}") for i in range(n)]}


def test_a_count_budget_runs_part_and_lists_the_rest_unrun(tmp_path):
    """Real suite: 1 of 3 runs (KILLED); the rest — one malformed — are UNRUN, and the result
    is `partial`, never `clean`."""
    root = _project(tmp_path / "repo", guard_test=REAL_GUARD_TEST)
    exp_path = tmp_path / "e.json"
    exp_path.write_text(json.dumps({"experiments": [_exp(), _exp(guard="g2"), "garbage"]}))
    result = judge.run_self_check(root, exp_path, test_cmd=TEST_CMD, max_experiments=1)
    assert [o["verdict"] for o in result["outcomes"]] == ["KILLED"]
    assert [(u["verdict"], u["file"], u["guard"]) for u in result["unrun"]] == [
        ("UNRUN", "guard.py", "g2"), ("UNRUN", "?", "garbage")]
    assert result["exhausted"] == "over the 1-experiment budget (3 given)"
    assert (result["state"], result["blocking"], result["cannot_verify"]) == ("partial", 0, "")


def test_the_wall_clock_budget_stops_before_the_next_experiment(tmp_path, monkeypatch):
    calls, selector = _stubbed(monkeypatch, step=10.0)
    report = judge.self_check(tmp_path, "t", _exps(5), timeout=1,
                              budget=sweep.SweepBudget(25.0, 12))
    assert [o.experiment.guard for o in report.outcomes] == ["g0", "g1", "g2"]
    assert [(o.experiment.guard, o.verdict, o.blocking) for o in report.unrun] == [
        ("g3", "UNRUN", False), ("g4", "UNRUN", False)]
    assert report.exhausted == "the 25s wall-clock budget ran out after 3 of 5 experiment(s)"
    assert report.unrun[0].reason == f"not run — {report.exhausted}"
    assert calls == [selector] * 3     # ⚡ per-experiment test selection reaches every one


def test_within_budget_everything_runs_and_nothing_is_unrun(tmp_path, monkeypatch):
    calls, _ = _stubbed(monkeypatch, step=1.0)
    report = judge.self_check(tmp_path, "t", _exps(3), timeout=1)
    assert (len(calls), report.unrun, report.exhausted, report.state) == (3, [], "", "clean")


def test_a_contaminated_experiment_stops_the_run(tmp_path, monkeypatch):
    calls, _ = _stubbed(monkeypatch)
    monkeypatch.setattr(judge, "_apply_experiments",
                        lambda *a, **k: calls.append(1) or ([], "guard.py NOT restored"))
    report = judge.self_check(tmp_path, "t", _exps(3), timeout=1)
    assert (report.cannot_verify, len(calls)) == ("guard.py NOT restored", 1)


_WF = "  self_check_budget_seconds: 60\n  self_check_max_experiments: 4\n"


@pytest.mark.parametrize("cli, wf_extra, expected", [
    ({}, "", (900.0, 12)),                                                 # unset → default
    ({}, "  self_check_budget_seconds: 0\n  self_check_max_experiments: 0\n", (900.0, 12)),
    ({}, _WF, (60.0, 4)),
    ({"budget_seconds": 30, "max_experiments": 2}, _WF, (30.0, 2)),
    ({"budget_seconds": 0, "max_experiments": 0}, _WF, (60.0, 4)),
    ({}, "  sweep_budget_seconds: 5\n  sweep_max_mutations: 5\n", (900.0, 12)),  # not ours
])
def test_budget_precedence_flag_then_workflow_then_default(tmp_path, monkeypatch, cli,
                                                           wf_extra, expected):
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\njudge:\n  test_cmd: x\n" + wf_extra + "---\n")
    (tmp_path / "e.json").write_text(json.dumps(_exps(1)))
    seen = {}
    monkeypatch.setattr(judge, "self_check", lambda *a, budget, **k: seen.setdefault(
        "b", (budget.seconds, budget.max_mutations)) and judge.Report())
    result = judge.run_self_check(tmp_path, tmp_path / "e.json", workflow_path=wf,
                                  test_cmd="y", timeout=5, **cli)   # budget still read
    assert seen["b"] == expected
    assert result["budget"] == {"seconds": expected[0], "max_mutations": expected[1]}


def test_judge_self_check_cli_exits_2_on_a_partial_run(tmp_path, monkeypatch, capsys):
    _stubbed(monkeypatch)
    (tmp_path / "e.json").write_text(json.dumps(_exps(2)))
    with patch.object(sys, "argv", ["chela", "judge", "self-check", "--experiments",
                                     str(tmp_path / "e.json"), "--test-cmd", "t", "--cwd",
                                     str(tmp_path), "--max-experiments", "1"]), \
            pytest.raises(SystemExit) as exc:
        main.main()
    out = capsys.readouterr().out
    assert exc.value.code == 2 and "safe to commit" not in out
    assert "[UNRUN   ] guard.py: g1" in out
    assert "CANNOT VERIFY — the self-check budget stopped it: over the 1-experiment" in out


@pytest.mark.parametrize("unrun, shown, hidden", [
    ([{"guard": "g1", "file": "guard.py", "verdict": "UNRUN"}],
     ["[UNRUN   ] guard.py: g1 — CANNOT VERIFY", "self-check PARTIAL: 1 of 2 experiment(s) "
      "measured, none survived; 1 UNRUN = CANNOT VERIFY, not passed (over budget)"],
     "every guard held"),
    ([], ["✓ self-check: every guard held (1 experiment(s))"], "PARTIAL"),
])
def test_task_finished_names_the_unrun_but_does_not_block(tmp_path, capsys, unrun, shown,
                                                          hidden):
    check = {"ok": True, "blocking": 0, "cannot_verify": "", "outcomes": [{"verdict": "KILLED",
             "guard": "g0", "file": "guard.py"}], "unrun": unrun, "exhausted": "over budget"}
    with patch.object(dispatcher, "check_hand_in_size", return_value={"ok": True}), \
            patch.object(dispatcher, "verify_self_check", return_value=check), \
            patch.object(dispatcher, "verify_sweep", return_value={"ok": True, "skipped": "x"}), \
            patch.object(dispatcher, "request_task_finished",
                         return_value={"ok": True, "task_id": "cmx-88"}) as req, \
            patch.object(sys, "argv", ["chela", "task-finished", "cmx-88",
                                        "--self-check-experiments", "e.json"]):
        main.main()
    out = capsys.readouterr().out
    assert all(s in out for s in shown) and hidden not in out
    req.assert_called_once_with("cmx-88", handoff=None)


def test_verify_self_check_passes_the_unrun_through(tmp_path, monkeypatch):
    _run_row(tmp_path, tmp_path)
    monkeypatch.setattr(judge, "run_self_check", lambda *a, **k: {
        "ok": True, "blocking": 0, "cannot_verify": "", "outcomes": [],
        "unrun": [{"guard": "g1"}], "exhausted": "over budget"})
    result = dispatcher.verify_self_check("cmx-88", str(tmp_path / "e.json"))
    assert (result["unrun"], result["exhausted"]) == ([{"guard": "g1"}], "over budget")
