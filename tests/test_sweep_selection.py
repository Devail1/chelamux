"""⚡ CMX-145 — ``chela judge sweep`` measures each mutation on the tests SELECTED for its file.

The sweep called ``judge._measure`` without ``pinned``, whose default was ``None`` ("replay a
full-suite selection"), so every mutation ran the FULL suite and the 900 s budget reached ~1
of 10. These run a real sweep over a throwaway repo with a real baseline JUnit report, and
record every suite command the sweep runs.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, judge, sweep

# No path argument: node ids appended to it must NARROW the run (a trailing `tests` would
# collect everything whatever is appended).
TEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider -p no:xdist'
SUBSET_CMD = f"{TEST_CMD} tests/test_feat.py"

BASE = "def old(x):\n    return x\n"
BRANCH = BASE + "\n\ndef new(x):\n    if x == 3:\n        return 'three'\n    return 'other'\n"

CATCHES = "from feat import new\n\n\ndef test_three():\n    assert new(3) == 'three'\n"
# Observes feat.py (imports it) but cannot tell the mutation apart.
BLIND = "from feat import new\n\n\ndef test_callable():\n    assert callable(new)\n"
# Catches the mutation, but selection cannot see it observes feat.py: the import is dynamic
# and the module's name is never spelled out.
HIDDEN = ("import importlib\n\n\ndef test_three_hidden():\n"
          "    new = importlib.import_module('fe' + 'at').new\n"
          "    assert new(3) == 'three' and new(4) == 'other'\n")
UNRELATED = "def test_nothing():\n    assert True\n"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def _repo(root: Path, tests: dict[str, str]) -> Path:
    root.mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "feat.py").write_text(BASE)
    (root / "tests" / "test_nothing.py").write_text(UNRELATED)
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    _git(root, "update-ref", "refs/remotes/origin/dev", _git(root, "rev-parse", "HEAD"))
    _git(root, "checkout", "-qb", "feature")
    (root / "feat.py").write_text(BRANCH)
    for name, body in tests.items():
        (root / "tests" / name).write_text(body)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "feature")
    return root


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela-dir"))
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _sweep(root: Path, monkeypatch, *, select_tests: bool = True):
    """Run a real sweep; return (report, the suite commands each MUTATION ran)."""
    real, cmds = judge.run_suite, []

    def spy(test_cmd, cwd, timeout=judge.SUITE_TIMEOUT_SECONDS, extra_env=None):
        cmds.append(test_cmd)
        return real(test_cmd, cwd, timeout, extra_env)

    monkeypatch.setattr(judge, "run_suite", spy)
    report = sweep.sweep(root, "origin/dev", TEST_CMD, timeout=120, select_tests=select_tests)
    assert report.cannot_verify == "", report.cannot_verify
    return report, cmds[1:]     # [0] is the baseline


def _by_kind(report) -> dict[str, sweep.SweepResult]:
    return {r.mutation.kind: r for r in report.results}


def test_a_selected_kill_is_a_kill_and_never_runs_the_full_suite(tmp_path, monkeypatch):
    root = _repo(tmp_path / "r", {"test_feat.py": CATCHES})
    report, cmds = _sweep(root, monkeypatch)
    res = _by_kind(report)["if-false"]
    assert res.status == judge.KILLED
    assert res.measured_by.startswith("1 selected test(s), ")
    assert res.full_suite == ""
    assert report.as_dict()["full_suite"] == []
    # Every mutation ran ONLY the selected file — never the full suite.
    assert cmds and set(cmds) == {SUBSET_CMD}


def test_a_selected_survivor_is_confirmed_on_the_full_suite_before_it_is_reported(
        tmp_path, monkeypatch):
    root = _repo(tmp_path / "r", {"test_feat.py": BLIND})
    report, cmds = _sweep(root, monkeypatch)
    res = _by_kind(report)["if-false"]
    assert res.status == judge.SURVIVED
    assert res.measured_by.startswith("1 selected test(s) green → confirmed on the full suite")
    assert cmds[:2] == [SUBSET_CMD, TEST_CMD]


def test_a_subset_survivor_the_full_suite_kills_is_killed_not_a_survivor(tmp_path,
                                                                         monkeypatch):
    root = _repo(tmp_path / "r", {"test_feat.py": BLIND, "test_hidden.py": HIDDEN})
    report, cmds = _sweep(root, monkeypatch)
    res = _by_kind(report)["if-false"]
    assert res.status == judge.KILLED
    assert res.measured_by.startswith("1 selected test(s) green → confirmed on the full suite")
    assert cmds[:2] == [SUBSET_CMD, TEST_CMD]
    assert report.survivors == []


@pytest.mark.parametrize("select_tests, why", [
    (True, "no test in the baseline observes it — full suite"),
    (False, "no per-mutation selection for this sweep"),
])
def test_a_full_suite_fallback_is_named_per_mutation(tmp_path, monkeypatch, select_tests, why):
    root = _repo(tmp_path / "r", {})
    report, cmds = _sweep(root, monkeypatch, select_tests=select_tests)
    assert report.results and cmds == [TEST_CMD] * len(report.results)
    for r in report.results:
        assert r.full_suite == why
        assert r.measured_by.startswith("full suite, ")
    rows = report.as_dict()["full_suite"]
    assert [row["full_suite"] for row in rows] == [why] * len(report.results)


# --- the fallback is printed, by both `chela judge sweep` and `task-finished` -----------------

_RESULT = {
    "ok": True, "cannot_verify": "", "recovered": "", "notes": [], "mutations": 1,
    "measured": 1, "killed": 1, "survivors": [], "unparseable": [], "not_applied": [],
    "invalid": [], "unrun": [], "seconds": 4.0, "exhausted": "", "unsupported": [],
    "selection": "per-mutation selection over the baseline's 9 test(s)",
    "full_suite": [{"key": "feat.py:5", "full_suite": "a conftest.py is imported by every "
                                                      "test under it — full suite"}],
}
_FULL_LINE = ("  [FULL    ] feat.py:5 — ran the full suite: a conftest.py is imported by every "
              "test under it — full suite")
_SEL_LINE = "  selection: per-mutation selection over the baseline's 9 test(s)"


def _cli(monkeypatch, result):
    from chela import main

    monkeypatch.setattr(sweep, "run_sweep", lambda cwd, **kw: result)
    with patch.object(sys, "argv", ["chela", "judge", "sweep", "--cwd", "."]):
        try:
            main.main()
        except SystemExit as e:
            return e.code
    return None


def test_cli_names_each_full_suite_fallback(monkeypatch, capsys):
    assert _cli(monkeypatch, dict(_RESULT)) is None
    out = capsys.readouterr().out.splitlines()
    assert _FULL_LINE in out and _SEL_LINE in out


def test_cli_prints_no_fallback_lines_when_every_mutation_was_selected(monkeypatch, capsys):
    assert _cli(monkeypatch, dict(_RESULT, full_suite=[])) is None
    out = capsys.readouterr().out
    assert "[FULL" not in out and "selection:" not in out


def test_task_finished_names_each_full_suite_fallback(capsys):
    from chela import main

    with patch.object(dispatcher, "verify_sweep", return_value=dict(_RESULT)), \
         patch.object(dispatcher, "check_no_new_guards", return_value=False), \
         patch.object(dispatcher, "request_task_finished",
                      return_value={"ok": True, "task_id": "cmx-88"}), \
         patch.object(sys, "argv", ["chela", "task-finished", "cmx-88", "--no-new-guards"]):
        try:
            main.main()
        except SystemExit as e:
            assert not e.code
    out = capsys.readouterr().out.splitlines()
    assert _FULL_LINE in out and _SEL_LINE in out
