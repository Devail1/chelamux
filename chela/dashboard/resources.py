"""Host resources (CPU/RAM/Disk) for the dashboard header strip (CMX-172).

Sampled on-request, in the dashboard process, from stdlib reads only — no new
dependency (``psutil`` is NOT in the repo), no daemon-side polling, no shared
state file. Every value degrades to ``None`` on a read failure rather than
raising, so :func:`sample` — and therefore ``/api/resources`` — can never turn
a permissions error or a /proc-less host (macOS) into a 500.

``PROC`` is a module-level path, same shim as :data:`chela.sessions.PROC`, so
a test can repoint the whole lookup at a fixture tree. ``_PROC_HOST`` is
decided from the REAL filesystem, deliberately not from ``PROC`` — a host
that genuinely has ``/proc`` (Linux) but whose repointed/fixture read fails
means the fact is ABSENT (return ``None``); only a host with no ``/proc`` at
all (macOS) falls back to ``os.getloadavg()``. Same distinction sessions.py
draws for the same reason: a test pointing PROC at an empty fixture must not
silently answer from the machine's own live /proc instead.
"""
from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from chela import config

PROC = Path("/proc")
_PROC_HOST = Path("/proc").is_dir()

# Wall-clock gap between the two /proc/stat reads cpu_pct needs. Short enough
# that an on-request sample stays cheap; long enough to see a real delta.
_CPU_SAMPLE_INTERVAL = 0.1


def pct(used: float | None, total: float | None) -> float:
    """``used`` as a percentage of ``total``, clamped to [0, 100].

    ``total`` of 0 (or falsy/None) is "nothing to divide by" — 0%, never a
    ZeroDivisionError/NaN.
    """
    if not total:
        return 0.0
    return max(0.0, min(100.0, round((used / total) * 100, 1)))


def cpu_pct(prev: tuple[int, int], cur: tuple[int, int]) -> float:
    """Busy percentage between two ``(idle, total)`` /proc/stat snapshots.

    Identical snapshots (no elapsed jiffies) read as 0%, not a divide-by-zero.
    A snapshot pair where idle never moved reads as 100% busy.
    """
    idle_delta = cur[0] - prev[0]
    total_delta = cur[1] - prev[1]
    busy_delta = total_delta - idle_delta
    return pct(busy_delta, total_delta)


def mem_from_meminfo(text: str) -> dict:
    """Parse ``/proc/meminfo`` text into ``{used, total, pct}`` (bytes).

    ``used = MemTotal - MemAvailable`` — the same "available" accounting the
    kernel itself recommends over the older ``MemFree`` (which undercounts
    reclaimable cache as "used").
    """
    values = {}
    for line in text.splitlines():
        key, _, rest = line.partition(":")
        key = key.strip()
        if key not in ("MemTotal", "MemAvailable"):
            continue
        try:
            values[key] = int(rest.strip().split()[0]) * 1024
        except (ValueError, IndexError):
            continue
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if total is None or available is None:
        return {"used": None, "total": None, "pct": None}
    used = max(0, total - available)
    return {"used": used, "total": total, "pct": pct(used, total)}


def _cpu_snapshot() -> tuple[int, int] | None:
    """One ``(idle, total)`` reading off the aggregate ``cpu `` line, or None."""
    try:
        text = (PROC / "stat").read_text()
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("cpu "):
            try:
                nums = [int(field) for field in line.split()[1:]]
            except ValueError:
                return None
            if not nums:
                return None
            # idle + iowait (fields 3/4, 0-indexed) — both count as "not busy".
            idle = nums[3] + (nums[4] if len(nums) > 4 else 0)
            return (idle, sum(nums))
    return None


def _cpu_pct_fallback() -> float | None:
    """1-minute load average / cpu count — the only signal on a /proc-less host."""
    try:
        load1 = os.getloadavg()[0]
    except (OSError, AttributeError):
        return None
    cpus = os.cpu_count() or 1
    return pct(load1, cpus)


def _sample_cpu() -> float | None:
    if not _PROC_HOST:
        return _cpu_pct_fallback()
    prev = _cpu_snapshot()
    if prev is None:
        return None
    time.sleep(_CPU_SAMPLE_INTERVAL)
    cur = _cpu_snapshot()
    if cur is None:
        return None
    return cpu_pct(prev, cur)


def _sample_mem() -> dict:
    if not _PROC_HOST:
        return {"used": None, "total": None, "pct": None}
    try:
        text = (PROC / "meminfo").read_text()
    except OSError:
        return {"used": None, "total": None, "pct": None}
    return mem_from_meminfo(text)


def _sample_disk() -> dict:
    try:
        usage = shutil.disk_usage(config.CHELA_DIR)
    except OSError:
        return {"used": None, "total": None, "pct": None}
    return {"used": usage.used, "total": usage.total, "pct": pct(usage.used, usage.total)}


def sample() -> dict:
    """One reading of host CPU/RAM/Disk, ``None`` per-field on anything unreadable."""
    mem = _sample_mem()
    disk = _sample_disk()
    return {
        "cpu_pct": _sample_cpu(),
        "mem_used": mem["used"],
        "mem_total": mem["total"],
        "mem_pct": mem["pct"],
        "disk_used": disk["used"],
        "disk_total": disk["total"],
        "disk_pct": disk["pct"],
    }
