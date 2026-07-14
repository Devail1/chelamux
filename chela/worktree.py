from __future__ import annotations
import logging
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def ensure_worktree(
    repo_path: Path,
    task_id: str,
    base_branch: str,
    project_key: str,
    task_number: int,
    root: Path,
) -> tuple[Path, bool]:
    """Idempotent. Returns (worktree_path, created) for task_id.

    `created` is True only when this call freshly created the worktree, and
    False when an existing worktree for the derived branch was reused (an
    idempotent re-dispatch). Callers use this to fire one-shot, per-worktree
    setup (e.g. `hooks.after_create`) exactly once on creation.

    Branch name follows the Jira-style scheme `{project_key.lower()}-{task_number}`
    (e.g. `proj-7`). The worktree directory is still keyed by `task_id` so the
    SHA-stable identity that powers idempotent dispatch survives — only the
    branch and tmux window name use the human-readable display.

    If a worktree already exists for the derived branch, return its path with
    created=False. Otherwise create one branched from base_branch (created=True).
    """
    branch = f"{project_key.lower()}-{task_number}"
    wt_path = (root / task_id).resolve()

    existing = _find_existing_worktree(repo_path, branch)
    if existing is not None:
        return existing, False

    if wt_path.exists():
        # Stale directory; let git reuse it if it can, else bail.
        log.warning("Worktree path %s exists but git has no record; attempting reuse", wt_path)

    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", "-b", branch, str(wt_path), base_branch],
        check=True, capture_output=True,
    )
    return wt_path, True


class BranchGone(RuntimeError):
    """The branch a run's work lives on does not exist any more.

    Raised by :func:`attach_worktree` — and it is a HARD stop, not something to paper
    over: forking a fresh worktree from the base branch would silently throw away every
    commit the agent has already pushed and the PR that points at them. The run goes to
    ``needs_human`` instead (see ``dispatcher._respawn_rework``).
    """


def attach_worktree(repo_path: Path, branch: str, wt_path: Path) -> tuple[Path, bool]:
    """The worktree for an EXISTING branch — reused if it is there, re-created if not.

    This is the rework loop's half of :func:`ensure_worktree`, and the difference is the
    whole point: ``ensure_worktree`` creates a branch (``-b``) forked from the base
    branch, which is exactly the wrong thing for a run that already has history, a
    pushed branch and an open PR. Here the branch is the input, not the output.

    Returns ``(path, attached)`` — ``attached`` is True when git had no worktree for the
    branch and one was checked out again (the original directory was cleaned up), False
    when the existing worktree was reused. Raises :class:`BranchGone` when the branch
    itself is gone: there is nothing to attach to, and inventing one would lose the work.
    """
    existing = _find_existing_worktree(repo_path, branch)
    if existing is not None and existing.is_dir():
        return existing, False

    if not _branch_exists(repo_path, branch):
        raise BranchGone(f"branch {branch!r} does not exist in {repo_path}")

    # git still has a worktree record but the directory is gone (a `rm -rf`, a cleanup):
    # prune it, or `worktree add` refuses the branch as "already checked out".
    if existing is not None:
        subprocess.run(
            ["git", "-C", str(repo_path), "worktree", "prune"],
            check=False, capture_output=True,
        )

    wt_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "add", str(wt_path), branch],
        check=True, capture_output=True,
    )
    return wt_path, True


def _branch_exists(repo_path: Path, branch: str) -> bool:
    out = subprocess.run(
        ["git", "-C", str(repo_path), "rev-parse", "--verify", "--quiet",
         f"refs/heads/{branch}"],
        capture_output=True, text=True,
    )
    return out.returncode == 0


def _find_existing_worktree(repo_path: Path, branch: str) -> Path | None:
    out = subprocess.run(
        ["git", "-C", str(repo_path), "worktree", "list", "--porcelain"],
        check=True, capture_output=True, text=True,
    ).stdout

    cur_path: str | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur_path = line[len("worktree "):]
        elif line.startswith("branch "):
            ref = line[len("branch "):]
            if ref == f"refs/heads/{branch}" and cur_path:
                return Path(cur_path)
    return None
