"""🗂️ CMX-326 — a Linear tracker scoped to one PROJECT of a team (`tracker.project`).

Several repos can share one Linear team: each workflow sets its own `project:`. Every test
stubs the GraphQL transport (`FakeTeam`) — no test talks to Linear. The fake applies the
team / project / number / state filters FROM THE QUERY TEXT, so a query that drops its
project filter gets the whole team back here, exactly as it would from Linear.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, dispatcher, runtime_truth
from chela.sources import linear
from chela.sources.linear import LinearError, LinearSource
from chela.workflow import load_workflow

SECRET = "lin_api_PROJECTscopeSECRET0123456789"

_TYPES = {"Backlog": "backlog", "Todo": "unstarted", "In Progress": "started",
          "In Review": "started", "Done": "completed", "Canceled": "canceled"}
PROJECTS = [{"id": "proj-chela", "name": "Chelamux", "slugId": "a1b2c3"},
            {"id": "proj-tap", "name": "Tapgrid", "slugId": "d4e5f6"}]


def issue(n, project, *, state="Todo", team="CMX", finished_at=None, blockers=()):
    kind = _TYPES[state]
    return {
        "id": f"uuid-{team}-{n}", "identifier": f"{team}-{n}", "number": n,
        "title": f"task {n}", "description": f"brief {n}", "priority": 0,
        "sortOrder": float(n), "url": f"https://linear.app/x/issue/{team}-{n}",
        "branchName": f"{team.lower()}-{n}-task", "archivedAt": None,
        "state": {"name": state, "type": kind},
        "completedAt": finished_at if kind == "completed" else None,
        "canceledAt": finished_at if kind == "canceled" else None,
        "labels": {"nodes": []}, "inverseRelations": {"nodes": list(blockers)},
        # Not a field chela asks for — the fake's own record of where the issue lives.
        "project": {"id": project} if project else None,
    }


def blocks(ident):
    return {"type": "blocks", "issue": {"identifier": ident, "archivedAt": None,
                                        "state": {"type": "unstarted"}}}


class FakeTeam:
    def __init__(self, issues=(), projects=PROJECTS):
        self.issues = {i["identifier"]: i for i in issues}
        self.projects = copy.deepcopy(list(projects))
        self.projects_reply: dict | None = None
        self.fail_projects: LinearError | None = None
        self.calls: list[tuple[str, dict]] = []
        self.next_number = 100

    def __call__(self, query, variables):
        name = {
            linear.OPEN_ISSUES_QUERY: "open", linear.OPEN_PROJECT_ISSUES_QUERY: "open",
            linear.ISSUES_BY_NUMBER_QUERY: "by_number",
            linear.PROJECT_ISSUES_BY_NUMBER_QUERY: "by_number",
            linear.TEAM_STATES_QUERY: "states", linear.TEAM_PROJECTS_QUERY: "projects",
            linear.SWEEP_QUERY: "sweep", linear.UPDATE_STATE_MUTATION: "update",
            linear.ARCHIVE_MUTATION: "archive", linear.CREATE_ISSUE_MUTATION: "create",
            linear.CREATE_RELATION_MUTATION: "relation",
        }[query]
        self.calls.append((name, json.loads(json.dumps(variables))))
        if name == "projects":
            if self.fail_projects is not None:
                raise self.fail_projects
            if self.projects_reply is not None:
                return self.projects_reply
            return {"teams": {"nodes": [{"projects": {"nodes": copy.deepcopy(self.projects)}}]}}
        if name == "states":
            return {"teams": {"nodes": [{"id": "team-uuid", "states": {"nodes": [
                {"id": f"st-{k}", "name": k, "type": t, "position": p}
                for p, (k, t) in enumerate(_TYPES.items())]}}]}}
        if name in ("open", "by_number", "sweep"):
            nodes = [i for i in self.issues.values() if _matches(query, i, variables)]
            return {"issues": {"nodes": copy.deepcopy(nodes),
                               "pageInfo": {"hasNextPage": False, "endCursor": None}}}
        if name == "create":
            inp = variables["input"]
            n = self.next_number
            self.next_number += 1
            node = issue(n, inp.get("projectId"))
            self.issues[node["identifier"]] = node
            return {"issueCreate": {"success": True, "issue": {
                "id": node["id"], "identifier": node["identifier"], "title": inp["title"],
                "url": node["url"]}}}
        if name == "relation":
            return {"issueRelationCreate": {"success": True}}
        target = next(i for i in self.issues.values() if i["id"] == variables["id"])
        if name == "update":
            sid = variables["stateId"][len("st-"):]
            target["state"] = {"name": sid, "type": _TYPES[sid]}
            return {"issueUpdate": {"success": True}}
        target["archivedAt"] = "2026-10-20T00:00:00Z"
        return {"issueArchive": {"success": True}}

    def names(self):
        return [n for n, _ in self.calls]


def _matches(query: str, node: dict, variables: dict) -> bool:
    if node["identifier"].split("-")[0] != variables.get("team"):
        return False
    proj = re.search(r"project:\s*\{\s*id:\s*\{\s*eq:\s*\$project\s*\}", query)
    if proj and (node["project"] or {}).get("id") != variables["project"]:
        return False
    if re.search(r"number:\s*\{\s*in:\s*\$numbers", query) and \
            node["number"] not in variables["numbers"]:
        return False
    archived = re.search(r"includeArchived:\s*(true|false)", query)
    if node["archivedAt"] and not (archived and archived.group(1) == "true"):
        return False
    nin = re.search(r"state:\s*\{\s*type:\s*\{\s*nin:\s*\[([^\]]*)\]", query)
    if nin and node["state"]["type"] in re.findall(r'"(\w+)"', nin.group(1)):
        return False
    return True


WORKFLOW = """---
project_key: {key}
tracker:
  kind: {kind}
  team: {team}
{extra}workspace:
  root: {root}
  base_branch: dev
concurrency:
  max: 5
---
brief: {{{{task_body}}}}
"""


def _write_wf(path: Path, *, key="CMX", kind="linear", team="CMX", project=None,
              keep_done=None, root=None) -> Path:
    extra = ""
    if project is not None:
        extra += f"  project: {project}\n"
    if keep_done is not None:
        extra += f"  keep_done: {keep_done}\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(WORKFLOW.format(key=key, kind=kind, team=team, extra=extra,
                                    root=root or path.parent / "worktrees"))
    return path


@pytest.fixture(autouse=True)
def _fresh(tmp_path, monkeypatch):
    for state in (linear._backoff, linear._last_sweep, linear._reported, linear._team_states):
        state.clear()
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-state")
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [])
    yield
    for state in (linear._backoff, linear._last_sweep, linear._reported, linear._team_states):
        state.clear()


def _src(tmp_path, fake, name="a", **kw) -> LinearSource:
    wf = load_workflow(_write_wf(tmp_path / name / "WORKFLOW.md", **kw))
    return LinearSource(wf, transport=fake)


def _team():
    return FakeTeam([issue(1, "proj-chela"), issue(2, "proj-tap"), issue(3, None),
                     issue(4, "proj-chela", state="In Progress"),
                     issue(5, "proj-tap", state="Backlog"), issue(1, "proj-chela", team="ENG")])


# --- reads and writes are scoped to the project ---------------------------------------

def test_a_project_scoped_workflow_lists_only_its_projects_issues(tmp_path):
    fake = _team()
    src = _src(tmp_path, fake, project="Chelamux")
    assert {t.id for t in src.list_open_tasks()} == {"CMX-1", "CMX-4"}
    assert src.read_failed is False
    reads = [v for n, v in fake.calls if n == "open"]
    assert reads and all(v["project"] == "proj-chela" for v in reads)


def test_without_project_the_open_set_is_the_whole_team_as_before(tmp_path):
    """The negative control: no `project:` ⇒ the team query, no project variable, and no
    projects lookup — behaviour unchanged."""
    fake = _team()
    src = _src(tmp_path, fake)
    assert {t.id for t in src.list_open_tasks()} == {"CMX-1", "CMX-2", "CMX-3", "CMX-4",
                                                     "CMX-5"}
    assert "projects" not in fake.names()
    assert all("project" not in v for _, v in fake.calls)


@pytest.mark.parametrize("project", ["Chelamux", "chelamux", " CHELAMUX ", "a1b2c3",
                                     "A1B2C3", "proj-chela"])
def test_the_project_is_named_by_name_slug_or_id(tmp_path, project):
    fake = _team()
    src = _src(tmp_path, fake, project=f'"{project}"')
    assert {t.id for t in src.list_open_tasks()} == {"CMX-1", "CMX-4"}


def test_an_id_match_wins_over_another_projects_name(tmp_path):
    """A project NAMED like another project's id must not capture it: id, then slug, then
    name."""
    fake = _team()
    fake.projects.append({"id": "proj-x", "name": "proj-chela", "slugId": "zz"})
    src = _src(tmp_path, fake, project="proj-chela")
    assert {t.id for t in src.list_open_tasks()} == {"CMX-1", "CMX-4"}


def test_a_slug_match_wins_over_another_projects_name(tmp_path):
    fake = _team()
    fake.projects.append({"id": "proj-x", "name": "d4e5f6", "slugId": "zz"})
    src = _src(tmp_path, fake, project="d4e5f6")
    assert {t.id for t in src.list_open_tasks()} == {"CMX-2", "CMX-5"}


def test_fetch_by_ids_never_returns_another_projects_issue(tmp_path):
    fake = _team()
    got = _src(tmp_path, fake, project="Tapgrid").fetch_by_ids(["CMX-1", "CMX-2", "CMX-3"])
    assert [t.id for t in got] == ["CMX-2"]
    reads = [v for n, v in fake.calls if n == "by_number"]
    assert reads and all(v["project"] == "proj-tap" for v in reads)


def test_a_write_never_touches_another_projects_issue(tmp_path):
    fake = _team()
    src = _src(tmp_path, fake, project="Tapgrid")
    assert src.close_tasks(["CMX-1"]) == {"CMX-1": "missing"}
    assert src.transition("CMX-1", "in_progress") == "missing"
    assert src.requeue_task("CMX-4") == "missing"
    assert "update" not in fake.names()
    assert fake.issues["CMX-1"]["state"]["name"] == "Todo"


def test_a_write_reaches_its_own_projects_issue(tmp_path):
    fake = _team()
    src = _src(tmp_path, fake, project="Tapgrid")
    assert src.transition("CMX-2", "in_progress") == "set"
    assert fake.issues["CMX-2"]["state"]["name"] == "In Progress"


def test_a_created_issue_is_put_in_the_project(tmp_path):
    fake = _team()
    created = _src(tmp_path, fake, project="Tapgrid").create_issue("new", "body")
    (inp,) = [v["input"] for n, v in fake.calls if n == "create"]
    assert inp["projectId"] == "proj-tap"
    assert fake.issues[created["identifier"]]["project"] == {"id": "proj-tap"}


def test_an_unscoped_create_names_no_project(tmp_path):
    fake = _team()
    _src(tmp_path, fake).create_issue("new", "body")
    (inp,) = [v["input"] for n, v in fake.calls if n == "create"]
    assert "projectId" not in inp


def test_a_size_gate_slice_stays_in_the_project(tmp_path):
    """create_issue with a parent — what the size gate's split calls."""
    fake = _team()
    _src(tmp_path, fake, project="Tapgrid").create_issue("slice", "b", parent="CMX-2")
    (inp,) = [v["input"] for n, v in fake.calls if n == "create"]
    assert (inp["projectId"], inp["parentId"]) == ("proj-tap", "uuid-CMX-2")


def test_a_blocker_from_another_project_is_refused_and_nothing_is_created(tmp_path):
    fake = _team()
    with pytest.raises(LinearError) as e:
        _src(tmp_path, fake, project="Tapgrid").create_issue("new", blocked_by=["CMX-1"])
    assert e.value.kind == "input" and "CMX-1" in str(e.value)
    assert "create" not in fake.names()


def test_claimable_only_offers_the_projects_issues(tmp_path):
    fake = FakeTeam([issue(1, "proj-chela"), issue(2, "proj-tap", blockers=[blocks("CMX-1")]),
                     issue(3, "proj-tap")])
    src = _src(tmp_path, fake, project="Tapgrid")
    candidates, _ = src.claimable(src.list_open_tasks())
    assert [t.id for t in candidates] == ["CMX-2", "CMX-3"]


# --- a project that cannot be resolved fails CLOSED -----------------------------------

PROJECT_FAILURES = {
    "unknown-name": lambda f: None,
    "ambiguous-name": lambda f: f.projects.append(
        {"id": "proj-dup", "name": "TAPGRID", "slugId": "q"}),
    "read-fails": lambda f: setattr(f, "fail_projects", LinearError("network", "reset")),
    "no-teams": lambda f: setattr(f, "projects_reply", {"teams": None}),
    "teams-not-object": lambda f: setattr(f, "projects_reply", {"teams": []}),
    "no-team-node": lambda f: setattr(f, "projects_reply", {"teams": {"nodes": []}}),
    "team-not-object": lambda f: setattr(f, "projects_reply", {"teams": {"nodes": ["x"]}}),
    "no-projects": lambda f: setattr(f, "projects_reply", {"teams": {"nodes": [{}]}}),
    "nodes-not-list": lambda f: setattr(f, "projects_reply",
                                        {"teams": {"nodes": [{"projects": {"nodes": {}}}]}}),
}


@pytest.mark.parametrize("failure", sorted(PROJECT_FAILURES))
def test_an_unresolvable_project_is_a_failed_read_never_the_whole_team(tmp_path, failure):
    fake = _team()
    PROJECT_FAILURES[failure](fake)
    project = "Nowhere" if failure == "unknown-name" else "Tapgrid"
    src = _src(tmp_path, fake, project=project)
    assert src.list_open_tasks() == [] and src.read_failed is True
    assert _src(tmp_path, fake, project=project).fetch_by_ids(["CMX-2"]) is None
    with pytest.raises(LinearError):
        _src(tmp_path, fake, project=project).create_issue("new")
    assert not {"open", "by_number", "create"} & set(fake.names())


def test_an_unresolvable_project_says_so_once(tmp_path, caplog):
    fake = _team()
    for _ in range(3):
        _src(tmp_path, fake, project="Nowhere").list_open_tasks()
    errors = [r for r in caplog.records if r.levelname == "ERROR" and "Nowhere" in r.message]
    assert len(errors) == 1
    assert "names no project of Linear team CMX" in errors[0].message


# --- two workflows on one team never claim the same issue -----------------------------

def _pair(tmp_path, monkeypatch, a: dict, b: dict):
    pa = _write_wf(tmp_path / "a" / "WORKFLOW.md", **a)
    pb = _write_wf(tmp_path / "b" / "WORKFLOW.md", **b)
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [pa.resolve(), pb.resolve()])
    fake = _team()
    return (LinearSource(load_workflow(pa), transport=fake),
            LinearSource(load_workflow(pb), transport=fake), fake)


UNSAFE = {
    "one-lacks-project": ({"project": "Chelamux"}, {}),
    "both-lack-project": ({}, {}),
    "same-project": ({"project": "Tapgrid"}, {"project": "tapgrid"}),
}


@pytest.mark.parametrize("case", sorted(UNSAFE))
def test_two_workflows_that_could_share_an_issue_both_refuse(tmp_path, monkeypatch, case):
    """⛔ The negative control: the unsafe configs are refused — both sides claim nothing,
    and neither even reads the team."""
    a, b = UNSAFE[case]
    sa, sb, fake = _pair(tmp_path, monkeypatch, a, b)
    for src, other in ((sa, "b"), (sb, "a")):
        assert src.config_error and "Linear team CMX" in src.config_error
        assert str(tmp_path / other / "WORKFLOW.md") in src.config_error
        assert src.list_open_tasks() == [] and src.read_failed is True
    assert fake.calls == []


def test_the_refusal_names_why(tmp_path, monkeypatch):
    sa, _, _ = _pair(tmp_path, monkeypatch, {"project": "Tapgrid"}, {"project": "Tapgrid"})
    assert "both set `project: Tapgrid`" in sa.config_error
    sa, _, _ = _pair(tmp_path, monkeypatch, {"project": "Tapgrid"}, {})
    assert "a workflow without `project:` reads the whole team" in sa.config_error


def test_two_project_scoped_workflows_on_one_team_see_disjoint_issues(tmp_path, monkeypatch):
    sa, sb, _ = _pair(tmp_path, monkeypatch, {"project": "Chelamux"}, {"project": "Tapgrid"})
    assert sa.config_error is None and sb.config_error is None
    ca, _ = sa.claimable(sa.list_open_tasks())
    cb, _ = sb.claimable(sb.list_open_tasks())
    assert [t.id for t in ca] == ["CMX-1"] and [t.id for t in cb] == ["CMX-2"]


@pytest.mark.parametrize("other", [{"team": "ENG"}, {"kind": "markdown"}])
def test_a_workflow_on_another_team_or_tracker_is_no_clash(tmp_path, monkeypatch, other):
    sa, sb, _ = _pair(tmp_path, monkeypatch, {}, other)
    assert sa.config_error is None


def test_the_team_key_is_compared_case_insensitively(tmp_path, monkeypatch):
    sa, _, _ = _pair(tmp_path, monkeypatch, {}, {"team": "cmx"})
    assert sa.config_error and "Linear team CMX" in sa.config_error


def test_a_workflow_listed_alone_is_not_its_own_sibling(tmp_path, monkeypatch):
    path = _write_wf(tmp_path / "a" / "WORKFLOW.md").resolve()
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [path])
    assert LinearSource(load_workflow(path), transport=_team()).config_error is None


def test_a_sibling_that_never_parsed_is_not_dispatching(tmp_path, monkeypatch):
    path = _write_wf(tmp_path / "a" / "WORKFLOW.md").resolve()
    broken = tmp_path / "b" / "WORKFLOW.md"
    broken.parent.mkdir()
    broken.write_text("---\n: [\n---\n")
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [path, broken.resolve()])
    assert LinearSource(load_workflow(path), transport=_team()).config_error is None


def test_doctor_reports_the_clash_as_refusing_to_claim_work(tmp_path, monkeypatch):
    pa = _write_wf(tmp_path / "a" / "WORKFLOW.md").resolve()
    pb = _write_wf(tmp_path / "b" / "WORKFLOW.md", project="Tapgrid").resolve()
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [pa, pb])
    monkeypatch.setattr(linear, "load_api_key", lambda: SECRET)
    monkeypatch.setattr(runtime_truth, "dispatched_workflows", lambda: [pa, pb])
    findings = runtime_truth._workflows_report([pa, pb], runtime_truth._workflows_read())
    refusing = [f for f in findings if "refusing to claim work" in f.title]
    assert len(refusing) == 2 and all(f.level == runtime_truth.ERROR for f in refusing)
    assert all("Linear team CMX" in f.detail for f in refusing)


# --- the archive sweep is the TEAM's, once ---------------------------------------------

def _finished_team():
    return FakeTeam([
        issue(1, "proj-chela", state="Done", finished_at="2026-09-01T00:00:00Z"),
        issue(2, "proj-tap", state="Done", finished_at="2026-09-02T00:00:00Z"),
        issue(3, "proj-chela", state="Done", finished_at="2026-09-03T00:00:00Z"),
        issue(4, "proj-tap", state="Canceled", finished_at="2026-09-04T00:00:00Z"),
        issue(5, None, state="Todo"),
    ])


def test_the_sweep_runs_once_per_team_across_project_workflows(tmp_path, monkeypatch):
    pa = _write_wf(tmp_path / "a" / "WORKFLOW.md", project="Chelamux", keep_done=1)
    pb = _write_wf(tmp_path / "b" / "WORKFLOW.md", project="Tapgrid", keep_done=1)
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [pa.resolve(), pb.resolve()])
    fake = _finished_team()
    first = LinearSource(load_workflow(pa), transport=fake).archive_sweep()
    second = LinearSource(load_workflow(pb), transport=fake).archive_sweep()
    assert (first, second) == (3, None)
    assert fake.names().count("sweep") == 1
    # TEAM-wide: both projects' finished issues were ranked together; the newest stays.
    assert [k for k, i in sorted(fake.issues.items()) if i["archivedAt"]] == [
        "CMX-1", "CMX-2", "CMX-3"]
    assert linear.read_published_counts()["CMX"]["count"] == 2


def test_the_sweep_reads_the_whole_team_even_when_scoped(tmp_path):
    fake = _finished_team()
    _src(tmp_path, fake, project="Chelamux", keep_done=0).archive_sweep(force=True)
    (sweep,) = [v for n, v in fake.calls if n == "sweep"]
    assert "project" not in sweep
    assert sorted(v["id"] for n, v in fake.calls if n == "archive") == [
        "uuid-CMX-1", "uuid-CMX-2", "uuid-CMX-3", "uuid-CMX-4"]


def test_the_team_sweep_keeps_the_largest_keep_done_on_the_team(tmp_path, monkeypatch):
    """Two keep_done values on one team would fight (one archives what the other keeps):
    both sweep with the larger."""
    pa = _write_wf(tmp_path / "a" / "WORKFLOW.md", project="Chelamux", keep_done=0)
    pb = _write_wf(tmp_path / "b" / "WORKFLOW.md", project="Tapgrid", keep_done=3)
    monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [pa.resolve(), pb.resolve()])
    fake = _finished_team()
    src = LinearSource(load_workflow(pa), transport=fake)
    assert (src.keep_done, src.sweep_keep_done) == (0, 3)
    assert src.archive_sweep(force=True) == 1
    assert [k for k, i in fake.issues.items() if i["archivedAt"]] == ["CMX-1"]


def test_alone_the_sweep_keeps_the_workflows_own_keep_done(tmp_path):
    fake = _finished_team()
    src = _src(tmp_path, fake, keep_done=2)
    assert src.sweep_keep_done == 2
    assert src.archive_sweep(force=True) == 2


# --- end to end: two dispatched repos on one team -------------------------------------

def _repo(tmp_path: Path, name: str, state: Path, **wf) -> Path:
    origin = tmp_path / f"{name}-origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True,
                   capture_output=True)
    work = tmp_path / name
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"),
                 ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(work), "config", k, v], check=True,
                       capture_output=True)
    _write_wf(work / "WORKFLOW.md", key=name.upper(), root=state / "worktrees" / name, **wf)
    (work / "README").write_text("x\n")
    for cmd in (["add", "-A"], ["commit", "-qm", "seed"], ["push", "-qu", "origin", "dev"]):
        subprocess.run(["git", "-C", str(work), *cmd], check=True, capture_output=True)
    return (work / "WORKFLOW.md").resolve()


@pytest.fixture
def daemon(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir()
    monkeypatch.setattr(config, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "CHELA_DIR", state)
    monkeypatch.setattr(dispatcher, "DB_PATH", state / "scheduler.db")
    fake = FakeTeam([issue(1, "proj-chela"), issue(2, "proj-tap"), issue(3, None),
                     issue(4, "proj-chela"), issue(5, "proj-tap")])
    monkeypatch.setattr(linear, "load_api_key", lambda: SECRET)
    monkeypatch.setattr(linear, "make_transport", lambda key: fake)
    launched = []
    monkeypatch.setattr(dispatcher, "_launch_agent",
                        lambda wf, task_id, window, worktree, prompt, conn, **kw:
                        launched.append((str(wf.path), task_id)))
    monkeypatch.setattr(dispatcher, "_run_critic", lambda *a, **k: None)

    def run(a: dict, b: dict):
        pa = _repo(tmp_path, "alpha", state, **a)
        pb = _repo(tmp_path, "beta", state, **b)
        monkeypatch.setattr(config, "DISPATCH_WORKFLOWS", [pa, pb])
        with patch.object(dispatcher, "_read_pr_url", return_value=None):
            summaries = [dispatcher.tick(pa), dispatcher.tick(pb)]
        return pa, pb, summaries

    return SimpleRun(run, launched, fake)


class SimpleRun:
    def __init__(self, run, launched, fake):
        self.run, self.launched, self.fake = run, launched, fake


def test_two_dispatched_repos_on_one_team_claim_disjoint_issues(daemon):
    pa, pb, _ = daemon.run({"project": "Chelamux"}, {"project": "Tapgrid"})
    by_wf = {str(pa): set(), str(pb): set()}
    for wf, task in daemon.launched:
        by_wf[wf].add(task)
    assert by_wf == {str(pa): {"CMX-1", "CMX-4"}, str(pb): {"CMX-2", "CMX-5"}}
    assert daemon.fake.names().count("sweep") == 1


def test_the_unsafe_pair_dispatches_nothing(daemon):
    """⛔ Negative control: one workflow on the shared team without `project:` — neither
    repo claims a single issue."""
    _, _, summaries = daemon.run({}, {"project": "Tapgrid"})
    assert daemon.launched == []
    assert all(s.get("tracker_read_failed") is True for s in summaries), summaries
    assert all(s.get("dispatched", 0) == 0 for s in summaries)


@pytest.mark.parametrize("theirs", ["a1b2c3", "proj-chela", "CHELAMUX"])
def test_two_names_for_one_project_are_a_clash_too(tmp_path, monkeypatch, theirs):
    """`Chelamux` and its slug (or id) differ as strings but are ONE project: the read
    fails closed on both sides instead of letting both claim it."""
    sa, sb, fake = _pair(tmp_path, monkeypatch, {"project": "Chelamux"}, {"project": theirs})
    if theirs == "CHELAMUX":                      # caught without a network call
        assert sa.config_error and sb.config_error
        return
    assert sa.config_error is None and sb.config_error is None
    for src, other in ((sa, "b"), (sb, "a")):
        assert src.list_open_tasks() == [] and src.read_failed is True
        assert src.fetch_by_ids(["CMX-1"]) is None
    assert not {"open", "by_number"} & set(fake.names())


def test_a_sibling_naming_a_different_project_by_slug_is_no_clash(tmp_path, monkeypatch):
    sa, _, _ = _pair(tmp_path, monkeypatch, {"project": "Chelamux"}, {"project": "d4e5f6"})
    assert {t.id for t in sa.list_open_tasks()} == {"CMX-1", "CMX-4"}
