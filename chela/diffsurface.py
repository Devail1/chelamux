"""Per-session CHANGED-FILES / DIFF surface (CMX-299).

Every agent tile already shows *how the session is doing* — branch, context %,
cost — but nothing has ever shown *what it actually touched*. This module is
the read-only git plumbing behind that: given a session's live working
directory (a tmux pane's cwd — usually a dispatcher worktree, but any git
checkout works the same), report every file changed since the last commit
(staged, unstaged, and untracked, merged into one list) and, on request, the
unified diff for one of those files.

Deliberately scoped to "since HEAD", not "since the branch's base" — a running
session has no reliable notion of its own base branch (an attended session in
a plain checkout has none at all), while HEAD is always well-defined. Renames
are reported as a delete+add pair (``--no-renames``): simpler and unambiguous,
at the cost of not flagging a pure rename as one row.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

_GIT_TIMEOUT = 15  # s — a pane's cwd is local disk; anything slower is a hang, not work

_STATUS_NAMES = {
    "A": "added",
    "M": "modified",
    "D": "deleted",
    "T": "modified",  # type change (e.g. file -> symlink) — no dedicated UI state for it
    "U": "conflicted",
}

# Read cap for the untracked-file line count (changed_files' additions estimate) —
# large enough for any real source file, small enough that a stray binary/log
# dropped in the worktree can't make the endpoint stall on a multi-GB read.
_UNTRACKED_READ_CAP = 5_000_000


def _run(cwd: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True, text=True, errors="replace", timeout=_GIT_TIMEOUT,
    )


def is_git_repo(cwd: Path) -> bool:
    out = _run(cwd, ["rev-parse", "--is-inside-work-tree"])
    return out.returncode == 0 and out.stdout.strip() == "true"


def _has_head(cwd: Path) -> bool:
    return _run(cwd, ["rev-parse", "--verify", "--quiet", "HEAD"]).returncode == 0


def _count_lines(path: Path) -> int:
    """Best-effort line count for an untracked file's "additions" estimate.
    Never raises: an unreadable/binary/vanished file just counts as 0 rather
    than failing the whole changed-files response over one stray path.
    """
    try:
        with open(path, "rb") as fh:
            data = fh.read(_UNTRACKED_READ_CAP)
    except OSError:
        return 0
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def changed_files(cwd: Path) -> dict:
    """Everything ``cwd`` has changed since its last commit, staged + unstaged +
    untracked, merged into one file list ordered as git reports it (tracked
    changes first, then untracked).

    Returns ``{"is_git", "has_head", "files", "additions", "deletions"}``.
    ``is_git`` False (not a directory, or not inside a git work tree) and
    ``has_head`` False (a repo with no commits yet — a fresh ``git init``) both
    degrade to an empty file list rather than raising; neither is an error, a
    dashboard poll just has nothing to show yet.
    """
    cwd = Path(cwd)
    if not cwd.is_dir() or not is_git_repo(cwd):
        return {"is_git": False, "has_head": False, "files": [], "additions": 0, "deletions": 0}

    has_head = _has_head(cwd)
    entries: dict[str, dict] = {}
    order: list[str] = []

    if has_head:
        name_status = _run(cwd, ["diff", "HEAD", "--no-renames", "--name-status"])
        if name_status.returncode == 0:
            for line in name_status.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t", 1)
                if len(parts) != 2:
                    continue
                code, path = parts
                if path in entries:
                    continue
                order.append(path)
                entries[path] = {
                    "path": path, "status": _STATUS_NAMES.get(code[0], "modified"),
                    "additions": 0, "deletions": 0,
                }

        numstat = _run(cwd, ["diff", "HEAD", "--no-renames", "--numstat"])
        if numstat.returncode == 0:
            for line in numstat.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t", 2)
                if len(parts) != 3:
                    continue
                added_s, deleted_s, path = parts
                entry = entries.get(path)
                if entry is None:
                    continue  # name-status and numstat disagreeing means stale data mid-poll
                if added_s != "-":  # "-" = binary file, no line-level signal
                    entry["additions"] = int(added_s)
                if deleted_s != "-":
                    entry["deletions"] = int(deleted_s)

        # `git diff HEAD --name-status` never reports the "U" code — it diffs
        # the worktree against a tree, and an in-progress merge conflict has
        # no unmerged concept at that level, so every conflicted path already
        # came back "M" above. The unmerged stages only show up in a diff
        # against the INDEX (no ref), filtered to just those: recover the
        # real "U" code from there and route it through _STATUS_NAMES too, so
        # a conflicted path is reported as "conflicted" instead of "modified".
        conflicted = _run(cwd, ["diff", "--no-renames", "--name-status", "--diff-filter=U"])
        if conflicted.returncode == 0:
            for line in conflicted.stdout.splitlines():
                if not line.strip():
                    continue
                parts = line.split("\t", 1)
                if len(parts) != 2:
                    continue
                code, path = parts
                entry = entries.get(path)
                if entry is not None:
                    entry["status"] = _STATUS_NAMES.get(code[0], entry["status"])

    untracked = _run(cwd, ["ls-files", "--others", "--exclude-standard"])
    if untracked.returncode == 0:
        for path in untracked.stdout.splitlines():
            path = path.strip()
            if not path or path in entries:
                continue
            order.append(path)
            entries[path] = {
                "path": path, "status": "untracked",
                "additions": _count_lines(cwd / path), "deletions": 0,
            }

    files = [entries[p] for p in order]
    return {
        "is_git": True,
        "has_head": has_head,
        "files": files,
        "additions": sum(f["additions"] for f in files),
        "deletions": sum(f["deletions"] for f in files),
    }


def file_patch(cwd: Path, path: str) -> dict:
    """Unified diff text for one file — but ONLY a file :func:`changed_files`
    itself just reported for this ``cwd``. That gate is what keeps ``path``
    safe to pass straight to git: every path changed_files can produce comes
    from git's own ``diff``/``ls-files`` output, which never leaves the repo,
    so there is no traversal surface here to sanitize separately.
    """
    cwd = Path(cwd)
    if not cwd.is_dir() or not is_git_repo(cwd):
        return {"ok": False, "error": "not a git repository"}

    state = changed_files(cwd)
    match = next((f for f in state["files"] if f["path"] == path), None)
    if match is None:
        return {"ok": False, "error": "not a changed file in this session"}

    if match["status"] == "untracked":
        # --no-index diffs two arbitrary paths outside any repo-relative walk,
        # so it exits 1 (not 0) on the expected case — files that differ.
        out = _run(cwd, ["diff", "--no-index", "--", "/dev/null", path])
        if out.returncode not in (0, 1):
            return {"ok": False, "error": out.stderr.strip() or "git diff failed"}
        return {"ok": True, "patch": out.stdout}

    out = _run(cwd, ["diff", "HEAD", "--no-renames", "--", path])
    if out.returncode != 0:
        return {"ok": False, "error": out.stderr.strip() or "git diff failed"}
    return {"ok": True, "patch": out.stdout}
