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

import io
import json
import sqlite3
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, hold, inbox, worktree
from chela.sources import Task
from chela.workflow import WorkflowDef


# --- fixtures ----------------------------------------------------------------

@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    """A runs DB per test. ``dispatcher.DB_PATH`` is latched at import (from the sandbox
    ``$CHELA_DIR``, see conftest), so without this every test in the session would share
    one scheduler.db — and these tests write rows with the same task ids."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _parse_escalation(last_error: str) -> tuple[str, str, list[str]]:
    """Split a composed ``last_error`` back into (reason, recommendation, options) — the
    inverse of ``dispatcher._format_escalation``. Lets a test assert the CONTENT of the
    recommendation/options, not just that the "Recommendation:"/"Options:" labels render."""
    reason, sep, rest = last_error.partition("\n\nRecommendation: ")
    if not sep:
        return last_error, "", []
    recommendation, sep2, rest2 = rest.partition("\n\nOptions:\n")
    if not sep2:
        return reason, recommendation, []
    options = [line[len("  - "):] for line in rest2.split("\n") if line.startswith("  - ")]
    return reason, recommendation, options


def _assert_actionable_escalation(last_error: str) -> None:
    """Structural guard (CMX-242, rework round 1): the recommendation must be non-empty, the
    menu must have ≥2 real options, and the recommendation must actually NAME one of them
    (or explicitly opt out) — not just that the labels render. Checked on the PARSED
    content, so it survives a pure formatting change."""
    _, recommendation, options = _parse_escalation(last_error)
    assert recommendation.strip(), "an automatic escalation must carry a non-empty recommendation"
    assert len(options) >= 2, "one option is not a choice"
    assert (
        any(o in recommendation for o in options)
        or recommendation.lower().startswith("none of these")
    ), "the recommendation must name one of its own options (or explicitly opt out)"


def _wf(tmp_path: Path, **cfg) -> WorkflowDef:
    """The workflow these tests dispatch — WITH HOOKS, and that is the point.

    🔴 The first cut of this fixture had no `hooks:` key at all, and that is exactly why its
    suite could not see the blocker: `_respawn_rework` called `_launch_agent` directly, so
    NEITHER hook ran on the rework path, and a fixture with no hooks cannot notice. This
    repo's real WORKFLOW.md runs `uv sync --all-extras` as before_run (the CMX-21 trap), so
    the reworking agent — ordered by its own prompt to re-run the CI gates and believe them
    — was being launched into a worktree whose venv was never synced.

    Every workflow this suite dispatches now carries both hooks. A future refactor that
    drops one goes red here.
    """
    (tmp_path / "TODO.md").write_text("- [ ] do a thing\n")
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={
            "project_key": "TEST",
            "tracker": {"kind": "markdown", "path": "TODO.md"},
            "workspace": {"root": str(tmp_path / ".chela" / "wts"), "base_branch": "dev"},
            "hooks": {
                "after_create": "seed-settings {{workspace_path}}",   # the permission file
                "before_run": "uv sync --all-extras",                 # the venv
            },
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
        # Every hook the dispatcher ran: (command, cwd). A hook is a shell=True call — the
        # ONE thing that distinguishes it from tmux argv, and the thing the rework path was
        # missing entirely.
        self.hooks: list[tuple[str, str]] = []

    def run(self, cmd, *args, **kwargs):
        self.calls.append(cmd)
        if kwargs.get("shell"):
            self.hooks.append((cmd, str(kwargs.get("cwd"))))

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
    """This task builds the carrier, not the judge — and merging stays a human's call.

    ⛔ The stub must answer the CHECK read too: since CMX-69 an approval reads the PR's
    checks back from GitHub and REFUSES a red one (and an unreadable one). A `gh` that
    cannot be run at all is `unknown`, and unknown is not a pass.
    """
    with dispatcher._db() as conn:
        _row(conn)

    def _gh(cmd, *a, **k):
        class R:
            returncode = 0
            stdout = json.dumps({"headRefOid": "abc", "statusCheckRollup": [
                {"__typename": "CheckRun", "name": "test", "status": "COMPLETED",
                 "conclusion": "SUCCESS"}]})
            stderr = ""
        return R()

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh):
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
    original_wt = tmp_path / ".chela" / "wts" / "abc123"
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
    # CMX-244: the escape hatch is unreachable if nobody tells the agent it exists — the
    # prompt must name the actual command, not just describe the problem.
    assert "chela rework-disputed abc123" in prompts[0]


def test_a_missing_worktree_is_recreated_from_the_branch(tmp_path):
    """The worktree was cleaned up; the branch (and its PR) still exist. Re-attach — do
    NOT fork from the base branch."""
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "dev", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base", "--allow-empty"],
                   check=True, env=_git_env())
    subprocess.run(["git", "-C", str(repo), "branch", "test-1"], check=True)

    gone = tmp_path / ".chela" / "wts" / "abc123"
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


def test_a_run_with_no_branch_at_all_escalates_with_a_recommendation_too(tmp_path):
    """The FIRST `_escalate` call site in `_respawn_rework` — a row that was never given a
    branch to rework in the first place (``branch_name`` is falsy), so there is nothing to
    reattach and `attach_worktree` is never even called (CMX-242)."""
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested", branch_name=None)

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree") as attach, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        dispatcher.tick(wf.path)

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert attach.call_count == 0            # no branch to reattach — never even tried
    assert "no branch" in (run["last_error"] or "")
    assert "Recommendation:" in run["last_error"]
    assert "Options:\n  - " in run["last_error"]
    _assert_actionable_escalation(run["last_error"])


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
    # CMX-242: a dead end still names what a human could try next — and it must be an
    # actual recommendation naming a real option, not just rendered labels.
    assert "Recommendation:" in run["last_error"]
    assert "Options:\n  - " in run["last_error"]
    _assert_actionable_escalation(run["last_error"])


def test_a_worktree_attach_failure_escalates_with_a_recommendation_too(tmp_path):
    """The third `_escalate` call site in `_respawn_rework` — a git error attaching the
    worktree (not a gone branch) — gets the same treatment (CMX-242)."""
    wf = _wf(tmp_path)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested")

    err = subprocess.CalledProcessError(returncode=128, cmd=["git", "worktree", "add"],
                                        stderr=b"fatal: already exists")
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree", side_effect=err), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        dispatcher.tick(wf.path)

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert "already exists" in run["last_error"]
    assert "Recommendation:" in run["last_error"]
    assert "Options:\n  - " in run["last_error"]
    _assert_actionable_escalation(run["last_error"])


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
    # CMX-242: the escalation itself (in last_error) carries a recommendation and options,
    # not just "it gave up".
    assert "Recommendation:" in run["last_error"]
    assert "Options:\n  - " in run["last_error"]
    _assert_actionable_escalation(run["last_error"])


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


# --- (c′) CMX-242: an automatic escalation names a recommendation and options, not just
#          a bare reason — the same fields `chela escalate` supports for a human-typed one.

def test_escalate_with_no_recommendation_or_options_writes_the_bare_reason(tmp_path):
    """Backward compat: a call site with nothing useful to add (none exist today, but the
    parameters are optional) must not grow a dangling "Recommendation:"/"Options:" section."""
    with dispatcher._db() as conn:
        row = _row(conn, status="changes_requested")
        dispatcher._escalate(conn, row, "plain reason, nothing more to say")

    run = dispatcher.resolve_run("abc123")
    assert run["last_error"] == "plain reason, nothing more to say"
    assert "Recommendation:" not in run["last_error"]
    assert "Options:" not in run["last_error"]


def test_escalate_formats_a_recommendation_and_every_option(tmp_path):
    with dispatcher._db() as conn:
        row = _row(conn, status="changes_requested")
        dispatcher._escalate(
            conn, row, "the loop gave up",
            recommendation="try this first",
            options=["do A", "do B", "do C"],
        )

    run = dispatcher.resolve_run("abc123")
    assert run["last_error"] == (
        "the loop gave up"
        "\n\nRecommendation: try this first"
        "\n\nOptions:\n  - do A\n  - do B\n  - do C"
    )


def test_actionable_escalation_helper_flags_a_recommendation_not_among_its_own_options():
    """Sanity on the parsed-content guard itself: a recommendation naming something that
    isn't in its own options list must fail — proving the check inspects content, not just
    that "Recommendation:"/"Options:" render (the ticket's explicit example)."""
    last_error = (
        "the loop gave up"
        "\n\nRecommendation: do something else entirely"
        "\n\nOptions:\n  - do A\n  - do B"
    )
    with pytest.raises(AssertionError):
        _assert_actionable_escalation(last_error)


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
    wt = tmp_path / ".chela" / "wts" / "abc123"
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


def test_reconcile_never_false_dones_a_SIBLING_WORKFLOWS_live_run(tmp_path):
    """CMX-102. `open_ids` is only THIS workflow's open tasks — a run belonging to a
    DIFFERENT registered workflow was never going to be in it. Before the fix, the
    reconcile query pulled active/review rows from every workflow, so a tick of workflow
    A saw workflow B's live run, found its task_id missing from A's open_ids, and killed
    it as "removed from source, window killed" — seconds after B dispatched it.

    Seen to go red: reverting the `WHERE workflow_path=?` scope on the reconcile query
    (chela/dispatcher.py) fails this test — the sibling row gets marked `done` and its
    window killed even though it belongs to a workflow this tick never touched.
    """
    wf = _wf(tmp_path)
    sibling_wf_path = str(tmp_path / "sibling" / "WORKFLOW.md")
    source = _Source("foo")     # workflow A's ONLY open task — "sibling-task" is not in it
    with dispatcher._db() as conn:
        _row(conn, task_id="foo", workflow_path=str(wf.path),
             status="awaiting_review", pr_state="open", window_name="test-1")
        _row(conn, task_id="sibling-task", workflow_path=sibling_wf_path,
             status="running", pr_state=None, pr_url=None, window_name="sib-1")

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1"), ("@2", "sib-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["reconciled_done"] == 0
    sibling = dispatcher.resolve_run("sibling-task")
    assert sibling["status"] == "running"          # NOT false-doned
    assert sibling["window_name"] == "sib-1"        # NOT killed
    assert dispatcher.resolve_run("foo")["status"] == "awaiting_review"


def test_reconcile_never_false_dones_a_PR_LESS_orphaned_run_CMX_100(tmp_path):
    """CMX-100 repro (`bcdcf0738775`): a `running` row whose worker window died before it
    ever opened a PR, whose task_id then leaves `open_ids` for ANY non-merge reason (tracker
    edited, task re-hashed, uncommitted-tracker churn), must NOT be marked `done` — "the task
    left the tracker" is not proof of a merge. It reconciles to `failed` instead, via the
    SAME window-gone→failed path a dead running window already takes, so it can re-dispatch
    (`failed` is not in `NOT_CLAIMABLE`; `done` is).

    Seen to go red: marking the `else` branch `done` unconditionally (the pre-fix behavior)
    fails this test — the row ends up `done` with no PR and no merge evidence.
    """
    wf = _wf(tmp_path)
    source = _Source()      # "abc123" has left the tracker entirely
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running",
             window_name="test-1", pr_url=None, pr_state=None)

    fake = _FakeTmux()      # "test-1" is NOT in fake.windows — the window is dead
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_url", return_value=None), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["reconciled_done"] == 0
    assert summary["reconciled_failed"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "failed"
    assert run["last_error"] == "tmux window disappeared"


def test_reconcile_done_ordering_never_preempts_the_failed_path(tmp_path):
    """Guard 5 — ordering. The `not in open_ids` check runs BEFORE the window-gone→failed
    check, so a PR-less dead-window row must not win a race into `done` by virtue of being
    checked first: the evidence gate has to reject it there and let the failed check (below)
    actually make the call. This is the same scenario as the cmx-100 repro, asserted from the
    ordering angle: if the done-branch were ever restored to fire unconditionally, this row
    would be marked `done` on the SAME tick the failed-path should have claimed it.
    """
    wf = _wf(tmp_path)
    source = _Source()
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running",
             window_name="test-1", pr_url=None, pr_state=None)

    fake = _FakeTmux()       # dead window AND not in open_ids, at the same time
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_url", return_value=None), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    # The done-branch never fires for this row — the failed-path (reused, not duplicated)
    # is what actually decides it.
    assert summary["reconciled_done"] == 0
    assert summary["reconciled_failed"] == 1
    assert dispatcher.resolve_run("abc123")["status"] == "failed"


def test_reconcile_done_from_a_MERGED_PR_even_with_no_review_state(tmp_path):
    """Guard 4 — merged-PR evidence counts even without a review state. A `running` row
    that left `open_ids` but whose `_read_pr_status` finds a MERGED PR is legitimate
    completion evidence (the agent pushed a PR directly and it merged before the loop ever
    saw an awaiting_review row) — it reconciles to `done`, same as the REVIEW-status path.

    Seen to go red: gating `done` on review-status alone (ignoring a merged PR when the row
    never reached a review state) fails this test — the row is left `running`/`failed`
    instead of `done` despite a real, verifiable merge.
    """
    wf = _wf(tmp_path)
    source = _Source()
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             pr_url="https://github.com/o/r/pull/80", pr_state="open")

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]     # window still alive — irrelevant once merged
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_url", return_value=None), \
         patch.object(dispatcher, "_read_pr_status", return_value=("merged", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["reconciled_done"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "done"
    assert run["pr_url"] == "https://github.com/o/r/pull/80"


def test_reconcile_done_from_a_review_state_row_that_left_the_tracker(tmp_path):
    """Guard 3 (confirm) — the genuine happy path stays intact. A REVIEW-status row (the
    agent opened a PR; a human struck the tracker line by hand, or the legacy self-strike
    flow removed it) whose task left `open_ids` still reconciles to `done` — the completion-
    evidence gate added for claimed/running rows must not touch this path.
    """
    wf = _wf(tmp_path)
    source = _Source()      # "abc123" has left the tracker
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="awaiting_review",
             window_name="test-1", pr_url="https://github.com/o/r/pull/80", pr_state="open")

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_url", return_value=None), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["reconciled_done"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "done"
    kill_calls = [c for c in fake.calls if isinstance(c, list) and c[:2] == ["tmux", "kill-window"]]
    assert any("test-1" in c[-1] for c in kill_calls)   # window killed


def test_a_vanished_window_in_a_review_state_is_completion_not_death(tmp_path):
    """The agent killed its own window on task-finished; the PR then failed review. That
    window is not a corpse — reporting it as one is the false-DIED bug in a new hat."""
    for status in ("changes_requested", "needs_human"):
        assert status in inbox.SETTLED_RUN_STATES
    event = inbox._gone_event("@7", "test-1", "", {"status": "changes_requested"})
    assert event["kind"] == "completed_gone" and event.get("silent") is True


# --- (f) 🔴 THE ENVIRONMENT HALF: a rework is launched into a PREPARED worktree ---------
#
# The review of PR #81 found the seam: `_respawn_rework` called `_launch_agent` directly, and
# `_launch_agent` was only the TMUX half of a spawn. The hooks — the half that makes the
# worktree usable — stayed behind in `_spawn`, so the rework ran NONE of them. This repo's
# before_run is `uv sync --all-extras` (the CMX-21 trap), and the rework prompt orders the
# agent to "re-run the SAME validation ... the CI gates are not optional". It would have been
# obeying that order in a worktree whose venv was never synced: phantom failures, and an
# agent that either "fixes" breakage that was never real or calls the verdict unfixable.

def _rework_tick(tmp_path, *, attached: bool, **kw):
    """One tick that re-spawns a sent-back run. Returns (summary, fake, prompts)."""
    wf = kw.pop("wf", None) or _wf(tmp_path)
    wt = tmp_path / ".chela" / "wts" / "abc123"
    wt.mkdir(parents=True, exist_ok=True)
    fake = _FakeTmux()
    prompts: list[str] = []
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             worktree_path=str(wt),
             review_history=json.dumps([{"round": 1, "at": "t", "body": "the wire is loose"}]),
             **kw)

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=_Source("abc123")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree", return_value=(wt, attached)), \
         patch.object(dispatcher, "ensure_worktree") as fresh_fork, \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "send_tmux", side_effect=lambda w, p: prompts.append(p) or True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")):
        summary = dispatcher.tick(wf.path)
    assert fresh_fork.call_count == 0
    return summary, fake, prompts


def test_a_rework_runs_before_run_LIKE_EVERY_OTHER_LAUNCH(tmp_path):
    """🔴 The blocker. `before_run` is what makes the venv real (`uv sync --all-extras`).
    Skip it and the reworking agent — under orders to re-run the CI gates and believe them —
    reads phantom failures out of a worktree that was never synced.

    Seen to go red: reverting `_respawn_rework` to call `_launch_agent` without the hooks
    leaves `fake.hooks == []` here."""
    wt = tmp_path / ".chela" / "wts" / "abc123"
    summary, fake, _ = _rework_tick(tmp_path, attached=False)

    assert summary["reworked"] == 1
    assert ("uv sync --all-extras", str(wt)) in fake.hooks     # IN THE WORKTREE, not the repo

    # ...and BEFORE the agent was launched into it. A venv synced after the prompt lands is
    # a race the agent loses.
    first_window = next(i for i, c in enumerate(fake.calls)
                        if isinstance(c, list) and c[:2] == ["tmux", "new-window"])
    first_hook = next(i for i, c in enumerate(fake.calls) if isinstance(c, str))
    assert first_hook < first_window


def test_a_RE_CREATED_worktree_gets_after_create_too(tmp_path):
    """`attach_worktree` had to re-create the directory: it holds the branch's tracked files
    and NOTHING else — no `.claude/settings.local.json`. _spawn's own comment calls a missing
    one a hard dispatch abort, because the agent hangs on its first permission prompt. A
    re-created worktree IS a fresh worktree."""
    wt = tmp_path / ".chela" / "wts" / "abc123"
    _, fake, _ = _rework_tick(tmp_path, attached=True)

    assert fake.hooks == [
        (f"seed-settings {wt}", str(wt)),        # after_create, rendered with the worktree
        ("uv sync --all-extras", str(wt)),       # then before_run — in that order
    ]


def test_a_REUSED_worktree_skips_after_create_but_still_syncs(tmp_path):
    """The worktree survived, so its settings file did too — seeding it again is the one
    thing after_create must not do. `before_run` still runs: it is per-LAUNCH, not
    per-worktree (the branch moved on, and so did the lockfile)."""
    wt = tmp_path / ".chela" / "wts" / "abc123"
    _, fake, _ = _rework_tick(tmp_path, attached=False)

    assert fake.hooks == [("uv sync --all-extras", str(wt))]


def test_a_hook_that_fails_does_not_launch_an_agent_into_a_broken_worktree(tmp_path):
    """A hard abort on the rework path too — and the run goes BACK to changes_requested (it
    is not a fresh dispatch to retry), one round poorer, so a hook that always fails walks
    the run to needs_human instead of spinning."""
    wf = _wf(tmp_path)
    wt = tmp_path / ".chela" / "wts" / "abc123"
    wt.mkdir(parents=True)
    fake = _FakeTmux()

    def _run(cmd, *a, **kw):
        if kw.get("shell"):
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
        return fake.run(cmd, *a, **kw)

    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             worktree_path=str(wt),
             review_history=json.dumps([{"round": 1, "at": "t", "body": "v1"}]))

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=_Source("abc123")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "attach_worktree", return_value=(wt, False)), \
         patch.object(dispatcher.subprocess, "run", side_effect=_run), \
         patch.object(dispatcher, "send_tmux") as send, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")):
        summary = dispatcher.tick(wf.path)

    assert summary["reworked"] == 0
    send.assert_not_called()                                   # no agent in a broken worktree
    assert fake.new_window_cwds() == []
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "changes_requested"                # NOT 'failed' — see (g)
    assert run["rework_count"] == 1                            # the attempt cost a round


# --- (g) 🔴 a rework that DIES re-enters the rework loop — it is never a fresh dispatch ---

def test_a_rework_that_cannot_launch_is_NEVER_re_claimed_as_a_FRESH_task(tmp_path, monkeypatch):
    """⛔ THE HOLE. `failed` is not in NOT_CLAIMABLE — deliberately, it is the retry state for
    a first dispatch. A rework dropped into it gets picked up by the claim loop as ordinary
    work: `_spawn` renders the ORIGINAL first-dispatch prompt ("branch, then open a PR") at an
    agent whose PR is already open, bumps `attempt`, and never looks at `rework_count`. The
    verdict is lost and the cap is bypassed.

    Three ticks with a launch that can never land: the run walks changes_requested → rounds
    spent → needs_human. It is never dispatched fresh, and it never spins.

    Seen to go red: restoring `UPDATE runs SET status='failed'` in the tick's rework
    exception handler makes tick 2 re-dispatch it with the fresh prompt.
    """
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    wf = _wf(tmp_path)
    wt = tmp_path / ".chela" / "wts" / "abc123"
    wt.mkdir(parents=True)
    task = _Source("abc123").list_open_tasks()[0]
    prompts: list[str] = []
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested",
             worktree_path=str(wt), attempt=1,
             review_history=json.dumps([{"round": 1, "at": "t", "body": "the wire is loose"}]))

    statuses = []
    for _ in range(3):
        with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
             patch.object(dispatcher, "get_source", return_value=_Source("abc123")), \
             patch.object(dispatcher, "_claim_order", return_value=[task]), \
             patch.object(dispatcher, "attach_worktree", return_value=(wt, False)), \
             patch.object(dispatcher, "ensure_worktree") as fresh_fork, \
             patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run), \
             patch.object(dispatcher, "_wait_for_ready", return_value=True), \
             patch.object(dispatcher, "_send_seed", return_value=False), \
             patch.object(dispatcher, "send_tmux", side_effect=lambda w, p: prompts.append(p)), \
             patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")):
            dispatcher.tick(wf.path)
        # ⛔ NOT ONCE, on any tick: a fresh worktree off the base branch would abandon the
        # PR's commits — and the fresh prompt would tell the agent to open a second PR.
        assert fresh_fork.call_count == 0
        statuses.append(dispatcher.resolve_run("abc123")["status"])

    # Round 1 fails → back to changes_requested. Round 2 fails → cap spent. Then it SURFACES.
    assert statuses == ["changes_requested", "changes_requested", "needs_human"]
    run = dispatcher.resolve_run("abc123")
    assert run["rework_count"] == 2                # every round accounted for; none bypassed
    assert run["attempt"] == 1                     # it was never re-dispatched as a new task
    assert dispatcher.latest_verdict(dict(run)) == "the wire is loose"   # the verdict SURVIVED
    assert all("fresh dispatch" not in p for p in prompts)


def test_a_dead_rework_WINDOW_returns_to_the_rework_loop_too(tmp_path):
    """The other route into `failed`: the watchdog. A running rework whose tmux window
    disappeared must go back to `changes_requested` — same reason, same hole.

    The one free slot is held by an unrelated run, so the tick cannot immediately re-spawn
    the rework it just reclaimed — which is what makes the intermediate state observable
    here. (It would be re-spawned on the next tick with a slot, and that is correct.)"""
    wf = _wf(tmp_path, concurrency={"max": 1})
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", rework_count=1,
             window_name="test-1",
             review_history=json.dumps([{"round": 1, "at": "t", "body": "v1"}]))
        _row(conn, task_id="busy", workflow_path=str(wf.path), status="running",
             window_name="test-2", branch_name="test-2", pr_url=None, pr_state=None)

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=_Source("abc123", "busy")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-2"}), \
         patch.object(dispatcher, "_pane_idle_empty_prompt", return_value=False), \
         patch.object(dispatcher, "attach_worktree") as attach, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        dispatcher.tick(wf.path)

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "changes_requested"      # ⛔ NOT 'failed'
    assert attach.call_count == 0                    # the slot was taken; it waits its turn
    # The round it was already working on is spent — and NOT double-charged. One dead window
    # must not burn the whole budget.
    assert run["rework_count"] == 1
    assert dispatcher.latest_verdict(dict(run)) == "v1"


def test_a_stuck_rework_is_re_nudged_with_its_REWORK_prompt_not_the_first_dispatch_one(tmp_path):
    """The watchdog re-sends the prompt to an agent idle at an empty prompt. For a rework
    that is the REWORK prompt: the two say opposite things ("branch and open a PR" vs "you
    are on your branch, your PR is open, here is the verdict"). Re-seeding the wrong one is
    the same lost verdict, delivered by hand."""
    wf = _wf(tmp_path)
    sent: list[str] = []
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", rework_count=1,
             window_name="test-1", started_at="2020-01-01T00:00:00+00:00",
             review_history=json.dumps([{"round": 1, "at": "t", "body": "the wire is loose"}]))

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=_Source("abc123")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-1"}), \
         patch.object(dispatcher, "_capture_pane", return_value=""), \
         patch.object(dispatcher, "_pane_idle_empty_prompt", return_value=True), \
         patch.object(dispatcher, "_agent_status", return_value="idle"), \
         patch.object(dispatcher, "_send_seed",
                      side_effect=lambda w, p, t: sent.append(p) or True), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["watchdog_renudged"] == 1
    assert sent and "REWORK" in sent[0] and "the wire is loose" in sent[0]
    assert "fresh dispatch" not in sent[0]
    # CMX-244: _renudge_prompt re-renders from the same template as the initial spawn — the
    # dispute escape hatch must survive a re-nudge too, or a re-nudged agent never learns it.
    assert "chela rework-disputed abc123" in sent[0]


# --- (h) 🔴 changes_requested is not a silent state, and a HOLD must not freeze the exit ---

def test_a_HOLD_pauses_the_rework_but_NEVER_the_escalation(tmp_path, monkeypatch):
    """A hold pauses CLAIMS, and a re-spawn is a claim — that part is right. But escalation is
    not a claim: it takes no slot and starts no agent. Freezing it behind a hold means a
    forgotten hold also silences the ONE transition that says "the loop gave up, come look".

    Seen to go red: moving the cap check back below the `hold.active()` early-return leaves
    the over-cap run in changes_requested with `escalated == 0`."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "1")
    wf = _wf(tmp_path)
    now = time.time()
    held = hold.Hold(reason="rewriting the queue", by="liav", pid=1,
                     created_at=now, expires_at=now + 3600)
    with dispatcher._db() as conn:
        _row(conn, task_id="over-cap", workflow_path=str(wf.path),
             status="changes_requested", rework_count=1)
        _row(conn, task_id="under-cap", workflow_path=str(wf.path),
             status="changes_requested", rework_count=0, branch_name="test-2",
             window_name="test-2")

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=_Source("over-cap", "under-cap")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.hold, "expire_if_stale", return_value=None), \
         patch.object(dispatcher.hold, "active", return_value=held), \
         patch.object(dispatcher, "attach_worktree") as attach, \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        summary = dispatcher.tick(wf.path)

    assert summary["held"] is True
    assert summary["escalated"] == 1                  # the exit is NOT frozen
    assert summary["reworked"] == 0                   # the claim IS
    assert attach.call_count == 0
    assert dispatcher.resolve_run("over-cap")["status"] == "needs_human"
    assert dispatcher.resolve_run("under-cap")["status"] == "changes_requested"


def test_changes_requested_ANNOUNCES_ITSELF(tmp_path):
    """It used to emit nothing at all: inbox.run_events fired for awaiting_review, needs_human
    and failed, and a run sent back for rework passed through in silence. If the tick that was
    supposed to pick it up never comes (a hold, a broken WORKFLOW.md, a workflow dropped from
    CHELA_DISPATCH_WORKFLOWS), the run parks there forever and NOTHING says so."""
    with dispatcher._db() as conn:
        _row(conn, status="changes_requested", rework_count=0,
             review_history=json.dumps([{"round": 1, "at": "t", "body": "the wire is loose"}]))
    run = dispatcher.resolve_run("abc123")

    events, seen = inbox.run_events([dict(run)], {})

    sent_back = [e for e in events if e["kind"] == "run_changes_requested"]
    assert len(sent_back) == 1
    assert "sent back for rework" in sent_back[0]["summary"]
    assert sent_back[0]["payload"]["reviews"][0]["body"] == "the wire is loose"

    # Edge-triggered, exactly like the others: it announces once, not once per 30s tick.
    assert inbox.run_events([dict(run)], seen)[0] == []

    # ...and it is dropped at DELIVERY once the loop has actually turned — which is the
    # normal case (the next tick re-spawns it), and saying so then would be noise.
    assert inbox.stale_reason(sent_back[0], [{"task_id": "abc123", "status": "running"}])
    assert inbox.stale_reason(sent_back[0], [dict(run)]) is None


# --- (i) 🔴 the verdict cannot resurrect a run that MOVED ------------------------------

def test_request_changes_will_not_resurrect_a_MERGED_run(tmp_path):
    """A dispatcher tick reconciles the row to `done` (the human merged the PR) in the window
    between the CLI's read and its write. With no compare-and-swap the UPDATE lands anyway:
    a merged run is dragged back to changes_requested, and the next tick re-spawns an agent
    onto a branch whose PR is closed.

    The stale read is simulated exactly: resolve_run answers with the row as it was, while
    the DB has already moved on.

    Seen to go red: dropping `AND status='awaiting_review'` from the UPDATE."""
    with dispatcher._db() as conn:
        _row(conn)
    stale = dict(dispatcher.resolve_run("abc123"))        # read: awaiting_review
    with dispatcher._db() as conn:                        # ...and the world moves on
        conn.execute("UPDATE runs SET status='done' WHERE task_id='abc123'")
        conn.commit()

    with patch.object(dispatcher, "resolve_run", return_value=stale), \
         patch.object(dispatcher.subprocess, "run", side_effect=FileNotFoundError()):
        result = dispatcher.request_changes("abc123", "the wire is loose")

    assert result["ok"] is False
    assert "done" in result["error"]
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "done"                        # NOT resurrected
    assert dispatcher.reviews_of(dict(run)) == []         # and nothing was written


class _ReviewArgs:
    """`chela review <run> --request-changes --body-file -`."""
    run = "abc123"
    approve = False
    request_changes = True
    body_file = None


def _review_output(capsys, monkeypatch, body="the wire is loose") -> str:
    from chela import main

    monkeypatch.setattr(main.sys, "stdin", io.StringIO(body))
    args = _ReviewArgs()
    args.body_file = "-"
    with patch.object(dispatcher.subprocess, "run", side_effect=FileNotFoundError()):
        main.cmd_review(args)
    return capsys.readouterr().out


def test_the_CLI_does_not_PROMISE_a_re_spawn_it_never_CHECKED(tmp_path, monkeypatch, capsys):
    """`chela review --request-changes` printed "The dispatcher re-spawns it in its own
    worktree on the next tick." UNCONDITIONALLY — a promise nothing verified. If the workflow
    is not in CHELA_DISPATCH_WORKFLOWS, no tick will ever come for that run: it parks in
    `changes_requested` forever while the reviewer walks away believing the loop is turning.
    The reviewer is the one person who can fix that, and this is the moment they are looking.

    Seen to go red: the old unconditional print promises the re-spawn for a workflow that
    nothing dispatches.
    """
    from chela import main

    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))

    # (a) NOTHING ticks this workflow → say so, and say what to do about it instead.
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    out = _review_output(capsys, monkeypatch)
    assert "changes_requested" in out                      # the verdict still landed
    assert "NOT in CHELA_DISPATCH_WORKFLOWS" in out
    assert f"chela dispatch {wf.path}" in out
    assert "re-spawns it" not in out                       # ⛔ never both

    # (b) the daemon does tick it, and nothing is in the way → the promise is TRUE.
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET status='awaiting_review' WHERE task_id='abc123'")
        conn.commit()
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [wf.path.resolve()])
    with patch.object(main.workflow, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(main.hold, "active", return_value=None):
        out = _review_output(capsys, monkeypatch)
    assert "The dispatcher re-spawns it in its own worktree on the next tick." in out

    # (c) the queue is HELD → a re-spawn is a claim, and claims are what a hold pauses.
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET status='awaiting_review' WHERE task_id='abc123'")
        conn.commit()
    now = time.time()
    held = hold.Hold(reason="rewriting the queue", by="liav", pid=1,
                     created_at=now, expires_at=now + 3600)
    with patch.object(main.workflow, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(main.hold, "active", return_value=held):
        out = _review_output(capsys, monkeypatch)
    assert "HELD" in out and "rewriting the queue" in out
    assert "re-spawns it" not in out


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
