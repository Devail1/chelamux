"""Host resources sampler (chela/dashboard/resources.py, CMX-172).

The pure math (``pct``/``cpu_pct``/``mem_from_meminfo``) is guarded directly —
each test is written to go RED under one specific corruption of the real
formula, per the CMX brief ("a guard that survives its own corruption is
decoration"). The degradation test mirrors ``tests/test_sessions_proc_shim.py``:
repoint the module's ``PROC`` at a directory that does not exist and require
the sampler to degrade CPU/RAM to ``None`` rather than raise — the whole
point being that a permissions error or a /proc-less host must never turn
``/api/resources`` into a 500.
"""
from __future__ import annotations

import pytest

from chela import config
from chela.dashboard import resources


# --- pct ---------------------------------------------------------------------

def test_pct_computes_the_ratio_as_a_percentage():
    assert resources.pct(25, 100) == 25.0
    assert resources.pct(1, 3) == 33.3


def test_pct_zero_total_is_zero_not_a_crash():
    # 🔴 GUARD: dropping this zero-guard turns a fresh/empty host into a
    # ZeroDivisionError, i.e. a 500 on /api/resources.
    assert resources.pct(0, 0) == 0.0
    assert resources.pct(5, 0) == 0.0


def test_pct_clamps_to_0_100():
    assert resources.pct(150, 100) == 100.0
    assert resources.pct(-5, 100) == 0.0


# --- cpu_pct -------------------------------------------------------------

def test_cpu_pct_identical_snapshots_is_zero():
    snap = (1000, 5000)
    assert resources.cpu_pct(snap, snap) == 0.0


def test_cpu_pct_fully_busy_delta_is_100():
    prev = (1000, 5000)
    cur = (1000, 5100)  # idle unchanged, total moved -> every jiffy was busy
    assert resources.cpu_pct(prev, cur) == 100.0


def test_cpu_pct_a_normal_delta_is_the_right_busy_fraction():
    prev = (1000, 5000)
    cur = (1200, 5500)  # idle +200, total +500 -> 300 busy / 500 total = 60%
    # 🔴 GUARD: swapping idle_delta/total_delta (or using idle_delta as the
    # numerator) gives 40% here instead of 60% — this pins the direction.
    assert resources.cpu_pct(prev, cur) == 60.0


# --- mem_from_meminfo ------------------------------------------------------

_MEMINFO = """MemTotal:       16384000 kB
MemFree:         1000000 kB
MemAvailable:    4096000 kB
Buffers:          200000 kB
Cached:          3000000 kB
"""


def test_mem_from_meminfo_parses_total_available_into_used_and_pct():
    result = resources.mem_from_meminfo(_MEMINFO)
    total = 16384000 * 1024
    available = 4096000 * 1024
    used = total - available
    assert result == {"used": used, "total": total, "pct": resources.pct(used, total)}


def test_mem_from_meminfo_missing_fields_returns_null():
    # 🔴 GUARD: a text blob missing MemAvailable (an ancient kernel, or a
    # corrupted read) must degrade to null, not raise/misparse to 0.
    assert resources.mem_from_meminfo("MemTotal: 16384000 kB\n") == {
        "used": None, "total": None, "pct": None,
    }


# --- sample() degradation ---------------------------------------------------

@pytest.fixture
def no_proc(monkeypatch, tmp_path):
    """Repoint PROC at a nonexistent path — mirrors test_sessions_proc_shim.py.

    Deliberately does NOT flip a host-level flag: a real Linux test runner has
    a real /proc, so ``resources._PROC_HOST`` stays True and every read
    through the repointed (bad) ``PROC`` must be treated as a genuine failure
    (-> null), not silently answered from the machine's own live /proc.
    """
    monkeypatch.setattr(resources, "PROC", tmp_path / "nonexistent-proc")
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    return tmp_path


def test_sample_degrades_cpu_and_ram_to_null_but_disk_stays_real(no_proc):
    result = resources.sample()
    assert result["cpu_pct"] is None
    assert result["mem_used"] is None
    assert result["mem_total"] is None
    assert result["mem_pct"] is None
    # Disk reads via shutil.disk_usage(config.CHELA_DIR), independent of PROC.
    assert result["disk_used"] is not None
    assert result["disk_total"] is not None
    assert result["disk_pct"] is not None


def test_sample_never_raises_even_when_proc_is_gone(no_proc):
    # Calling sample() at all (not just inspecting its fields) is the guard:
    # a read that raises instead of degrading would blow up right here.
    resources.sample()


# --- /api/resources ----------------------------------------------------------

@pytest.fixture
def client():
    from chela.dashboard import app as dash
    return dash.app.test_client()


def test_api_resources_returns_200_json(client):
    resp = client.get("/api/resources")
    assert resp.status_code == 200
    data = resp.get_json()
    for key in ("cpu_pct", "mem_used", "mem_total", "mem_pct", "disk_used", "disk_total", "disk_pct"):
        assert key in data


def test_api_resources_is_200_not_500_when_proc_is_gone(no_proc, client):
    resp = client.get("/api/resources")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["cpu_pct"] is None
    assert data["mem_pct"] is None
