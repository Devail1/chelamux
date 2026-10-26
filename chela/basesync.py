"""🔀 CMX-189: the hand-in base-sync gate. ``chela task-finished`` REFUSES a branch that
conflicts with a fresh ``origin/<base>``, or (unless ``--allow-behind``) is behind it, so the
judge grades what will actually merge. "Merge dev before hand-in" was prose, and prose lost."""
from __future__ import annotations

import subprocess
from pathlib import Path

FETCH_TIMEOUT = 120
GIT_TIMEOUT = 60


def _git(worktree: str | Path, *argv: str, timeout: int = GIT_TIMEOUT):
    cmd = ["git", *argv]
    try:
        return subprocess.run(cmd, cwd=str(worktree), capture_output=True, text=True,
                              timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"`{' '.join(cmd)}` could not run: {e}") from e


def check(worktree: str | Path, base: str) -> dict:
    """``{"behind": n, "conflicts": [paths]}`` for HEAD against a freshly fetched
    ``origin/<base>``. Raises RuntimeError when that cannot be measured — an unreachable
    remote or an unreadable ref is unknown, and unknown must never read as in sync."""
    fetch = _git(worktree, "fetch", "--quiet", "origin",
                 f"+refs/heads/{base}:refs/remotes/origin/{base}", timeout=FETCH_TIMEOUT)
    if fetch.returncode != 0:
        raise RuntimeError(f"`git fetch origin {base}` failed: {fetch.stderr.strip()[:200]}")
    count = _git(worktree, "rev-list", "--count", f"HEAD..origin/{base}")
    if count.returncode != 0 or not count.stdout.strip().isdigit():
        raise RuntimeError(f"could not count commits behind origin/{base}: "
                           f"{count.stderr.strip()[:200]}")
    merge = _git(worktree, "merge-tree", "--write-tree", "--name-only", "HEAD",
                 f"origin/{base}")
    if merge.returncode not in (0, 1):
        raise RuntimeError(f"`git merge-tree` against origin/{base} failed: "
                           f"{merge.stderr.strip()[:200]}")
    conflicts: list[str] = []
    if merge.returncode == 1:
        # Output: the tree OID, then one conflicted path per line, a blank line, messages.
        for line in merge.stdout.splitlines()[1:]:
            if not line:
                break
            if line not in conflicts:
                conflicts.append(line)
        if not conflicts:
            raise RuntimeError(f"`git merge-tree` reported a conflict with origin/{base} but named no file")
    return {"behind": int(count.stdout.strip()), "conflicts": conflicts}


def base_sha(worktree: str | Path, base: str) -> str:
    """The sha ``origin/<base>`` points at, as last fetched. Raises RuntimeError when unreadable."""
    out = _git(worktree, "rev-parse", "--verify", "--quiet", f"origin/{base}^{{commit}}")
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(f"could not read origin/{base}: {out.stderr.strip()[:200]}")
    return out.stdout.strip()


def moved(worktree: str | Path, base: str, since: str) -> dict | None:
    """⚖️🔀 CMX-334: did ``origin/<base>`` move past ``since`` (the sha the hand-in's checks
    started on) without HEAD having it? ``None`` when not; else :func:`check`'s answer plus
    ``to`` and ``dev_files`` — every path the new base commits touched. Raises RuntimeError
    when git cannot say: an unknown move is never "did not move"."""
    state = check(worktree, base)
    to = base_sha(worktree, base)
    if to == since or not state["behind"]:
        return None
    diff = _git(worktree, "diff", "--name-only", "--no-renames", since, to)
    if diff.returncode != 0:
        raise RuntimeError(f"could not list what origin/{base} changed since {since[:12]}: "
                           f"{diff.stderr.strip()[:200]}")
    return dict(state, to=to, dev_files=[f for f in diff.stdout.splitlines() if f.strip()])


def refusal(base: str, behind: int, conflicts: list[str]) -> str:
    """The actionable message: what is wrong, then the exact commands to fix it. A merge,
    never a rebase — ``request-push`` does a plain push, which a rewritten branch fails."""
    if conflicts:
        what = (f"this branch CONFLICTS with origin/{base} in {len(conflicts)} file(s):\n"
                + "".join(f"     {p}\n" for p in conflicts))
    else:
        what = (f"this branch is {behind} commit(s) behind origin/{base} — the judge must "
                "grade what will actually merge.\n")
    return (what +
            f"   Merge origin/{base} in (do NOT rebase — request-push does a plain push):\n"
            f"     git fetch origin {base}\n"
            f"     git merge origin/{base}\n"
            "     # resolve, `git add`, `git commit`, re-run the tests, then task-finished again\n"
            + ("" if conflicts else
               "   A deliberate hand-in on a stale base: pass --allow-behind (recorded).\n"))


def render(record: dict) -> str:
    """One line for the judge from the ``runs.base_sync`` record, "" when there is none."""
    lines = []
    if record.get("allow_behind"):
        lines.append(f"🔀 Handed in {record.get('behind')} commit(s) behind origin/"
                     f"{record.get('base')} with --allow-behind (merges cleanly).")
    move = record.get("moved")
    if isinstance(move, dict):
        overlap = move.get("overlap") or []
        lines.append(f"🔀 origin/{record.get('base')} moved mid-check ({move.get('from')} → "
                     f"{move.get('to')}); merged in, related tests re-run ({move.get('tests')}), "
                     + (f"experiments re-run on the overlap: {', '.join(overlap)}." if overlap
                        else "no PR file overlapped."))
    return " ".join(lines)
