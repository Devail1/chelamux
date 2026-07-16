"""Time-windowed cost — ``context.windowed_cost`` and ``/api/cost?window=``.

``cost_usd`` is CUMULATIVE per session (Claude Code's own running session total), and
``session_name`` is unique per session — a restarted agent gets a new session_name
starting near 0. So each session_name's readings are MONOTONIC: there are no
in-session resets to fight, only session boundaries. Windowed spend for one session is
``max(0, last_cum(<= window_end) - last_cum(< window_start))``, reading the baseline as
0 when the session has no snapshot before window_start. These tests pin that formula
against the cases that break a naive "just diff the endpoints" implementation:

* a session that starts and ends entirely inside the window,
* a session that started before the window and is still running inside it,
* a session with no activity inside the window at all (must contribute $0, not be
  skipped in a way that undercounts, and must not be double counted),
* an agent that restarted (two session_names) within one window — summed, not
  overwritten,
* and the live-vs-windowed split in the HTTP layer: ``window=live`` must NOT depend on
  the snapshot DB at all (it's the always-available current-snapshot read), so it
  keeps working even when context_snapshots is empty.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from chela import context, discovery
from chela.dashboard import app as dash


@pytest.fixture
def chela_db(tmp_path, monkeypatch):
    """context.py binds DB_PATH at import time, so point IT at a tmp file directly."""
    monkeypatch.setattr(context, "DB_PATH", tmp_path / "scheduler.db")
    return tmp_path


@pytest.fixture
def client():
    return dash.app.test_client()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _insert(agent: str, session_name: str, ts: datetime, cost_usd: float, model: str = "Sonnet") -> None:
    with closing(context._get_db()) as conn:
        conn.execute(
            "INSERT INTO context_snapshots (agent, ts, cost_usd, model, session_name) "
            "VALUES (?, ?, ?, ?, ?)",
            (agent, _iso(ts), cost_usd, model, session_name),
        )
        conn.commit()


NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=timezone.utc)


def test_session_entirely_inside_the_window_counts_its_full_cost(chela_db):
    start = NOW - timedelta(hours=2)
    _insert("cmx-1", "sess-a", NOW - timedelta(hours=1), 3.50)
    rows = context.windowed_cost(start, NOW)
    assert rows == [{"name": "cmx-1", "model": "Sonnet", "cost_usd": 3.50}]


def test_session_spanning_the_window_start_counts_only_the_delta(chela_db):
    # Cost accrued before the window (1.00) must not be re-counted; only the 2.00
    # that accrued DURING the window belongs to this window's total.
    start = NOW - timedelta(hours=1)
    _insert("cmx-1", "sess-a", NOW - timedelta(hours=2), 1.00)  # before window
    _insert("cmx-1", "sess-a", NOW - timedelta(minutes=30), 3.00)  # inside window
    rows = context.windowed_cost(start, NOW)
    assert rows == [{"name": "cmx-1", "model": "Sonnet", "cost_usd": 2.00}]


def test_session_idle_before_the_window_contributes_zero(chela_db):
    # All of this session's activity is well before the window; nothing changed
    # during [start, end], so it must contribute exactly $0 - not be dropped in a
    # way that silently undercounts, and not show a stale nonzero total either.
    start = NOW - timedelta(hours=1)
    _insert("cmx-1", "sess-a", NOW - timedelta(hours=5), 4.00)
    rows = context.windowed_cost(start, NOW)
    assert rows == [{"name": "cmx-1", "model": "Sonnet", "cost_usd": 0.0}]


def test_session_with_no_snapshot_at_or_before_window_end_is_excluded(chela_db):
    # The only snapshot is AFTER window_end - as of window_end this session hadn't
    # reported anything yet, so it must not appear at all.
    start = NOW - timedelta(hours=1)
    end = NOW - timedelta(hours=2)
    _insert("cmx-1", "sess-a", NOW, 5.00)
    rows = context.windowed_cost(start, end)
    assert rows == []


def test_agent_restart_within_window_sums_across_session_names(chela_db):
    # Same agent (tmux window), two Claude Code sessions inside one window (e.g. a
    # /clear or crash-restart) - a NEW session_name starts near 0. Both sessions'
    # windowed spend must be SUMMED for the agent, not overwritten by whichever
    # session's row happens to be read last.
    start = NOW - timedelta(hours=3)
    _insert("cmx-1", "sess-old", NOW - timedelta(hours=2), 2.00)
    _insert("cmx-1", "sess-new", NOW - timedelta(minutes=10), 0.75)
    rows = context.windowed_cost(start, NOW)
    assert rows == [{"name": "cmx-1", "model": "Sonnet", "cost_usd": 2.75}]


def test_sums_across_agents_and_a_null_cost_row_is_ignored(chela_db):
    start = NOW - timedelta(hours=1)
    _insert("cmx-1", "sess-a", NOW - timedelta(minutes=30), 1.25)
    _insert("cmx-2", "sess-b", NOW - timedelta(minutes=20), 2.25)
    with closing(context._get_db()) as conn:
        conn.execute(
            "INSERT INTO context_snapshots (agent, ts, cost_usd, model, session_name) VALUES (?, ?, ?, ?, ?)",
            ("cmx-3", _iso(NOW - timedelta(minutes=10)), None, "Sonnet", "sess-c"),
        )
        conn.commit()
    rows = context.windowed_cost(start, NOW)
    by_name = {r["name"]: r["cost_usd"] for r in rows}
    assert by_name == {"cmx-1": 1.25, "cmx-2": 2.25}
    assert "cmx-3" not in by_name


def test_api_cost_window_live_reads_the_current_snapshot_not_the_db(chela_db, client):
    # window=live must be the always-available read (context.live_snapshot) - it
    # keeps working even with an EMPTY context_snapshots table, which is exactly
    # the state of a fresh deploy or one where the capture cadence hasn't wired in
    # yet.
    with patch.object(discovery, "get_all_windows", return_value={"cmx-1": "@1"}), \
            patch.object(context, "live_snapshot", return_value={
                "name": "cmx-1", "model": "Opus", "cost_usd": 9.999,
            }):
        resp = client.get("/api/cost?window=live")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body == [{"name": "cmx-1", "model": "Opus", "cost_usd": 10.0}]


def test_api_cost_window_today_reads_windowed_history(chela_db, client):
    # No datetime mocking needed: insert relative to the REAL now and let the
    # endpoint compute its own "today" window. A single reading placed
    # "well inside today" (the previous version of this test) cannot tell
    # apart a correct since-UTC-midnight window from a 7d/30d-style lookback -
    # every one of those windows contains it too. Pin the actual boundary
    # instead: one reading from BEFORE today's UTC midnight (25h back always
    # crosses one, DST-free since this is UTC) that a since-midnight window
    # must exclude from the baseline, and one reading from today. If "today"
    # were corrupted into a longer lookback, the yesterday reading would fall
    # inside the window too, the baseline would be read as 0 instead of
    # yesterday's cumulative total, and the result would inflate to 10.5
    # instead of the correct delta of 1.5.
    real_now = datetime.now(timezone.utc)
    yesterday = real_now - timedelta(hours=25)
    _insert("cmx-1", "sess-a", yesterday, 9.00)
    _insert("cmx-1", "sess-a", real_now, 10.50)
    resp = client.get("/api/cost?window=today")
    assert resp.status_code == 200
    assert resp.get_json() == [{"name": "cmx-1", "model": "Sonnet", "cost_usd": 1.5}]


def test_api_cost_rejects_an_unknown_window_by_falling_back_to_live(chela_db, client):
    with patch.object(discovery, "get_all_windows", return_value={}):
        resp = client.get("/api/cost?window=nonsense")
    assert resp.status_code == 200
    assert resp.get_json() == []
