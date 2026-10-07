"""🗂️📐 CMX-23 — chela mirrors and drives a Linear team's workflow states.

* The Work board's columns come from the team's states, in Linear's order.
* chela writes claim → In Progress, PR open → In Review, close → Canceled / Duplicate —
  each edge ONCE.
* A died run with retries left is re-claimed from In Progress / In Review, not only Todo;
  at MAX_ATTEMPTS it is not.

The GraphQL transport is an in-memory fake (`Team`) — no test talks to Linear.
"""
from __future__ import annotations

import copy
import subprocess
from types import SimpleNamespace

import pytest

from chela import config, dispatcher
from chela.sources import linear
from chela.sources.linear import LinearError, LinearSource

SECRET = "lin_api_CMX23fakeKEYvalue0123456789"

# Deliberately NOT in board order: `workflow_states` must sort them.
STATES = [
    ("st-done", "Done", "completed", 0),
    ("st-review", "In Review", "started", 2),
    ("st-dup", "Duplicate", "canceled", 1),
    ("st-todo", "Todo", "unstarted", 0),
    ("st-cancel", "Canceled", "canceled", 0),
    ("st-backlog", "Backlog", "backlog", 0),
    ("st-progress", "In Progress", "started", 1),
]
TYPE_OF = {name: kind for _, name, kind, _ in STATES}
ID_OF = {name: sid for sid, name, _, _ in STATES}


def issue(n, state="Todo"):
    return {
        "id": f"uuid-{n}", "identifier": f"CMX-{n}", "number": n, "title": f"task {n}",
        "description": f"do task {n}", "priority": 0, "sortOrder": float(n),
        "url": f"https://linear.app/acme/issue/CMX-{n}", "branchName": f"cmx-{n}-task",
        "archivedAt": None, "state": {"name": state, "type": TYPE_OF[state]},
        "labels": {"nodes": []}, "inverseRelations": {"nodes": []},
    }


class Team:
    """An in-memory Linear team: reads, `issueUpdate`, `issueRelationCreate`."""

    def __init__(self, *issues, states=STATES):
        self.issues = {i["identifier"]: i for i in issues}
        self.states = states
        self.calls: list[tuple[str, dict]] = []
        self.refuse_update = False

    def updates(self):
        return [(v["id"], v["stateId"]) for n, v in self.calls if n == "update"]

    def __call__(self, query, variables):
        name = {
            linear.OPEN_ISSUES_QUERY: "open", linear.ISSUES_BY_NUMBER_QUERY: "by_number",
            linear.TEAM_STATES_QUERY: "states", linear.SWEEP_QUERY: "sweep",
            linear.UPDATE_STATE_MUTATION: "update", linear.ARCHIVE_MUTATION: "archive",
            linear.CREATE_RELATION_MUTATION: "relation",
        }[query]
        self.calls.append((name, copy.deepcopy(variables)))
        page = {"pageInfo": {"hasNextPage": False, "endCursor": None}}
        if name in ("open", "sweep"):
            nodes = [i for i in self.issues.values()
                     if name == "sweep" or i["state"]["type"] not in ("completed", "canceled")]
            return {"issues": {"nodes": copy.deepcopy(nodes), **page}}
        if name == "by_number":
            nodes = [i for i in self.issues.values() if i["number"] in variables["numbers"]]
            return {"issues": {"nodes": copy.deepcopy(nodes), **page}}
        if name == "states":
            return {"teams": {"nodes": [{"id": "team", "states": {"nodes": [
                {"id": sid, "name": nm, "type": kind, "position": pos}
                for sid, nm, kind, pos in self.states]}}]}}
        if name == "update":
            if self.refuse_update:
                return {"issueUpdate": {"success": False}}
            target = next(i for i in self.issues.values() if i["id"] == variables["id"])
            state, kind = next((nm, k) for sid, nm, k, _ in self.states
                               if sid == variables["stateId"])
            target["state"] = {"name": state, "type": kind}
            return {"issueUpdate": {"success": True}}
        if name == "relation":
            return {"issueRelationCreate": {"success": True}}
        return {"issueArchive": {"success": True}}


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    for d in (linear._backoff, linear._last_sweep, linear._team_states):
        d.clear()
    linear._reported.clear()
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-state")
    yield
    for d in (linear._backoff, linear._last_sweep, linear._team_states):
        d.clear()


def _wf(tmp_path, **tracker):
    cfg = {"project_key": "CMX", "tracker": {"kind": "linear", "team": "CMX", **tracker}}

    def get(*keys, default=None):
        cur = cfg
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    return SimpleNamespace(path=tmp_path / "WORKFLOW.md", get=get, config=cfg,
                           project_key="CMX")


def _src(tmp_path, team, **tracker):
    return LinearSource(_wf(tmp_path, **tracker), transport=team)


# --- 1. the columns -------------------------------------------------------------------

def test_workflow_states_are_linears_board_order_with_canceled_types_hidden(tmp_path):
    cols = _src(tmp_path, Team()).workflow_states()
    assert [c["name"] for c in cols] == [
        "Backlog", "Todo", "In Progress", "In Review", "Done", "Canceled", "Duplicate"]
    assert [c["name"] for c in cols if c["hidden"]] == ["Canceled", "Duplicate"]
    # Negative control: no visible column is a chela run status.
    assert not {c["name"] for c in cols if not c["hidden"]} & {"failed", "Review", "running"}


def test_an_open_task_carries_its_linear_state_name(tmp_path):
    team = Team(issue(1, "In Review"), issue(2, "Backlog"))
    got = {t.id: t.tracker_state for t in _src(tmp_path, team).list_open_tasks()}
    assert got == {"CMX-1": "In Review", "CMX-2": "Backlog"}


def test_a_failed_state_read_gives_no_columns(tmp_path):
    def down(query, variables):
        raise LinearError("network", "down")

    assert _src(tmp_path, down).workflow_states() is None


# --- 2. the writes (unit) --------------------------------------------------------------

def test_a_transition_moves_the_issue_and_a_repeat_writes_nothing(tmp_path):
    team = Team(issue(1, "Todo"))
    src = _src(tmp_path, team)
    assert src.transition("CMX-1", "in_progress") == "set"
    assert team.updates() == [("uuid-1", "st-progress")]
    assert src.transition("CMX-1", "in_progress") == "already"
    assert team.updates() == [("uuid-1", "st-progress")]


@pytest.mark.parametrize("state", ["Done", "Canceled"])
def test_a_finished_issue_is_never_moved_back(tmp_path, state):
    team = Team(issue(1, state))
    assert _src(tmp_path, team).transition("CMX-1", "in_review") == "skipped"
    assert team.updates() == []


def test_a_refused_write_is_failed_and_never_raises(tmp_path):
    team = Team(issue(1, "Todo"))
    team.refuse_update = True
    assert _src(tmp_path, team).transition("CMX-1", "in_progress") == "failed"


def test_a_close_naming_the_superseding_issue_is_a_duplicate_with_the_relation(tmp_path):
    team = Team(issue(1, "In Review"), issue(9, "Todo"))
    src = _src(tmp_path, team)
    assert src.cancel_task("CMX-1", "superseded by CMX-9") == "set"
    assert team.issues["CMX-1"]["state"]["name"] == "Duplicate"
    rels = [v["input"] for n, v in team.calls if n == "relation"]
    assert rels == [{"issueId": "uuid-1", "relatedIssueId": "uuid-9", "type": "duplicate"}]


def test_a_close_that_merely_mentions_an_issue_is_canceled_without_a_relation(tmp_path):
    team = Team(issue(1, "In Progress"), issue(9, "Todo"))
    assert _src(tmp_path, team).cancel_task("CMX-1", "flaky idea, see CMX-9") == "set"
    assert team.issues["CMX-1"]["state"]["name"] == "Canceled"
    assert [n for n, _ in team.calls if n == "relation"] == []


# --- the dispatcher, end to end -------------------------------------------------------

WORKFLOW = """---
project_key: CMX
tracker:
  kind: linear
  team: CMX
workspace:
  root: {root}
  base_branch: dev
concurrency:
  max: 2
---
brief: {{{{task_body}}}}
"""


@pytest.fixture
def repo(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "DB_PATH", state / "scheduler.db")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True,
                   capture_output=True)
    work = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"),
                 ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(work), "config", k, v], check=True,
                       capture_output=True)
    (work / "WORKFLOW.md").write_text(WORKFLOW.format(root=state / "worktrees"))
    (work / "README").write_text("x\n")
    for cmd in (["add", "-A"], ["commit", "-qm", "seed"], ["push", "-qu", "origin", "dev"]):
        subprocess.run(["git", "-C", str(work), *cmd], check=True, capture_output=True)
    return work


@pytest.fixture
def team(monkeypatch):
    fake = Team()
    monkeypatch.setattr(linear, "load_api_key", lambda: SECRET)
    monkeypatch.setattr(linear, "make_transport", lambda key: fake)
    return fake


@pytest.fixture
def launched(monkeypatch):
    calls = []
    monkeypatch.setattr(dispatcher, "_launch_agent",
                        lambda wf, task_id, window, worktree, prompt, conn, **kw:
                        calls.append(task_id))
    monkeypatch.setattr(dispatcher, "_run_critic", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "_read_pr_url", lambda *a, **k: None)
    return calls


def _row(task_id):
    with dispatcher._db() as conn:
        r = conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()
        return dict(r) if r else None


def _seed(repo, task_id, status, attempt=1, **cols):
    names = ["task_id", "workflow_path", "title", "status", "attempt", "started_at", *cols]
    with dispatcher._db() as conn:
        conn.execute(
            f"INSERT INTO runs ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
            (task_id, str((repo / "WORKFLOW.md").resolve()), "t", status, attempt,
             dispatcher._now(), *cols.values()),
        )
        conn.commit()


def _set(task_id, **cols):
    with dispatcher._db() as conn:
        conn.execute(f"UPDATE runs SET {', '.join(f'{k}=?' for k in cols)} WHERE task_id=?",
                     (*cols.values(), task_id))
        conn.commit()


def test_each_edge_is_written_exactly_once(repo, team, launched):
    """claim → In Progress; PR open → In Review — each ONCE, however often it syncs."""
    team.issues = {"CMX-7": issue(7, "Todo")}
    wf = repo / "WORKFLOW.md"
    assert dispatcher.tick(wf)["dispatched"] == 1
    assert team.updates() == [("uuid-7", "st-progress")]
    assert _row("CMX-7")["tracker_edge"] == "in_progress"
    loaded = dispatcher.load_workflow(wf)
    with dispatcher._db() as conn:                       # same edge again: no write
        assert dispatcher._sync_tracker_states(conn, loaded, dispatcher.get_source(loaded)) == 0
    assert team.updates() == [("uuid-7", "st-progress")]

    # The PR opened: the run is parked in review. Two ticks — ONE write.
    _set("CMX-7", status="awaiting_review", pr_url="https://github.com/o/r/pull/1",
         pr_state="open")
    for _ in range(2):
        with dispatcher._db() as conn:
            dispatcher._sync_tracker_states(conn, loaded, dispatcher.get_source(loaded))
    assert team.updates() == [("uuid-7", "st-progress"), ("uuid-7", "st-review")]
    assert _row("CMX-7")["tracker_edge"] == "in_review"


def test_a_rework_bounces_in_progress_in_review_and_lands_done_one_write_per_edge(
        repo, team, launched, monkeypatch):
    """Liav's mapping (CMX-23, 2026-10-07): In Progress = the agent is working — the first
    attempt AND every rework round, though the PR stays open; In Review = the PR is out of
    the agent's hands. Every hop is driven by ``tick`` itself, idle ticks included, so the
    sync's call-site in the tick is under test too — and each edge is ONE write."""
    class _EveryWindowLives(set):           # the fake launch opens no tmux window
        def __contains__(self, _):
            return True
    monkeypatch.setattr(dispatcher, "_tmux_windows", _EveryWindowLives)
    team.issues = {"CMX-7": issue(7, "Todo")}
    wf = repo / "WORKFLOW.md"

    def tick(times=1):                     # >1 = idle ticks: they must write nothing
        for _ in range(times):
            summary = dispatcher.tick(wf)
        return summary

    assert tick()["dispatched"] == 1                                   # claim
    tick()                                                             # idle tick
    pr = {"pr_url": "https://github.com/o/r/pull/1", "pr_state": "open"}
    _set("CMX-7", status="awaiting_review", **pr)                           # judging
    tick(2)
    _set("CMX-7", status="changes_requested")                               # sent back
    tick()
    # Sent back is already the agent's again — before the rework agent even spawns.
    assert team.issues["CMX-7"]["state"]["name"] == "In Progress"
    _set("CMX-7", status="running", rework_count=1)                         # rework agent
    tick()
    _set("CMX-7", status="awaiting_review")                                 # judged again
    tick(2)
    _set("CMX-7", status="done", pr_state="merged", ended_at=dispatcher._now())  # merged
    tick(2)

    assert [s for _, s in team.updates()] == [
        "st-progress", "st-review", "st-progress", "st-review", "st-done"]
    assert team.issues["CMX-7"]["state"]["name"] == "Done"


def test_the_tick_drives_the_tracker_even_when_dispatch_is_held(repo, team, launched):
    """A PR opened since the last tick → In Review on the NEXT tick, before the dispatch
    gates: a held queue (nothing is claimed, so the post-claim sync never runs) still keeps
    the board honest."""
    from chela import hold
    team.issues = {"CMX-7": issue(7, "In Progress")}
    _seed(repo, "CMX-7", "awaiting_review", pr_url="https://github.com/o/r/pull/1",
          pr_state="open", tracker_edge="in_progress")
    hold.take("rewriting the queue", by="test")
    summary = dispatcher.tick(repo / "WORKFLOW.md")
    assert summary.get("held") and summary["dispatched"] == 0, summary
    assert summary["tracker_transitions"] == 1
    assert team.updates() == [("uuid-7", "st-review")]
    assert _row("CMX-7")["tracker_edge"] == "in_review"


def test_an_edge_already_set_by_the_github_integration_is_not_written_again(repo, team):
    """Linear's GitHub integration moved the issue to In Review first — chela records the
    edge and writes nothing (it does not fight the integration)."""
    team.issues = {"CMX-7": issue(7, "In Review")}
    _seed(repo, "CMX-7", "awaiting_review", pr_url="https://github.com/o/r/pull/1")
    wf = dispatcher.load_workflow(repo / "WORKFLOW.md")
    with dispatcher._db() as conn:
        assert dispatcher._sync_tracker_states(conn, wf, dispatcher.get_source(wf)) == 1
    assert team.updates() == []
    assert _row("CMX-7")["tracker_edge"] == "in_review"


def test_a_failed_write_is_retried_and_never_blocks_the_claim(repo, team, launched):
    team.issues = {"CMX-7": issue(7, "Todo")}
    team.refuse_update = True
    wf = repo / "WORKFLOW.md"
    assert dispatcher.tick(wf)["dispatched"] == 1         # the claim went ahead
    assert _row("CMX-7")["tracker_edge"] is None          # not recorded ⇒ retried
    team.refuse_update = False
    loaded = dispatcher.load_workflow(wf)
    with dispatcher._db() as conn:
        assert dispatcher._sync_tracker_states(conn, loaded, dispatcher.get_source(loaded)) == 1
    assert _row("CMX-7")["tracker_edge"] == "in_progress"


# --- 3. a died run is re-claimed whatever the Linear state ------------------------------

@pytest.mark.parametrize("state", ["In Review", "In Progress"])
def test_a_died_run_in_a_started_state_is_reclaimed(repo, team, launched, state):
    team.issues = {"CMX-19": issue(19, state)}
    # It died after its PR opened: chela had already written In Review for it.
    _seed(repo, "CMX-19", "failed", attempt=1, branch_name="cmx-19-task",
          tracker_edge="in_review")
    summary = dispatcher.tick(repo / "WORKFLOW.md")
    assert summary["dispatched"] == 1, summary
    row = _row("CMX-19")
    assert (row["status"], row["attempt"]) == ("running", 2)
    assert launched == ["CMX-19"]
    # Re-claimed ⇒ In Progress again.
    assert team.issues["CMX-19"]["state"]["name"] == "In Progress"


def test_a_died_run_at_max_attempts_is_not_reclaimed(repo, team, launched):
    team.issues = {"CMX-19": issue(19, "In Review")}
    _seed(repo, "CMX-19", "failed", attempt=dispatcher.MAX_ATTEMPTS, branch_name="cmx-19-task")
    summary = dispatcher.tick(repo / "WORKFLOW.md")
    assert summary["dispatched"] == 0
    assert launched == []
    assert _row("CMX-19")["status"] == "failed"


def test_an_in_review_issue_with_no_died_run_is_not_claimed(repo, team, launched):
    """Negative control: In Review alone is not claimable — only a DIED run's retry is."""
    team.issues = {"CMX-20": issue(20, "In Review")}
    assert dispatcher.tick(repo / "WORKFLOW.md")["dispatched"] == 0
    assert launched == []


def test_a_died_run_parked_in_backlog_is_not_reclaimed(repo, team, launched):
    """A human moving the issue to Backlog is a decision, not a leftover."""
    team.issues = {"CMX-21": issue(21, "Backlog")}
    _seed(repo, "CMX-21", "failed", attempt=1)
    assert dispatcher.tick(repo / "WORKFLOW.md")["dispatched"] == 0


def test_chela_close_marks_a_superseded_issue_duplicate(repo, team):
    team.issues = {"CMX-7": issue(7, "In Progress"), "CMX-9": issue(9, "Todo")}
    _seed(repo, "CMX-7", "failed")
    got = dispatcher.close_run("CMX-7", "superseded by CMX-9")
    assert got["ok"] and got["tracker"] == "set"
    assert team.issues["CMX-7"]["state"]["name"] == "Duplicate"
    assert _row("CMX-7")["tracker_edge"] == "canceled"


# --- the dashboard payload ------------------------------------------------------------

def test_the_work_api_carries_the_columns_and_each_cards_linear_state(tmp_path, monkeypatch):
    from chela.dashboard import app as dash

    team = Team(issue(1, "Todo"), issue(2, "In Review"))
    monkeypatch.setattr(linear, "load_api_key", lambda: SECRET)
    monkeypatch.setattr(linear, "make_transport", lambda key: team)
    repo = tmp_path / "repo"
    repo.mkdir()
    wf = repo / "WORKFLOW.md"
    wf.write_text("---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\n---\nDo it.\n")
    wf = wf.resolve()
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [wf])
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [{
        "task_id": "CMX-2", "workflow_path": str(wf), "status": "failed", "title": "t",
        "attempt": 1}])
    resp = dash.app.test_client().get("/api/dispatcher",
                                      headers={"Sec-Fetch-Site": "same-origin"})
    (entry,) = resp.get_json()["workflows"]
    assert [c["name"] for c in entry["tracker_columns"] if not c["hidden"]] == [
        "Backlog", "Todo", "In Progress", "In Review", "Done"]
    assert {t["id"]: t["tracker_state"] for t in entry["open_tasks"]} == {"CMX-1": "Todo"}
    (died,) = entry["recent_runs"]
    assert (died["task_id"], died["tracker_state"]) == ("CMX-2", "In Review")


# --- rework 2: guards that assert each invariant itself ---------------------------------

def _in(n, name, kind):
    """An issue in a state of a custom team (one `issue()`'s STATES does not have)."""
    node = issue(n)
    node["state"] = {"name": name, "type": kind}
    return node


def test_a_team_without_the_default_names_falls_back_to_the_state_types(tmp_path):
    """In Progress = the FIRST `started` state, In Review = the LAST — by position."""
    states = [("s-todo", "Todo", "unstarted", 0), ("s-rev", "Reviewing", "started", 7),
              ("s-doing", "Doing", "started", 3), ("s-kill", "Killed", "canceled", 0)]
    team = Team(_in(1, "Todo", "unstarted"), states=states)
    src = _src(tmp_path, team)
    assert src.transition("CMX-1", "in_progress") == "set"
    assert src.transition("CMX-1", "in_review") == "set"
    assert src.transition("CMX-1", "canceled") == "set"
    assert [s for _, s in team.updates()] == ["s-doing", "s-rev", "s-kill"]


def test_a_team_with_one_started_state_has_no_in_review_to_write(tmp_path):
    """Negative control for the fallback: one `started` state is In Progress, never also
    In Review — the edge is skipped, not written onto the same column."""
    states = [("s-todo", "Todo", "unstarted", 0), ("s-doing", "Doing", "started", 0)]
    team = Team(_in(1, "Todo", "unstarted"), states=states)
    assert _src(tmp_path, team).transition("CMX-1", "in_review") == "skipped"
    assert team.updates() == []


def test_a_configured_state_name_wins_over_the_default(tmp_path):
    states = [*STATES, ("st-code", "Code Review", "started", 5)]
    team = Team(issue(1, "In Progress"), states=states)
    src = _src(tmp_path, team, states={"in_review": "Code Review"})
    assert src.transition("CMX-1", "in_review") == "set"
    assert team.updates() == [("uuid-1", "st-code")]
    # Control: an edge not configured keeps its default name.
    assert src.transition("CMX-1", "in_progress") == "set"
    assert team.updates()[-1] == ("uuid-1", "st-progress")


def test_an_unknown_edge_is_skipped_without_touching_linear(tmp_path):
    team = Team(issue(1, "Todo"))
    assert _src(tmp_path, team).transition("CMX-1", "in_limbo") == "skipped"
    assert team.calls == []


def test_an_archived_issue_is_never_moved(tmp_path):
    node = issue(1, "In Progress")
    node["archivedAt"] = "2026-10-01T00:00:00Z"
    team = Team(node)
    assert _src(tmp_path, team).transition("CMX-1", "in_review") == "skipped"
    assert team.updates() == []
    # Control: the same issue unarchived does move.
    node["archivedAt"] = None
    assert _src(tmp_path, Team(node)).transition("CMX-1", "in_review") == "set"


def test_the_team_states_are_read_once_per_ttl_across_sources(tmp_path):
    """The dashboard builds a fresh source on every poll — the TTL cache is per TEAM."""
    team = Team()
    _src(tmp_path, team).workflow_states()
    assert _src(tmp_path, team).workflow_states()[0]["name"] == "Backlog"
    assert [n for n, _ in team.calls].count("states") == 1
    # Control: once the cache is stale it is read again.
    stamp, states = linear._team_states["CMX"]
    linear._team_states["CMX"] = (stamp - linear.TEAM_STATES_TTL_SECONDS - 1, states)
    _src(tmp_path, team).workflow_states()
    assert [n for n, _ in team.calls].count("states") == 2


@pytest.mark.parametrize("reason", [
    "superseded by CMX-9", "duplicate of CMX-9", "dup of CMX-9", "replaced by CMX-9",
    "subsumed into CMX-9", "folded into CMX-9",
])
def test_every_supersede_wording_is_a_duplicate(tmp_path, reason):
    team = Team(issue(1, "In Review"), issue(9, "Todo"))
    assert _src(tmp_path, team).cancel_task("CMX-1", reason) == "set"
    assert team.issues["CMX-1"]["state"]["name"] == "Duplicate"


def test_a_reason_naming_the_closed_issue_itself_first_still_finds_the_superseding_one(tmp_path):
    team = Team(issue(1, "In Review"), issue(9, "Todo"))
    assert _src(tmp_path, team).cancel_task("CMX-1", "CMX-1 is superseded by CMX-9") == "set"
    assert team.issues["CMX-1"]["state"]["name"] == "Duplicate"
    rels = [v["input"]["relatedIssueId"] for n, v in team.calls if n == "relation"]
    assert rels == ["uuid-9"]                  # never a relation to itself


def test_needs_human_is_in_review(repo, team):
    """needs_human / override pending: the PR is out of the agent's hands → In Review."""
    team.issues = {"CMX-7": issue(7, "In Progress")}
    _seed(repo, "CMX-7", "needs_human", pr_url="https://github.com/o/r/pull/1",
          tracker_edge="in_progress")
    wf = dispatcher.load_workflow(repo / "WORKFLOW.md")
    with dispatcher._db() as conn:
        assert dispatcher._sync_tracker_states(conn, wf, dispatcher.get_source(wf)) == 1
    assert team.updates() == [("uuid-7", "st-review")]


def test_a_skipped_edge_is_recorded_so_it_is_not_retried_every_tick(repo, team):
    """A human closed the issue in Linear while the run is still alive: chela never
    resurrects it, and records the edge so the next tick does not re-read it."""
    team.issues = {"CMX-7": issue(7, "Done")}
    _seed(repo, "CMX-7", "running")
    wf = dispatcher.load_workflow(repo / "WORKFLOW.md")
    for _ in range(2):
        with dispatcher._db() as conn:
            dispatcher._sync_tracker_states(conn, wf, dispatcher.get_source(wf))
    assert team.updates() == []
    assert _row("CMX-7")["tracker_edge"] == "in_progress"
    assert [n for n, _ in team.calls].count("by_number") == 1


def test_a_recorded_edge_is_committed_by_the_sync_itself(repo, team):
    """The sync commits what it recorded: a tick that fails AFTER it must not roll the
    edges back (each would then be written to Linear a second time)."""
    team.issues = {"CMX-7": issue(7, "Todo")}
    _seed(repo, "CMX-7", "running")
    wf = dispatcher.load_workflow(repo / "WORKFLOW.md")
    with dispatcher._db() as conn:
        assert dispatcher._sync_tracker_states(conn, wf, dispatcher.get_source(wf)) == 1
        conn.rollback()                         # what a later failure in the tick does
    assert _row("CMX-7")["tracker_edge"] == "in_progress"


def test_one_transition_that_raises_never_stops_the_others(repo, monkeypatch):
    calls = []

    def transition(task_id, edge):
        calls.append(task_id)
        if task_id == "CMX-1":
            raise RuntimeError("boom")
        return "set"

    _seed(repo, "CMX-1", "running")
    _seed(repo, "CMX-2", "running")
    wf = dispatcher.load_workflow(repo / "WORKFLOW.md")
    with dispatcher._db() as conn:
        got = dispatcher._sync_tracker_states(conn, wf, SimpleNamespace(transition=transition))
    assert got == 1 and sorted(calls) == ["CMX-1", "CMX-2"]
    assert (_row("CMX-1")["tracker_edge"], _row("CMX-2")["tracker_edge"]) == (None, "in_progress")


def test_a_reclaimed_run_is_moved_to_in_progress_again(repo, team, launched):
    """A human moved a died run's issue back to Todo; chela had recorded In Progress for
    its LAST attempt. The re-claim forgets that edge, so the new attempt writes it again."""
    team.issues = {"CMX-19": issue(19, "Todo")}
    _seed(repo, "CMX-19", "failed", attempt=1, branch_name="cmx-19-task",
          tracker_edge="in_progress")
    assert dispatcher.tick(repo / "WORKFLOW.md")["dispatched"] == 1
    assert team.issues["CMX-19"]["state"]["name"] == "In Progress"
    assert team.updates() == [("uuid-19", "st-progress")]


def test_a_died_run_whose_pr_merged_is_not_retried(repo, team, launched):
    """Its work landed — a stale In Review issue is the merge's to finish, not a retry."""
    team.issues = {"CMX-19": issue(19, "In Review")}
    _seed(repo, "CMX-19", "failed", attempt=1, branch_name="cmx-19-task",
          pr_url="https://github.com/o/r/pull/1", pr_state="merged")
    assert dispatcher.tick(repo / "WORKFLOW.md")["dispatched"] == 0
    assert launched == []


def test_a_died_run_in_triage_is_not_reclaimed(repo, team, launched):
    """Only a `started`-type state is chela's own leftover; any other non-ready state
    (Triage here) is a human's decision."""
    team.states = [*STATES, ("st-triage", "Triage", "triage", 0)]
    team.issues = {"CMX-21": _in(21, "Triage", "triage")}
    _seed(repo, "CMX-21", "failed", attempt=1)
    assert dispatcher.tick(repo / "WORKFLOW.md")["dispatched"] == 0
    assert launched == []


def test_a_misconfigured_source_has_no_columns_and_logs_nothing_per_poll(tmp_path, caplog):
    """The dashboard asks on every poll: a config error (already reported where the
    workflow is checked) must not turn into a warning per poll."""
    team = Team()
    src = _src(tmp_path, team, api_key="lin_api_leaked")
    assert src.config_error
    with caplog.at_level("WARNING", logger=linear.log.name):
        assert src.workflow_states() is None
    assert caplog.records == [] and team.calls == []


def test_a_team_without_a_duplicate_state_closes_a_superseded_issue_into_its_canceled_type(
        tmp_path):
    states = [("s-todo", "Todo", "unstarted", 0), ("s-doing", "Doing", "started", 0),
              ("s-kill", "Killed", "canceled", 0)]
    team = Team(_in(1, "Doing", "started"), _in(9, "Todo", "unstarted"), states=states)
    assert _src(tmp_path, team).cancel_task("CMX-1", "superseded by CMX-9") == "set"
    assert team.updates() == [("uuid-1", "s-kill")]


def test_a_failed_close_write_does_not_record_the_canceled_edge(repo, team):
    team.issues = {"CMX-7": issue(7, "In Progress")}
    team.refuse_update = True
    _seed(repo, "CMX-7", "failed", tracker_edge="in_progress")
    got = dispatcher.close_run("CMX-7", "not needed")
    assert got["ok"] and got["tracker"] == "failed"
    assert _row("CMX-7")["tracker_edge"] == "in_progress"
