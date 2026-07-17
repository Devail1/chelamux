"""Tests for chela.worktree.ensure_worktree — collision-proof slot reuse (CMX-104).

`task_number` is minted per-workflow as `MAX(task_number)+1` over whatever the runs
DB currently remembers, and the DB does not remember forever (`_prune_done_rows`,
`delete_run`). So a later, unrelated task_id can land on a task_number a prior task_id
already used, and find its branch name / worktree directory still sitting on disk with
nothing in the DB pointing at it. None of these leftovers may fail a fresh dispatch:
  - a stale branch (no worktree attached) at the derived branch name
  - an orphaned worktree directory git has no record of
  - a dead worktree administrative record (directory already gone)
A LIVE worktree must still win the idempotent-reuse path untouched.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from chela import worktree


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A real local git repo on `main` with one commit."""
    repo_path = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(repo_path), "config", k, v], check=True, capture_output=True)
    (repo_path / "README.md").write_text("seed\n")
    subprocess.run(["git", "-C", str(repo_path), "add", "README.md"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo_path), "commit", "-q", "-m", "seed"], check=True, capture_output=True)
    return repo_path


def _branches(repo_path: Path) -> set[str]:
    out = subprocess.run(
        ["git", "-C", str(repo_path), "branch", "--format=%(refname:short)"],
        check=True, capture_output=True, text=True,
    ).stdout
    return {line.strip() for line in out.splitlines() if line.strip()}


def _head_sha(path: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], check=True, capture_output=True, text=True,
    ).stdout.strip()


def test_creates_fresh_worktree(repo: Path, tmp_path: Path):
    root = tmp_path / "worktrees"
    wt_path, created = worktree.ensure_worktree(repo, "task-1", "main", "PROJ", 1, root)

    assert created is True
    assert wt_path == (root / "task-1").resolve()
    assert wt_path.is_dir()
    assert "proj-1" in _branches(repo)


def test_idempotent_redispatch_reuses_the_live_worktree(repo: Path, tmp_path: Path):
    root = tmp_path / "worktrees"
    first, created1 = worktree.ensure_worktree(repo, "task-1", "main", "PROJ", 1, root)
    (first / "agent-work.txt").write_text("in progress\n")  # proves it's the SAME dir

    second, created2 = worktree.ensure_worktree(repo, "task-1", "main", "PROJ", 1, root)

    assert created2 is False
    assert second == first
    assert (second / "agent-work.txt").exists()


def test_survives_a_stale_branch_with_no_worktree_attached(repo: Path, tmp_path: Path):
    # A prior task_id used this slot (task_number=1), got a stray commit, and then had
    # its DB row pruned/deleted with only the worktree cleaned up — not the branch.
    root = tmp_path / "worktrees"
    old_wt, _ = worktree.ensure_worktree(repo, "old-task", "main", "PROJ", 1, root)
    subprocess.run(["git", "-C", str(old_wt), "commit", "--allow-empty", "-q", "-m", "stray"],
                    check=True, capture_output=True)
    worktree.remove_worktree(repo, old_wt)  # worktree gone, branch "proj-1" left behind
    assert "proj-1" in _branches(repo)
    assert not old_wt.exists()

    # A FRESH task_id lands on the same task_number.
    new_wt, created = worktree.ensure_worktree(repo, "new-task", "main", "PROJ", 1, root)

    assert created is True
    assert new_wt.is_dir()
    assert new_wt != old_wt  # different task_id, different directory
    # Recreated fresh from base — the old task's stray commit is gone, not inherited.
    assert _head_sha(new_wt) == _head_sha(repo)


def test_survives_an_orphaned_directory_git_has_no_record_of(repo: Path, tmp_path: Path):
    root = tmp_path / "worktrees"
    wt_path = (root / "task-1").resolve()
    wt_path.mkdir(parents=True)
    (wt_path / "junk.txt").write_text("leftover from a crashed create\n")

    new_wt, created = worktree.ensure_worktree(repo, "task-1", "main", "PROJ", 1, root)

    assert created is True
    assert new_wt == wt_path
    assert new_wt.is_dir()
    assert not (new_wt / "junk.txt").exists()  # the orphaned junk was cleared, not inherited
    assert "proj-1" in _branches(repo)


def test_survives_a_dead_worktree_administrative_record(repo: Path, tmp_path: Path):
    # Directory manually removed (rm -rf) WITHOUT `git worktree remove` — git's
    # admin record under .git/worktrees/ is now dangling until pruned.
    root = tmp_path / "worktrees"
    wt_path, _ = worktree.ensure_worktree(repo, "task-1", "main", "PROJ", 1, root)
    subprocess.run(["rm", "-rf", str(wt_path)], check=True)

    new_wt, created = worktree.ensure_worktree(repo, "task-1", "main", "PROJ", 1, root)

    assert created is True
    assert new_wt == wt_path
    assert new_wt.is_dir()
