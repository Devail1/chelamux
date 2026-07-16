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
from types import SimpleNamespace
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


# --- the PRODUCTION call-sites: the daemon loop actually calls maintenance_tick and the ------
# --- prune branch, not just the extracted seams -----------------------------------------------

def _run_one_daemon_tick(monkeypatch) -> None:
    """Drive exactly ONE iteration of ``cmd_run``'s ``while not stop.stopping`` loop with
    every other subsystem kept inert (no scheduler work, no window renames, no dispatch,
    no notify, no inbox, no rooms) so the tick reaches the capture/prune call-sites and stops.

    Mirrors ``tests/test_orchestrator_autolaunch.py::test_the_daemon_loop_calls_maybe_wake_
    on_the_inbox_tick`` — the same shape of test that catches a call-site being unwired
    (e.g. ``last_capture = maintenance_tick(...)`` reverted to ``last_capture = last_capture``,
    or the prune ``if _due(...)`` reverted to ``if False and _due(...)``) even though every
    unit test of the extracted seam (``maintenance_tick``, ``_due``, ``prune_snapshots``)
    stays green, because none of them exercise ``cmd_run`` itself.
    """
    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)

    def stop_after_one(self, _seconds):
        self._event.set()
        return True

    monkeypatch.setattr(main.GracefulShutdown, "wait", stop_after_one)

    monkeypatch.setattr(main.scheduler, "init", lambda: None)
    monkeypatch.setattr(main.scheduler, "tick", lambda: 0)
    monkeypatch.setattr(main.agent_manager, "reconcile_window_names", lambda: [])
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.notify, "enabled", lambda: False)
    monkeypatch.setattr(main.rooms, "has_pending", lambda: False)
    monkeypatch.setattr(main.inbox, "enabled", lambda: False)


def test_the_daemon_loop_calls_maintenance_tick_every_pass(monkeypatch):
    """🔴 WIRING (production call-site) — every test above exercises ``maintenance_tick`` in
    isolation, so they ALL stay green even if ``cmd_run`` never calls it (e.g. reverted to
    ``last_capture = last_capture``). This drives one real tick of the daemon loop and proves
    the wire is connected."""
    _run_one_daemon_tick(monkeypatch)

    calls = []
    monkeypatch.setattr(main, "maintenance_tick",
                        lambda last_capture, now: calls.append((last_capture, now)) or now)

    main.cmd_run(SimpleNamespace())

    assert len(calls) == 1, (
        "cmd_run did NOT call maintenance_tick on the loop pass — capture accrual is unwired "
        "and can be reverted with the suite green"
    )
    assert calls[0][0] == 0.0   # the loop's initial last_capture, fed through unmodified


def test_the_daemon_loop_calls_prune_snapshots_when_due(monkeypatch):
    """🔴 WIRING (production call-site) — ``test_prune_snapshots_deletes_old_keeps_recent``
    exercises ``context.prune_snapshots`` in isolation, so it stays green even if ``cmd_run``
    never reaches it (e.g. the prune guard reverted to ``if False and _due(...)``). This drives
    one real tick — ``last_prune`` starts at 0.0, so the real (unmocked) ``_due`` gate is due on
    the very first pass — and proves prune is actually invoked from the loop."""
    _run_one_daemon_tick(monkeypatch)

    calls = []
    monkeypatch.setattr(main.context, "prune_snapshots", lambda days: calls.append(days) or 0)
    # Keep maintenance_tick a no-op so this test isolates the prune call-site only.
    monkeypatch.setattr(main, "maintenance_tick", lambda last_capture, now: last_capture)

    main.cmd_run(SimpleNamespace())

    assert len(calls) == 1, (
        "cmd_run did NOT call context.prune_snapshots when the prune interval was due — "
        "retention is unwired and can be reverted with the suite green"
    )
    assert calls[0] == main.CONTEXT_SNAPSHOT_RETENTION_DAYS


# --- cadence PERSISTENCE across ticks: the single-tick tests above prove the loop calls -----
# --- the seam, but not that the cadence STATE survives to the next pass --------------------

def _run_daemon_ticks(monkeypatch, n: int) -> None:
    """Like ``_run_one_daemon_tick``, but drives exactly ``n`` iterations of the daemon loop.

    Round-1's single-tick tests can't see a cadence variable failing to persist between
    passes (``last_capture = maintenance_tick(...)`` discarding its return, or
    ``last_prune = now`` reverted to ``last_prune = last_prune``) — both leave the *first*
    tick looking identical to correct code. Only a second pass exposes it: a
    discarded/frozen cadence variable re-triggers the same call on tick 2 that should only
    have fired once.

    Deliberately does NOT mock ``time.time`` — ``main.time`` is the real stdlib module, shared
    process-wide, and Python's own logging calls ``time.time()`` once per LogRecord, so a
    scripted return-value sequence gets consumed (and exhausted) by log lines this test never
    intended to drive. Real wall-clock time works fine here on its own: two ticks execute
    microseconds apart, far below either cadence interval, while the real epoch (it's 2026) is
    already far *past* both — so "is this due" comes out correct in both directions without
    needing to fake it.
    """
    monkeypatch.setattr(main.GracefulShutdown, "install", lambda self: self)

    remaining = n

    def stop_after_n(self, _seconds):
        nonlocal remaining
        remaining -= 1
        if remaining <= 0:
            self._event.set()
            return True
        return False

    monkeypatch.setattr(main.GracefulShutdown, "wait", stop_after_n)

    monkeypatch.setattr(main.scheduler, "init", lambda: None)
    monkeypatch.setattr(main.scheduler, "tick", lambda: 0)
    monkeypatch.setattr(main.agent_manager, "reconcile_window_names", lambda: [])
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.notify, "enabled", lambda: False)
    monkeypatch.setattr(main.rooms, "has_pending", lambda: False)
    monkeypatch.setattr(main.inbox, "enabled", lambda: False)


def test_the_daemon_loop_persists_last_capture_across_ticks(monkeypatch):
    """🔴 WIRING (cadence persistence) — ``maintenance_tick(last_capture, now)`` called but its
    return DISCARDED (``last_capture`` never reassigned) still calls it once per tick, so the
    round-1 single-tick test above stays green. Drive two ticks and prove tick 2 is called with
    tick 1's returned timestamp, not the original ``0.0`` — otherwise capture keys off a frozen
    ``last_capture`` and runs on every 30s pass instead of on the interval config.py promises."""
    _run_daemon_ticks(monkeypatch, n=2)

    seen = []

    def fake_maintenance_tick(last_capture, now):
        seen.append((last_capture, now))
        return now   # simulate: capture ran and the cadence should advance to `now`

    monkeypatch.setattr(main, "maintenance_tick", fake_maintenance_tick)
    monkeypatch.setattr(main.context, "prune_snapshots", lambda days: 0)

    main.cmd_run(SimpleNamespace())

    assert len(seen) == 2
    assert seen[0][0] == 0.0             # tick 1: the loop's initial last_capture, unmodified
    assert seen[1][0] == seen[0][1], (   # tick 2's last_capture must be tick 1's returned `now`
        "cmd_run did not feed maintenance_tick's returned timestamp back in as the next "
        "tick's last_capture — the return is being discarded, so capture would re-run on "
        f"every daemon pass instead of on CAPTURE_INTERVAL_SECONDS (got {seen})"
    )


def test_the_daemon_loop_advances_last_prune_after_pruning(monkeypatch):
    """🔴 WIRING (cadence persistence) — the prune guard is inline (``last_prune = now``
    right before calling ``prune_snapshots``), so reverting it to ``last_prune = last_prune``
    still lets the round-1 single-tick test above pass (that test only drives ONE tick, and
    ``last_prune`` starts at ``0.0`` so the first pass is due either way). Drive two REAL
    ticks, microseconds apart — real code prunes once (tick 1 advances ``last_prune`` to ~now,
    so tick 2's microsecond gap isn't due); a frozen ``last_prune`` prunes on BOTH ticks (it
    never advances off ``0.0``, and the real epoch is always >> PRUNE_INTERVAL_SECONDS), which
    is a DELETE+commit against scheduler.db every 30s pass instead of once a day."""
    _run_daemon_ticks(monkeypatch, n=2)

    monkeypatch.setattr(main, "maintenance_tick", lambda last_capture, now: last_capture)
    calls = []
    monkeypatch.setattr(main.context, "prune_snapshots", lambda days: calls.append(days) or 0)

    main.cmd_run(SimpleNamespace())

    assert len(calls) == 1, (
        "context.prune_snapshots was called on both ticks — last_prune is not advancing "
        "after a prune, so retention runs on EVERY daemon pass instead of "
        f"PRUNE_INTERVAL_SECONDS (called {len(calls)} times)"
    )


def test_the_default_capture_interval_binds_the_real_gate(monkeypatch):
    """🔴 WIRING (default-interval binding) — the loop calls ``maintenance_tick(last_capture, now)``
    with NO explicit interval, so the cadence rides entirely on the
    ``interval=CAPTURE_INTERVAL_SECONDS`` default. Every test above either mocks
    ``maintenance_tick`` wholesale or passes ``interval=300`` by hand, so severing that default to
    ``0.0`` (capture fires on every 30s pass; the config knob is dead) stays green. Drive two REAL
    ticks microseconds apart through the real ``maintenance_tick``/``_due`` and prove ``capture_all``
    fires exactly ONCE: tick 1 is due (the 2026 epoch is far past the default interval vs
    ``last_capture=0.0``), tick 2's microsecond gap is not — unless the default is 0.0, which makes
    tick 2 due too. Mocks only ``capture_all`` (not the seam), so it also re-covers the
    discard-return mutation with less mocking."""
    _run_daemon_ticks(monkeypatch, n=2)

    captured = Mock()
    monkeypatch.setattr(context, "capture_all", captured)
    monkeypatch.setattr(main.context, "prune_snapshots", lambda days: 0)

    main.cmd_run(SimpleNamespace())

    assert captured.call_count == 1, (
        "capture_all fired more than once across two microsecond-apart ticks — the real "
        "CAPTURE_INTERVAL_SECONDS default is not binding the cadence gate (severing it to 0.0 "
        f"makes capture run on every daemon pass instead of on the interval); got "
        f"{captured.call_count} calls"
    )


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
