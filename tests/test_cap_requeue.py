"""♻️ CMX-290 — a spent rework cap re-queues a narrower fresh attempt; only the SECOND
exhaustion of the same task goes to ``needs_human``.

Real git and worktrees, the in-memory Linear team — the CMX-65 requeue suite's fixtures.
"""
from __future__ import annotations

import json

import pytest

from chela import dispatcher, inbox
from tests import test_close_requeue as _cr
from tests.test_linear_workflow_states import _row, _set, issue

repo = _cr.repo
team = _cr.team
PR, PR2 = _cr.PR, "https://github.com/o/r/pull/34"

ROUNDS = [
    {"round": 1, "verdict": "changes_requested", "body": "guard is decoration\nmore text",
     "mutations": [{"guard": "g_eq", "file": "chela/a.py", "before": "if x == 1:",
                    "after": "if x != 1:"},
                   {"guard": "g_eq2", "file": "chela/b.py", "before": "if x == 1:",
                    "after": "if x != 1:"}]},
    {"round": 2, "verdict": "retry", "body": "keep going"},
    {"round": 3, "verdict": "changes_requested", "body": "CI red on test_foo\ntrace",
     "mutations": []},
]


@pytest.fixture
def gh(monkeypatch):
    """Every PR close (url, body) and every launched prompt — nothing talks to GitHub/tmux."""
    seen = {"closed": [], "prompts": []}
    monkeypatch.setattr(dispatcher, "_gh_pr_close",
                        lambda url, repo_dir, body: seen["closed"].append((url, body))
                        or (True, "closed"))
    monkeypatch.setattr(dispatcher, "_post_pr_comment", lambda *a, **k: (True, "posted"))
    monkeypatch.setattr(dispatcher, "_launch_agent",
                        lambda wf, tid, win, wt, prompt, conn, **kw:
                        seen["prompts"].append(prompt))
    monkeypatch.setattr(dispatcher, "_run_critic", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "_read_pr_url", lambda *a, **k: None)
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    return seen


def _exhausted(**cols):
    _set("CMX-33", status="changes_requested", rework_count=2, review_reworks=2,
         rework_cause="review", **cols)


def test_survivor_rounds_groups_each_round_by_shape_and_falls_back_to_the_verdict_line():
    text = dispatcher._survivor_rounds(ROUNDS)
    assert text == ("**Round 1**\n- `compare` on `x==1` — g_eq (chela/a.py); g_eq2 (chela/b.py)"
                    "\n\n**Round 3**\n- CI red on test_foo")
    assert dispatcher._survivor_rounds([]) == "(no review round recorded a verdict)"


def test_first_exhaustion_requeues_with_the_addendum_and_the_second_is_needs_human(
        repo, team, gh):
    team.issues = {"CMX-33": issue(33, "In Review")}
    _cr._old_attempt(repo, status="changes_requested", review_history=json.dumps(ROUNDS))
    _exhausted()

    summary = dispatcher.tick(_cr._wf(repo))
    assert (summary["cap_requeued"], summary["escalated"]) == (1, 0)
    # The PR is closed ONCE, its comment carrying every round's survivors.
    [(url, body)] = gh["closed"]
    assert url == PR and "Rework cap reached" in body
    assert "`compare` on `x==1` — g_eq (chela/a.py)" in body and "CI red on test_foo" in body
    # ...and the fresh attempt is claimed on a NEW branch, its prompt carrying the addendum.
    row = _row("CMX-33")
    assert row["status"] == "running" and row["branch_name"] == "cmx-33-task-r2"
    assert row["cap_requeues"] == 1
    [prompt] = gh["prompts"]
    assert "## ♻️ Re-queued after the rework cap" in prompt
    assert "**Start fresh** from `dev`" in prompt
    assert "(`git diff origin/dev...origin/cmx-33-task`)" in prompt   # the OLD branch to read
    assert "g_eq2 (chela/b.py)" in prompt and "(2 review round(s))" in prompt

    # The fresh attempt exhausts too ⇒ only NOW needs_human, said to be the second.
    _exhausted(pr_url=PR2, pr_state="open")
    summary = dispatcher.tick(_cr._wf(repo))
    assert (summary["cap_requeued"], summary["escalated"]) == (0, 1)
    run = dispatcher.resolve_run("CMX-33")
    assert run["status"] == "needs_human"
    assert run["last_error"].startswith("second exhaustion: rework cap reached (2/2;")
    [notice] = [e for e in inbox.run_events([run], {})[0] if e["kind"] == "run_needs_human"]
    assert "second exhaustion: rework cap reached 2/2" in notice["summary"]
    assert len(gh["closed"]) == 1                       # the second PR is left open


def test_a_requeue_close_run_refuses_leaves_the_run_for_the_next_tick(repo, team, gh):
    team.issues = {"CMX-33": issue(33, "In Review")}
    _cr._old_attempt(repo, status="changes_requested", review_history=json.dumps(ROUNDS))
    _exhausted(pr_state="merged")                       # close_run refuses a merged PR

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='CMX-33'").fetchone()
        assert dispatcher._cap_requeue(conn, dispatcher.load_workflow(_cr._wf(repo)), row) == 0
    row = _row("CMX-33")
    assert (row["status"], row["cap_requeues"], row["requeue_addendum"]) == (
        "changes_requested", 0, None)
    assert gh["closed"] == []


def test_the_addendum_names_the_workflows_own_base_not_dev(repo, team, gh):
    """A base other than the fixture's `dev` (lean-alpha's `main`): a `{base}` hardcoded to
    `dev` in the addendum's start point or diff command reads the wrong branch."""
    team.issues = {"CMX-33": issue(33, "In Review")}
    _cr._old_attempt(repo, status="changes_requested", review_history=json.dumps(ROUNDS))
    _exhausted()
    wf = dispatcher.load_workflow(_cr._wf(repo))
    wf.config["workspace"]["base_branch"] = "main"

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='CMX-33'").fetchone()
        assert dispatcher._cap_requeue(conn, wf, row) == 1
    addendum = _row("CMX-33")["requeue_addendum"]
    assert "**Start fresh** from `main`" in addendum
    assert "(`git diff origin/main...origin/cmx-33-task`)" in addendum
    assert "origin/dev" not in addendum
