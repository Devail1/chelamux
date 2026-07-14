"""The REWORK LOOP (CMX-68) — a PR that fails review has somewhere to go.

Before this, `awaiting_review` was terminal unless a human climbed into the worktree and
hand-spawned a fix agent (which is what actually happened on 2026-07-14, reviewing PR #80:
three real defects, and no mechanism to send them back). These tests pin the carrier:

* the verdict is written on the RUN ROW and projected onto the PR as a COMMENT — never as
  a `gh pr review`, which GitHub refuses on a PR the calling account authored;
* the re-spawn re-enters the ORIGINAL worktree on the ORIGINAL branch (a fresh fork from
  the base branch would abandon the commits the PR points at);
* a rework takes a concurrency slot on the same terms as anything else — it can neither
  exceed `concurrency.max` nor preempt a run that already holds a slot;
* the loop is BOUNDED: past `CHELA_MAX_REWORKS` it escalates to `needs_human`, freeing the
  slot, keeping the branch and the worktree, and carrying EVERY verdict into the payload;
* a PR that gets merged anyway — from any of the new states — still reconciles to `done`.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, inbox, worktree
from chela.sources import Task
from chela.workflow import WorkflowDef


# --- fixtures ----------------------------------------------------------------

@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    """A runs DB per test. ``dispatcher.DB_PATH`` is latched at import (from the sandbox
    ``$CHELA_DIR``, see conftest), so without this every test in the session would share
    one scheduler.db — and these tests write rows with the same task ids."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _wf(tmp_path: Path, **cfg) -> WorkflowDef:
    (tmp_path / "TODO.md").write_text("- [ ] do a thing\n")
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={
            "project_key": "TEST",
            "tracker": {"kind": "markdown", "path": "TODO.md"},
            "workspace": {"root": str(tmp_path / "wts"), "base_branch": "dev"},
            **cfg,
        },
        prompt_template="fresh dispatch: {{task_title}}",
    )


def _row(conn, task_id="abc123", **over) -> sqlite3.Row:
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "do a thing",
        "status": "awaiting_review", "window_name": "test-1", "worktree_path": "/wt/abc123",
        "branch_name": "test-1", "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/80", "pr_state": "open",
        "rework_count": 0, "review_history": None,
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()


class _Source:
    """The tracker, as the tick reads it. A run whose task has LEFT the tracker reconciles
    to done (a human struck the line), so a rework test has to keep its task open — the
    dispatcher strikes the line only when the PR merges."""

    def __init__(self, *task_ids: str):
        self._tasks = [
            Task(id=tid, title="do a thing", file="TODO.md", line_number=i + 1,
                 raw="- [ ] do a thing")
            for i, tid in enumerate(task_ids)
        ]

    def list_open_tasks(self):
        return list(self._tasks)


class _FakeTmux:
    """The tmux stand-in from test_dispatcher_spawn_retry, plus a record of every argv."""

    def __init__(self):
        self.windows: list[tuple[str, str]] = []
        self._next_id = 100
        self.calls: list[list[str]] = []

    def run(self, cmd, *args, **kwargs):
        self.calls.append(cmd)

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

    def new_window_cwds(self) -> list[str]:
        return [c[c.index("-c") + 1] for c in self.calls
                if isinstance(c, list) and c[:2] == ["tmux", "new-window"]]


# --- (a) the verdict: the run row is the authority; the comment is the projection ------

def test_request_changes_writes_the_row_and_posts_a_pr_COMMENT(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)
    gh: list[list[str]] = []

    def _run(cmd, *a, **k):
        gh.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    with patch.object(dispatcher.subprocess, "run", side_effect=_run):
        result = dispatcher.request_changes("abc123", "1. the slot arithmetic is wrong\n")

    assert result["ok"] and result["status"] == "changes_requested"
    assert result["comment_posted"] is True

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "changes_requested"
    # The verdict is on the row — that is the authority the dispatcher acts on.
    reviews = dispatcher.reviews_of(run)
    assert [r["round"] for r in reviews] == [1]
    assert "slot arithmetic" in reviews[0]["body"]
    # rework_count is NOT spent by the verdict — only by an actual re-spawn.
    assert (run["rework_count"] or 0) == 0

    # 🔴 `gh pr comment`, NEVER `gh pr review`: GitHub REFUSES --request-changes on a PR
    # the calling account authored ("Can not request changes on your own pull request"),
    # and the whole fleet is one account. Anything keyed on reviewDecision is built on sand.
    posted = [c for c in gh if c[:2] == ["gh", "pr"]]
    assert posted, "the verdict was never projected onto the PR"
    assert posted[0][:3] == ["gh", "pr", "comment"]
    assert "review" not in posted[0][:4]
    assert posted[0][3] == "80"          # the PR number, off the run row's url


def test_a_failed_pr_comment_does_not_stop_the_loop(tmp_path):
    """The row is the authority. `gh` missing/offline loses the human-readable copy — not
    the rework."""
    with dispatcher._db() as conn:
        _row(conn)

    with patch.object(dispatcher.subprocess, "run", side_effect=FileNotFoundError("no gh")):
        result = dispatcher.request_changes("abc123", "fix it")

    assert result["ok"] and result["comment_posted"] is False
    assert dispatcher.resolve_run("abc123")["status"] == "changes_requested"


def test_a_verdict_only_lands_on_a_run_that_is_actually_under_review(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, task_id="running1", status="running")
    assert dispatcher.request_changes("running1", "nope")["ok"] is False
    assert dispatcher.request_changes("no-such-run", "nope")["ok"] is False


def test_approve_changes_nothing_and_never_merges(tmp_path):
    """This task builds the carrier, not the judge — and merging stays a human's call."""
    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=FileNotFoundError()):
        result = dispatcher.approve("abc123", "LGTM")
    assert result["ok"] and result["status"] == "awaiting_review"
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"


def test_a_run_resolves_by_branch_name_too(tmp_path):
    """The reviewer has `cmx-68` in hand (the PR title, the branch), not a task id."""
    with dispatcher._db() as conn:
        _row(conn, task_id="deadbeef", branch_name="test-9", window_name="test-9")
    assert dispatcher.resolve_run("test-9")["task_id"] == "deadbeef"
    assert dispatcher.resolve_run("TEST-9")["task_id"] == "deadbeef"
    assert dispatcher.resolve_run("nope") is None


# --- (b) the re-spawn: the ORIGINAL worktree, on the ORIGINAL branch -------------------

def test_the_tick_respawns_into_the_existing_worktree_and_branch(tmp_path):
    """⛔ NOT a fresh fork from the base branch — that would abandon the PR's commits."""
    wf = _wf(tmp_path)
    source = _Source("abc123")
    original_wt = tmp_path / "wts" / "abc123"
    original_wt.mkdir(parents=True)
    fake = _FakeTmux()
    prompts: list[str] = []

    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             worktree_path=str(original_wt), branch_name="test-1",
             review_history=json.dumps([{"round": 1, "at": "t", "body": "the wire is loose"}]))

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "ensure_worktree") as fresh_fork, \
         patch.object(dispatcher, "attach_worktree", return_value=(original_wt, False)) as attach, \
         patch.object(dispatcher, "send_tmux", side_effect=lambda w, p: prompts.append(p) or True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")):
        summary = dispatcher.tick(wf.path)

    assert summary["reworked"] == 1
    # It attached the EXISTING branch; it never forked a new worktree off `dev`.
    assert attach.call_args[0][1] == "test-1"
    assert fresh_fork.call_count == 0
    # The tmux window opened IN the original worktree — the two-step pattern, by the
    # dispatcher's own spawn path (new-window -c <wt>, then send-keys 'claude …').
    assert fake.new_window_cwds() == [str(original_wt)]

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"
    assert run["worktree_path"] == str(original_wt)
    assert run["branch_name"] == "test-1"
    assert run["rework_count"] == 1

    # The prompt carries the verdict AND tells the agent to read the PR thread itself.
    assert prompts and "the wire is loose" in prompts[0]
    assert "gh pr view 80 --comments" in prompts[0]
    assert "test-1" in prompts[0]


def test_a_missing_worktree_is_recreated_from_the_branch(tmp_path):
    """The worktree was cleaned up; the branch (and its PR) still exist. Re-attach — do
    NOT fork from the base branch."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base", "--allow-empty"],
                   check=True, env=_git_env())
    subprocess.run(["git", "-C", str(repo), "branch", "test-1"], check=True)

    gone = tmp_path / "wts" / "abc123"
    assert not gone.exists()

    path, attached = worktree.attach_worktree(repo, "test-1", gone)

    assert attached is True
    assert path == gone and (gone / ".git").exists()
    head = subprocess.run(["git", "-C", str(gone), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert head == "test-1"          # the ORIGINAL branch, not `dev`


def test_a_missing_branch_is_a_hard_error(tmp_path):
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base", "--allow-empty"],
                   check=True, env=_git_env())
    with pytest.raises(worktree.BranchGone):
        worktree.attach_worktree(repo, "test-1", tmp_path / "wt")


def test_a_run_whose_branch_is_gone_escalates_instead_of_forking_a_fresh_one(tmp_path):
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested")

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree", side_effect=worktree.BranchGone("gone")), \
         patch.object(dispatcher, "ensure_worktree") as fresh_fork, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        dispatcher.tick(wf.path)

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert fresh_fork.call_count == 0        # it did NOT invent a replacement branch
    assert "gone" in (run["last_error"] or "")


# --- (c) the cap: bounded, then it SURFACES — it never spins ---------------------------

def test_round_three_escalates_to_needs_human_with_every_verdict_and_a_free_slot(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    wf = _wf(tmp_path)
    source = _Source("abc123")
    verdicts = [{"round": i, "at": "t", "body": f"verdict {i}"} for i in (1, 2, 3)]
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             rework_count=2, review_history=json.dumps(verdicts))

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree") as attach, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["escalated"] == 1 and summary["reworked"] == 0
    assert attach.call_count == 0            # round 3 never spawns

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    # NOTHING is thrown away: the branch, the worktree and the PR all survive.
    assert run["branch_name"] == "test-1"
    assert run["worktree_path"] == "/wt/abc123"
    assert run["pr_url"].endswith("/80")

    # The slot is FREE — needs_human is not an active status, so a stuck run cannot pin
    # the queue behind it forever.
    with dispatcher._db() as conn:
        active = conn.execute(
            "SELECT COUNT(*) FROM runs WHERE status IN ('claimed','running')").fetchone()[0]
    assert active == 0

    # The escalation carries the HISTORY of what was tried — every verdict, not the last.
    events, _ = inbox.run_events([run], {})
    escalation = [e for e in events if e["kind"] == "run_needs_human"]
    assert len(escalation) == 1
    bodies = [r["body"] for r in escalation[0]["payload"]["reviews"]]
    assert bodies == ["verdict 1", "verdict 2", "verdict 3"]
    assert escalation[0]["payload"]["rework_count"] == 2
    assert "NEEDS A HUMAN" in escalation[0]["summary"]


def test_max_reworks_zero_escalates_the_very_first_verdict(tmp_path, monkeypatch):
    """The knob is real: CHELA_MAX_REWORKS=0 turns the loop off entirely."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "0")
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested", rework_count=0)

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree") as attach, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["escalated"] == 1 and attach.call_count == 0
    assert dispatcher.resolve_run("abc123")["status"] == "needs_human"


# --- (d) 🔴 the slot arithmetic: a rework CANNOT exceed max_concurrent -----------------

def test_a_rework_waits_its_turn_and_never_preempts(tmp_path):
    """max=1, one run already `running` → the sent-back run must WAIT. It may not spawn a
    second agent, and it may not kill the one that holds the slot (hold.py: preemption is
    deliberately NO, and that stands)."""
    wf = _wf(tmp_path, concurrency={"max": 1})
    source = _Source("busy", "sent-back")
    fake = _FakeTmux()
    with dispatcher._db() as conn:
        _row(conn, task_id="busy", workflow_path=str(wf.path), status="running",
             branch_name="test-2", window_name="test-2", pr_url=None, pr_state=None)
        _row(conn, task_id="sent-back", workflow_path=str(wf.path),
             status="changes_requested")

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree") as attach, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-2"}), \
         patch.object(dispatcher, "_capture_pane", return_value=""), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["reworked"] == 0
    assert attach.call_count == 0
    assert fake.new_window_cwds() == []                      # nothing was spawned
    assert dispatcher.resolve_run("sent-back")["status"] == "changes_requested"
    assert dispatcher.resolve_run("busy")["status"] == "running"   # never preempted


def test_a_rework_takes_the_slot_before_a_fresh_task_claims_it(tmp_path):
    """Finishing work beats starting more of it — and both draw on the same one slot."""
    wf = _wf(tmp_path, concurrency={"max": 1})
    source = _Source("abc123", "fresh")
    wt = tmp_path / "wts" / "abc123"
    wt.mkdir(parents=True)
    fresh = Task(id="fresh", title="a new thing", file=str(tmp_path / "TODO.md"),
                 line_number=1, raw="- [ ] a new thing")

    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             worktree_path=str(wt))

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[fresh]), \
         patch.object(dispatcher, "attach_worktree", return_value=(wt, False)), \
         patch.object(dispatcher, "ensure_worktree") as fresh_fork, \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run), \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")):
        summary = dispatcher.tick(wf.path)

    assert summary["reworked"] == 1
    assert summary["dispatched"] == 0        # the only slot went to the rework
    assert fresh_fork.call_count == 0
    assert dispatcher.resolve_run("fresh") is None


@pytest.mark.parametrize("status", ["changes_requested", "needs_human"])
def test_a_parked_task_is_never_RE_CLAIMED_as_a_fresh_one(tmp_path, status):
    """⛔ THE HOLE THIS CLOSES. A parked run frees its concurrency slot (by design — a run
    waiting on a human must not pin the fleet) and its tracker line is still OPEN (the
    dispatcher only strikes it when the PR MERGES). So the very next tick offers that task
    to the claim loop with a slot free — and a claim filter that did not know these states
    would fork a SECOND worktree off the base branch, abandoning the branch, the commits
    and the PR the reviewer is looking at, while the first agent's work sits there.

    Seen to go red: reverting `NOT_CLAIMABLE` to the old
    ``('claimed','running','awaiting_review','done')`` list fails this test.
    """
    wf = _wf(tmp_path, concurrency={"max": 2})     # a slot IS free — that is the point
    source = _Source("abc123")
    task = source.list_open_tasks()[0]
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status=status, rework_count=9)

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[task]), \
         patch.object(dispatcher, "ensure_worktree") as fresh_fork, \
         patch.object(dispatcher, "attach_worktree") as attach, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["dispatched"] == 0
    assert fresh_fork.call_count == 0              # no second worktree, ever
    assert attach.call_count == 0                  # (rework_count is past the cap)
    run = dispatcher.resolve_run("abc123")
    assert run["status"] in ("changes_requested", "needs_human")
    assert run["branch_name"] == "test-1"          # its branch is untouched
    # ...and a parked run holds no slot, which is what made this reachable at all.
    assert dispatcher.ACTIVE_STATUSES == ("claimed", "running")


# --- (e) reconciliation must not fight the loop ---------------------------------------

@pytest.mark.parametrize("status", ["changes_requested", "needs_human"])
def test_a_merged_pr_still_reconciles_to_done_from_any_review_state(tmp_path, status):
    """A human merged it anyway — because the verdict was wrong, or because they fixed it
    themselves. A merged PR is done, whatever state the loop left the row in."""
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status=status, pr_state="open")

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_status", return_value=("merged", "MERGEABLE")), \
         patch.object(dispatcher, "_strike_merged_tasks", return_value=1), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["reconciled_done"] == 1
    assert dispatcher.resolve_run("abc123")["status"] == "done"


def test_a_vanished_window_in_a_review_state_is_completion_not_death(tmp_path):
    """The agent killed its own window on task-finished; the PR then failed review. That
    window is not a corpse — reporting it as one is the false-DIED bug in a new hat."""
    for status in ("changes_requested", "needs_human"):
        assert status in inbox.SETTLED_RUN_STATES
    event = inbox._gone_event("@7", "test-1", "", {"status": "changes_requested"})
    assert event["kind"] == "completed_gone" and event.get("silent") is True


# --- schema: an old DB must keep working ----------------------------------------------

def test_a_legacy_runs_table_migrates_and_its_rows_read_as_never_reworked():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE runs (
            task_id TEXT PRIMARY KEY, workflow_path TEXT NOT NULL, title TEXT NOT NULL,
            status TEXT NOT NULL, window_name TEXT, worktree_path TEXT, branch_name TEXT,
            started_at TEXT, ended_at TEXT, attempt INTEGER, last_error TEXT
        )"""
    )
    conn.execute("INSERT INTO runs (task_id, workflow_path, title, status) "
                 "VALUES ('old', 'w', 't', 'awaiting_review')")

    dispatcher.ensure_schema(conn)

    row = dict(conn.execute("SELECT * FROM runs WHERE task_id='old'").fetchone())
    assert row["review_history"] is None
    assert (row["rework_count"] or 0) == 0
    assert dispatcher.reviews_of(row) == []
    assert dispatcher.latest_verdict(row) == ""


# --- helpers ---------------------------------------------------------------------------

def _status(wf: WorkflowDef):
    from chela.workflow import WorkflowStatus

    return WorkflowStatus(path=wf.path, workflow=wf, error=None)


def _git_env() -> dict:
    import os

    return {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}
