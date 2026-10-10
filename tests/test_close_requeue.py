"""🗂️🔁 CMX-65 — a closed run must never silently make its task unclaimable.

The incident: CMX-33's run was force-closed 21 s after its claim because the brief changed,
and its Linear issue was moved back to Todo expecting a fresh dispatch. `closed` is in
NOT_CLAIMABLE (CMX-265), so nothing happened for ~11 hours — no run, no signal — and the
two issues blocked on it stalled too.

These pin both halves of the fix:

* WITHOUT ``--requeue`` a close stays terminal (the CMX-265 intent), but ``chela close``
  says so out loud: the task will not be re-dispatched, and here is how to requeue it.
* ``chela close <run> --requeue`` closes the old PR, puts the issue back in Todo, and the
  next tick claims a FRESH attempt on a NEW branch and worktree — never the closed run's.
  A run whose PR merged is refused.

Real git (a bare origin + a clone) and real worktrees; the Linear transport is the
in-memory ``Team`` fake from ``test_linear_workflow_states``.
"""
from __future__ import annotations

import io
import subprocess
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import dispatcher
from tests import test_linear_workflow_states as _linear
from tests.test_linear_workflow_states import Team, _row, _seed, issue

# The real-git repo, the in-memory Linear team and the stubbed agent launch — shared with
# the CMX-23 suite, re-registered here as this module's own fixtures.
repo = _linear.repo
team = _linear.team
launched = _linear.launched

PR = "https://github.com/o/r/pull/33"


@pytest.fixture
def pr_closes(monkeypatch):
    """Every ``gh pr close`` a close/requeue asked for — no test talks to GitHub."""
    calls: list[str] = []

    def fake(pr_url, repo_dir, body):
        calls.append(pr_url)
        return True, "closed"

    monkeypatch.setattr(dispatcher, "_gh_pr_close", fake)
    monkeypatch.setattr(dispatcher, "_post_pr_comment", lambda *a, **k: (True, "posted"))
    return calls


def _wf(repo) -> Path:
    return repo / "WORKFLOW.md"


def _old_attempt(repo, status="awaiting_review", **cols):
    """CMX-33's first attempt, for real: its branch and worktree exist on disk at the path
    a first claim takes (`<root>/CMX-33`), and its PR is open."""
    wf = dispatcher.load_workflow(_wf(repo))
    root = dispatcher.resolve_workspace_root(wf)
    worktree, _ = dispatcher.ensure_worktree(repo, "CMX-33", "dev", "CMX", 33, root,
                                             branch="cmx-33-task")
    fields = {"branch_name": "cmx-33-task", "worktree_path": str(worktree),
              "task_number": 33, "pr_url": PR, "pr_state": "open"}
    fields.update(cols)
    _seed(repo, "CMX-33", status, **fields)
    return worktree


# --- 1. close WITHOUT requeue + the issue back in Todo ⇒ not claimed, and SAID so ---------

def test_a_closed_task_moved_back_to_todo_is_not_claimed(
        repo, team, launched, pr_closes):
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    assert dispatcher.close_run("CMX-33", "brief changed", force=True)["ok"]
    assert team.issues["CMX-33"]["state"]["name"] == "Canceled"

    # A human drags it back to Todo, expecting a fresh dispatch. It is NOT one.
    team.issues["CMX-33"]["state"] = {"name": "Todo", "type": "unstarted"}
    assert dispatcher.tick(_wf(repo))["dispatched"] == 0
    assert launched == []
    assert _row("CMX-33")["status"] == "closed"


def test_chela_close_says_the_task_will_not_be_redispatched_and_how_to_requeue(
        repo, team, pr_closes):
    from chela import main

    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    args = SimpleNamespace(run="CMX-33", reason="brief changed", force=True, close_pr=False,
                           keep_pr=True, remove_worktree=False, requeue=False)
    out = io.StringIO()
    with redirect_stdout(out):
        main.cmd_close(args)
    text = out.getvalue()
    assert "will NOT be re-dispatched" in text
    assert "chela close CMX-33 --requeue" in text


# --- 2. close --requeue ⇒ a fresh attempt, NEW branch + worktree, old PR closed ----------

def test_requeue_claims_a_fresh_attempt_on_a_new_branch_and_worktree(
        repo, team, launched, pr_closes):
    team.issues = {"CMX-33": issue(33, "In Review")}
    old_wt = _old_attempt(repo, tracker_edge="in_review")

    got = dispatcher.close_run("CMX-33", "brief changed", requeue=True, force=True)
    assert got["ok"] and got["requeued"], got
    assert pr_closes == [PR]                           # the old PR is closed, once
    assert team.issues["CMX-33"]["state"]["name"] == "Todo"
    row = _row("CMX-33")
    assert (row["status"], row["requeue_pending"], row["requeue_count"]) == ("closed", 1, 1)

    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    assert launched == ["CMX-33"]
    row = _row("CMX-33")
    assert row["status"] == "running"
    assert row["attempt"] == 1
    # A NEW branch and worktree — never the closed run's.
    assert row["branch_name"] == "cmx-33-task-r2"
    assert Path(row["worktree_path"]).name == "CMX-33-r2"
    assert Path(row["worktree_path"]).is_dir()
    assert Path(row["worktree_path"]) != old_wt
    assert old_wt.is_dir()                             # the closed run's worktree is kept
    # Nothing of the closed attempt leaks into the fresh one.
    assert row["pr_url"] is None and row["pr_state"] is None
    assert row["close_reason"] is None and row["requeue_pending"] == 0
    # The requeue is in the history, with the closed attempt's branch and PR.
    last = dispatcher.reviews_of(row)[-1]
    assert (last["verdict"], last["branch_name"], last["pr_url"]) == (
        "requeued", "cmx-33-task", PR)
    assert team.issues["CMX-33"]["state"]["name"] == "In Progress"
    assert pr_closes == [PR]                           # the old PR was never touched again


def test_requeue_on_a_run_whose_pr_merged_is_refused(repo, team, launched, pr_closes):
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo, pr_state="merged")
    got = dispatcher.close_run("CMX-33", "brief changed", requeue=True, force=True)
    assert got["ok"] is False
    assert "MERGED" in got["error"] and "requeue" in got["error"]
    row = _row("CMX-33")
    assert (row["status"], row["requeue_pending"] or 0) == ("awaiting_review", 0)
    assert pr_closes == []


def test_an_already_closed_run_can_be_requeued_once(repo, team, launched, pr_closes):
    """The CMX-33 repair path: it was closed WITHOUT --requeue (and canceled in Linear);
    requeueing it now resurrects the issue to Todo and the next tick claims it."""
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    assert dispatcher.close_run("CMX-33", "brief changed", force=True)["ok"]
    assert team.issues["CMX-33"]["state"]["name"] == "Canceled"

    got = dispatcher.close_run("CMX-33", "brief rewritten — run it again", requeue=True)
    assert got["ok"], got
    assert team.issues["CMX-33"]["state"]["name"] == "Todo"
    again = dispatcher.close_run("CMX-33", "twice", requeue=True)
    assert again["ok"] is False and "already requeued" in again["error"]

    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    assert _row("CMX-33")["branch_name"] == "cmx-33-task-r2"


def test_a_requeued_attempt_that_fails_to_spawn_retries_on_the_new_branch(
        repo, team, launched, pr_closes, monkeypatch):
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    assert dispatcher.close_run("CMX-33", "brief changed", requeue=True, force=True)["ok"]

    real = dispatcher.ensure_worktree
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("disk hiccup")
        return real(*a, **kw)

    monkeypatch.setattr(dispatcher, "ensure_worktree", flaky)
    dispatcher.tick(_wf(repo))
    row = _row("CMX-33")
    assert row["status"] == "failed"
    # The closed run's branch and PR are off the row: its retry cannot fall back onto them.
    assert row["branch_name"] is None and row["pr_url"] is None

    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    row = _row("CMX-33")
    assert row["branch_name"] == "cmx-33-task-r2"
    assert Path(row["worktree_path"]).name == "CMX-33-r2"


def test_without_requeue_close_stays_terminal(repo, team, launched, pr_closes):
    """NEGATIVE CONTROL for the requeue test: the same close without --requeue, the issue
    in Todo — no claim, no fresh worktree (CMX-265's intent, unchanged)."""
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    assert dispatcher.close_run("CMX-33", "brief changed", force=True)["ok"]
    team.issues["CMX-33"]["state"] = {"name": "Todo", "type": "unstarted"}
    for _ in range(2):
        assert dispatcher.tick(_wf(repo))["dispatched"] == 0
    assert launched == []
    assert not (Path(_row("CMX-33")["worktree_path"]).parent / "CMX-33-r2").exists()


def test_a_requeued_closed_run_is_not_terminal():
    assert dispatcher.run_is_terminal({"status": "closed"})
    assert not dispatcher.run_is_terminal({"status": "closed", "requeue_pending": 1})


def test_requeue_does_not_touch_linear_issues_that_are_done(tmp_path):
    from tests.test_linear_workflow_states import _src

    team_ = Team(issue(1, "Done"))
    assert _src(tmp_path, team_).requeue_task("CMX-1") == "skipped"
    assert team_.updates() == []


# --- 3. the guards the judge corrupted (rework round 1) ----------------------------------

def test_a_requeued_row_whose_pr_merged_out_of_band_never_gets_a_second_run(
        repo, team, launched, pr_closes):
    """The claim's own merged-PR guard on a requeued row: the old PR merged anyway after
    the requeue (someone merged it by hand on GitHub). The row is still `closed` +
    `requeue_pending`, so the claim must refuse it ITSELF — no spawn, no fresh worktree."""
    team.issues = {"CMX-33": issue(33, "In Review")}
    old_wt = _old_attempt(repo)
    assert dispatcher.close_run("CMX-33", "brief changed", requeue=True, force=True)["ok"]
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET pr_state='merged' WHERE task_id='CMX-33'")
        conn.commit()

    assert dispatcher.tick(_wf(repo))["dispatched"] == 0
    assert launched == []
    assert not (old_wt.parent / "CMX-33-r2").exists()
    assert _row("CMX-33")["branch_name"] == "cmx-33-task"   # never forked onto -r2


def test_a_requeued_task_is_claimed_even_when_its_issue_is_not_back_in_todo(
        repo, team, launched, pr_closes, monkeypatch):
    """The requeue itself is the claim signal, not the tracker state: the tracker
    projection failed (the issue stays In Review, where chela put it) and the next tick
    still claims the fresh attempt. Nobody has to drag the issue back by hand."""
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    monkeypatch.setattr(dispatcher, "_tracker_requeue", lambda run: None)
    assert dispatcher.close_run("CMX-33", "brief changed", requeue=True, force=True)["ok"]
    assert team.issues["CMX-33"]["state"]["name"] == "In Review"

    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    assert launched == ["CMX-33"]
    assert _row("CMX-33")["branch_name"] == "cmx-33-task-r2"


def _cli_close(**kw):
    from chela import main

    args = SimpleNamespace(run="CMX-33", reason="brief changed", force=True, close_pr=False,
                           keep_pr=False, remove_worktree=False, requeue=False)
    for k, v in kw.items():
        setattr(args, k, v)
    out = io.StringIO()
    with redirect_stdout(out):
        main.cmd_close(args)
    return out.getvalue()


def test_cli_requeue_with_keep_pr_is_refused_and_changes_nothing(
        repo, team, launched, pr_closes, monkeypatch, capsys):
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    called = []
    monkeypatch.setattr(dispatcher, "close_run", lambda *a, **k: called.append(k) or {})
    with pytest.raises(SystemExit) as e:
        _cli_close(requeue=True, keep_pr=True)
    assert e.value.code == 2
    assert "--keep-pr" in capsys.readouterr().err
    assert called == []


def test_cli_requeue_requeues_and_closes_the_old_pr(repo, team, launched, pr_closes):
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    text = _cli_close(requeue=True)
    assert "requeued" in text and "will NOT be re-dispatched" not in text
    row = _row("CMX-33")
    assert (row["status"], row["requeue_pending"]) == ("closed", 1)
    assert pr_closes == [PR]


# --- 4. the guards the judge corrupted (rework round 2) ----------------------------------

def test_a_second_requeue_takes_r3_never_the_r2_runs_kept_branch_or_worktree(
        repo, team, launched, pr_closes):
    """`requeue_count` must survive the requeued claim. If the claim reset it, the next
    `--requeue` would count from 0 again and the third attempt would land on `-r2` — the
    branch and the KEPT worktree of the attempt that was just closed."""
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    assert dispatcher.close_run("CMX-33", "brief changed", requeue=True, force=True)["ok"]
    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    row = _row("CMX-33")
    assert (row["branch_name"], row["requeue_count"]) == ("cmx-33-task-r2", 1)
    r2_wt = Path(row["worktree_path"])

    assert dispatcher.close_run("CMX-33", "brief changed again", requeue=True,
                                force=True)["ok"]
    assert _row("CMX-33")["requeue_count"] == 2
    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    row = _row("CMX-33")
    assert row["branch_name"] == "cmx-33-task-r3"
    assert Path(row["worktree_path"]).name == "CMX-33-r3"
    assert Path(row["worktree_path"]) != r2_wt and r2_wt.is_dir()
    assert launched == ["CMX-33", "CMX-33"]


MD_WORKFLOW = """---
project_key: CMX
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: {root}
  base_branch: dev
---
brief: {{{{task_body}}}}
"""


def test_a_requeued_markdown_task_gets_a_fresh_r2_branch_never_the_closed_runs(
        repo, launched, pr_closes):
    """A tracker with NO branch name of its own (markdown, gh_issues): the branch is
    `<key>-<N>`, so without the `-r<N>` arm a requeue would hand the fresh attempt the
    CLOSED run's `cmx-<N>` — every other requeue test runs on Linear's `task.branch`."""
    state = Path(dispatcher.CHELA_DIR)
    (repo / "WORKFLOW.md").write_text(MD_WORKFLOW.format(root=state / "worktrees"))
    (repo / "TODO.md").write_text("- [ ] window naming\n")
    wf = dispatcher.load_workflow(_wf(repo))
    task = next(t for t in dispatcher.get_source(wf).list_open_tasks()
                if t.title == "window naming")
    root = dispatcher.resolve_workspace_root(wf)
    old_wt, _ = dispatcher.ensure_worktree(repo, task.id, "dev", "CMX", 7, root,
                                           branch="cmx-7")
    _seed(repo, task.id, "awaiting_review", branch_name="cmx-7",
          worktree_path=str(old_wt), task_number=7, pr_url=PR, pr_state="open")

    assert dispatcher.close_run(task.id, "brief changed", requeue=True, force=True)["ok"]
    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    assert launched == [task.id]
    row = _row(task.id)
    assert row["branch_name"] == "cmx-7-r2"
    assert Path(row["worktree_path"]) != old_wt and old_wt.is_dir()


def test_ready_task_ids_is_none_when_the_tracker_read_failed():
    """"Could not read" is never "nothing is ready": an empty set here would read as "no
    task is waiting" on a tracker that was never actually read."""
    failed = SimpleNamespace(list_open_tasks=lambda: [], read_failed=True)
    assert dispatcher.ready_task_ids(failed) is None
    assert dispatcher.ready_task_ids(failed, open_tasks=[]) is None
    ok = SimpleNamespace(list_open_tasks=lambda: [], read_failed=False)
    assert dispatcher.ready_task_ids(ok) == set()                    # control


def test_ready_task_ids_reads_the_failure_the_listing_itself_just_set():
    """The listing is what sets ``read_failed`` (Linear: :meth:`list_open_tasks`). A check
    made BEFORE the read — or one that trusts the stale flag of an earlier, good read —
    would answer a set for a read that just failed."""
    class Flaky:
        read_failed = False                       # the previous read was fine

        def list_open_tasks(self):
            self.read_failed = True               # ...this one is not
            return []

    assert dispatcher.ready_task_ids(Flaky()) is None


# --- 5. the six guards the scope cut names (rework round 3) ------------------------------

def test_ready_task_ids_narrows_linear_to_its_ready_state(tmp_path):
    """A tracker WITH states draws from its READY state only (Linear: Todo). Backlog is a
    human parking it; In Progress / In Review are chela's own edges — none of them is
    "waiting for a claim". Without the narrowing every open issue would read as ready."""
    from tests.test_linear_workflow_states import _src

    team_ = Team(issue(1, "Todo"), issue(2, "Backlog"), issue(3, "In Review"), issue(4, "Todo"))
    src = _src(tmp_path, team_)
    assert dispatcher.ready_task_ids(src) == {"CMX-1", "CMX-4"}
    # The same tasks handed in already read (the dashboard's path) narrow the same way.
    src2 = _src(tmp_path, team_)
    assert dispatcher.ready_task_ids(src2, src2.list_open_tasks()) == {"CMX-1", "CMX-4"}
    # Control: the open set really holds the parked ones — the narrowing is what drops them.
    assert {t.id for t in src2.list_open_tasks()} == {"CMX-1", "CMX-2", "CMX-3", "CMX-4"}


def test_ready_task_ids_of_a_tracker_without_states_is_every_open_task():
    """Control for the narrowing: markdown / gh_issues have no ``claimable`` — every open
    task is ready."""
    tasks = [SimpleNamespace(id="a"), SimpleNamespace(id="b")]
    plain = SimpleNamespace(list_open_tasks=lambda: tasks, read_failed=False)
    assert dispatcher.ready_task_ids(plain) == {"a", "b"}


def test_a_requeued_claim_is_attempt_1_with_zeroed_rework_counters(
        repo, team, launched, pr_closes):
    """A FRESH attempt: the closed run was on attempt 2 with two rework rounds behind it.
    Inheriting either would start the requeued task one failure from the attempt cap, or
    already AT its rework cap — the "fresh" attempt would be dead on arrival."""
    team.issues = {"CMX-33": issue(33, "In Review")}
    wf = dispatcher.load_workflow(_wf(repo))
    root = dispatcher.resolve_workspace_root(wf)
    worktree, _ = dispatcher.ensure_worktree(repo, "CMX-33", "dev", "CMX", 33, root,
                                             branch="cmx-33-task")
    _seed(repo, "CMX-33", "changes_requested", attempt=2, branch_name="cmx-33-task",
          worktree_path=str(worktree), task_number=33, pr_url=PR, pr_state="open",
          rework_count=2)
    assert dispatcher.close_run("CMX-33", "brief changed", requeue=True, force=True)["ok"]
    assert (_row("CMX-33")["attempt"], _row("CMX-33")["rework_count"]) == (2, 2)

    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    row = _row("CMX-33")
    assert row["status"] == "running"
    assert row["attempt"] == 1
    assert (row["rework_count"] or 0) == 0
    assert not dispatcher.rework_cap_reached(row)


def test_a_requeue_is_consumed_by_its_claim_so_a_later_close_is_terminal_again(
        repo, team, launched, pr_closes):
    """``requeue_pending`` is spent by the claim it bought. If it survived, the next time
    the fresh attempt's row became ``closed`` by ANY path — here reconcile's closed-PR
    branch, which writes ``status='closed'`` and nothing else — it would be re-claimed
    as a requeue no human asked for."""
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    assert dispatcher.close_run("CMX-33", "brief changed", requeue=True, force=True)["ok"]
    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    row = _row("CMX-33")
    assert (row["requeue_pending"] or 0) == 0
    assert row["requeue_count"] == 1                  # the counter is kept; the flag is spent

    # The -r2 attempt's PR is closed on GitHub; reconcile marks the row closed — terminal.
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET status='closed' WHERE task_id='CMX-33'")
        conn.commit()
    assert dispatcher.run_is_terminal(dict(_row("CMX-33")))
    team.issues["CMX-33"]["state"] = {"name": "Todo", "type": "unstarted"}
    for _ in range(2):
        assert dispatcher.tick(_wf(repo))["dispatched"] == 0
    assert launched == ["CMX-33"]
    assert _row("CMX-33")["branch_name"] == "cmx-33-task-r2"


def test_a_requeued_attempt_that_dies_retries_on_its_own_r2_branch(
        repo, team, launched, pr_closes):
    """The requeued attempt's own failed retry is a RETRY, not a second requeue: it keeps
    the ``-r2`` branch and worktree it took (never ``-r3``, never the closed run's), the
    attempt counter advances, and ``requeue_count`` does not grow."""
    team.issues = {"CMX-33": issue(33, "In Review")}
    _old_attempt(repo)
    assert dispatcher.close_run("CMX-33", "brief changed", requeue=True, force=True)["ok"]
    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    r2_wt = _row("CMX-33")["worktree_path"]
    # The -r2 attempt pushed before it died — the realistic case, and the one that tells a
    # retry (keep `-r2`) from a fresh claim (which must skip a name origin already has).
    subprocess.run(["git", "-C", r2_wt, "push", "-q", "origin", "HEAD:cmx-33-task-r2"],
                   check=True, capture_output=True)
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET status='failed' WHERE task_id='CMX-33'")
        conn.commit()

    assert dispatcher.tick(_wf(repo))["dispatched"] == 1
    row = _row("CMX-33")
    assert (row["status"], row["attempt"]) == ("running", 2)
    assert row["branch_name"] == "cmx-33-task-r2"
    assert row["worktree_path"] == r2_wt
    assert row["requeue_count"] == 1
