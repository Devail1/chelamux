"""⚖️🔒 issue #502 B1 — the completion hop moves off the dispatched agent's own process.

`chela task-finished` used to run `dispatcher.mark_awaiting_review` INSIDE the agent's own
process: an `UPDATE` against `~/.chela/scheduler.db` and a `tmux kill-window` against the
shared control socket. Both are privileged: an agent that can write `runs` can rewrite every
OTHER run's row, and one that can reach the tmux socket can `send-keys` into ANY window
(including the operator's) with no `from` attribution — the two blockers named in
docs/SANDBOX_BOUNDARY.md §5 B1 that make OS-level sandboxing of dispatched agents cosmetic.

These tests pin the split: `dispatcher.request_task_finished` (agent-side, called from
`chela task-finished`) only ever writes a marker file into the run's OWN worktree — the guard
is that it NEVER shells out (no `subprocess.run` at all, so no `tmux` and no other privileged
write path) — and `dispatcher.tick()` (daemon-side, unsandboxed, already polling) is the only
thing that reads the marker, calls `mark_awaiting_review`, and kills the window.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from chela import dispatcher

from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    """A runs DB per test — mirrors the identically-named fixture elsewhere; without it every
    test in the session would share one `scheduler.db`."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


# --- dispatcher.request_task_finished (agent-side) --------------------------------------


def test_request_task_finished_writes_a_marker_and_never_shells_out(tmp_path):
    """🔴 GUARD (accept case): the whole point of moving this off the agent's process is
    that it touches NOTHING privileged. Corrupt by having `request_task_finished` call
    `mark_awaiting_review` (or `_kill_window`) directly instead of writing the marker →
    `subprocess.run` fires (the `tmux kill-window` call) → RED.
    """
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(wt))

    with patch.object(dispatcher.subprocess, "run") as run:
        result = dispatcher.request_task_finished("t1")

    run.assert_not_called()
    assert result == {"ok": True, "task_id": "t1", "requested": True}
    marker = wt / ".chela-task-finished-request.json"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert payload["task_id"] == "t1"
    assert "requested_at" in payload
    # ⛔ the row itself must be untouched — the transition is the DAEMON's job, not this call's.
    row = dispatcher.resolve_run("t1")
    assert row["status"] == "running"


def test_request_task_finished_accepts_a_claimed_row_too(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="claimed", worktree_path=str(wt))

    result = dispatcher.request_task_finished("t1")

    assert result == {"ok": True, "task_id": "t1", "requested": True}
    assert (wt / ".chela-task-finished-request.json").exists()


def test_request_task_finished_unknown_task_id_errors():
    result = dispatcher.request_task_finished("no-such-task")
    assert result == {"ok": False, "error": "no run found for task_id no-such-task"}


@pytest.mark.parametrize("status", ["awaiting_review", "done", "failed", "needs_human"])
def test_request_task_finished_refuses_a_status_that_is_not_claimed_or_running(tmp_path, status):
    """⛔ Same refusal `mark_awaiting_review` used to make directly — an already-settled run
    (or one a human already moved) must not get a fresh marker dropped on top of it."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status=status, worktree_path=str(wt))

    result = dispatcher.request_task_finished("t1")

    assert result["ok"] is False
    assert f"status {status!r}" in result["error"]
    assert not (wt / ".chela-task-finished-request.json").exists()


def test_request_task_finished_errors_when_the_row_has_no_worktree_path(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=None)

    result = dispatcher.request_task_finished("t1")

    assert result["ok"] is False
    assert "worktree_path" in result["error"]


# --- dispatcher.tick(): applying a pending request (daemon-side) ------------------------


def test_tick_applies_a_pending_completion_request_and_kills_the_window(tmp_path):
    """🔴 GUARD (accept case, apply side): a `running` row carrying a completion-request
    marker in its worktree must transition to `awaiting_review` AND have its window torn
    down — same two effects `mark_awaiting_review` always produced, now performed by the
    unsandboxed daemon instead of the agent's own process. Corrupt by dropping the marker
    check from `tick()` → the row is never picked up → RED.
    """
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    (wt / ".chela-task-finished-request.json").write_text(
        json.dumps({"task_id": "abc123", "requested_at": "2026-09-13T00:00:00+00:00"})
    )
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             worktree_path=str(wt), pr_url=None, pr_state=None)

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_url", return_value="https://github.com/o/r/pull/1"), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["task_finished_applied"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert run["pr_url"] == "https://github.com/o/r/pull/1"
    kill_calls = [c for c in fake.calls if isinstance(c, list) and c[:2] == ["tmux", "kill-window"]]
    assert any("test-1" in c[-1] for c in kill_calls), "the agent's window must be torn down"
    assert not (wt / ".chela-task-finished-request.json").exists(), \
        "the marker must be consumed so it is never re-applied"


def test_tick_applies_a_pending_completion_request_for_a_claimed_row(tmp_path):
    """The marker is checked before dispatch has even started running a spawned agent's
    real work — `claimed` is a valid starting status too (mirrors `mark_awaiting_review`'s
    own status check)."""
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    (wt / ".chela-task-finished-request.json").write_text(json.dumps({"task_id": "abc123"}))
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="claimed", window_name="test-1",
             worktree_path=str(wt), pr_url=None, pr_state=None)

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_url", return_value=None), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["task_finished_applied"] == 1
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"


def test_tick_leaves_a_running_row_with_no_marker_completely_alone(tmp_path):
    """⛔⛔ THE COUNTERWEIGHT: a `running` row with NO completion-request marker must never
    be swept into this path — that would transition a live agent's run out from under it
    with no PR and no real completion evidence. Corrupt by matching on status alone
    (dropping the `.exists()` check) → every running row reconciles on the first tick → RED.
    """
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             worktree_path=str(wt), pr_url=None, pr_state=None,
             started_at=dispatcher._now())

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-1"}), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["task_finished_applied"] == 0
    assert dispatcher.resolve_run("abc123")["status"] == "running"
    kill_calls = [c for c in fake.calls if isinstance(c, list) and c[:2] == ["tmux", "kill-window"]]
    assert kill_calls == []


def test_tick_drops_a_marker_that_could_not_be_applied_without_crashing(tmp_path):
    """The row moved out of `claimed`/`running` between the outer snapshot and this row's
    turn (e.g. a human ran `chela escalate` in the same window) — `mark_awaiting_review`
    refuses, and the tick must not raise or re-queue the same row forever; it logs and
    moves on, having consumed the stale marker."""
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    (wt / ".chela-task-finished-request.json").write_text(json.dumps({"task_id": "abc123"}))
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             worktree_path=str(wt), pr_url=None, pr_state=None)

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "mark_awaiting_review",
                       return_value={"ok": False, "error": "run is in status 'needs_human'"}):
        summary = dispatcher.tick(wf.path)

    assert summary["task_finished_applied"] == 0
    assert not (wt / ".chela-task-finished-request.json").exists()
