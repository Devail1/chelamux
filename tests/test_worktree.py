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

import logging
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
    worktree.remove_worktree(repo, old_wt, root)  # worktree gone, branch "proj-1" left behind
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


# --- remove_worktree: all four ways a worktree can go stale (CMX-164) -------------------

def test_remove_worktree_removes_a_live_worktree(repo: Path, tmp_path: Path):
    root = tmp_path / "worktrees"
    wt_path, _ = worktree.ensure_worktree(repo, "task-1", "main", "PROJ", 1, root)

    assert worktree.remove_worktree(repo, wt_path, root) is True
    assert not wt_path.exists()


def test_remove_worktree_falls_back_to_a_direct_delete_for_an_unregistered_directory(
    repo: Path, tmp_path: Path,
):
    """git has no administrative record of this path at all — `git worktree remove`
    refuses it ("not a working tree"). Make it give up instead of falling back → RED."""
    wt_path = tmp_path / "orphan"
    wt_path.mkdir()
    (wt_path / "junk.txt").write_text("leftover from a crashed create\n")

    removed = worktree.remove_worktree(repo, wt_path, tmp_path)

    assert removed is True
    assert not wt_path.exists()


def test_remove_worktree_on_EPERM_logs_loudly_and_leaves_the_directory(
    repo: Path, tmp_path: Path, monkeypatch, caplog,
):
    """Root-owned remnants a Docker build left behind (chela runs as the user, not root).
    Swallow the PermissionError silently (no log, or pretend it succeeded) → RED."""
    wt_path = tmp_path / "root_owned"
    wt_path.mkdir()
    (wt_path / "cant_touch_this").write_text("root:root\n")

    def _boom(path):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(worktree.shutil, "rmtree", _boom)

    with caplog.at_level(logging.WARNING):
        removed = worktree.remove_worktree(repo, wt_path, tmp_path)

    assert removed is False
    assert wt_path.is_dir()                  # left in place, not half-deleted
    assert str(wt_path) in caplog.text        # named loudly, not a silent no-op


def test_disk_usage_bytes_sums_file_sizes_recursively(tmp_path: Path):
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    (root / "a.txt").write_bytes(b"x" * 100)
    (root / "sub" / "b.txt").write_bytes(b"y" * 250)

    assert worktree.disk_usage_bytes(root) == 350


def test_disk_usage_bytes_on_a_missing_root_is_zero_not_a_crash(tmp_path: Path):
    assert worktree.disk_usage_bytes(tmp_path / "does-not-exist") == 0


# ---------------------------------------------------------------------------
# CMX-320 — never delete a real repository (issue #398)
# ---------------------------------------------------------------------------

def test_remove_worktree_REFUSES_the_repository_itself(repo, tmp_path):
    """🔴 The incident, reproduced. `adopt-397`'s row recorded `worktree_path` as the MAIN
    REPO; task cleanup removed "its worktree" and the checkout was gone, taking a gitignored
    ~614KB TODO.md with it — twice, because the poisoned column re-arms on every daemon
    start.

    `git worktree remove` alone does NOT save you here: it fails on a real repo, and the
    `shutil.rmtree` fallback below it then runs precisely because git has no record of the
    path. That fallback is what did the damage.
    """
    ignored = repo / "TODO.md"
    ignored.write_text("the gitignored tracker that no clone can restore\n")

    # root=repo.parent (== tmp_path here) so the STRUCTURAL check, not the root-membership
    # check, is what fires — the repo is "inside" this root, exactly as the real incident's
    # main repo was inside the wider filesystem the daemon could see.
    with pytest.raises(worktree.NotAWorktree) as exc:
        worktree.remove_worktree(repo, repo, repo.parent)

    assert repo.is_dir(), "the repository was deleted"
    assert ignored.exists(), "the gitignored file was destroyed"
    assert "is the repository" in str(exc.value).lower() or "IS the repository" in str(exc.value)


def test_remove_worktree_REFUSES_a_directory_containing_the_repository(repo):
    """A path that is an ANCESTOR of the repo can never be a worktree, and deleting it takes
    the repo with it."""
    with pytest.raises(worktree.NotAWorktree):
        worktree.remove_worktree(repo, repo.parent, repo.parent)
    assert repo.is_dir()


def test_remove_worktree_REFUSES_an_unrelated_clone(repo, tmp_path):
    """The structural test, independent of any configured workspace root: a real clone's
    `.git` is a DIRECTORY, a linked worktree's `.git` is a FILE. Anything with a `.git`
    directory is a repository and must never be deleted as a worktree — even one this repo
    has never heard of.
    """
    other = tmp_path / "someone-elses-repo"
    subprocess.run(["git", "init", "-q", str(other)], check=True, capture_output=True)
    precious = other / "notes.txt"
    precious.write_text("not chela's to delete\n")

    with pytest.raises(worktree.NotAWorktree) as exc:
        worktree.remove_worktree(repo, other, tmp_path)

    assert precious.exists()
    assert ".git is a DIRECTORY" in str(exc.value)


def test_remove_worktree_STILL_REMOVES_a_real_worktree(repo, tmp_path):
    """⭐ MUST BE ACCEPTED — the guard is worthless if it refuses the normal case. A guard
    that raises on everything would pass all three tests above while breaking every cleanup
    in the system.
    """
    wt = tmp_path / "wt"
    worktree.detached_worktree(repo, "main", wt, tmp_path)
    assert wt.is_dir()
    assert (wt / ".git").is_file(), "a linked worktree's .git must be a file, not a dir"

    assert worktree.remove_worktree(repo, wt, tmp_path) is True
    assert not wt.exists(), "a genuine worktree was left behind"


def test_remove_worktree_STILL_REMOVES_an_unregistered_leftover(repo, tmp_path):
    """MUST BE ACCEPTED — the rmtree fallback's real purpose: a directory git has no record
    of (crash mid-create, hand-copied). It has no `.git` at all, so it is not a repository
    and the guard must let it through.
    """
    orphan = tmp_path / "orphan"
    orphan.mkdir()
    (orphan / "junk.txt").write_text("leftover\n")

    assert worktree.remove_worktree(repo, orphan, tmp_path) is True
    assert not orphan.exists()


def test_remove_worktree_is_silent_on_a_path_that_does_not_exist(repo, tmp_path):
    """MUST BE ACCEPTED — nothing to delete is not an error, and must not raise."""
    worktree.refuse_if_not_a_worktree(repo, tmp_path / "never-existed", tmp_path)


def test_the_guard_runs_BEFORE_any_deletion_path(repo, monkeypatch):
    """Ordering, pinned: the refusal must precede both `git worktree remove` AND the
    `shutil.rmtree` fallback. A guard placed after either is a guard that fires once the
    damage is done.
    """
    called: list[str] = []
    monkeypatch.setattr(worktree.shutil, "rmtree",
                        lambda *a, **k: called.append("rmtree"))
    monkeypatch.setattr(worktree.subprocess, "run",
                        lambda *a, **k: called.append("git") or (_ for _ in ()).throw(
                            AssertionError("git ran despite the refusal")))

    with pytest.raises(worktree.NotAWorktree):
        worktree.remove_worktree(repo, repo, repo.parent)

    assert called == [], f"a deletion path ran before the guard refused: {called}"


# ---------------------------------------------------------------------------
# CMX-325 — the invariant is "inside the worktrees root", not just "not the main
# repo" (issue #398, second deletion)
# ---------------------------------------------------------------------------

def test_remove_worktree_REFUSES_a_path_outside_the_worktrees_root(repo, tmp_path):
    """A directory that is a perfectly normal, unregistered leftover would otherwise sail
    through the structural checks (no `.git` at all) and hit the `shutil.rmtree` fallback —
    exactly the mechanism that deleted the main repo. Root-membership must catch it even
    when nothing about the path itself looks like a repository.
    """
    root = tmp_path / "worktrees"
    root.mkdir()
    outside = tmp_path / "elsewhere" / "some-directory"
    outside.mkdir(parents=True)
    (outside / "precious.txt").write_text("not under the worktrees root\n")

    with pytest.raises(worktree.NotAWorktree) as exc:
        worktree.remove_worktree(repo, outside, root)

    assert outside.exists(), "a path outside the worktrees root was deleted"
    assert "outside the worktrees root" in str(exc.value).lower()


def test_remove_worktree_STILL_REMOVES_a_leftover_inside_the_worktrees_root(repo, tmp_path):
    """⭐ MUST BE ACCEPTED — the root-membership guard must not refuse the ordinary case:
    an unregistered leftover directory that genuinely lives under the configured root."""
    root = tmp_path / "worktrees"
    inside = root / "task-1"
    inside.mkdir(parents=True)
    (inside / "junk.txt").write_text("leftover\n")

    assert worktree.remove_worktree(repo, inside, root) is True
    assert not inside.exists()


def test_find_existing_worktree_ignores_a_match_outside_root(repo, tmp_path):
    """🔴 The write-side half of issue #398, reproduced directly: `cmx-319`'s branch was
    checked out in the MAIN REPO when its rework respawned. `git worktree list` reports the
    main working tree exactly like any linked worktree, so a naive lookup returns it as "the
    existing worktree for this branch" — and callers trusted that enough to record it as a
    run's `worktree_path`. A match outside `root` must never be returned as reusable.
    """
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "proj-1"],
                    check=True, capture_output=True)

    root = tmp_path / "worktrees"
    found = worktree._find_existing_worktree(repo, "proj-1", root)

    assert found is None


def test_find_existing_worktree_still_returns_a_match_inside_root(repo, tmp_path):
    """⭐ MUST BE ACCEPTED — the ordinary rework-reuse case: a live worktree genuinely under
    the configured root must still be found."""
    root = tmp_path / "worktrees"
    wt_path, _ = worktree.ensure_worktree(repo, "task-1", "main", "PROJ", 1, root)
    subprocess.run(["git", "-C", str(repo), "worktree", "prune"], check=True, capture_output=True)

    found = worktree._find_existing_worktree(repo, "proj-1", root)

    assert found == wt_path


def test_attach_worktree_refuses_to_reuse_the_main_repo_as_the_worktree(repo, tmp_path):
    """The full write path: a branch checked out in the main repo must never come back out
    of `attach_worktree` as a reusable path — git then refuses to add a second worktree for
    the same branch, which surfaces as a loud `CalledProcessError` instead of a silently
    recorded `worktree_path` pointing at the main repo.
    """
    subprocess.run(["git", "-C", str(repo), "checkout", "-b", "proj-1"],
                    check=True, capture_output=True)

    root = tmp_path / "worktrees"
    with pytest.raises(subprocess.CalledProcessError):
        worktree.attach_worktree(repo, "proj-1", root / "task-1", root)

