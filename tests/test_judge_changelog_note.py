"""⚖️📝 CMX-309 — a PR that changes non-prose files without touching CHANGELOG.md gets a
NOTE, mechanically, instead of nothing at all.

CONTRIBUTING.md says "any user-facing change adds a CHANGELOG entry" — that was pure prose,
and it failed TWICE: cutting 0.7.0 from `dev` would have shipped notes missing half of it (4
of the last 8 merges carried no entry, backfilled by hand in #382). These tests pin
``judge._changelog_missing_note`` and its wiring into ``run_experiments``.
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
    (work / "CHANGELOG.md").write_text("## [Unreleased]\n")
    (work / "README.md").write_text("# hello\n")
    _git("add", "test_suite.py", "CHANGELOG.md", "README.md", cwd=work)
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


# --- _changelog_missing_note: the pure git mechanics ----------------------------------------


def test_changelog_missing_note_fires_when_code_changes_without_a_changelog_entry(
    tmp_path, repo, origin,
):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    note = judge._changelog_missing_note(wt, "dev")

    assert note is not None
    assert "CHANGELOG.md" in note["title"]


def test_changelog_missing_note_is_none_when_the_changelog_was_touched(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n### Added\n\n- A feature. (#1)\n"
    )
    _git("add", "feature.py", "CHANGELOG.md", cwd=repo)
    _git("commit", "-m", "add a feature, with a changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    assert judge._changelog_missing_note(wt, "dev") is None


def test_changelog_missing_note_is_none_for_a_prose_only_diff(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "README.md").write_text("# hello\n\nmore words.\n")
    _git("commit", "-am", "docs only, no user-facing change to log", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    assert judge._changelog_missing_note(wt, "dev") is None


def test_changelog_missing_note_is_none_without_a_base_branch(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    assert judge._changelog_missing_note(wt, "") is None


def test_changelog_missing_note_is_none_when_the_base_ref_does_not_resolve(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    assert judge._changelog_missing_note(wt, "does-not-exist") is None


def test_changelog_missing_note_is_none_on_an_empty_diff(tmp_path, repo):
    wt = _detached_worktree(repo, "dev", tmp_path / "wt")
    assert judge._changelog_missing_note(wt, "dev") is None


# --- wired into run_experiments's notes, on every report state -----------------------------


def test_run_experiments_carries_the_note_even_on_a_cannot_verify_report(
    tmp_path, repo, origin,
):
    """A missing CHANGELOG entry is independent of whether the judge could run its mutation
    battery — the note must survive a report that never gets past 'no experiments proposed'.
    """
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "feature.py", cwd=repo)
    _git("commit", "-m", "add a feature, no changelog entry, no experiments proposed", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD, {"experiments": []}, timeout=60, base_branch="dev",
    )

    assert report.state == judge.J_CANNOT_VERIFY
    titles = [n.get("title") for n in report.notes]
    assert "No CHANGELOG.md entry" in titles


def test_run_experiments_carries_no_note_when_the_changelog_was_touched(tmp_path, repo, origin):
    _branch_from_head(repo, "pr-1")
    _git("checkout", "pr-1", cwd=repo)
    (repo / "feature.py").write_text("def add(a, b):\n    return a + b\n")
    (repo / "CHANGELOG.md").write_text(
        "## [Unreleased]\n\n### Added\n\n- A feature. (#1)\n"
    )
    _git("add", "feature.py", "CHANGELOG.md", cwd=repo)
    _git("commit", "-m", "add a feature, with a changelog entry", cwd=repo)
    wt = _prep_worktree(repo, "pr-1", tmp_path)

    report = judge.run_experiments(
        wt, TEST_CMD, {"experiments": []}, timeout=60, base_branch="dev",
    )

    titles = [n.get("title") for n in report.notes]
    assert "No CHANGELOG.md entry" not in titles
