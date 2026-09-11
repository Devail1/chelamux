"""🏃‍♂️💀 Issue #491: a merged PR whose row is `running` never reconciled.

`_respawn_rework` flips an EXISTING review-state row — one that already owns an open PR —
back to `running` to work a fresh verdict. That flip can race a human merging the PR out of
band (`gh pr merge`, never through `chela merge`): the merge can land in the gap between the
row going `running` and the next tick's `pr_state` refresh, or on any later tick once the row
already reads `running`. Before this fix, `running` was not in `RECONCILE_MERGE_STATUSES`, so
a row in that shape sat forever — nothing closed it, and nothing tore down the now-pointless
agent's window — until the (much slower) idle watchdog eventually caught it.

RECONCILE is the only half this needs: a row that is ALREADY `running` with a merged PR (the
race already happened) closes to `done` and its window is torn down, same as a review-state
row. There is no separate spawn-side refusal in the rework loop (3b) — a `changes_requested`
row's `pr_state` is refreshed in phase 0 and, if it reads `merged`, step 1's reconcile (which
runs before 3b, same tick, same connection) already closes it to `done` before 3b's
`WHERE status='changes_requested'` query can ever select it. A `pr_state == "merged"` check
inside 3b was tried and shipped as dead code — see docs/defeat_shapes/360-*.md — and was
removed rather than pinned with a fixture, because no fixture can put a row in front of 3b in
that state; the system itself forecloses it one step earlier.

⛔⛔ The counterweight, tested here too: a `running` row whose PR is NOT merged is a live
agent doing real work and must be left completely alone.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from chela import dispatcher

from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    """A runs DB per test — ``dispatcher.DB_PATH`` is latched at import, so without this
    every test in the session shares one ``scheduler.db``. Mirrors the identically-named
    fixture in ``test_dispatcher_rework.py``."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


# --- reconcile: a `running` row already stranded by the race ---------------------------

def test_a_running_rework_row_with_a_merged_pr_closes_and_kills_its_window(tmp_path):
    """🔴 GUARD (accept case, reconcile side): the race already happened — the row is
    `running` (a rework in flight) and its `pr_state` reads `merged`. It must close to
    `done` AND its window must be torn down — an orphaned agent must not keep working a
    merged branch. Both halves are asserted; proving only one is not enough (issue #491's
    own point: flipping the status without killing the window leaves a live orphan).
    Corrupt by leaving `running` out of the merge-reconcile check → this row is stuck
    forever → RED.
    """
    wf = _wf(tmp_path)
    source = _Source("abc123")   # still open in the tracker — the line is never struck
    #                              until this row reaches `done`, exactly issue #491's shape
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             pr_url="https://github.com/o/r/pull/80", pr_state="open", rework_count=1)

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]    # the window is very much alive
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_status", return_value=("merged", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["reconciled_done"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "done"
    kill_calls = [c for c in fake.calls if isinstance(c, list) and c[:2] == ["tmux", "kill-window"]]
    assert any("test-1" in c[-1] for c in kill_calls), "the orphaned window must be torn down"


def test_a_running_row_whose_pr_is_NOT_merged_is_left_completely_alone(tmp_path):
    """⛔⛔ THE COUNTERWEIGHT — the guard that matters most. A `running` row is a live agent
    doing real work whenever its PR has NOT merged, whatever the row's history (fresh
    dispatch or rework). Reconciling every `running` row regardless of `pr_state` would kill
    in-flight agents mid-task. Corrupt by reconciling `running` unconditionally (dropping the
    `pr_state == "merged"` half of the check) → this run is falsely closed and its very much
    alive window is killed → RED.
    """
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             pr_url="https://github.com/o/r/pull/80", pr_state="open", rework_count=1,
             started_at=dispatcher._now())   # recent — never reaches the (unrelated) watchdog

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-1"}), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["reconciled_done"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"      # untouched
    kill_calls = [c for c in fake.calls if isinstance(c, list) and c[:2] == ["tmux", "kill-window"]]
    assert kill_calls == []                # the live window was never killed


def test_a_first_dispatch_running_row_with_no_pr_yet_is_left_alone_too(tmp_path):
    """The other shape of `running`: a FIRST dispatch that has not opened a PR yet
    (`pr_url` is `None`, `pr_state` is `None`) — same as `claimed`, nothing to reconcile
    against. This must not accidentally match the new merged-check."""
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             pr_url=None, pr_state=None, rework_count=0, started_at=dispatcher._now())

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-1"}), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["reconciled_done"] == 0
    assert dispatcher.resolve_run("abc123")["status"] == "running"


# --- MUST STILL PASS: the ordinary idle-watchdog strand is untouched --------------------

def test_the_idle_watchdog_still_fails_an_ordinary_stranded_running_row(tmp_path):
    """⛔ GUARD: this fix must not route every `running` row through the new merge-reconcile
    path. A `running` row stranded for the ORDINARY reason (a dropped prompt, no merge
    involved) must still reach the idle watchdog and fail exactly as before. Corrupt by
    short-circuiting all `running` rows into the merge-reconcile branch (e.g. treating a
    `None`/`'open'` pr_state as done-worthy) → this row never reaches the watchdog → RED.
    """
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    wf = _wf(tmp_path, concurrency={"max": 1})
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             pr_url=None, pr_state=None, rework_count=0,
             started_at=old, idle_nudged_at=old)

    bare_idle = (
        "╭───────────────────────────────────╮\n"
        "│ ❯                                 │\n"
        "╰───────────────────────────────────╯\n"
    )
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-1"}), \
         patch.object(dispatcher, "_capture_pane", return_value=bare_idle), \
         patch.object(dispatcher, "_agent_status", return_value="idle"), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["reconciled_failed"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "failed"
    assert "idle at empty prompt" in run["last_error"]
