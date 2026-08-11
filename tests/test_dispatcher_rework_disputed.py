"""``chela rework-disputed`` (CMX-244) — a rework agent's "nothing to push" escape hatch.

A rework agent that reads the verdict and concludes it is wrong, already fixed, or
otherwise unfixable has no new commit to offer — `task-finished` assumes one landed (the
row flips to `awaiting_review` so the NEXT tick judges the new head), and the dispatcher
judges once per head commit, so a rework that just says "nothing to fix" in its final
message and stops leaves the row in `running` forever: nothing about that state changes
without a fresh judge verdict, and the idle watchdog just re-sends the same rework prompt
on a timer — every liveness signal (session status, idle-nudge) reads healthy while the
run itself never moves again.

These tests pin the escape hatch: a rework `running` row moves straight to `needs_human`
(never `awaiting_review`, which would carry the SAME already-judged head), `rework_count`
is left exactly as `_respawn_rework` spent it, and the branch/worktree/PR are untouched.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from chela import dispatcher


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _row(conn, task_id="abc123", **over) -> sqlite3.Row:
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "do a thing",
        "status": "running", "window_name": "cmx-1", "worktree_path": "/wt/abc123",
        "branch_name": "cmx-1", "started_at": "2026-08-12T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/80", "pr_state": "open",
        "rework_count": 1, "review_history": json.dumps([
            {"round": 1, "at": "t1", "body": "the wire is loose", "verdict": "changes_requested"},
        ]),
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()


def _gh_router(comment_ok=True):
    def _run(cmd, *a, **k):
        class R:
            returncode = 0 if comment_ok else 1
            stdout = ""
            stderr = "" if comment_ok else "gh pr comment failed"
        return R()
    return _run


# --- (a) the happy path: running (rework) -> needs_human, everything else untouched ------

def test_dispute_flips_a_rework_to_needs_human_and_posts_a_comment():
    with dispatcher._db() as conn:
        _row(conn)
    gh: list[list[str]] = []

    def _run(cmd, *a, **k):
        gh.append(cmd)
        return _gh_router()(cmd, *a, **k)

    with patch.object(dispatcher.subprocess, "run", side_effect=_run), \
         patch.object(dispatcher, "_kill_window") as kill:
        result = dispatcher.mark_rework_disputed("abc123", "the verdict describes code that "
                                                             "was already fixed last round")

    assert result["ok"] is True
    assert result["status"] == "needs_human"
    assert result["comment_posted"] is True
    assert result["rework_count"] == 1          # unchanged — the round was already spent
    assert result["max_reworks"] == dispatcher.max_reworks()
    kill.assert_called_once_with("cmx-1")        # the window is killed, same as task-finished

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert run["rework_count"] == 1
    assert "the verdict describes code" in run["last_error"]
    # the branch/worktree/PR fields are untouched.
    assert run["branch_name"] == "cmx-1"
    assert run["worktree_path"] == "/wt/abc123"
    assert run["pr_url"] == "https://github.com/o/r/pull/80"

    reviews = dispatcher.reviews_of(dict(run))
    assert len(reviews) == 2
    assert reviews[-1]["verdict"] == "disputed"
    assert "already fixed" in reviews[-1]["body"]

    posted = [c for c in gh if c[:3] == ["gh", "pr", "comment"]]
    assert posted and posted[0][3] == "80"


def test_a_failed_pr_comment_does_not_block_the_dispute():
    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router(comment_ok=False)), \
         patch.object(dispatcher, "_kill_window"):
        result = dispatcher.mark_rework_disputed("abc123", "nothing to fix")
    assert result["ok"] is True and result["comment_posted"] is False
    assert dispatcher.resolve_run("abc123")["status"] == "needs_human"


# --- (b) ONLY a rework `running` row (rework_count > 0) can be disputed ------------------

@pytest.mark.parametrize("status,rework_count", [
    ("running", 0),          # a first dispatch, not a rework — nothing to dispute
    ("awaiting_review", 1),
    ("changes_requested", 1),
    ("needs_human", 1),
    ("done", 1),
    ("failed", 1),
    ("claimed", 1),
])
def test_dispute_refuses_anything_that_is_not_a_rework_in_flight(status, rework_count):
    with dispatcher._db() as conn:
        _row(conn, status=status, rework_count=rework_count)
    with patch.object(dispatcher, "_kill_window") as kill:
        result = dispatcher.mark_rework_disputed("abc123", "nothing to fix")
    assert result["ok"] is False
    assert "in flight" in result["error"].lower()
    kill.assert_not_called()
    assert dispatcher.resolve_run("abc123")["status"] == status


def test_dispute_with_no_reason_is_refused():
    with dispatcher._db() as conn:
        _row(conn)
    result = dispatcher.mark_rework_disputed("abc123", "   ")
    assert result["ok"] is False
    assert "reason" in result["error"]
    assert dispatcher.resolve_run("abc123")["status"] == "running"


def test_dispute_on_a_missing_task_id_is_refused():
    result = dispatcher.mark_rework_disputed("no-such-task", "nothing to fix")
    assert result["ok"] is False
    assert "no run found" in result["error"]


# --- (c) the CLI wires straight through to the dispatcher function ----------------------

def test_cli_rework_disputed_prints_needs_human_on_success(capsys):
    with dispatcher._db() as conn:
        _row(conn)
    from chela import main as main_mod

    class Args:
        task_id = "abc123"
        reason = "already fixed last round"

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()), \
         patch.object(dispatcher, "_kill_window"):
        main_mod.cmd_rework_disputed(Args())

    out = capsys.readouterr().out
    assert "disputed" in out
    assert "needs_human" in out


def test_cli_rework_disputed_exits_nonzero_on_refusal(capsys):
    from chela import main as main_mod

    class Args:
        task_id = "no-such-task"
        reason = "nothing to fix"

    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_rework_disputed(Args())
    assert exc.value.code == 1
    assert "no run found" in capsys.readouterr().out
