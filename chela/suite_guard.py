"""🧠 CMX-119: every full-suite run (judge battery, ``judge sweep``, ``judge self-check``,
``task-finished --self-check-experiments``) goes through :func:`chela.judge.run_suite`, and
this is what that one choke point wraps it in.

1. **A memory ceiling per suite run** — a ``systemd-run --user --scope`` with ``MemoryMax``
   and no swap, so ONE runaway run is killed inside its own scope and reported as CANNOT
   VERIFY ("suite exceeded 5.1G"), not the machine. On 10-13 two self-checks were stopped by
   Claude Code for "critically low on memory" while leaked PM2 daemons (CMX-115) held 12 GB.
   ⛔ The guard is claimed only when the suite's own ``/proc/self/cgroup`` names the scope —
   a wrapper that exited 0 proves nothing about where the suite ran.
2. **Leak reaping** — after the run, whatever the suite left in its scope is SIGKILLed. A
   child reparented to PID 1 keeps its cgroup, so this reaches 10-13's leaked PM2 daemons.

Admission control (at most K suites at once, judges AND agents) is CMX-119 slice 2.

⛔ Never fix an OOM by raising the ceiling blindly: a fatter ceiling is a fatter target.
"""
from __future__ import annotations

import contextlib
import logging
import shlex
import subprocess
import tempfile
import uuid
from pathlib import Path

from chela import config, memcap

log = logging.getLogger(__name__)

# Peak cgroup memory.peak of ONE full `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` run
# (8,669 tests: pytest + 4 xdist workers + node/jsdom + browser tests), measured 2026-10-17
# with the CMX-115 leak fixed: 1,812,647,936 bytes. Sizes the default; it is not a limit.
SUITE_PEAK_BYTES = int(1.7 * 1024 ** 3)
DEFAULT_CEILING_BYTES = 3 * SUITE_PEAK_BYTES


def ceiling_bytes() -> int:
    """``CHELA_SUITE_MEMORY_MAX`` > ``suite_memory_max`` in config.json > 3x the measured
    peak. ``0`` turns the ceiling off."""
    return config.dashboard_setting("suite_memory_max", "CHELA_SUITE_MEMORY_MAX",
                                    DEFAULT_CEILING_BYTES, cast=config._cast_size)


def verdict(unit: str, cap: int, files: Path, returncode: int) -> str | None:
    """None when the run was bounded and stayed under ``cap``; otherwise why it CANNOT be
    read as a result. Reads what the scope itself wrote from inside — never the wrapper."""
    try:
        cgroup = (files / "cgroup").read_text()
    except OSError:
        cgroup = ""
    if f"/{unit}.scope" not in cgroup:
        return (f"the {config.human_size(cap)} memory ceiling was NOT applied — the suite's "
                f"/proc/self/cgroup does not name {unit}.scope, so chela refuses to read it")
    try:
        events = dict(line.split() for line in (files / "events").read_text().splitlines())
        oom = int(events.get("oom_kill", 0)) > 0
    except (OSError, ValueError):
        oom = returncode in (137, -9)     # the whole scope died before it could report
    if oom:
        return (f"suite exceeded {config.human_size(cap)} (its memory ceiling, CMX-119) and "
                f"was killed inside its own scope — CANNOT VERIFY, never a pass")
    return None


def _scope_works() -> bool:
    """A harmless scope, launched for real — ``systemd-run`` on PATH is not a user session."""
    if not memcap.available():
        return False
    try:
        subprocess.run(["systemd-run", "--user", "--scope", "--collect", "-q", "--", "true"],
                       check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return True


@contextlib.contextmanager
def bounded(test_cmd: str):
    """Yield ``(cmd, check)``: ``cmd`` runs ``test_cmd`` inside its own memory-capped scope
    and ``check(returncode)`` is :func:`verdict`. With the ceiling off, or no
    ``systemd-run``, ``cmd`` is ``test_cmd`` unchanged and ``check`` always passes."""
    cap = ceiling_bytes()
    if not cap or not _scope_works():
        if cap:
            log.warning("suite_guard: no working `systemd-run --user --scope` — this suite "
                        "runs WITHOUT a memory ceiling (CMX-119)")
        yield test_cmd, lambda _rc: None
        return
    unit = f"chela-suite-{uuid.uuid4().hex[:12]}"
    in_slice = f" --slice={memcap.SLICE_NAME}" if config.memory_slice_budget_bytes() else ""
    with tempfile.TemporaryDirectory(prefix="chela-suite-guard-") as tmp:
        files = Path(tmp)
        q = shlex.quote
        inner = (f"cat /proc/self/cgroup > {q(str(files / 'cgroup'))}; sh -c {q(test_cmd)}; "
                 f"rc=$?; cg=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup); "
                 f"cat \"$cg/memory.events\" > {q(str(files / 'events'))}; exit $rc")
        cmd = (f"systemd-run --user --scope --collect -q --unit={unit}{in_slice} "
               f"-p MemoryMax={cap} -p MemorySwapMax=0 -p OOMPolicy=continue "
               f"-- sh -c {q(inner)}")
        try:
            yield cmd, lambda rc: verdict(unit, cap, files, rc)
        finally:
            _reap(unit)


def _reap(unit: str) -> None:
    """SIGKILL whatever the suite left in its scope. A child reparented to PID 1 keeps its
    cgroup, so this reaches what 10-13's leaked PM2 daemons escaped (they ignored SIGTERM).
    An already-empty scope is collected and the call simply fails — nothing to do."""
    try:
        subprocess.run(["systemctl", "--user", "kill", "--signal=SIGKILL", f"{unit}.scope"],
                       capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        log.warning("suite_guard: could not reap %s.scope", unit)
