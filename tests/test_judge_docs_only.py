"""⚖️📄 CMX-205 — a docs-only PR's `cannot_verify` must be named STRUCTURAL, not generic.

Before this, "the judge proposed NO experiments" was the whole message whether the PR was
pure prose (nothing COULD be mutated) or had real code the judge inexplicably skipped (worth
investigating). Measured on cmx-204 (#264, `CHANGELOG.md` + `README.md` only): the judge came
back with the generic sentence and `chela merge` refused, and a human had no way to tell "this
is expected" from "this is a judge bug" without reading the diff themselves. These tests pin
``judge._docs_only_diff`` and its wiring into ``run_experiments``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from chela import judge

TEST_CMD = f'"{sys.executable}" -m pytest -q'

PASSING_TEST = "def test_ok():\n    assert True\n"


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
    )


@pytest.fixture
def origin(tmp_path) -> Path:
    o = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(o)], check=True, capture_output=True)
    return o


@pytest.fixture
def repo(tmp_path, origin) -> Path:
    work = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "test_suite.py").write_text(PASSING_TEST)
    (work / "README.md").write_text("# hello\n")
    _git("add", "test_suite.py", "README.md", cwd=work)
    _git("commit", "-m", "seed: a green suite", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    return work


def _branch_from_head(repo: Path, name: str) -> str:
    _git("branch", name, cwd=repo)
    return _git("rev-parse", name, cwd=repo).stdout.strip()


def _detached_worktree(repo: Path, ref: str, path: Path) -> Path:
    _git("worktree", "add", "--detach", str(path), ref, cwd=repo)
    return path


def _prep_worktree(repo: Path, branch: str, tmp_path: Path) -> Path:
    wt = _detached_worktree(repo, branch, tmp_path / "wt")
    _git("fetch", "origin", "dev", cwd=wt)
    return wt


# --- _docs_only_diff: the pure git mechanics ----------------------------------------------


def test_docs_only_diff_is_true_when_every_changed_file_is_prose(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "README.md").write_text("# hello\n\nmore words.\n")
    (repo / "CHANGELOG.md").write_text("## v2\n- prose only\n")
    _git("add", "README.md", "CHANGELOG.md", cwd=repo)
    _git("commit", "-m", "docs only", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    assert judge._docs_only_diff(wt, "dev") is True


def test_docs_only_diff_is_false_when_any_changed_file_is_code(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "README.md").write_text("# hello\n\nmore words.\n")
    (repo / "test_suite.py").write_text("def test_ok():\n    assert True\n\ndef test_two():\n"
                                         "    assert True\n")
    _git("add", "README.md", "test_suite.py", cwd=repo)
    _git("commit", "-m", "docs plus code", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    assert judge._docs_only_diff(wt, "dev") is False


def test_docs_only_diff_is_unknown_without_a_base_branch(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")

    assert judge._docs_only_diff(wt, "") is None


def test_docs_only_diff_is_unknown_when_the_base_ref_does_not_resolve(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")

    assert judge._docs_only_diff(wt, "does-not-exist") is None


def test_docs_only_diff_is_unknown_on_an_empty_diff(tmp_path, repo):
    """The worktree tip IS origin/dev — no PR content to classify either way."""
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    _git("fetch", "origin", "dev", cwd=wt)

    assert judge._docs_only_diff(wt, "dev") is None


# --- wired into run_experiments's zero-experiments report ---------------------------------


def test_zero_experiments_on_a_docs_only_pr_says_STRUCTURAL_not_generic(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "README.md").write_text("# hello\n\nmore words.\n")
    _git("commit", "-am", "docs only", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(wt, TEST_CMD, {"experiments": []}, timeout=60, base_branch="dev")

    assert report.state == judge.J_CANNOT_VERIFY
    assert "DOCS-ONLY" in report.cannot_verify
    assert "not a finding" in report.cannot_verify
    assert report.blocking == []


def test_zero_experiments_on_a_code_touching_pr_keeps_the_generic_message(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "test_suite.py").write_text("def test_ok():\n    assert True\n\ndef test_two():\n"
                                         "    assert True\n")
    _git("commit", "-am", "a real code change, judged with no experiments anyway", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(wt, TEST_CMD, {"experiments": []}, timeout=60, base_branch="dev")

    assert report.state == judge.J_CANNOT_VERIFY
    assert "DOCS-ONLY" not in report.cannot_verify
    assert "NO experiments" in report.cannot_verify
