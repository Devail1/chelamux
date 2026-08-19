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


# --- _is_prose_path: the classifier table --------------------------------------------------


@pytest.mark.parametrize("name", [
    "LICENSE",
    "NOTICE",
    "AUTHORS",
    "CODEOWNERS",
    "docs/LICENSE",
    "README.md",
    "CHANGELOG.md",
    "changelog.d/CMX-312.md",
    "notes.mdx",
    "notes.rst",
    "notes.txt",
    "README.MD",
])
def test_is_prose_path_true_for_prose(name):
    assert judge._is_prose_path(name) is True


@pytest.mark.parametrize("name", [
    "test_suite.py",
    "chela/judge.py",
    "index.js",
    "Makefile",
    "LICENSE.py",
])
def test_is_prose_path_false_for_code(name):
    assert judge._is_prose_path(name) is False


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


def test_docs_only_diff_is_unknown_when_git_diff_itself_fails(tmp_path, repo, monkeypatch):
    """The ref resolves fine (rev-parse succeeds) but the diff invocation itself errors out.

    This must stay an unknown, not collapse to "yes, docs-only": a resolved ref only proves
    the base exists, it says nothing about whether the diff between it and HEAD could be
    computed. Returning ``True`` here would report DOCS-ONLY for a diff nobody actually read.
    """
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    _git("fetch", "origin", "dev", cwd=wt)

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and "diff" in cmd:
            return subprocess.CompletedProcess(cmd, 128, stdout="", stderr="fatal: boom")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(judge.subprocess, "run", fake_run)

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


# --- ⛔ THE PRODUCTION CALL SITES PASS base_branch ----------------------------------
#
# ⚖️📄 CMX-205 round 4, a WIRING finding. `judge_run` reaches `run_experiments` down TWO
# paths — the CMX-201 one taken when a reaped worktree must be rebuilt first, and the
# normal one taken when it is already there. Dropping `base_branch=base_branch` from
# either leaves the whole suite green, and the consequence is not cosmetic:
# `_docs_only_diff` diffs against `origin/<base_branch>`, so with no base it returns None,
# the docs-only branch never fires, and this feature goes silently inert while still
# reporting success.
#
# ⛔ BOTH call sites, in one test, by running the judge TWICE against the same run: the
# first call finds no worktree and rebuilds (branch A), the second finds the one the first
# left behind (branch B). The judge corrupted only the `else:` branch — a guard pinning
# just that one would leave its sibling, six lines up and identical, free to lose the
# kwarg. That asymmetry has been the source of repeated findings in this repo.

def test_judge_run_hands_run_experiments_the_real_base_branch(tmp_path, monkeypatch):
    import json as _json
    from unittest.mock import patch
    from chela import dispatcher
    import chela.judge as judge_mod
    from tests.test_judge import _git_workflow_repo, _run_row, REAL_GUARD_TEST

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda name: None)
    task_id = "basebranchwiring"
    # ⛔ _git_workflow_repo deliberately does NOT pre-provision the judge worktree, so the
    # FIRST judge_run takes the CMX-201 rebuild branch. The second finds the worktree that
    # first call left behind (cleanup=False) and takes the normal branch. One test, both
    # call sites — which is the whole point, since the judge only corrupted the second.
    repo, head_sha = _git_workflow_repo(tmp_path, task_id, REAL_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, task_id, pr_head_sha=head_sha)

    seen: list[object] = []
    real = judge_mod.run_experiments

    def spy(worktree, test_cmd, raw, *, timeout=None, base_branch=None):
        # ⛔ Record the VALUE, not that a call happened: the defect is a kwarg quietly
        # defaulting to "", which a call-count spy cannot tell from a real base branch.
        seen.append(base_branch)
        return real(worktree, test_cmd, raw, timeout=timeout, base_branch=base_branch)

    monkeypatch.setattr(judge_mod, "run_experiments", spy)

    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(_json.dumps({"experiments": []}))
    with patch.object(dispatcher, "_post_pr_comment", side_effect=lambda u, d, b: (True, "")):
        judge_mod.judge_run(task_id, exp_file, cleanup=False)   # branch A: worktree reaped
        judge_mod.judge_run(task_id, exp_file, cleanup=False)   # branch B: worktree present

    assert len(seen) == 2, (
        f"expected BOTH run_experiments call sites to be exercised, saw {len(seen)} — "
        "if this drops to 1 the sibling branch is silently no longer covered"
    )
    assert all(b for b in seen), (
        f"judge_run passed a falsy base_branch {seen!r} — `_docs_only_diff` diffs against "
        "origin/<base_branch>, so an empty base makes the docs-only diagnosis silently inert"
    )
