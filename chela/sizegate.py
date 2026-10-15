"""📏 CMX-126: the hand-in size gate. ``chela task-finished`` REFUSES a diff over
``hand_in.max_pr_lines`` changed lines (``hand_in.size_exclude`` globs don't count) unless the
agent gives ``--oversize-reason`` or a ``--split-plan`` (slice 1 stays on this PR; chela files
the rest as ordered sub-issues). The rule was prose in the brief, and prose lost (CMX-95:
~1,300 lines, a 5h+ sweep)."""
from __future__ import annotations

import fnmatch
import json
import subprocess
from pathlib import Path

MAX_PR_LINES = 300
SIZE_EXCLUDE = ("docs/*", "*.md", "changelog.d/*", "*.png", "*.jpg", "*.gif", "*.svg",
                "*.mp4", "*.webm")
SLICE_KEYS = ("title", "files", "behaviour", "tests")


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


def load_plan(path: str | Path) -> tuple[list[dict], str | None]:
    """``(slices, error)`` from a ``--split-plan`` file: ``{"slices": [{title, files,
    behaviour, tests}, …]}``, at least two, slice 1 being what stays on this PR."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [], f"split plan {path} could not be read: {e}"
    slices = raw.get("slices") if isinstance(raw, dict) else None
    if not isinstance(slices, list) or len(slices) < 2:
        return [], "a split plan needs `slices`: a list of at least 2 (slice 1 = this PR)"
    for i, s in enumerate(slices, 1):
        missing = [k for k in SLICE_KEYS if not (isinstance(s, dict) and s.get(k))]
        if missing:
            return [], f"slice {i} is missing {', '.join(missing)}"
    return slices, None


def rest_branch(task_id: str) -> str:
    """The parked branch the agent moves the not-yet-shipped slices to."""
    return f"{task_id.lower()}-rest"


def slice_brief(s: dict, n: int, total: int, parent: str, branch: str) -> str:
    def _list(v) -> str:
        return ", ".join(map(str, v)) if isinstance(v, list) else str(v)
    return (f"{s['behaviour']}\n\n**Files:** {_list(s['files'])}\n**Tests:** {_list(s['tests'])}"
            f"\n\nSlice {n} of {total}, split from {parent} by the hand-in size gate. "
            f"Builds on branch `{branch}` — start from it.")


def file_sub_issues(source, parent: str, slices: list[dict], branch: str) -> list[str]:
    """Create one sub-issue of ``parent`` per slice after the first, each blocked by the one
    before it (the first by ``parent``, whose PR carries slice 1), so they run in order, and
    each with ``parent``'s priority. 📏 CMX-143: every created issue is READ BACK, and a
    missing ``blockedBy`` or a different priority refuses — an unverified write must not read
    as a filed one. Returns their identifiers. Raises RuntimeError naming what failed AND what
    was already filed — a half-filed split is not a filed one, and must never read as one."""
    info = source.issue_links(parent)
    if not info or isinstance(info.get("priority"), bool) or not isinstance(
            info.get("priority"), int):
        raise RuntimeError(f"could not read {parent}'s priority — filed nothing")
    priority = info["priority"]
    created: list[str] = []
    prev = parent
    for n, s in enumerate(slices[1:], 2):
        try:
            issue = source.create_issue(str(s["title"]),
                                        slice_brief(s, n, len(slices), parent, branch),
                                        priority=priority, blocked_by=[prev], parent=parent)
        except Exception as e:
            raise RuntimeError(f"filing slice {n} ({s['title']!r}) failed: {e}"
                               f" — already filed: {', '.join(created) or 'none'}") from e
        ident = issue["identifier"]
        created.append(ident)
        if issue.get("warnings"):
            raise RuntimeError(f"slice {n} filed as {ident} but "
                               f"{'; '.join(issue['warnings'])} — already filed: "
                               f"{', '.join(created)}")
        got = source.issue_links(ident)
        if not got:
            problem = "it could not be read back"
        elif prev.upper() not in got.get("blocked_by", ()):
            problem = f"the read-back has no blockedBy {prev} (it has {got.get('blocked_by') or 'none'})"
        elif got.get("priority") != priority:
            problem = f"the read-back has priority {got.get('priority')!r}, not {parent}'s {priority!r}"
        else:
            problem = None
        if problem:
            raise RuntimeError(f"slice {n} filed as {ident} but {problem} — already filed: "
                               f"{', '.join(created)}")
        prev = ident
    return created


def render(record: dict) -> str:
    """The PR-body / judge-prompt note for a gated hand-in ("" when nothing to say)."""
    if record.get("oversize_reason"):
        return (f"📏 **Oversize override:** {record['lines']} changed lines (limit "
                f"{record['limit']}). Agent's reason: {record['oversize_reason']}")
    if record.get("sub_issues"):
        return (f"📏 **Split hand-in:** slice 1 of {len(record['sub_issues']) + 1}; the rest "
                f"is parked on `{record['rest_branch']}` and filed as "
                f"{', '.join(record['sub_issues'])}.")
    return ""
