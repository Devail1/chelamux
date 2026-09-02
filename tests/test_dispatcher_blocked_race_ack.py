"""``chela judge ack-blocked-race`` (CMX-336) — the operator exit for a `J_BLOCKED_RACE`
row that can never resolve on its own once its PR is merged or closed.

``_blocked_race_resolved`` (chela/runtime_truth.py) clears a row on exactly one condition:
the PR's head moving past the judged commit (`judge_sha != pr_head_sha`). That can never
happen once the PR is merged or closed — the branch is gone, nothing will ever push to it
again — so `chela doctor` (and the ntfy notifier it feeds) would report the row forever,
with no operator exit besides hand-editing `scheduler.db`.

`dispatcher.acknowledge_blocked_race` is that exit. These tests pin: it stamps
`blocked_race_ack_by`/`_at`/`_note`/`_sha` WITHOUT touching `judge_state`/`judge_detail`/
`judge_sha` themselves (the verdict is acknowledged, never rewritten); it refuses when there
is nothing to acknowledge (wrong `judge_state`, unknown run); and the acknowledgement is
scoped to the exact `judge_sha` it was given for, so a later race on a NEW sha is not
silently covered by an old acknowledgement.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from chela import dispatcher, event_log, judge


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _row(conn, task_id="abc123", **over) -> sqlite3.Row:
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "do a thing",
        "status": "done", "window_name": None, "worktree_path": "/wt/abc123",
        "branch_name": "test-1", "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/80", "pr_state": "merged",
        "judge_state": judge.J_BLOCKED_RACE, "judge_sha": "de291ca34a62",
        "judge_detail": "a guard SURVIVED corruption",
        "review_history": json.dumps([]),
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()


# --- (a) the happy path: stamps the ack columns, leaves the verdict untouched -----------

def test_acknowledge_stamps_by_at_note_sha_without_touching_the_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("USER", "liav")
    with dispatcher._db() as conn:
        _row(conn)

    result = dispatcher.acknowledge_blocked_race("abc123", note="already shipped, safe to ack")

    assert result["ok"] is True
    assert result["task_id"] == "abc123"
    assert result["by"] == "liav"
    assert result["sha"] == "de291ca34a62"
    assert result["pr_url"] == "https://github.com/o/r/pull/80"

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] == "liav"
    assert row["blocked_race_ack_sha"] == "de291ca34a62"
    assert row["blocked_race_ack_note"] == "already shipped, safe to ack"
    assert row["blocked_race_ack_at"]
    # ⛔ the original verdict is UNTOUCHED — acknowledged, not rewritten.
    assert row["judge_state"] == judge.J_BLOCKED_RACE
    assert row["judge_sha"] == "de291ca34a62"
    assert row["judge_detail"] == "a guard SURVIVED corruption"


def test_acknowledge_logs_an_event(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)

    dispatcher.acknowledge_blocked_race("abc123", by="liav")

    kinds = [e["type"] for e in event_log.read()["events"]]
    assert "blocked_race_ack" in kinds


def test_acknowledge_defaults_by_to_env_user_when_not_given(tmp_path, monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setenv("USERNAME", "windows-liav")
    with dispatcher._db() as conn:
        _row(conn)

    result = dispatcher.acknowledge_blocked_race("abc123")
    assert result["by"] == "windows-liav"


def test_acknowledge_falls_back_to_unknown_with_no_actor_available(tmp_path, monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    with dispatcher._db() as conn:
        _row(conn)

    result = dispatcher.acknowledge_blocked_race("abc123")
    assert result["by"] == "unknown"


# --- (b) refuses when there is nothing to acknowledge ------------------------------------

def test_acknowledge_refuses_a_run_that_is_not_blocked_race(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, judge_state=judge.J_CLEAN)

    result = dispatcher.acknowledge_blocked_race("abc123")
    assert result["ok"] is False
    assert judge.J_BLOCKED_RACE in result["error"]

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] is None


def test_acknowledge_refuses_an_unknown_run(tmp_path):
    result = dispatcher.acknowledge_blocked_race("nope")
    assert result["ok"] is False
    assert "no run matches" in result["error"]


# --- (c) the scope guard: an ack under an OLD sha does not cover a NEW race --------------

def test_acknowledge_is_scoped_to_the_current_judge_sha(tmp_path, monkeypatch):
    """CAS on judge_state AND judge_sha together: if the row's REAL judge_sha has moved on
    since this call's own read (a fresh judge landed a NEW blocked_race verdict on a
    different commit, in the gap between the read and this write), the acknowledgement must
    be refused rather than silently stamping itself onto the new verdict under the stale
    sha it was given."""
    with dispatcher._db() as conn:
        _row(conn, judge_sha="new-sha")  # the row's REAL, current sha

    stale = dict(dispatcher.list_runs()[0])
    stale["judge_sha"] = "old-sha"  # what a stale read saw before the concurrent re-run
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: stale)

    result = dispatcher.acknowledge_blocked_race("abc123")
    assert result["ok"] is False
    assert "changed while this was being written" in result["error"]

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] is None
    assert row["judge_sha"] == "new-sha"
