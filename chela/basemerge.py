"""🔀 CMX-262: merge ``origin/<base>`` into a CONFLICTING PR's branch, in a SCRATCH worktree
detached at the PR's pushed head (never the run's own: an agent may be in it). ⛔ A textual
conflict is never resolved here (no ours/theirs): git merges cleanly, or the caller hands it back.
"""
from __future__ import annotations

import contextlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from chela import judge, judge_select
from chela.tui_text import sanitize

GIT_TIMEOUT = 60
FETCH_TIMEOUT = 120
TEST_TIMEOUT = 15 * 60

MERGED = "merged"       # clean, targeted tests green, merge commit pushed
CONFLICT = "conflict"   # git reported conflicts: ``files`` are the conflicted paths
RED = "red"             # clean, but the targeted tests failed: ``files`` are those tests
CURRENT = "current"     # the branch already contains origin/<base>: nothing to merge


def _git(cwd: str | Path, *argv: str, timeout: int = GIT_TIMEOUT) -> str:
    """``git argv`` in ``cwd``, its stdout. Raises RuntimeError on anything but exit 0."""
    try:
        cp = subprocess.run(["git", *argv], cwd=str(cwd), capture_output=True, text=True,
                            timeout=timeout)
    except (OSError, subprocess.SubprocessError) as e:
        raise RuntimeError(f"`git {' '.join(argv)}` could not run: {e}") from e
    if cp.returncode != 0:
        raise RuntimeError(f"`git {' '.join(argv)}` failed: "
                           f"{(cp.stderr or cp.stdout).strip()[:200]}")
    return cp.stdout.strip()


def targeted_tests(wt: Path, touched: list[str]) -> list[str]:
    """The PR's own ``test_*.py`` files, plus every ``tests/**/test_*.py`` that imports any
    other Python file it touches (a helper under tests/ included; pytest collects neither)."""
    own = {f for f in touched if Path(f).name.startswith("test_") and f.endswith(".py")}
    tests = {f for f in own if (wt / f).is_file()}
    mods = {m for f in set(touched) - own if f.endswith(".py")
            for m in judge_select.module_names(f)}
    for path in sorted((wt / "tests").rglob("test_*.py")):
        rel = path.relative_to(wt).as_posix()
        if judge_select._imports_module(
                judge_select.py_imports(rel, path.read_text(errors="replace")), mods):
            tests.add(rel)
    return sorted(tests)


def attempt(repo: str | Path, branch: str, base: str, test_cmd: str) -> dict:
    """Merge a fresh ``origin/<base>`` into the pushed ``origin/<branch>``; clean ⇒ run the
    targeted tests (none, or no ``test_cmd``: the PR's CI gates it) and push only when green.
    ``{outcome, base_sha, head_sha, merge_sha, files, detail}``; RuntimeError = unmeasured."""
    _git(repo, "fetch", "--quiet", "origin", f"+refs/heads/{base}:refs/remotes/origin/{base}",
         f"+refs/heads/{branch}:refs/remotes/origin/{branch}", timeout=FETCH_TIMEOUT)
    base_sha = _git(repo, "rev-parse", f"refs/remotes/origin/{base}")
    head_sha = _git(repo, "rev-parse", f"refs/remotes/origin/{branch}")
    out = {"base_sha": base_sha, "head_sha": head_sha, "merge_sha": None, "files": [],
           "detail": ""}
    scratch = Path(tempfile.mkdtemp(prefix="chela-basemerge-"))
    wt = scratch / "wt"
    try:
        _git(repo, "worktree", "add", "--detach", str(wt), head_sha)
        touched = _git(wt, "diff", "--name-only", f"{base_sha}...{head_sha}").splitlines()
        try:
            _git(wt, "merge", "--no-ff", "--no-edit", "-m",
                 f"Merge origin/{base} ({base_sha[:12]}) into {branch}", base_sha)
        except RuntimeError as e:
            files = _git(wt, "diff", "--name-only", "--diff-filter=U").splitlines()
            if not files:
                raise RuntimeError(f"the merge of origin/{base} failed naming no file: {e}") from e
            return {**out, "outcome": CONFLICT, "files": files}
        merge_sha = _git(wt, "rev-parse", "HEAD")
        if merge_sha == head_sha:
            return {**out, "outcome": CURRENT}
        tests = targeted_tests(wt, touched)
        if tests and test_cmd:
            cmd = (judge_select.extend(test_cmd, tests) if judge_select.extendable(test_cmd)
                   else test_cmd)
            res = judge.run_suite(cmd, wt, timeout=TEST_TIMEOUT)
            if not res.ok:      # never ran: nothing about the code, so not the agent's red
                raise RuntimeError(f"the targeted tests could not run: {res.detail}")
            if res.exit_code != 0 or res.timed_out:
                return {**out, "outcome": RED, "merge_sha": merge_sha, "files": tests,
                        "detail": (res.tail or res.detail)[-1500:]}
        _git(wt, "push", "origin", f"HEAD:refs/heads/{branch}", timeout=FETCH_TIMEOUT)
        return {**out, "outcome": MERGED, "merge_sha": merge_sha, "files": tests}
    finally:
        with contextlib.suppress(RuntimeError):
            _git(repo, "worktree", "remove", "--force", str(wt))
        shutil.rmtree(scratch, ignore_errors=True)
        with contextlib.suppress(RuntimeError):
            _git(repo, "worktree", "prune")


def rework_body(res: dict, base: str) -> str:
    """The verdict a CONFLICT or RED result sends back. Never charged against the cap."""
    sha = res["base_sha"][:12]
    files = "".join(f"- `{sanitize(f).replace('`', '')}`\n" for f in res["files"]) or "- (none)\n"
    if res["outcome"] == CONFLICT:
        head = (f"🔀 **Conflict with base after `{sha}` merged: merge origin/{base} and "
                f"resolve.**\n\nConflicting files:\n{files}")
    else:
        # Pasted into an agent's pane: sanitized, and fenced wider than any backtick run in it.
        tail = sanitize(res["detail"])
        fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", tail)), default=0) + 1)
        head = (f"🔀 **origin/{base} (`{sha}`) merges cleanly, but the targeted tests go red "
                f"on the merge: merge origin/{base} and fix them.**\n\nTests run:\n{files}"
                f"\n{fence}\n{tail}\n{fence}\n")
    return (f"{head}\n_Resolve it by hand; nothing was pushed. This round is not charged "
            "against the rework cap._\n")
