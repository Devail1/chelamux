"""⚖️🧰 CMX-259: tool-config files — their guard is the CI job that runs them, not the suite.

CMX-176 (#751) burned 4 judge rounds on single lines of `eslint.config.js`,
`.dependency-cruiser.cjs` and `package.json` scripts while CI was green throughout: each
round's fix added a fixture test for that round's survivors, and the next round mutated a
different line. A scaffold has dozens of such lines, so the loop never converges.

A SURVIVED mutation in one of these files is therefore ADVISORY — listed in the verdict, never
blocking — UNLESS a test the PR itself changes NAMES the file. A claimed guard must still be
real, so a claim keeps it blocking. ⛔ Every unknown keeps it blocking too: an unreadable diff,
an unreadable file, a JSON key scope that cannot be proven.

``judge.tool_config`` in WORKFLOW.md replaces :data:`DEFAULT_GLOBS` (``[]`` turns the policy
off). An entry is a glob — matched against the repo-relative path, and against the basename
when it has no ``/`` — optionally ``:key``-scoped to ONE top-level key of a JSON file.
"""
from __future__ import annotations

import fnmatch
import json
import subprocess
from pathlib import Path, PurePosixPath

DEFAULT_GLOBS = (
    "eslint.config.*", ".dependency-cruiser.*", "tsconfig*.json", "vite.config.*",
    "vitest.config.*", "package.json:scripts", ".github/workflows/*",
)
GIT_TIMEOUT_SECONDS = 60


def globs_from(value: object) -> tuple[str, ...]:
    """``judge.tool_config`` as globs; anything but a list of non-empty strings = the default."""
    if isinstance(value, list) and all(isinstance(v, str) and v.strip() for v in value):
        return tuple(v.strip() for v in value)
    return DEFAULT_GLOBS


def _matches(rel: str, glob: str) -> bool:
    # A glob with a `/` can never match a basename, so only a bare one matches at any depth.
    return fnmatch.fnmatchcase(rel, glob) or fnmatch.fnmatchcase(PurePosixPath(rel).name, glob)


def entry_for(rel: str, globs: tuple[str, ...]) -> str | None:
    """The first ``judge.tool_config`` entry ``rel`` falls under, or None for code."""
    for entry in globs:
        if _matches(rel, entry.partition(":")[0]):
            return entry
    return None


def code_first(items: list, globs: tuple[str, ...]) -> list:
    """Experiments on code before experiments on tool config (stable), so a budget or a cap
    that runs out starves the config ones, never the code ones."""
    def is_config(raw) -> bool:
        rel = raw.get("file") if isinstance(raw, dict) else None
        return isinstance(rel, str) and entry_for(rel.strip(), globs) is not None
    return sorted(items, key=is_config)


def only_key_changed(text: str, before: str, after: str, key: str) -> bool:
    """Does the mutation change ``key`` of this JSON object and nothing else?"""
    try:
        old, new = json.loads(text), json.loads(text.replace(before, after, 1))
    except ValueError:
        return False
    if not isinstance(old, dict) or not isinstance(new, dict):
        return False
    rest = lambda d: {k: v for k, v in d.items() if k != key}  # noqa: E731
    return old.get(key) != new.get(key) and rest(old) == rest(new)


def _git(worktree: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(worktree), *args], capture_output=True,
                             text=True, errors="replace", timeout=GIT_TIMEOUT_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout if out.returncode == 0 else None


def _is_test(rel: str) -> bool:
    from chela import sweep     # sweep imports judge, which imports this module

    parts = PurePosixPath(rel).parts[:-1]
    return sweep.is_test_path(rel) or any(p in ("tests", "__tests__") for p in parts)


def changed_tests(worktree: Path, base_branch: str) -> dict[str, str] | None:
    """``{path: text}`` of every test file this branch changes vs ``origin/<base_branch>`` —
    committed, uncommitted or untracked (a self-check runs before the commit). None when git
    cannot say, which keeps every tool-config survivor BLOCKING."""
    base = _git(worktree, "merge-base", f"origin/{base_branch}", "HEAD")
    diff = _git(worktree, "diff", "--name-only", "--no-renames", base.strip()) if base else None
    untracked = _git(worktree, "ls-files", "--others", "--exclude-standard")
    if diff is None or untracked is None:
        return None
    tests = {}
    for rel in (diff + untracked).splitlines():
        if rel.strip() and _is_test(rel):
            try:
                tests[rel] = (worktree / rel).read_text(errors="replace")
            except OSError:
                continue    # a deleted test claims nothing
    return tests


def claimed_by(rel: str, tests: dict[str, str]) -> list[str]:
    """The changed tests that name ``rel`` (its path, or its basename)."""
    name = PurePosixPath(rel).name
    return [t for t, text in tests.items() if rel in text or name in text]


def classify(outcomes: list, worktree: Path, base_branch: str,
             globs: tuple[str, ...]) -> None:
    """Mark each unclaimed SURVIVED tool-config outcome ADVISORY, in place."""
    tests: dict[str, str] | None = None
    looked = False
    for o in outcomes:
        exp = o.experiment
        entry = entry_for(exp.file, globs) if o.verdict == "SURVIVED" else None
        if entry is None:
            continue
        key = entry.partition(":")[2]
        if key:
            try:
                text = (worktree / exp.file).read_text()
            except (OSError, ValueError):     # UnicodeDecodeError is a ValueError
                continue
            if not only_key_changed(text, exp.before, exp.after, key):
                continue
        if not looked:
            tests, looked = changed_tests(worktree, base_branch), True
        if tests is None:
            o.reason += " — blocking: the changed tests could not be read to rule out a claim"
            continue
        claims = claimed_by(exp.file, tests)
        if claims:
            o.reason += (f" — blocking: `{claims[0]}` names this tool-config file, so it "
                         "claims to guard it")
            continue
        o.advisory = (f"`{exp.file}` is tool config (`{entry}`) that CI enforces, and no test "
                      "this PR changes names it")
