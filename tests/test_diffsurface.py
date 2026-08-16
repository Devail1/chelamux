"""chela.diffsurface — the per-session CHANGED-FILES / DIFF surface (CMX-299).

Real git repos, real `git diff`/`git status` subprocess calls (same style as
tests/test_worktree.py) — this module IS the git plumbing, so a mock would just
re-assert the mock's own behaviour instead of testing anything real.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import diffsurface


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    repo_path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git(repo_path, "config", k, v)
    (repo_path / "tracked.txt").write_text("one\ntwo\nthree\n")
    (repo_path / "to_delete.txt").write_text("bye\n")
    _git(repo_path, "add", "tracked.txt", "to_delete.txt")
    _git(repo_path, "commit", "-q", "-m", "seed")
    return repo_path


# --- is_git_repo / not-a-repo degradation -----------------------------------

def test_changed_files_not_a_directory(tmp_path: Path):
    result = diffsurface.changed_files(tmp_path / "does-not-exist")
    assert result == {"is_git": False, "has_head": False, "files": [], "additions": 0, "deletions": 0}


def test_changed_files_plain_directory_not_a_repo(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = diffsurface.changed_files(plain)
    assert result["is_git"] is False
    assert result["files"] == []


def test_changed_files_repo_with_no_commits(tmp_path: Path):
    empty_repo = tmp_path / "empty"
    subprocess.run(["git", "init", "-q", str(empty_repo)], check=True, capture_output=True)
    result = diffsurface.changed_files(empty_repo)
    assert result["is_git"] is True
    assert result["has_head"] is False
    assert result["files"] == []


# --- the merged file list ----------------------------------------------------

def test_changed_files_clean_worktree(repo: Path):
    result = diffsurface.changed_files(repo)
    assert result == {"is_git": True, "has_head": True, "files": [], "additions": 0, "deletions": 0}


def test_changed_files_reports_modified_added_deleted_and_untracked(repo: Path):
    # 🔴 GUARD: this is the actual union changed_files exists to build — drop any
    # one of the four git calls it makes (name-status, numstat, ls-files) and one
    # of these four rows silently disappears from the result.
    (repo / "tracked.txt").write_text("one\ntwo\nthree\nfour\n")   # modified: +1 line
    (repo / "to_delete.txt").unlink()                               # deleted
    (repo / "new_tracked.txt").write_text("brand new\n")
    _git(repo, "add", "new_tracked.txt")                            # staged add
    (repo / "scratch.txt").write_text("a\nb\n")                     # untracked, never added

    result = diffsurface.changed_files(repo)
    by_path = {f["path"]: f for f in result["files"]}

    assert by_path["tracked.txt"]["status"] == "modified"
    assert by_path["tracked.txt"]["additions"] == 1
    assert by_path["tracked.txt"]["deletions"] == 0

    assert by_path["to_delete.txt"]["status"] == "deleted"
    assert by_path["to_delete.txt"]["deletions"] == 1

    assert by_path["new_tracked.txt"]["status"] == "added"
    assert by_path["new_tracked.txt"]["additions"] == 1

    assert by_path["scratch.txt"]["status"] == "untracked"
    assert by_path["scratch.txt"]["additions"] == 2  # best-effort line count

    assert set(by_path) == {"tracked.txt", "to_delete.txt", "new_tracked.txt", "scratch.txt"}
    assert result["additions"] == sum(f["additions"] for f in result["files"])
    assert result["deletions"] == sum(f["deletions"] for f in result["files"])


def test_changed_files_untracked_file_without_trailing_newline_still_counts_its_last_line(repo: Path):
    # 🔴 GUARD: a file whose last line has no trailing "\n" (the common case
    # for a file someone is still mid-edit on) must still count that line —
    # dropping the `+ (0 if ... endswith(b"\n") else 1)` half of _count_lines
    # silently undercounts every such file's additions estimate by exactly 1.
    # The clean-trailing-newline case above (scratch.txt, "a\nb\n") cannot
    # catch this: `data.count(b"\n")` alone already gives the right answer
    # when the file DOES end in a newline.
    (repo / "no_trailing_newline.txt").write_bytes(b"one\ntwo")
    result = diffsurface.changed_files(repo)
    by_path = {f["path"]: f for f in result["files"]}
    assert by_path["no_trailing_newline.txt"]["status"] == "untracked"
    assert by_path["no_trailing_newline.txt"]["additions"] == 2


def test_all_git_subprocess_calls_are_bounded_by_git_timeout(repo: Path, monkeypatch):
    # 🔴 GUARD: _GIT_TIMEOUT is what turns a wedged git process (e.g. a
    # pane's cwd on a stalled network mount) into a bounded failure instead
    # of a permanent hang behind /api/agents/<wid>/diff. Every call this
    # module makes goes through the single `_run` helper — spying on
    # `subprocess.run` itself (not `_run`) means a future call that bypasses
    # `_run` would also show up unbound here, not just a dropped kwarg on
    # the one call site a narrower mock would target.
    calls = []
    real_run = subprocess.run

    def spy(cmd, **kwargs):
        calls.append(kwargs.get("timeout"))
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(diffsurface.subprocess, "run", spy)

    (repo / "tracked.txt").write_text("one\ntwo\nthree\nfour\n")
    diffsurface.changed_files(repo)
    diffsurface.file_patch(repo, "tracked.txt")

    assert calls, "no git subprocess calls were recorded — the spy is not wired in"
    assert all(t == diffsurface._GIT_TIMEOUT for t in calls), calls


def test_changed_files_partially_staged_edit_sums_both_halves(repo: Path):
    # Stage one change, then make a second unstaged edit on top — `git diff HEAD`
    # (not `--cached` alone) is what makes both halves land in one row.
    (repo / "tracked.txt").write_text("one\ntwo\nthree\nSTAGED\n")
    _git(repo, "add", "tracked.txt")
    (repo / "tracked.txt").write_text("one\ntwo\nthree\nSTAGED\nUNSTAGED\n")

    result = diffsurface.changed_files(repo)
    assert len(result["files"]) == 1
    assert result["files"][0]["additions"] == 2


# --- file_patch: gated to changed_files' own list ----------------------------

def test_file_patch_returns_the_unified_diff_for_a_modified_file(repo: Path):
    (repo / "tracked.txt").write_text("one\ntwo\nthree\nfour\n")
    result = diffsurface.file_patch(repo, "tracked.txt")
    assert result["ok"] is True
    assert "+four" in result["patch"]


def test_file_patch_returns_full_content_for_an_untracked_file(repo: Path):
    (repo / "scratch.txt").write_text("hello\n")
    result = diffsurface.file_patch(repo, "scratch.txt")
    assert result["ok"] is True
    assert "+hello" in result["patch"]


def test_file_patch_rejects_a_path_this_session_never_changed(repo: Path):
    # 🔴 GUARD: this is the ONLY thing standing between an arbitrary caller-
    # supplied `path` and a bare `git diff HEAD -- <path>` — dropping the
    # membership check would let /api/agents/<wid>/diff/patch read any path
    # git can resolve, not just what changed_files itself just reported.
    result = diffsurface.file_patch(repo, "tracked.txt")  # unchanged — nothing to diff
    assert result == {"ok": False, "error": "not a changed file in this session"}


def test_file_patch_not_a_git_repo(tmp_path: Path):
    plain = tmp_path / "plain"
    plain.mkdir()
    result = diffsurface.file_patch(plain, "whatever.txt")
    assert result == {"ok": False, "error": "not a git repository"}
