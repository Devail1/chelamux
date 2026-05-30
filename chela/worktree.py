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
    (e.g. `pclw-7`). The worktree directory is still keyed by `task_id` so the
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
