"""⚖️🔀 CMX-334 — task-finished accepts a hand-in whose base moved mid-check, without a
second battery: only the related tests, and experiments only on the overlapping files."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import basesync, dispatcher, handinmove, judge
from tests.test_judge_sweep import TEST_CMD, _git, _run_row, _task_finished

PR = "def f(x):\n    return x\n\n\n\n\ndef g(x):\n    return x\n"
REAL_BASE_SYNC = True
GREEN = judge.SuiteResult(True, 0, 1, 0, 0, "")


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """``wt`` is a run on ``feature`` (edits p.py's first function) off a bare ``origin``;
    ``seed`` moves dev. Returns ``(seed, wt, since)`` — ``since`` is dev before any move."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    remote, seed, wt = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "wt"
    _git(tmp_path, "init", "-q", "--bare", "-b", "dev", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    _advance(seed, {"p.py": PR, "README.md": "r\n", "tests/test_other.py": "X = 1\n"})
    _git(tmp_path, "clone", "-q", "-b", "dev", str(remote), str(wt))
    for key, val in (("user.email", "t@t"), ("user.name", "t")):
        _git(wt, "config", key, val)
    _git(wt, "checkout", "-qb", "feature")
    (wt / "p.py").write_text(PR.replace("return x\n\n", "return x + 1\n\n", 1))
    _git(wt, "commit", "-qam", "feature")
    _run_row(tmp_path, wt)
    return seed, wt, _git(seed, "rev-parse", "HEAD")


def _advance(seed: Path, files: dict[str, str]) -> None:
    for name, body in files.items():
        (seed / name).parent.mkdir(parents=True, exist_ok=True)
        (seed / name).write_text(body)
        _git(seed, "add", name)
    _git(seed, "commit", "-qm", "dev")
    _git(seed, "push", "-q", "origin", "dev")


def _reconcile(repo, experiments=None):
    wf = dispatcher.load_workflow(repo[1].parent / "WORKFLOW.md")
    return handinmove.reconcile(repo[1], wf, "W.md", "dev", repo[2], experiments)


def test_a_non_overlapping_move_mid_check_is_accepted_with_no_second_battery(repo, capsys):
    """The acceptance case, end to end: dev moves WHILE the sweep runs."""
    def sweep_while_dev_moves(task_id):
        _advance(repo[0], {"README.md": "moved\n"})
        return {"ok": True, "skipped": "x"}

    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
            patch.object(dispatcher, "verify_sweep", side_effect=sweep_while_dev_moves) as sw, \
            patch.object(dispatcher, "verify_self_check") as sc, \
            patch.object(judge, "run_suite") as suite:
        assert _task_finished() is None
    assert sw.call_count == 1 and not sc.called and not suite.called   # no second battery
    assert _git(repo[1], "rev-parse", "HEAD^2") == _git(repo[0], "rev-parse", "HEAD")
    record = json.loads(dispatcher._task_finished_request_path(repo[1]).read_text())["base_sync"]
    moved = {"from": repo[2][:12], "to": _git(repo[0], "rev-parse", "HEAD")[:12],
             "dev_files": 1, "overlap": [], "tests": "0 file(s)", "experiments_rerun": 0}
    assert record == {"base": "dev", "moved": moved}
    assert json.loads(dispatcher._push_request_path(repo[1]).read_text())["pr_title"] is None
    assert "no PR file overlapped" in capsys.readouterr().out


def test_an_unmoved_base_changes_nothing(repo):
    with patch.object(judge, "run_suite") as suite:
        assert _reconcile(repo) == {"ok": True, "moved": None}
    assert not suite.called and not dispatcher._push_request_path(repo[1]).exists()


def test_an_overlap_re_runs_only_the_overlapping_experiments(repo, tmp_path):
    _advance(repo[0], {"p.py": PR.replace("def g(x):\n    return x", "def g(x):\n    return -x"),
                       "q.py": "Q = 1\n"})
    exps = tmp_path / "e.json"
    mine = {"guard": "f", "file": "./p.py", "before": "x + 1", "after": "x - 1"}
    other = {"guard": "q", "file": "q.py", "before": "1", "after": "2"}
    exps.write_text(json.dumps({"experiments": [other, mine]}))
    seen = []

    def self_check(worktree, path, **kw):
        seen.append(json.loads(Path(path).read_text())["experiments"])
        return {"ok": True, "blocking": 0, "cannot_verify": "", "unrun": []}

    with patch.object(judge, "run_suite", return_value=GREEN), \
            patch.object(judge, "run_self_check", side_effect=self_check):
        out = _reconcile(repo, str(exps))
    assert seen == [[mine]]
    assert out["moved"]["overlap"] == ["p.py"] and out["moved"]["experiments_rerun"] == 1
    assert "def g(x):\n    return -x" in (repo[1] / "p.py").read_text()


@pytest.mark.parametrize("reply, said", [
    ({"ok": True, "blocking": 1, "cannot_verify": "", "unrun": []}, "1 SURVIVED"),
    ({"ok": True, "blocking": 0, "cannot_verify": "hung", "unrun": []}, "CANNOT VERIFY: hung"),
    ({"ok": True, "blocking": 0, "cannot_verify": "", "unrun": [{}]}, "1 UNRUN"),
    ({"ok": False, "error": "boom"}, "could not re-run them — boom")])
def test_an_overlap_experiment_that_does_not_hold_refuses(repo, tmp_path, reply, said):
    _advance(repo[0], {"p.py": PR.replace("def g(x):\n    return x", "def g(x):\n    return -x")})
    exps = tmp_path / "e.json"
    exps.write_text(json.dumps({"experiments": [{"guard": "f", "file": "p.py",
                                                 "before": "x + 1", "after": "x - 1"}]}))
    with patch.object(judge, "run_self_check", return_value=reply), \
            patch.object(judge, "run_suite", return_value=GREEN):
        out = _reconcile(repo, str(exps))
    assert out["ok"] is False and said in out["error"] and "touched this PR's p.py" in out["error"]


@pytest.mark.parametrize("result, ok", [
    (GREEN, True), (judge.SuiteResult(True, 1, 0, 1, 0, "bad"), False)])
def test_the_related_tests_run_and_a_red_one_refuses(repo, result, ok):
    _advance(repo[0], {"lib.py": "L = 1\n", "tests/test_lib.py": "import lib\n"})
    with patch.object(judge, "run_suite", return_value=result) as suite:
        out = _reconcile(repo)
    assert suite.call_args.args == (f"{TEST_CMD} tests/test_lib.py", repo[1])
    assert suite.call_args.kwargs == {"timeout": 120}
    assert out["ok"] is ok
    assert ok or "is red (exit 1):\nbad" in out["error"]


def test_a_code_file_no_test_observes_runs_the_full_suite(repo):
    _advance(repo[0], {"lib.py": "L = 1\n"})
    with patch.object(judge, "run_suite", return_value=GREEN) as suite:
        assert _reconcile(repo)["moved"]["tests"] == "full suite"
    assert suite.call_args.args[0] == TEST_CMD


def test_a_conflicting_move_and_an_unreadable_base_refuse(repo, tmp_path):
    _advance(repo[0], {"p.py": "conflict\n"})
    out = _reconcile(repo)
    assert out["ok"] is False and "CONFLICTS with origin/dev in 1 file(s)" in out["error"]
    _git(repo[1], "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    assert _reconcile(repo)["error"].startswith("could not re-check the base after the checks")


def test_no_suite_configured_refuses(repo):
    _advance(repo[0], {"lib.py": "L = 1\n"})
    with patch.object(judge, "judge_test_cmd", return_value=""):
        assert "sets no `judge.test_cmd`" in _reconcile(repo)["error"]


def test_the_cli_refuses_a_failed_reconcile(repo, capsys):
    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
            patch.object(dispatcher, "verify_sweep", return_value={"ok": True, "skipped": "x"}), \
            patch.object(dispatcher, "reconcile_base_move",
                         return_value={"ok": False, "error": "nope"}), \
            patch.object(dispatcher, "request_task_finished") as req:
        assert _task_finished() == 1
    assert "task-finished: 🔀 nope" in capsys.readouterr().out and not req.called


@pytest.mark.parametrize("replies, error", [
    ([(0, "", ""), (0, "0", ""), (0, "t\n", ""), (128, "", "bad")], "could not read origin/dev"),
    ([(0, "", ""), (0, "1", ""), (0, "t\n", ""), (0, "abc\n", ""), (1, "", "x")],
     "could not list what origin/dev changed since s")])
def test_moved_fails_closed(tmp_path, replies, error):
    side = [subprocess.CompletedProcess([], rc, o, e) for rc, o, e in replies]
    with patch.object(basesync.subprocess, "run", side_effect=side), \
            pytest.raises(RuntimeError, match=error):
        basesync.moved(tmp_path, "dev", "s")


def test_render_names_the_move():
    rec = {"base": "dev", "moved": {"from": "a", "to": "b", "tests": "2 file(s)",
                                    "overlap": ["p.py", "q.py"]}}
    assert basesync.render(rec) == ("🔀 origin/dev moved mid-check (a → b); merged in, related "
                                    "tests re-run (2 file(s)), experiments re-run on the "
                                    "overlap: p.py, q.py.")
    assert basesync.render({}) == ""


def test_a_pending_push_request_is_kept_and_a_missing_one_filed(repo):
    _advance(repo[0], {"README.md": "moved\n"})
    dispatcher.request_push("cmx-88", pr_title="T", pr_body="B")
    out = dispatcher.reconcile_base_move("cmx-88", repo[2], None)
    assert out["ok"] and out["record"]["moved"]["dev_files"] == 1
    assert json.loads(dispatcher._push_request_path(repo[1]).read_text())["pr_title"] == "T"
    assert dispatcher.reconcile_base_move("cmx-1", "s", None)["error"] == \
        "no run with a worktree on record for cmx-1"


def test_a_failed_merge_or_an_unreadable_pr_refuses(repo):
    _advance(repo[0], {"README.md": "moved\n"})
    (repo[1] / "README.md").write_text("local edit\n")
    out = _reconcile(repo)
    assert out["error"].startswith("`git merge origin/dev` failed: ")
    assert _git(repo[1], "rev-parse", "HEAD^@") == _git(repo[1], "rev-parse", "HEAD~1")
    with patch.object(judge, "_changed_files", return_value=None):
        assert _reconcile(repo)["error"] == "could not list this PR's own files"


def test_unlisted_tests_run_the_full_suite(tmp_path):
    failed = subprocess.CompletedProcess([], 128, "", "no repo")
    with patch.object(basesync, "_git", return_value=failed) as git:
        assert handinmove.related_tests(tmp_path, ["README.md"]) is None
    git.assert_called_once_with(tmp_path, "ls-files", "--", "*.py")


def test_no_experiment_on_the_overlap_runs_none(repo, tmp_path):
    _advance(repo[0], {"p.py": PR.replace("def g(x):\n    return x", "def g(x):\n    return -x")})
    exps = tmp_path / "e.json"
    exps.write_text(json.dumps({"experiments": [{"guard": "q", "file": "q.py",
                                                 "before": "1", "after": "2"}]}))
    with patch.object(judge, "run_suite", return_value=GREEN), \
            patch.object(judge, "run_self_check") as sc:
        out = _reconcile(repo, str(exps))
    assert out["ok"] and out["moved"]["experiments_rerun"] == 0 and not sc.called


def test_a_push_that_cannot_be_requested_refuses(repo):
    _advance(repo[0], {"README.md": "moved\n"})
    with patch.object(dispatcher, "request_push", return_value={"ok": False, "error": "x"}):
        out = dispatcher.reconcile_base_move("cmx-88", repo[2], None)
    assert out == {"ok": False, "error": "merged origin/dev, but could not request its push: x"}


@pytest.mark.parametrize("tier, passed", [("core", "e.json"), ("standard", None)])
def test_only_a_tier_that_runs_experiments_re_runs_them(repo, tier, passed):
    import sys

    from chela import main

    ok = {"ok": True, "blocking": 0, "cannot_verify": "", "outcomes": [], "unrun": []}
    argv = ["chela", "task-finished", "cmx-88", "--self-check-experiments", "e.json"]
    with patch.object(sys, "argv", argv), \
            patch.object(dispatcher, "hand_in_tier", return_value=tier), \
            patch.object(dispatcher, "verify_self_check", return_value=ok), \
            patch.object(dispatcher, "verify_sweep", return_value={"ok": True, "skipped": "x"}), \
            patch.object(dispatcher, "verify_standard_hand_in", return_value={"ok": True}), \
            patch.object(dispatcher, "reconcile_base_move", return_value={"ok": True}) as rec:
        main.main()
    rec.assert_called_once_with("cmx-88", repo[2], passed)


@pytest.mark.parametrize("files, want", [
    (["README.md", "docs/a.md", "changelog.d/x.md"], []),
    (["uv.lock"], None), (["pyproject.toml"], None), (["WORKFLOW.md"], None)])
def test_prose_selects_no_tests_and_any_other_non_code_file_runs_the_full_suite(
        tmp_path, files, want):
    listed = subprocess.CompletedProcess([], 0, "", "")
    with patch.object(basesync, "_git", return_value=listed):
        assert handinmove.related_tests(tmp_path, files) == want
