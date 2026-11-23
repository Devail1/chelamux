"""⚡ CMX-488: one GREEN baseline per tree, shared by self-check, sweep and the judge.

A core hand-in runs the self-check and then the sweep on the same tree, and the judge's
first round measures that tree again in its own worktree. A green baseline's JUnit report is
filed under ``$CHELA_DIR/baselines`` by ``(tree sha, test_cmd, deselects)`` so the later
readers reuse it (``docs/spikes/TEST_SPEED.md`` §4, slice D).

* **The tree sha is the WORKING tree's**, not HEAD's: tracked edits and untracked,
  non-ignored files count, so a self-check on uncommitted edits keys to what it ran.
* **Reuse never crosses tree shas.** An entry stores its own key and a read checks it.
* **Unknown never reads as green.** A missing, unreadable, malformed or non-green entry, or
  a tree sha that cannot be computed, is a miss and the suite runs.

The cache is an optimisation: a failure here is a miss, never an exception into the judge.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)

#: Bumped when the entry layout changes: an older entry is then a miss, never misread.
VERSION = 1
GIT_TIMEOUT = 120
#: A report is megabytes and every tree adds one: only the newest are kept.
KEEP = 60
#: Ignored dirs no suite reads its result from; ``.venv``/``node_modules`` are fingerprinted.
CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".ruff_cache"})
MAX_IGNORED_BYTES = 256 * 1024 * 1024


def cache_dir() -> Path:
    return Path(config.CHELA_DIR) / "baselines"


def _git(worktree: Path, *args: str, index: Path | None = None) -> str | None:
    # ⛔ No operator GIT_* (GIT_DIR, GIT_INDEX_FILE): they would point git at another repo.
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    try:
        out = subprocess.run(["git", "-C", str(worktree), *args], capture_output=True,
                             text=True, errors="replace", env=env, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def tree_sha(worktree: Path) -> str:
    """The sha of the worktree's CURRENT contents; ``""`` when it cannot be computed.

    ⛔ Built from HEAD in a throwaway index, never a copy of the real one: a copy carries its
    stat cache with a newer index mtime, which defeats git's racy-edit check (a same-size
    edit would keep the OLD sha), plus its skip-worktree/assume-unchanged bits."""
    try:
        with tempfile.TemporaryDirectory(prefix="chela-baseline-tree-") as scratch:
            index = Path(scratch) / "index"
            for args in (("read-tree", "HEAD"), ("add", "-A")):
                if _git(worktree, *args, index=index) is None:
                    return ""
            return _git(worktree, "write-tree", index=index) or ""
    except OSError:
        return ""


def _tool_dir_lines(path: Path, name: str) -> list[str]:
    """What a provisioned dependency dir has INSTALLED (its bytes differ per worktree)."""
    if name == ".venv":
        return sorted(p.name for p in path.glob("lib/*/site-packages/*.dist-info"))
    lock = path / ".package-lock.json"
    return [hashlib.sha256(lock.read_bytes()).hexdigest()] if lock.is_file() else []


def env_sha(worktree: Path) -> str:
    """What the tree sha cannot see and a suite can: the ignored files (by content) and the
    installed packages. ``""`` when unknown. ⛔ Without it, a judge whose worktree lacks an
    ignored file the agent's had would reuse that green, be red on every mutation for want of
    the file, and report every guard KILLED."""
    worktree = Path(worktree)
    listed = _git(worktree, "ls-files", "-z", "-o", "-i", "--exclude-standard", "--directory")
    if listed is None:
        return ""
    lines: list[str] = []
    budget = MAX_IGNORED_BYTES
    try:
        for rel in sorted(filter(None, listed.split("\0"))):
            path, parts = worktree / rel, Path(rel).parts
            if any(part in CACHE_DIRS for part in parts):
                continue
            if parts[-1] in (".venv", "node_modules"):
                lines += [rel, *_tool_dir_lines(path, parts[-1])]
                continue
            for f in sorted(path.rglob("*")) if path.is_dir() else [path]:
                name = f.relative_to(worktree)
                if not f.is_file() or any(part in CACHE_DIRS for part in name.parts):
                    continue
                budget -= f.stat().st_size
                if budget < 0:
                    return ""
                lines += [str(name), hashlib.sha256(f.read_bytes()).hexdigest()]
    except OSError:
        return ""
    return hashlib.sha256(json.dumps(lines).encode()).hexdigest()


def deselects_of(test_cmd: str) -> tuple[str, ...] | None:
    """The sorted node ids ``test_cmd`` deselects; ``None`` if it does not tokenise."""
    try:
        tokens = shlex.split(test_cmd)
    except ValueError:
        return None
    found = {t.removeprefix("--deselect=") for t in tokens if t.startswith("--deselect=")}
    found |= {b for a, b in zip(tokens, tokens[1:]) if a == "--deselect"}
    return tuple(sorted(found))


@dataclass(frozen=True)
class Key:
    tree: str
    test_cmd: str           # verbatim: quoting changes what the shell runs
    deselects: tuple[str, ...]
    env: str = ""           # :func:`env_sha`

    def fields(self) -> dict:
        return {"version": VERSION, "tree": self.tree, "test_cmd": self.test_cmd,
                "deselects": list(self.deselects), "env": self.env}

    def path(self) -> Path:
        raw = json.dumps(self.fields(), sort_keys=True)
        return cache_dir() / f"{hashlib.sha256(raw.encode()).hexdigest()}.json"


def key_for(worktree: Path, test_cmd: str) -> Key | None:
    """The cache key for ``test_cmd`` on ``worktree`` as it is NOW, or ``None``."""
    deselects = deselects_of(test_cmd)
    if deselects is None:
        return None
    sha, env = tree_sha(worktree), env_sha(worktree)
    return Key(sha, test_cmd, deselects, env) if sha and env else None


@dataclass(frozen=True)
class Entry:
    passed: int
    tail: str
    junit: str          # the JUnit report's text, written back to the reader's own scratch


def load(key: Key | None) -> Entry | None:
    """The GREEN baseline stored for exactly ``key``, or ``None`` (a miss)."""
    if key is None:
        return None
    try:
        data = json.loads(key.path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    want = {**key.fields(), "green": True, "failed": 0, "errors": 0}
    if any(type(data.get(k)) is not type(v) or data[k] != v for k, v in want.items()):
        return None
    passed, tail, junit = data.get("passed"), data.get("tail"), data.get("junit")
    if type(passed) is not int or passed <= 0:
        return None     # a "green" that ran nothing is not green
    if not isinstance(junit, str) or not isinstance(tail, str):
        return None
    return Entry(passed, tail, junit)


def save(key: Key | None, *, passed: int, failed: int, errors: int, tail: str,
         junit: Path) -> bool:
    """File a GREEN baseline under ``key``, atomically. Counts that are not green are refused."""
    if key is None or passed <= 0 or failed or errors:
        return False
    tmp = ""
    try:
        data = {**key.fields(), "green": True, "passed": passed, "failed": failed,
                "errors": errors, "tail": tail, "junit": junit.read_text(encoding="utf-8")}
        cache_dir().mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache_dir(), prefix=".tmp-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, key.path())
    except (OSError, ValueError) as e:
        log.warning("baseline cache: could not store a baseline: %s", e)
        if tmp:
            Path(tmp).unlink(missing_ok=True)
        return False
    _prune()
    return True


def _prune() -> None:
    try:
        # every file, not just entries: a killed writer's temp file ages out too
        entries = sorted(cache_dir().iterdir(), key=lambda p: p.stat().st_mtime_ns)
        for old in entries[:-KEEP]:
            old.unlink(missing_ok=True)
    except OSError:
        pass
