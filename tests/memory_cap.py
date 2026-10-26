"""CMX-354: a per-process memory cap for a pytest run that nothing else caps.

A suite started by ``chela task-finished`` or the judge runs inside a ``chela-suite-*``
systemd scope with ``MemoryMax`` (:func:`chela.suite_guard.bounded`). An agent's own
``pytest`` does not: on 2026-10-26 one xdist worker of a targeted run reached 11 GB RSS
(an unbounded loop under a fake ``subprocess.run``), the box hit 0 GB free, and the
low-memory reaper killed other agents' suites. A rule in prose loses to the prompt, so
the guard lives in the tool: ``tests/conftest.py`` calls :func:`install` at session start.

Outside a capped scope, every process that runs tests (each xdist worker, or the lone
process of a ``-p no:xdist`` / ``-n 0`` run) gets a soft ``RLIMIT_AS`` of
:data:`DEFAULT_CAP_BYTES`. A runaway test then dies of ``MemoryError`` in its own worker
and the session carries on. ``CHELA_TEST_MEMORY_CAP`` sets the size (``6G``, ``512M``,
bytes); ``0`` turns it off.

**Subprocesses are exempt.** ``RLIMIT_AS`` counts reserved address space, not memory used,
and is inherited across ``exec``. Node reserves gigabytes it never touches, and Chromium a
terabyte-scale cage, so a cap sized for a Python worker would stop vitest or playwright
from starting. :func:`install` therefore lifts the soft limit back to what it was for the
instant ``subprocess.Popen`` forks and execs a child, and puts it back after. Every
subprocess in this repo goes through ``Popen``. Only the soft limit is lowered; the hard
limit stays, which is what lets it be raised again.
"""
from __future__ import annotations

import os
import resource
import subprocess
import threading
from pathlib import Path

ENV_VAR = "CHELA_TEST_MEMORY_CAP"
DEFAULT_CAP_BYTES = 3 * 1024 ** 3
CGROUP_FILE = Path("/proc/self/cgroup")

_UNITS = {"": 1, "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4}

_lock = threading.Lock()
_state: dict = {"cap": None, "uncapped": None, "spawning": 0, "execute_child": None}


def parse_size(raw: str) -> int | None:
    """Bytes for ``"3G"``, ``"512m"``, ``"1.5G"``, ``"1048576"``; None if unparseable."""
    text = raw.strip().upper().removesuffix("B").removesuffix("I")
    unit = text[-1:] if text[-1:] in _UNITS else ""
    try:
        n = float(text[:-1] if unit else text)
    except ValueError:
        return None
    if not 0 <= n < float("inf"):           # negative, NaN or infinite
        return None
    return int(n * _UNITS[unit])


def cap_bytes(env) -> int:
    """``CHELA_TEST_MEMORY_CAP`` when it parses, else :data:`DEFAULT_CAP_BYTES`. ``0`` = off.
    A typo keeps the cap on: an unreadable knob must not quietly uncap the box."""
    raw = env.get(ENV_VAR, "").strip()
    if not raw:
        return DEFAULT_CAP_BYTES
    parsed = parse_size(raw)
    return DEFAULT_CAP_BYTES if parsed is None else parsed


def in_capped_scope(cgroup_text: str) -> bool:
    """True when ``/proc/self/cgroup`` puts this process under a cgroup that already has a
    memory ceiling: a ``chela-suite-*`` scope or an operator's ``memcap`` unit/slice."""
    for line in cgroup_text.splitlines():
        for part in line.split(":", 2)[-1].split("/"):
            if part.startswith("chela-suite-") or "memcap" in part:
                return True
    return False


def _read_cgroup() -> str:
    try:
        return CGROUP_FILE.read_text()
    except OSError:
        return ""


def wanted_cap(env=None) -> int:
    """The cap this process should get: 0 inside a capped scope or when switched off."""
    env = os.environ if env is None else env
    if in_capped_scope(_read_cgroup()):
        return 0
    return cap_bytes(env)


def describe(cap: int) -> str:
    return (f"chela tests: memory cap ON, {cap / 1024 ** 3:.1f} GiB of address space per "
            f"test process (subprocesses exempt); raise it with {ENV_VAR}=6G, or "
            f"{ENV_VAR}=0 to turn it off")


def _execute_child_uncapped(self, *args, **kwargs):
    with _lock:
        if _state["spawning"] == 0 and _state["cap"] is not None:
            resource.setrlimit(resource.RLIMIT_AS, _state["uncapped"])
        _state["spawning"] += 1
    try:
        return _state["execute_child"](self, *args, **kwargs)
    finally:
        with _lock:
            _state["spawning"] -= 1
            if _state["spawning"] == 0 and _state["cap"] is not None:
                resource.setrlimit(resource.RLIMIT_AS, _state["cap"])


def install(cap: int) -> bool:
    """Lower this process's soft ``RLIMIT_AS`` to ``cap`` (never raise it, never touch the
    hard limit) and exempt the children ``subprocess.Popen`` starts. False when ``cap`` is
    0 or the platform refuses the limit."""
    if cap <= 0:
        return False
    soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    new_soft = cap
    if soft != resource.RLIM_INFINITY:
        new_soft = min(new_soft, soft)
    if hard != resource.RLIM_INFINITY:
        new_soft = min(new_soft, hard)
    try:
        resource.setrlimit(resource.RLIMIT_AS, (new_soft, hard))
    except (ValueError, OSError):
        return False
    with _lock:
        _state["cap"] = (new_soft, hard)
        if _state["uncapped"] is None:      # a second install must not record the cap
            _state["uncapped"] = (soft, hard)
        if _state["execute_child"] is None:
            _state["execute_child"] = subprocess.Popen._execute_child  # type: ignore[attr-defined]
            subprocess.Popen._execute_child = _execute_child_uncapped  # type: ignore[attr-defined]
    return True
