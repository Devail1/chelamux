"""Cost-history accrual (CMX-94).

``context.capture_all()`` writes one ``context_snapshots`` row per live statusLine
cache file — the ONLY thing that makes the Cost tab's Today/7d/30d windows possible,
since without accrued history there is nothing to sum over. It already existed but was
never called from anywhere; this covers the pieces that make it actually run: the
cadence gate (``chela.main._due``), the daemon-loop seam that calls it when due
(``chela.main.maintenance_tick``), and retention (``context.prune_snapshots``) so an
always-on daemon doesn't grow ``scheduler.db`` without bound.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from chela import context, main


@pytest.fixture
def db_and_cache(tmp_path, monkeypatch):
    """Point context.py's import-time-latched DB_PATH/CONTEXT_CACHE_DIR at a scratch
    dir (both are bound from config at import — see conftest's DB_PATH note)."""
    db_path = tmp_path / "scheduler.db"
    cache_dir = tmp_path / "context-cache"
    monkeypatch.setattr(context, "DB_PATH", db_path)
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", cache_dir)
    return db_path, cache_dir


def _write_cache(cache_dir, agent_name: str, cost_usd: float) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{agent_name}.json").write_text(json.dumps({
        "context_window": {
            "used_percentage": 12.5,
            "context_window_size": 200000,
            "remaining_percentage": 87.5,
        },
        "model": {"display_name": "claude-sonnet-5"},
        "cost": {"total_cost_usd": cost_usd},
        "session_name": "s1",
    }))


def _rows(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM context_snapshots")]
    conn.close()
    return rows


# --- capture_all ---------------------------------------------------------------

def test_capture_all_writes_a_row_per_live_snapshot(db_and_cache):
    db_path, cache_dir = db_and_cache
    _write_cache(cache_dir, "agent-1", cost_usd=1.23)

    results = context.capture_all()

    assert len(results) == 1
    rows = _rows(db_path)
    assert len(rows) == 1
    assert rows[0]["agent"] == "agent-1"
    assert rows[0]["cost_usd"] == 1.23


# --- _due (cadence gate) ---------------------------------------------------------

@pytest.mark.parametrize("last,now,interval,expected", [
    (0.0, 300.0, 300, True),     # exactly the interval has elapsed
    (0.0, 299.9, 300, False),    # just short
    (100.0, 500.0, 300, True),   # well past
    (0.0, 0.0, 300, False),      # no time elapsed yet
])
def test_due_is_true_iff_interval_has_elapsed(last, now, interval, expected):
    assert main._due(last, now, interval) is expected


# --- maintenance_tick (the daemon-loop wiring) ------------------------------------

def test_maintenance_tick_calls_capture_when_due(monkeypatch):
    captured = Mock()
    monkeypatch.setattr(context, "capture_all", captured)

    new_last = main.maintenance_tick(last_capture=0.0, now=300.0, interval=300)

    captured.assert_called_once()
    assert new_last == 300.0


def test_maintenance_tick_skips_capture_when_not_due(monkeypatch):
    captured = Mock()
    monkeypatch.setattr(context, "capture_all", captured)

    new_last = main.maintenance_tick(last_capture=100.0, now=200.0, interval=300)

    captured.assert_not_called()
    assert new_last == 100.0        # unchanged — the next call still measures from it


# --- prune_snapshots (retention) --------------------------------------------------

def _insert_snapshot(db_path, agent: str, age_days: float) -> None:
    ts = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO context_snapshots (agent, ts, cost_usd) VALUES (?, ?, ?)",
        (agent, ts, 1.0),
    )
    conn.commit()
    conn.close()


def test_prune_snapshots_deletes_old_keeps_recent(db_and_cache):
    db_path, _ = db_and_cache
    context._get_db().close()   # create the schema before inserting directly
    _insert_snapshot(db_path, "old-agent", age_days=40)
    _insert_snapshot(db_path, "recent-agent", age_days=1)

    deleted = context.prune_snapshots(older_than_days=30)

    assert deleted == 1
    remaining = _rows(db_path)
    assert len(remaining) == 1
    assert remaining[0]["agent"] == "recent-agent"
