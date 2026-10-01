"""⏸️⚖️ CMX-413: every hold surface says WHAT the hold stops and WHAT is still finishing.

A held queue starts no new agent of any kind — no claim, no judge, no rework re-spawn — and
kills nothing. Nothing said either half, so "paused" read the same as "paused, but three
things are still running". The CLI (`--pause` / `--hold-status`), `chela doctor` and the
Settings row now all print::

    Held: new claims, judges and rework re-spawns. Still finishing: N agent(s), M judge(s)

with each in-flight item's CMX id and elapsed time. These tests pin that the counts come from
the runs table (never a constant), and that every surface actually renders them.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone

import pytest

from chela import dispatcher, doctor, hold, judge, main
from chela.dashboard import app as dash

NOW = datetime(2026, 9, 30, 20, 0, tzinfo=timezone.utc)


def _ago(minutes: int) -> str:
    return (NOW - timedelta(minutes=minutes)).isoformat()


def _insert(conn, task_id, status, branch, **over):
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "t",
        "status": status, "branch_name": branch, "window_name": branch,
        "started_at": _ago(1), "attempt": 1,
    }
    fields.update(over)
    conn.execute(
        f"INSERT INTO runs ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    """A runs DB per test — ``dispatcher.DB_PATH`` is latched at import (see conftest)."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


@pytest.fixture
def fleet():
    """Two agents and one live judge in flight — plus three rows that must NOT count."""
    with dispatcher._db() as conn:
        _insert(conn, "t7", "running", "cmx-7", started_at=_ago(12))
        _insert(conn, "t8", "claimed", "cmx-8", started_at=_ago(2))
        _insert(conn, "t9", "awaiting_review", "cmx-9",
                judge_state=judge.J_RUNNING, judge_started_at=_ago(4))
        # A judge_state of running with NO judge_started_at never launched — not live.
        _insert(conn, "t10", "awaiting_review", "cmx-10", judge_state=judge.J_RUNNING)
        _insert(conn, "t11", "awaiting_review", "cmx-11", judge_state=judge.J_CLEAN,
                judge_started_at=_ago(30))
        _insert(conn, "t12", "done", "cmx-12")


HEAD = ("Held: new claims, judges and rework re-spawns. "
        "Still finishing: 2 agent(s), 1 judge(s)")


def test_the_in_flight_counts_come_from_the_runs_table(fleet):
    inflight = dispatcher.hold_inflight(now=NOW)
    assert [a["ref"] for a in inflight["agents"]] == ["CMX-7", "CMX-8"]
    assert [j["ref"] for j in inflight["judges"]] == ["CMX-9"]
    assert inflight["agents"][0]["elapsed"] == 12 * 60
    assert inflight["judges"][0]["elapsed"] == 4 * 60

    lines = dispatcher.hold_inflight_lines(inflight)
    assert lines == [HEAD, "agent CMX-7 (running 12m)", "agent CMX-8 (running 2m)",
                     "judge CMX-9 (running 4m)"]


def test_an_idle_fleet_says_zero_not_nothing():
    assert dispatcher.hold_inflight_lines() == [
        "Held: new claims, judges and rework re-spawns. Still finishing: 0 agent(s), 0 judge(s)"
    ]


def _cli(**flags):
    args = argparse.Namespace(pause=False, resume=False, hold_status=False,
                              ttl="600", reason="gaming")
    for k, v in flags.items():
        setattr(args, k, v)
    assert main.cmd_dispatch_hold(args) is True


def test_pause_and_hold_status_print_the_scope_and_what_is_still_finishing(fleet, capsys):
    _cli(pause=True)
    out = capsys.readouterr().out
    assert HEAD in out
    assert "agent CMX-7 (running " in out and "judge CMX-9 (running " in out
    assert "CMX-10" not in out and "CMX-11" not in out and "CMX-12" not in out

    _cli(hold_status=True)
    out = capsys.readouterr().out
    assert HEAD in out and "agent CMX-8 (running " in out


def test_doctor_says_the_scope_and_what_is_still_finishing(fleet):
    hold.take(reason="gaming", ttl_seconds=600, by="liav")
    [finding] = doctor.audit(doctor.fact("dispatch.hold"))
    assert HEAD in finding.detail
    assert "agent CMX-7 (running " in finding.detail
    assert "judge CMX-9 (running " in finding.detail


def test_the_settings_row_says_the_scope_and_what_is_still_finishing(fleet, monkeypatch):
    monkeypatch.setattr(dash.capabilities, "live", lambda: {
        "pid": 4242,
        "capabilities": [{"key": "dispatch", "label": "Work dispatcher", "on": True}],
    })
    hold.take(reason="gaming", ttl_seconds=600, by="liav")
    data = dash.app.test_client().get("/api/settings").get_json()
    row = {it["label"]: it for s in data["sections"] for it in s["items"]}["Work dispatcher"]
    assert row["state"] == "Held"
    assert HEAD in row["detail"]
    assert "agent CMX-8 (running " in row["detail"]
    assert "judge CMX-9 (running " in row["detail"]
