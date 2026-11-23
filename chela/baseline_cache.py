"""⚡ CMX-488: one GREEN baseline per tree, shared by self-check, sweep and the judge.

A core hand-in runs the self-check and then the sweep on the same tree, and the judge's
first round measures that same tree again in its own worktree. Each used to run its own
full-suite baseline (~7 min apiece). This module keys a green baseline's JUnit report by
``(tree sha, test_cmd, deselects)`` under ``$CHELA_DIR/baselines`` so the second and third
reader reuse the first one's run instead (``docs/spikes/TEST_SPEED.md`` §4, slice D).

The rules, each one fail-closed:

* **The tree sha is the WORKING tree**, not HEAD: tracked edits and untracked, non-ignored
  files are hashed in (a throwaway index, ``git add -A`` + ``git write-tree``). A self-check
  on uncommitted edits therefore keys to what it actually ran, and a judge of the committed
  head only reuses it if the trees are byte-identical.
* **Reuse never crosses tree shas.** The entry stores its own key and a read checks it
  field by field; a file for another tree, command or deselect set is a miss.
* **Only green is stored**, and only a run whose tree did not move under it (the sha is
  taken before and after; a mismatch stores nothing). A baseline that read green only
  because a flake was re-run is not stored either: the flake is evidence that belongs to the
  run that saw it.
* **Unknown never reads as green.** A missing, unreadable, malformed or non-green entry, or
  a tree sha that cannot be computed, is a miss and the suite runs as before.

The cache is an optimisation only: every failure here returns ``None``/``""`` and never
raises into the judge.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)

#: Bumped when the entry layout changes: an older entry is then a miss, never misread.
VERSION = 1
GIT_TIMEOUT = 120


def cache_dir() -> Path:
    """``$CHELA_DIR/baselines`` — read per call, so a test's scratch ``CHELA_DIR`` holds."""
    return Path(os.environ.get("CHELA_DIR") or config.CHELA_DIR) / "baselines"


def _git_env(index: Path | None = None) -> dict[str, str]:
    """The operator's env minus any ``GIT_*`` that would point git at another repo/index."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return env


def _git(worktree: Path, *args: str, env: dict[str, str]) -> str | None:
    try:
        out = subprocess.run(["git", "-C", str(worktree), *args], capture_output=True,
                             text=True, errors="replace", env=env, timeout=GIT_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def tree_sha(worktree: Path) -> str:
    """The sha of the worktree's CURRENT contents (tracked edits and untracked, non-ignored
    files included), written through a throwaway index so the real one is never touched.
    ``""`` when it cannot be computed: no sha, no reuse."""
    worktree = Path(worktree)
    real_index = _git(worktree, "rev-parse", "--path-format=absolute", "--git-path", "index",
                      env=_git_env())
    if not real_index:
        return ""
    try:
        with tempfile.TemporaryDirectory(prefix="chela-baseline-tree-") as scratch:
            index = Path(scratch) / "index"
            if Path(real_index).is_file():      # the stat cache: hash only what changed
                shutil.copyfile(real_index, index)
            env = _git_env(index)
            if _git(worktree, "add", "-A", env=env) is None:
                return ""
            return _git(worktree, "write-tree", env=env) or ""
    except OSError:
        return ""


def split_deselects(test_cmd: str) -> tuple[str, tuple[str, ...]] | None:
    """``(the command without its deselects, the sorted deselected node ids)``, so the same
    deselect set in another order keys to the same entry. ``None`` if the command does not
    tokenise (⇒ no key, no reuse)."""
    try:
        tokens = shlex.split(test_cmd)
    except ValueError:
        return None
    rest: list[str] = []
    deselects: set[str] = set()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok == "--deselect" and i + 1 < len(tokens):
            deselects.add(tokens[i + 1])
            i += 2
            continue
        if tok.startswith("--deselect="):
            deselects.add(tok[len("--deselect="):])
        else:
            rest.append(tok)
        i += 1
    return shlex.join(rest), tuple(sorted(deselects))


@dataclass(frozen=True)
class Key:
    tree: str
    test_cmd: str
    deselects: tuple[str, ...]

    @property
    def digest(self) -> str:
        raw = json.dumps([VERSION, self.tree, self.test_cmd, list(self.deselects)])
        return hashlib.sha256(raw.encode()).hexdigest()

    def path(self) -> Path:
        return cache_dir() / f"{self.digest}.json"


def key_for(worktree: Path, test_cmd: str) -> Key | None:
    """The cache key for ``test_cmd`` on ``worktree`` as it is NOW, or ``None``."""
    split = split_deselects(test_cmd)
    if split is None:
        return None
    sha = tree_sha(worktree)
    if not sha:
        return None
    return Key(sha, split[0], split[1])


@dataclass(frozen=True)
class Entry:
    passed: int
    failed: int
    errors: int
    tail: str
    junit: str          # the JUnit report's text, written back to the reader's own scratch


def _count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def load(key: Key | None) -> Entry | None:
    """The GREEN baseline stored for exactly ``key``, or ``None`` (a miss). Anything short of
    a well-formed, green entry whose stored key matches field by field is a miss."""
    if key is None:
        return None
    try:
        data = json.loads(key.path().read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    if (data.get("version") != VERSION or data.get("tree") != key.tree
            or data.get("test_cmd") != key.test_cmd
            or data.get("deselects") != list(key.deselects)):
        return None
    if data.get("green") is not True or data.get("exit_code") != 0:
        return None
    passed, failed, errors = (data.get(k) for k in ("passed", "failed", "errors"))
    if not (_count(passed) and _count(failed) and _count(errors)):
        return None
    assert isinstance(passed, int) and isinstance(failed, int) and isinstance(errors, int)
    if passed <= 0 or failed or errors:
        return None     # a "green" that ran nothing, or reported failures, is not green
    junit, tail = data.get("junit"), data.get("tail")
    if not isinstance(junit, str) or not junit or not isinstance(tail, str):
        return None
    return Entry(passed, failed, errors, tail, junit)


def save(key: Key | None, *, passed: int, failed: int, errors: int, tail: str,
         junit: Path) -> bool:
    """Store a GREEN baseline under ``key``. The caller vouches it is green; this refuses
    counts that say otherwise. Written atomically, so a reader never sees half a file."""
    if key is None or passed <= 0 or failed or errors:
        return False
    try:
        report = junit.read_text()
    except OSError:
        return False
    if not report:
        return False
    data = {"version": VERSION, "tree": key.tree, "test_cmd": key.test_cmd,
            "deselects": list(key.deselects), "green": True, "exit_code": 0,
            "passed": passed, "failed": failed, "errors": errors, "tail": tail,
            "junit": report}
    try:
        cache_dir().mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=cache_dir(), prefix=".tmp-", suffix=".json")
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)
        os.replace(tmp, key.path())
    except OSError as e:
        log.warning("baseline cache: could not store %s: %s", key.digest[:12], e)
        return False
    return True
