"""📏 CMX-126: the hand-in size gate. ``chela task-finished`` REFUSES a diff over
``hand_in.max_pr_lines`` changed lines (``hand_in.size_exclude`` globs don't count) unless the
agent gives ``--oversize-reason``. The rule was prose in the brief, and prose lost (CMX-95:
~1,300 lines, a 5h+ sweep)."""
from __future__ import annotations

import fnmatch
import subprocess
from pathlib import Path

MAX_PR_LINES = 300
SIZE_EXCLUDE = ("docs/*", "*.md", "changelog.d/*", "*.png", "*.jpg", "*.gif", "*.svg",
                "*.mp4", "*.webm")


def settings(wf) -> tuple[int, tuple[str, ...]]:
    """``(max_pr_lines, size_exclude)`` from the workflow. Raises ValueError on a bad value —
    a limit chela cannot read must refuse, never fall back to "no limit"."""
    limit = wf.get("hand_in", "max_pr_lines", default=MAX_PR_LINES)
    exclude = wf.get("hand_in", "size_exclude", default=SIZE_EXCLUDE)
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise ValueError(f"hand_in.max_pr_lines must be a positive integer, got {limit!r}")
    if not isinstance(exclude, (list, tuple)):
        raise ValueError(f"hand_in.size_exclude must be a list of globs, got {exclude!r}")
    return limit, tuple(str(p) for p in exclude)


def measure(worktree: str | Path, base: str, exclude) -> int:
    """Changed lines (added + deleted) on this branch since it forked from ``origin/<base>``,
    skipping paths matching ``exclude`` and binary files. Raises RuntimeError when git cannot
    say — an unknown size must never read as a small one."""
    argv = ["git", "diff", "--numstat", f"origin/{base}...HEAD"]
    try:
        proc = subprocess.run(argv, cwd=str(worktree), capture_output=True, text=True,
                              timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"could not measure the diff: {e}") from e
    if proc.returncode != 0:
        raise RuntimeError(f"`{' '.join(argv)}` failed: {proc.stderr.strip()[:200]}")
    total = 0
    for row in proc.stdout.splitlines():
        added, deleted, path = row.split("\t", 2)
        if added == "-" or any(fnmatch.fnmatch(path, p) for p in exclude):
            continue
        total += int(added) + int(deleted)
    return total


def render(record: dict) -> str:
    """The PR-body / judge-prompt note for a gated hand-in ("" when nothing to say)."""
    if record.get("oversize_reason"):
        return (f"📏 **Oversize override:** {record['lines']} changed lines (limit "
                f"{record['limit']}). Agent's reason: {record['oversize_reason']}")
    return ""
