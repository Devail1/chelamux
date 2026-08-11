"""``chela retry`` (CMX-237) — "keep going" on a ``needs_human`` run, no fix required.

``reopen`` (CMX-96) answers ONE human intent: "I fixed the branch myself, re-verify the new
head" — and its new-commit gate REFUSES an unchanged one, correctly, because flipping
straight to `awaiting_review` on a stale head would let an already-rejected commit reach
`merge` unjudged. That refusal left the OTHER intent a `needs_human` verdict provokes just
as often with no in-contract exit: a human who does not want to fix it by hand and does not
want to merge past it either — just wants the automatic loop to have one more swing at the
SAME code. Hit live on CMX-231, twice, with no escape but hand-editing the runs DB.

These tests pin the missing edge: `needs_human` -> `changes_requested` (the automatic
loop's own carrier, not `reopen`'s `awaiting_review`), a SEPARATE `retry_count` counter that
never touches `rework_count`, and the escalation cap check honoring the grant.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from chela import dispatcher
from chela.workflow import WorkflowDef


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _row(conn, task_id="abc123", **over) -> sqlite3.Row:
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "do a thing",
        "status": "needs_human", "window_name": None, "worktree_path": "/wt/abc123",
        "branch_name": "test-1", "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/80", "pr_state": "open",
        "rework_count": 2, "review_history": json.dumps([
            {"round": 1, "at": "t1", "body": "the wire is loose", "verdict": "changes_requested"},
            {"round": 2, "at": "t2", "body": "still loose", "verdict": "changes_requested"},
        ]),
        "last_error": "rework cap reached (2/2) — the PR still fails review.",
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()


def _no_gh(cmd, *a, **k):
    class R:
        returncode = 0
        stdout = ""
        stderr = ""
    return R()


def _gh_router(comment_ok=True):
    def _run(cmd, *a, **k):
        if comment_ok:
            return _no_gh(cmd, *a, **k)
        class R:
            returncode = 1
            stdout = ""
            stderr = "gh pr comment failed"
        return R()
    return _run


# --- (a) the happy path: needs_human -> changes_requested, everything else untouched ---

def test_retry_flips_needs_human_to_changes_requested_and_posts_a_comment(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)
    gh: list[list[str]] = []

    def _run(cmd, *a, **k):
        gh.append(cmd)
        return _gh_router()(cmd, *a, **k)

    with patch.object(dispatcher.subprocess, "run", side_effect=_run):
        result = dispatcher.retry("abc123", "let it try once more")

    assert result["ok"] is True
    assert result["status"] == "changes_requested"
    assert result["comment_posted"] is True
    assert result["retry_count"] == 1
    assert result["rework_count"] == 2          # unchanged — retry spends no rework budget
    assert result["max_reworks"] == dispatcher.max_reworks()

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "changes_requested"
    assert run["rework_count"] == 2
    assert run["retry_count"] == 1
    assert run["last_error"] is None
    # the branch/worktree/PR fields are untouched.
    assert run["branch_name"] == "test-1"
    assert run["worktree_path"] == "/wt/abc123"
    assert run["pr_url"] == "https://github.com/o/r/pull/80"

    reviews = dispatcher.reviews_of(dict(run))
    assert len(reviews) == 3
    assert reviews[-1]["verdict"] == "retry"
    assert "let it try once more" in reviews[-1]["body"]

    posted = [c for c in gh if c[:3] == ["gh", "pr", "comment"]]
    assert posted and posted[0][3] == "80"


def test_retry_with_no_reason_still_writes_a_default_note(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()):
        result = dispatcher.retry("abc123")
    assert result["ok"] is True
    reviews = dispatcher.reviews_of(dict(dispatcher.resolve_run("abc123")))
    assert reviews[-1]["verdict"] == "retry"
    assert reviews[-1]["body"]          # non-empty default


def test_a_failed_pr_comment_does_not_block_the_retry(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router(comment_ok=False)):
        result = dispatcher.retry("abc123", "keep going")
    assert result["ok"] is True and result["comment_posted"] is False
    assert dispatcher.resolve_run("abc123")["status"] == "changes_requested"


# --- (b) ONLY needs_human can be retried -------------------------------------------------

@pytest.mark.parametrize("status", ["running", "awaiting_review", "changes_requested",
                                     "done", "failed", "claimed"])
def test_retry_refuses_every_status_that_is_not_needs_human(tmp_path, status):
    """🔴 Asserts the EXPLICIT upfront-check's own message, not just any refusal.

    The compare-and-swap write would ALSO refuse a row that was never `needs_human` (its
    `WHERE status='needs_human'` matches nothing), but with a DIFFERENT, misleading message
    — "run moved to X while this was being written", as if a concurrent tick raced this
    call, when actually the row started in the wrong state and nothing raced anything. A
    test that only checked `status in error` would pass under either message (the CAS
    message embeds the status too) and could not tell the explicit check apart from
    coincidentally-similar CAS behavior — decoration, caught by corrupting the guard.
    """
    with dispatcher._db() as conn:
        _row(conn, status=status)
    result = dispatcher.retry("abc123", "keep going")
    assert result["ok"] is False
    assert "only a run the rework loop actually gave up on can be retried" in result["error"]
    assert dispatcher.resolve_run("abc123")["status"] == status   # untouched


def test_retry_refuses_an_unknown_run(tmp_path):
    result = dispatcher.retry("no-such-run", "keep going")
    assert result["ok"] is False


# --- (c) 🔴 retry cannot resurrect a run that MOVED --------------------------------------

def test_retry_will_not_resurrect_a_run_that_moved_under_it(tmp_path):
    """Same race `reopen`/`request_changes` guard against: a concurrent tick reconciles the
    row to `done` (the human merged the stale PR directly) in the gap between this call's
    read and its write. With no compare-and-swap the UPDATE lands anyway and a merged,
    needs_human run is dragged back into the automatic loop.

    Seen to go red: dropping `AND status='needs_human'` from the UPDATE.
    """
    with dispatcher._db() as conn:
        _row(conn)
    stale = dict(dispatcher.resolve_run("abc123"))        # read: needs_human
    with dispatcher._db() as conn:                        # ...and the world moves on
        conn.execute("UPDATE runs SET status='done' WHERE task_id='abc123'")
        conn.commit()

    with patch.object(dispatcher, "resolve_run", return_value=stale), \
         patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()):
        result = dispatcher.retry("abc123", "keep going")

    assert result["ok"] is False
    assert "done" in result["error"]
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "done"                        # NOT resurrected
    reviews = dispatcher.reviews_of(dict(run))
    assert len(reviews) == 2                              # nothing appended


# --- (d) retry_count climbs independently of rework_count --------------------------------

def _escalate_back_to_needs_human(task_id: str) -> None:
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET status='needs_human' WHERE task_id=?", (task_id,))
        conn.commit()


def test_retry_count_climbs_independently_of_rework_count(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()):
        r1 = dispatcher.retry("abc123", "round 1")
    assert r1["ok"] is True and r1["retry_count"] == 1
    assert dispatcher.resolve_run("abc123")["rework_count"] == 2

    _escalate_back_to_needs_human("abc123")
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()):
        r2 = dispatcher.retry("abc123", "round 2")
    assert r2["retry_count"] == 2

    run = dispatcher.resolve_run("abc123")
    # the AUTOMATIC counter never moves — retrying still spends no rework budget.
    assert run["rework_count"] == 2
    assert run["retry_count"] == 2


# --- (e) `latest_verdict` skips the meta "retry" entry ------------------------------------

def test_latest_verdict_skips_a_retry_entry_and_returns_the_real_defect():
    """The rework prompt renders `latest_verdict()` as `{{verdict}}` — the thing the agent
    is told to fix. `retry()`'s own note ("keep going") is not a review of the code; were
    it not skipped, the very next rework round would tell the agent to fix nothing in
    particular, discarding the real defect the previous round actually failed on."""
    run = {"review_history": json.dumps([
        {"round": 1, "at": "t1", "body": "the wire is loose", "verdict": "changes_requested"},
        {"round": 2, "at": "t2", "body": "asked to keep going", "verdict": "retry"},
    ])}
    assert dispatcher.latest_verdict(run) == "the wire is loose"


def test_latest_verdict_still_reads_a_real_verdict_when_there_is_no_retry():
    run = {"review_history": json.dumps([
        {"round": 1, "at": "t1", "body": "still loose", "verdict": "changes_requested"},
    ])}
    assert dispatcher.latest_verdict(run) == "still loose"


# --- (f) the escalation cap honors the grant, at the dispatcher-tick level ---------------

def _wf(tmp_path) -> WorkflowDef:
    (tmp_path / "TODO.md").write_text("- [ ] do a thing\n")
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={
            "project_key": "TEST",
            "tracker": {"kind": "markdown", "path": "TODO.md"},
            "workspace": {"root": str(tmp_path / ".chela" / "wts"), "base_branch": "dev"},
            "hooks": {
                "after_create": "seed-settings {{workspace_path}}",
                "before_run": "uv sync --all-extras",
            },
        },
        prompt_template="fresh dispatch: {{task_title}}",
    )


def _status(wf: WorkflowDef):
    from chela.workflow import WorkflowStatus

    return WorkflowStatus(path=wf.path, workflow=wf, error=None)


class _Source:
    def __init__(self, *task_ids: str):
        from chela.sources import Task
        self._tasks = [
            Task(id=tid, title="do a thing", file="TODO.md", line_number=i + 1,
                 raw="- [ ] do a thing")
            for i, tid in enumerate(task_ids)
        ]

    def list_open_tasks(self):
        return list(self._tasks)


class _FakeTmux:
    def __init__(self):
        self.windows: list[tuple[str, str]] = []
        self._next_id = 100

    def run(self, cmd, *args, **kwargs):
        class R:
            returncode = 0
            stdout = ""

        if isinstance(cmd, list) and cmd[:2] == ["tmux", "list-windows"]:
            R.stdout = "".join(f"{wid} {name}\n" for wid, name in self.windows)
        elif isinstance(cmd, list) and cmd[:2] == ["tmux", "new-window"]:
            name = cmd[cmd.index("-n") + 1]
            wid = f"@{self._next_id}"
            self._next_id += 1
            self.windows.append((wid, name))
            R.stdout = wid + "\n"
        return R()


def test_a_retry_grant_lets_the_loop_go_one_round_past_the_automatic_cap(tmp_path, monkeypatch):
    """rework_count == cap would normally escalate (see the plain-cap test below) — but a
    single `chela retry` grant raises the effective cap by one, so the SAME row instead
    gets respawned for one more round."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    wf = _wf(tmp_path)
    source = _Source("abc123")
    original_wt = tmp_path / ".chela" / "wts" / "abc123"
    original_wt.mkdir(parents=True)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             worktree_path=str(original_wt), branch_name="test-1",
             rework_count=2, retry_count=1,
             review_history=json.dumps([{"round": 1, "at": "t", "body": "verdict 1"}]))

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree", return_value=(original_wt, False)), \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["escalated"] == 0 and summary["reworked"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"
    assert run["rework_count"] == 3              # the grant let it spend one more round


def test_the_same_row_WOULD_escalate_without_the_grant(tmp_path, monkeypatch):
    """Control for the test above: identical row, `retry_count=0` — it escalates exactly
    as it always has. Pins that the grant, not some other change, is what moved the line."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             rework_count=2, retry_count=0,
             review_history=json.dumps([{"round": 1, "at": "t", "body": "verdict 1"}]))

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree") as attach, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["escalated"] == 1 and summary["reworked"] == 0
    assert attach.call_count == 0
    assert dispatcher.resolve_run("abc123")["status"] == "needs_human"


def test_a_used_up_grant_escalates_again_at_the_new_effective_cap(tmp_path, monkeypatch):
    """The grant is spent the moment the respawned round ALSO fails: `rework_count` (3) now
    equals `cap + retry_count` (2 + 1), so the row escalates again — a grant buys exactly
    one extra round, not an unbounded exemption."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             rework_count=3, retry_count=1,
             review_history=json.dumps([{"round": 1, "at": "t", "body": "verdict 1"}]))

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree") as attach, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["escalated"] == 1 and summary["reworked"] == 0
    assert attach.call_count == 0
    assert dispatcher.resolve_run("abc123")["status"] == "needs_human"


# --- (g) the CLI --------------------------------------------------------------------------

class _RetryArgs:
    run = "abc123"
    reason = "let it try again"


def test_cmd_retry_success(tmp_path, capsys):
    from chela import main

    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()):
        main.cmd_retry(_RetryArgs())
    out = capsys.readouterr().out
    assert "changes_requested" in out
    assert dispatcher.resolve_run("abc123")["status"] == "changes_requested"


def test_cmd_retry_failure_exits_nonzero(tmp_path, capsys):
    from chela import main

    with dispatcher._db() as conn:
        _row(conn, status="running")
    with pytest.raises(SystemExit) as exc:
        main.cmd_retry(_RetryArgs())
    assert exc.value.code != 0
    out = capsys.readouterr().out
    assert "not 'needs_human'" in out


def test_chela_retry_reaches_the_dispatcher_end_to_end(tmp_path):
    """``chela retry cmx-96`` must actually parse AND reach ``dispatcher.retry`` — the
    dispatch call-site is the guard here. Mutate ``elif args.command == "retry": …`` to
    ``pass`` and this fails: a subparser that parses but is never wired is silent."""
    import sys

    from chela import main

    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()), \
         patch.object(sys, "argv", ["chela", "retry", "abc123", "--reason", "keep going"]):
        main.main()
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "changes_requested"
    assert dispatcher.reviews_of(dict(run))[-1]["body"] == "keep going"


# --- (h) the listing chip -------------------------------------------------------------

def test_a_fresh_run_shows_no_retry_count_in_the_listing(tmp_path):
    from chela import main

    with dispatcher._db() as conn:
        _row(conn, status="awaiting_review", retry_count=0)
    row = dispatcher.resolve_run("abc123")
    assert "retry=" not in main._format_awaiting_run(dict(row))


def test_a_retried_runs_listing_shows_its_retry_count(tmp_path):
    from chela import main

    with dispatcher._db() as conn:
        _row(conn, status="awaiting_review", retry_count=2)
    row = dispatcher.resolve_run("abc123")
    assert "retry=2" in main._format_awaiting_run(dict(row))
