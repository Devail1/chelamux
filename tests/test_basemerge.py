"""🔀 CMX-262 against a real bare origin + clone: clean + green ⇒ pushed; else nothing pushed."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from chela import basemerge, judge

PYTEST = f"{sys.executable} -m pytest -q -p no:cacheprovider"
MOD = "def f():\n    return 1\n\n\ndef g():\n    return 2\n"
TEST = "from mod import f\n\n\ndef test_f():\n    assert f() == 1\n"


def _g(cwd, *argv) -> str:
    return subprocess.run(["git", *argv], cwd=cwd, check=True, capture_output=True,
                          text=True).stdout.strip()


def _commit(cwd, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        (Path(cwd) / rel).parent.mkdir(parents=True, exist_ok=True)
        (Path(cwd) / rel).write_text(text)
    _g(cwd, "add", *files)
    _g(cwd, "commit", "-qm", msg)
    return _g(cwd, "rev-parse", "HEAD")


@pytest.fixture
def fx(tmp_path):
    """origin (bare) with `dev`, and a PR branch `feat` that touched `mod.py` + its test."""
    origin, repo = tmp_path / "origin.git", tmp_path / "repo"
    _g(tmp_path, "init", "-q", "--bare", "-b", "dev", str(origin))
    _g(tmp_path, "clone", "-q", "-c", "user.name=t", "-c", "user.email=t@example.com",
       str(origin), str(repo))
    _g(repo, "checkout", "-qb", "dev")
    _commit(repo, {"mod.py": MOD, "tests/test_mod.py": TEST, "other.py": "x = 1\n"}, "base")
    _g(repo, "push", "-q", "origin", "dev")
    _g(repo, "checkout", "-qb", "feat")
    head = _commit(repo, {"mod.py": MOD.replace("return 2", "return 3")}, "pr")
    _g(repo, "push", "-q", "origin", "feat")
    _g(repo, "checkout", "-q", "dev")
    return repo, head


def _dev(repo, files) -> str:
    _g(repo, "checkout", "-q", "dev")
    sha = _commit(repo, files, "dev moved")
    return _g(repo, "push", "-q", "origin", "dev") or sha


def _origin_feat(repo) -> str:
    return _g(repo, "ls-remote", "origin", "refs/heads/feat").split()[0]


def _worktrees(repo) -> list[str]:
    return _g(repo, "worktree", "list").splitlines()


def test_a_clean_base_merge_runs_the_targeted_tests_and_pushes(fx, monkeypatch):
    repo, head = fx
    base = _dev(repo, {"other.py": "x = 2\n"})
    ran = []
    real = judge.run_suite
    monkeypatch.setattr(judge, "run_suite", lambda cmd, cwd, timeout: (
        ran.append((cmd, Path(cwd).name, Path(cwd).parent.name[:16], timeout))
        or real(cmd, cwd, timeout)))

    res = basemerge.attempt(repo, "feat", "dev", PYTEST)

    assert res["outcome"] == "merged"
    assert (res["base_sha"], res["head_sha"]) == (base, head)
    assert ran == [(f"{PYTEST} tests/test_mod.py", "wt", "chela-basemerge-", 900)]
    assert (res["files"], _origin_feat(repo)) == (["tests/test_mod.py"], res["merge_sha"])
    assert _g(repo, "rev-parse", f"{res['merge_sha']}^@").split() == [head, base]
    assert len(_worktrees(repo)) == 1                      # the scratch worktree is gone


def test_a_real_conflict_lists_the_files_and_pushes_nothing(fx):
    repo, head = fx
    _dev(repo, {"mod.py": MOD.replace("return 2", "return 4")})
    agent = repo.parent / "agent-wt"                       # the run's own worktree, mid-edit
    _g(repo, "worktree", "add", "-q", str(agent), "feat")
    (agent / "mod.py").write_text("half-typed")

    res = basemerge.attempt(repo, "feat", "dev", PYTEST)

    assert (res["outcome"], res["files"], res["merge_sha"]) == ("conflict", ["mod.py"], None)
    assert _origin_feat(repo) == head == _g(agent, "rev-parse", "HEAD")
    assert (agent / "mod.py").read_text() == "half-typed"   # never touched
    assert len(_worktrees(repo)) == 2


def test_a_clean_merge_whose_targeted_tests_go_red_is_not_pushed(fx):
    repo, head = fx
    _dev(repo, {"tests/test_mod.py": TEST.replace("== 1", "== 9")})

    res = basemerge.attempt(repo, "feat", "dev", PYTEST)

    assert (res["outcome"], res["files"], _origin_feat(repo)) == ("red", ["tests/test_mod.py"], head)
    assert "1 failed" in res["detail"]


def test_a_branch_that_already_has_the_base_is_current_and_pushes_nothing(fx):
    repo, head = fx
    res = basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert (res["outcome"], _origin_feat(repo)) == ("current", head)


@pytest.mark.parametrize("cmd, ran_as, outcome", [("", [], "merged"),
                                                  ("make test", ["make test"], "red")])
def test_no_test_cmd_pushes_for_ci_to_gate_and_an_unextendable_one_runs_as_is(
        fx, monkeypatch, cmd, ran_as, outcome):
    repo, head = fx
    _dev(repo, {"other.py": "x = 2\n"})
    ran = []
    monkeypatch.setattr(judge, "run_suite", lambda c, cwd, timeout: ran.append(c) or
                        judge.SuiteResult(True, 1, 0, 1, 0, "boom"))
    res = basemerge.attempt(repo, "feat", "dev", cmd)
    assert (ran, res["outcome"]) == (ran_as, outcome)
    assert _origin_feat(repo) == (res["merge_sha"] if outcome == "merged" else head)


@pytest.mark.parametrize("result", [judge.SuiteResult(False, -1, 0, 0, 0, "", "no shell"),
                                    judge.SuiteResult(True, 0, 0, 0, 0, "", timed_out=True)])
def test_a_suite_that_did_not_run_or_finish_is_red_never_green(fx, monkeypatch, result):
    repo, head = fx
    _dev(repo, {"other.py": "x = 2\n"})
    monkeypatch.setattr(judge, "run_suite", lambda *a, **k: result)
    assert basemerge.attempt(repo, "feat", "dev", PYTEST)["outcome"] == "red"
    assert _origin_feat(repo) == head


def test_an_unfetchable_branch_raises_and_leaves_no_worktree(fx):
    repo, _ = fx
    with pytest.raises(RuntimeError, match="fetch"):
        basemerge.attempt(repo, "gone", "dev", PYTEST)
    assert len(_worktrees(repo)) == 1


def test_a_failed_merge_naming_no_file_raises_and_cleans_up(fx, monkeypatch):
    repo, _ = fx
    _dev(repo, {"other.py": "x = 2\n"})
    real = basemerge._git
    monkeypatch.setattr(basemerge, "_git", lambda cwd, *a, **k: (
        (_ for _ in ()).throw(RuntimeError("merge refused")) if a[0] == "merge"
        else real(cwd, *a, **k)))
    with pytest.raises(RuntimeError, match="naming no file"):
        basemerge.attempt(repo, "feat", "dev", PYTEST)
    assert len(_worktrees(repo)) == 1


def test_git_that_cannot_start_is_a_runtime_error(monkeypatch, tmp_path):
    monkeypatch.setattr(basemerge.subprocess, "run", lambda *a, **k: open(tmp_path / "no/git"))
    with pytest.raises(RuntimeError, match="could not run"):
        basemerge._git(tmp_path, "status")


def test_targeted_tests_are_the_prs_tests_and_the_importers_of_its_sources(tmp_path):
    for rel, text in {"tests/test_a.py": "import pkg.mod\n", "tests/test_b.py": "import os\n",
                      "tests/test_c.py": "from pkg import mod\n", "pkg/mod.py": "",
                      "tests/sub/test_d.py": "from pkg.mod import f\n"}.items():
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text(text)
    assert basemerge.targeted_tests(tmp_path, ["pkg/mod.py", "tests/test_b.py",
                                               "tests/test_gone.py", "README.md"]) == [
        "tests/sub/test_d.py", "tests/test_a.py", "tests/test_b.py", "tests/test_c.py"]
    assert basemerge.targeted_tests(tmp_path, ["README.md"]) == []


def test_the_rework_bodies_name_the_base_sha_and_the_files():
    res = {"base_sha": "abcdef1234567890", "files": ["a.py", "b.py"], "detail": "x ``` y"}
    body = basemerge.rework_body({**res, "outcome": "conflict"}, "dev")
    assert "Conflict with base after `abcdef123456` merged: merge origin/dev and resolve" in body
    assert "- `a.py`\n- `b.py`\n" in body and "x ``` y" not in body and "not charged" in body
    red = basemerge.rework_body({**res, "outcome": "red"}, "dev")
    assert "targeted tests go red" in red and "\n````\nx ``` y\n````\n" in red
    assert "(git named none)" in basemerge.rework_body({**res, "files": [],
                                                        "outcome": "conflict"}, "dev")
