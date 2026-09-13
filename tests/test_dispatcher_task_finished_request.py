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


def _runs_snapshot() -> list[dict]:
    with dispatcher._db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY task_id").fetchall()]


def test_request_task_finished_writes_a_marker_and_touches_no_run_row(tmp_path):
    """🔴 GUARD (accept case): the whole point of moving this off the agent's process is
    that it touches NOTHING privileged — not just "no subprocess.run" (that only proves it
    doesn't shell out), but "the runs table is byte-for-byte unchanged". An IN-PROCESS
    `UPDATE runs ...` needs no subprocess at all, so checking `run.assert_not_called()` plus
    this row's own status (unchanged because the mutation targeted OTHER rows) let
    `conn.execute("UPDATE runs SET status='failed' WHERE task_id<>?", (task_id,))` survive —
    see docs/defeat_shapes/366-in-process-write-hides-behind-a-no-subprocess-guard.md.

    Snapshots the WHOLE runs table before/after, over TWO rows: the one being finished and a
    SECOND, unrelated one — so "did not touch its own row" can never stand in for "did not
    touch any row" the way the single-row version of this test did.
    """
    wt = tmp_path / "wt"
    wt.mkdir()
    wt2 = tmp_path / "wt2"
    wt2.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(wt))
        _row(conn, task_id="t2", status="running", worktree_path=str(wt2),
             window_name="test-2", branch_name="test-2")

    before = _runs_snapshot()

    with patch.object(dispatcher.subprocess, "run") as run:
        result = dispatcher.request_task_finished("t1")

    run.assert_not_called()
    assert result == {"ok": True, "task_id": "t1", "requested": True}
    marker = wt / ".chela-task-finished-request.json"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert payload["task_id"] == "t1"
    assert "requested_at" in payload
    # ⛔ the runs table — EVERY row, not just t1's — must be untouched — the transition is
    # the DAEMON's job, not this call's.
    after = _runs_snapshot()
    assert after == before, "request_task_finished must not write ANY row in the runs table"


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


# --- dispatcher.mark_awaiting_review: benign vs. genuine refusal (daemon-side) -----------


@pytest.mark.parametrize("status", ["awaiting_review", "done", "failed", "needs_human", "closed"])
def test_mark_awaiting_review_flags_an_already_settled_row_as_benign(tmp_path, status):
    """🔴 GUARD (hazard 3 of the brief — orchestrator round-1 finding): `tick()` decides
    whether to drop a stale marker quietly or escalate a genuine failure by reading
    `already_settled` off this return value — a status that is not claimed/running means
    something else already moved the row, which is BENIGN. Corrupt by dropping the
    `already_settled` key from this refusal → `tick()` can no longer tell it apart from an
    unexplained failure and either escalates every already-settled row (breaking the
    ordinary path) or (as shipped pre-fix) silently drops every genuine failure too → RED.
    """
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status=status, worktree_path=str(wt))

    result = dispatcher.mark_awaiting_review("t1")

    assert result["ok"] is False
    assert result.get("already_settled") is True
    assert f"status {status!r}" in result["error"]


def test_mark_awaiting_review_does_not_flag_a_missing_row_as_already_settled(tmp_path):
    """The COUNTERWEIGHT: a task_id `mark_awaiting_review` cannot even find is NOT "someone
    else already settled it" — it is unexplained, and must fall through to `tick()`'s
    escalation path. Corrupt by flagging this case `already_settled` too (or by making the
    already-settled check `True` by default) → a genuinely missing row would be silently
    dropped like a benign one instead of escalated → RED.
    """
    result = dispatcher.mark_awaiting_review("no-such-task")

    assert result["ok"] is False
    assert "already_settled" not in result
    assert "no run found" in result["error"]


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


def test_tick_drops_a_marker_that_was_already_settled_without_crashing(tmp_path):
    """The row moved out of `claimed`/`running` between the outer snapshot and this row's
    turn (e.g. a human ran `chela escalate` in the same window) — `mark_awaiting_review`
    refuses with `already_settled=True`, and the tick must not raise, escalate, or re-queue
    the same row forever; it logs and moves on, having consumed the stale marker. This is
    the ONLY `ok: False` shape that drops the marker quietly — contrast with the genuine-
    failure test below, which must NOT drop it."""
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
                       return_value={"ok": False, "already_settled": True,
                                     "error": "run is in status 'needs_human'"}):
        summary = dispatcher.tick(wf.path)

    assert summary["task_finished_applied"] == 0
    assert summary["escalated"] == 0
    assert not (wt / ".chela-task-finished-request.json").exists()
    assert dispatcher.resolve_run("abc123")["status"] == "running"


def test_tick_escalates_and_keeps_the_marker_on_a_genuine_apply_failure(tmp_path):
    """🔴 GUARD (hazard 3 of the brief — orchestrator round-1 finding): a request the daemon
    cannot apply for an UNEXPLAINED reason (not "already settled by someone else") must FAIL
    LOUDLY, not strand the run in `running` forever with only a `log.warning` nobody reads.
    Corrupt by treating every `ok: False` the same (drop the marker, log, move on) →
    `summary["escalated"]` stays 0 and the row is left `running` with no trace in the inbox
    → RED. The marker must also survive as evidence for whoever handles the escalation.
    """
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
                       return_value={"ok": False, "error": "no run found for task_id abc123"}):
        summary = dispatcher.tick(wf.path)

    assert summary["task_finished_applied"] == 0
    assert summary["escalated"] == 1
    assert (wt / ".chela-task-finished-request.json").exists(), \
        "the marker is the agent's completion evidence and must survive an escalation"
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert "task-finished" in run["last_error"]
    assert "no run found for task_id abc123" in run["last_error"]


def test_tick_applies_a_pending_completion_request_even_when_the_tracker_read_failed(tmp_path):
    """🔴 GUARD (judge round 1, mutation 1): the marker is DIRECT completion evidence from
    the agent itself and must not wait on `tracker_read_failed` — that flag gates a WEAKER
    signal ("removed from the tracker"), not this one. Corrupt by adding `not
    tracker_read_failed and` to the marker-check condition → a tracker blip (a `git clean
    -xdf`, a `gh` timeout) freezes every live agent's own completion report until the
    tracker recovers → RED.
    """
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    (wt / ".chela-task-finished-request.json").write_text(json.dumps({"task_id": "abc123"}))
    source = _Source("abc123")
    source.read_failed = True
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             worktree_path=str(wt), pr_url=None, pr_state=None)

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_url", return_value=None), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["tracker_read_failed"] is True
    assert summary["task_finished_applied"] == 1
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"
