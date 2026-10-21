"""🧠 CMX-119: every full-suite run (judge battery, ``judge sweep``, ``judge self-check``,
``task-finished --self-check-experiments``) goes through :func:`chela.judge.run_suite`, and
this is what that one choke point wraps it in.

1. **A memory ceiling per suite run** — a ``systemd-run --user --scope`` with ``MemoryMax``
   and no swap, so ONE runaway run is killed inside its own scope and reported as CANNOT
   VERIFY ("suite exceeded 8.3G"), not the machine. On 10-13 two self-checks were stopped by
   Claude Code for "critically low on memory" while leaked PM2 daemons (CMX-115) held 12 GB.
   ⛔ The guard is claimed only when the suite's own ``/proc/self/cgroup`` names the scope —
   a wrapper that exited 0 proves nothing about where the suite ran.
2. **Leak reaping** — after the run, whatever the suite left in its scope is SIGKILLed. A
   child reparented to PID 1 keeps its cgroup, so this reaches 10-13's leaked PM2 daemons.
3. **Admission control** — at most K suites run at once box-wide, judges AND agents alike:
   K ``flock`` slots in ``$CHELA_DIR/suite-slots``. A lock dies with its holder, so a crashed
   run never strands a slot. A run waiting for one says so, and its wait is credited back
   (:func:`busy_clock`, :func:`waits_in`) so the wait is never read as a stuck or slow run.

⛔ Never fix an OOM by raising the ceiling blindly: a fatter ceiling is a fatter target.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

from chela import config, memcap

log = logging.getLogger(__name__)

# Peak anonymous memory of ONE full `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` run (10,075
# tests: pytest + 4 xdist workers + node/jsdom/Vitest + browser tests), sampled from the run's
# own cgroup `memory.stat` on 2026-10-21 with Vitest capped at 4 workers: 2,954,940,416 bytes
# (cgroup memory.peak 4.69 GB, the rest reclaimable page cache). Sizes the defaults; it is
# not a limit.
SUITE_PEAK_BYTES = 2_954_940_416
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


_scope_ok = False      # a probe that once worked is not re-run by every suite in a battery


def _scope_works() -> bool:
    """A harmless scope, launched for real — ``systemd-run`` on PATH is not a user session.
    Only a success is remembered: a failed probe is retried by the next run."""
    global _scope_ok
    if _scope_ok:
        return True
    if not memcap.available():
        return False
    try:
        subprocess.run(["systemd-run", "--user", "--scope", "--collect", "-q", "--", "true"],
                       check=True, capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    _scope_ok = True
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


# --- admission control: at most K suites at once, box-wide ------------------------------

SLOTS_DIRNAME = "suite-slots"
SLOT_POLL_SECONDS = 1.0
# A run waits at most this many of its own suite timeouts for a slot: every holder is itself
# bounded by one, so a queue that has not moved in that long is not a queue any more.
SLOT_WAIT_TIMEOUTS = 4
# The share of RAM the default K lets full suites use at their measured peak. The rest is
# the agents, judges, tmux and the dashboard themselves.
SUITE_RAM_SHARE = 0.5
DEFAULT_SLOTS_MAX = 4

_waited = 0.0                     # seconds THIS process has spent waiting for a slot
_waited_lock = threading.Lock()
_thread_waited: dict[tuple[int, str], float] = {}  # per (thread, cwd): what a ledger publishes


def _cast_slots(raw) -> int:
    """A whole number >= 0; anything else raises, so the next precedence level decides."""
    if isinstance(raw, (bool, float)):
        raise ValueError("not a slot count")
    n = int(raw.strip() if isinstance(raw, str) else raw)
    if n < 0:
        raise ValueError("not a slot count")
    return n


def _mem_total_bytes(meminfo: Path = Path("/proc/meminfo")) -> int | None:
    try:
        for line in meminfo.read_text().splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def default_slots() -> int:
    """``SUITE_RAM_SHARE`` of RAM over the measured peak, in [1, ``DEFAULT_SLOTS_MAX``].
    RAM that cannot be read is ONE slot — unknown never buys more concurrency."""
    total = _mem_total_bytes()
    if not total:
        return 1
    return max(1, min(DEFAULT_SLOTS_MAX, int(total * SUITE_RAM_SHARE) // SUITE_PEAK_BYTES))


def slots() -> int:
    """``CHELA_SUITE_SLOTS`` > ``suite_slots`` in config.json > :func:`default_slots`.
    ``0`` turns admission control off."""
    n = config.dashboard_setting("suite_slots", "CHELA_SUITE_SLOTS", None, cast=_cast_slots)
    return default_slots() if n is None else n


def max_wait(timeout: float) -> float:
    """How long a run with this suite ``timeout`` waits for a slot:
    ``CHELA_SUITE_SLOT_MAX_WAIT`` (seconds, > 0) > ``SLOT_WAIT_TIMEOUTS`` x ``timeout``."""
    try:
        override = float(os.environ.get("CHELA_SUITE_SLOT_MAX_WAIT", ""))
    except ValueError:
        override = 0.0
    return override if override > 0 else SLOT_WAIT_TIMEOUTS * timeout


def busy_clock() -> float:
    """``time.monotonic()`` minus what this process spent waiting for a slot — the clock a
    wall-clock BUDGET reads, so a queued run is not charged for the queue."""
    return time.monotonic() - _waited


def _proc_stat(pid: int) -> tuple[str, str] | None:
    """``(state, starttime)`` from ``/proc/<pid>/stat``, or None when it is gone."""
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return fields[0], fields[19]
    except (OSError, IndexError):
        return None


def _waits_dir() -> Path:
    return config.CHELA_DIR / SLOTS_DIRNAME / "waits"


class _Ledger:
    """One waiter's public record, ``waits/<pid>-<tid>.json``: where it waits, since when
    (``None`` once it holds a slot) and how long it has waited in all. It is what orders the
    queue (:func:`_must_queue`) and what the stuck probes credit (:func:`waits_in`)."""

    def __init__(self, cwd: str):
        self.path = _waits_dir() / f"{os.getpid()}-{threading.get_ident()}.json"
        self.cwd, self.waited = cwd, _thread_waited.get((threading.get_ident(), cwd), 0.0)
        stat = _proc_stat(os.getpid())
        self.start = stat[1] if stat else None

    def write(self, waiting_since: float | None) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"pid": os.getpid(), "start": self.start,
                                       "cwd": self.cwd, "waiting_since": waiting_since,
                                       "waited": self.waited}))
            tmp.replace(self.path)
        except OSError:
            log.debug("suite_guard: could not publish the slot wait", exc_info=True)


def _ledgers() -> list[tuple[Path, dict]]:
    """Every LIVE waiter's record. One whose process is gone, or whose pid was reused (the
    ``/proc`` start time differs), is removed — never credited, never queued behind."""
    try:
        files = list(_waits_dir().glob("*.json"))
    except OSError:
        return []
    out = []
    for path in files:
        try:
            rec = json.loads(path.read_text())
            stat = _proc_stat(int(rec["pid"]))
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if stat is None or stat[1] != rec.get("start"):
            path.unlink(missing_ok=True)
            continue
        out.append((path, {**rec, "state": stat[0]}))
    return out


def _must_queue(me: Path, since: float) -> bool:
    """Is an OLDER waiter still queued? Then it goes first: a battery that frees its slot and
    takes the next one a millisecond later must not starve a run already waiting. A stopped
    waiter (a fleet pause, ``T``) does not hold the line."""
    for path, rec in _ledgers():
        opened = rec.get("waiting_since")
        if path == me or opened is None or rec["state"] in ("T", "t"):
            continue
        if (float(opened), path.name) < (since, me.name):
            return True
    return False


def waits_in(worktree: str | Path) -> dict | None:
    """``{"waiting_since": epoch|None, "waited": seconds}`` over every live process that
    waited for a suite slot inside ``worktree`` (the earliest open wait; the summed total),
    or None when none did."""
    try:
        root = Path(worktree).resolve()
    except OSError:
        return None
    found, since, hit = 0.0, None, False
    for _path, rec in _ledgers():
        try:
            cwd, waited = Path(rec["cwd"]), float(rec.get("waited") or 0.0)
            opened = rec.get("waiting_since")
            opened = float(opened) if opened is not None else None
        except (KeyError, TypeError, ValueError):
            continue
        if cwd != root and root not in cwd.parents:
            continue
        hit, found = True, found + waited
        if opened is not None:
            since = opened if since is None else min(since, opened)
    return {"waiting_since": since, "waited": found} if hit else None


def _take(d: Path, k: int) -> int | None:
    """An fd holding one of the ``k`` slot locks, or None when every one is held."""
    for i in range(k):
        fd = os.open(d / f"slot-{i}", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            continue
        except BaseException:
            os.close(fd)
            raise
        return fd
    return None


def _holders(d: Path, k: int) -> str:
    """Who holds the slots, for the waiting line: ``"CMX-12-r1 (pid 7, 4m)"``…"""
    out = []
    for i in range(k):
        try:
            rec = json.loads((d / f"slot-{i}").read_text() or "{}")
            age = int(time.time() - float(rec["since"])) // 60
            out.append(f"{Path(rec['cwd']).name} (pid {rec['pid']}, {age}m)")
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return ", ".join(out) or "holders unknown"


class SlotTimeout(TimeoutError):
    """No suite slot came free in time. An ``OSError``, so ``run_suite`` reports it as a
    suite that could not be run — CANNOT VERIFY, never a pass."""


@contextlib.contextmanager
def slot(cwd: str | Path, on_wait=None, max_wait: float | None = None,
         poll: float = SLOT_POLL_SECONDS):
    """Hold one of the K suite slots for the ``with`` body; wait (visibly, first come first
    served) for one first, at most ``max_wait`` seconds (:class:`SlotTimeout`).

    ``on_wait(since)`` is told when a wait begins (``since`` = its wall-clock start) and
    ends (``None``). With ``K == 0`` the body runs at once. A slot dir that cannot be made
    raises ``OSError`` — :func:`chela.judge.run_suite` reports that as CANNOT VERIFY."""
    global _waited
    k = slots()
    if k == 0:
        yield
        return
    d = config.CHELA_DIR / SLOTS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    ledger = _Ledger(str(cwd))
    since, started, fd = time.time(), None, None
    try:
        while True:
            if not _must_queue(ledger.path, since) and (fd := _take(d, k)) is not None:
                break
            if started is None:
                started = time.monotonic()
                ledger.write(since)
                print(f"⏳ waiting for a suite slot — all {k} busy: {_holders(d, k)} "
                      f"(CMX-119; CHELA_SUITE_SLOTS)", file=sys.stderr, flush=True)
                if on_wait is not None:
                    on_wait(since)
            elif max_wait is not None and time.monotonic() - started >= max_wait:
                raise SlotTimeout(f"no suite slot came free in {max_wait:.0f}s "
                                  f"(all {k} busy: {_holders(d, k)})")
            time.sleep(poll)
    finally:
        if started is not None:
            spent = time.monotonic() - started
            with _waited_lock:
                _waited += spent
            ledger.waited += spent
            _thread_waited[(threading.get_ident(), ledger.cwd)] = ledger.waited
            ledger.write(None)      # kept: what the stuck probes credit, while we live
            if on_wait is not None:
                on_wait(None)
    try:
        os.ftruncate(fd, 0)
        os.pwrite(fd, json.dumps({"pid": os.getpid(), "cwd": str(cwd),
                                  "since": time.time()}).encode(), 0)
        yield
    finally:
        os.close(fd)
