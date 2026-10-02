"""➕📐 CMX-6 — "New task" in the dashboard creates a Linear issue in the configured team.

The GraphQL transport is stubbed (`FakeTeam`, behind `linear.make_transport`) — no test
talks to Linear. The route runs through the real Flask app (test client), the real
WORKFLOW.md loader and the real `chela.env` key loader.
"""
from __future__ import annotations

import json

import pytest

from chela import config
from chela.dashboard import app as dash
from chela.sources import linear
from chela.sources.linear import LinearError, LinearSource

SECRET = "lin_api_NEWTASKsecretVALUE0123456789"
OWNER = {"Sec-Fetch-Site": "same-origin"}

_STATES = [("Backlog", "backlog"), ("Todo", "unstarted"), ("In Progress", "started"),
           ("Done", "completed"), ("Canceled", "canceled")]


def _node(n, title, *, state="Todo", desc=None, priority=0, blockers=()):
    return {
        "id": f"uuid-{n}", "identifier": f"CMX-{n}", "number": n, "title": title,
        "description": desc, "priority": priority, "sortOrder": float(n),
        "url": f"https://linear.app/acme/issue/CMX-{n}", "branchName": f"cmx-{n}-t",
        "archivedAt": None, "state": {"name": state, "type": dict(_STATES)[state]},
        "labels": {"nodes": []}, "inverseRelations": {"nodes": list(blockers)},
    }


class FakeTeam:
    """An in-memory Linear team: reads, `issueCreate`, `issueRelationCreate`."""

    def __init__(self, *issues):
        self.issues = {i["identifier"]: i for i in issues}
        self.calls: list[tuple[str, dict]] = []
        self.fail_create: LinearError | None = None
        self.fail_relation = False
        self.next_number = 100

    def __call__(self, query, variables):
        name = {
            linear.OPEN_ISSUES_QUERY: "open", linear.ISSUES_BY_NUMBER_QUERY: "by_number",
            linear.TEAM_STATES_QUERY: "states", linear.CREATE_ISSUE_MUTATION: "create",
            linear.CREATE_RELATION_MUTATION: "relation",
        }[query]
        self.calls.append((name, json.loads(json.dumps(variables))))
        page = {"pageInfo": {"hasNextPage": False, "endCursor": None}}
        if name == "open":
            nodes = [i for i in self.issues.values()
                     if i["state"]["type"] not in ("completed", "canceled")]
            return {"issues": {"nodes": nodes, **page}}
        if name == "by_number":
            nodes = [i for i in self.issues.values() if i["number"] in variables["numbers"]]
            return {"issues": {"nodes": nodes, **page}}
        if name == "states":
            return {"teams": {"nodes": [{"id": "team-uuid", "states": {"nodes": [
                {"id": f"st-{s}", "name": s, "type": t, "position": p}
                for p, (s, t) in enumerate(_STATES)]}}]}}
        if name == "create":
            if self.fail_create is not None:
                raise self.fail_create
            inp = variables["input"]
            assert inp["teamId"] == "team-uuid"
            n = self.next_number
            self.next_number += 1
            state = inp["stateId"][len("st-"):]
            node = _node(n, inp["title"], state=state, desc=inp["description"],
                         priority=inp["priority"])
            self.issues[node["identifier"]] = node
            return {"issueCreate": {"success": True, "issue": {
                "id": node["id"], "identifier": node["identifier"], "title": node["title"],
                "url": node["url"]}}}
        # relation: stored on the blocker, visible on the blocked issue's inverseRelations
        if self.fail_relation:
            raise LinearError("graphql", "relation refused")
        inp = variables["input"]
        blocker = next(i for i in self.issues.values() if i["id"] == inp["issueId"])
        blocked = next(i for i in self.issues.values() if i["id"] == inp["relatedIssueId"])
        blocked["inverseRelations"]["nodes"].append({"type": inp["type"], "issue": {
            "identifier": blocker["identifier"], "archivedAt": None, "state": blocker["state"]}})
        return {"issueRelationCreate": {"success": True}}

    def names(self):
        return [n for n, _ in self.calls]

    def inputs(self, name):
        return [v["input"] for n, v in self.calls if n == name]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    linear._backoff.clear()
    linear._reported.clear()
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-state")
    env = tmp_path / "chela.env"
    env.write_text(f"LINEAR_API_KEY={SECRET}\n")
    monkeypatch.setenv("CHELA_ENV_FILE", str(env))
    yield
    linear._backoff.clear()


@pytest.fixture
def team(monkeypatch):
    fake = FakeTeam(_node(1, "existing open task"), _node(2, "another one", state="In Progress"))
    seen_keys = []

    def make(key):
        seen_keys.append(key)
        return fake

    monkeypatch.setattr(linear, "make_transport", make)
    fake.seen_keys = seen_keys
    return fake


@pytest.fixture
def wf_path(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    p = repo / "WORKFLOW.md"
    p.write_text("---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\n"
                 "  ready_states: [Todo]\n---\nDo the task.\n")
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [p.resolve()])
    return p.resolve()


@pytest.fixture
def client():
    return dash.app.test_client()


def _post(client, wf_path, headers=OWNER, **body):
    payload = {"workflow_path": str(wf_path), "title": "Add a thing",
               "description": "## Brief\n\nDo **it**.", "priority": 2, **body}
    return client.post("/api/dispatcher/linear/issue", json=payload, headers=headers)


# --- the adapter --------------------------------------------------------------------------

def _source(fake, **tracker):
    cfg = {"tracker": {"kind": "linear", "team": "CMX", **tracker}}

    class WF:
        path = "WORKFLOW.md"

        @staticmethod
        def get(*keys, default=None):
            cur = cfg
            for k in keys:
                if not isinstance(cur, dict) or k not in cur:
                    return default
                cur = cur[k]
            return cur

    return LinearSource(WF(), transport=fake)


def test_create_issue_sends_team_title_description_priority_and_the_ready_state():
    fake = FakeTeam()
    out = _source(fake).create_issue("Ship it", "the **brief**", 3)

    assert fake.inputs("create") == [{
        "teamId": "team-uuid", "title": "Ship it", "description": "the **brief**",
        "priority": 3, "stateId": "st-Todo",
    }]
    assert out["identifier"] == "CMX-100"
    assert out["url"] == "https://linear.app/acme/issue/CMX-100"
    assert out["warnings"] == []


def test_create_issue_uses_the_configured_ready_state_not_a_hardcoded_todo():
    fake = FakeTeam()
    _source(fake, ready_states=["Backlog"]).create_issue("x")
    assert fake.inputs("create")[0]["stateId"] == "st-Backlog"


def test_create_issue_never_creates_a_sub_issue():
    fake = FakeTeam(_node(1, "parent-ish"))
    _source(fake).create_issue("child", blocked_by=["CMX-1"])
    for inp in fake.inputs("create"):
        assert "parentId" not in inp


def test_blocked_by_creates_a_blocks_relation_from_the_blocker():
    fake = FakeTeam(_node(1, "blocker"), _node(2, "another blocker"))
    out = _source(fake).create_issue("blocked task", blocked_by=["CMX-1", "cmx-2"])

    assert sorted(fake.inputs("relation"), key=lambda i: i["issueId"]) == [
        {"issueId": "uuid-1", "relatedIssueId": "uuid-100", "type": "blocks"},
        {"issueId": "uuid-2", "relatedIssueId": "uuid-100", "type": "blocks"},
    ]
    # …and the adapter's own reader sees the new issue as blocked by both.
    task = _source(fake).fetch_by_ids(["CMX-100"])[0]
    assert set(task.depends) == {"CMX-1", "CMX-2"}
    assert out["warnings"] == []


def test_an_unknown_blocker_refuses_before_anything_is_created():
    fake = FakeTeam(_node(1, "blocker"))
    with pytest.raises(LinearError):
        _source(fake).create_issue("x", blocked_by=["CMX-77"])
    with pytest.raises(LinearError):
        _source(fake).create_issue("x", blocked_by=["OTHER-1"])
    assert "create" not in fake.names()


def test_a_failed_relation_is_a_warning_once_the_issue_exists():
    fake = FakeTeam(_node(1, "blocker"))
    fake.fail_relation = True
    out = _source(fake).create_issue("x", blocked_by=["CMX-1"])
    assert out["identifier"] == "CMX-100"
    assert out["warnings"] and "CMX-1" in out["warnings"][0]


@pytest.mark.parametrize("priority", [-1, 5, True, "2"])
def test_create_issue_refuses_an_out_of_range_priority(priority):
    fake = FakeTeam()
    with pytest.raises(LinearError):
        _source(fake).create_issue("x", priority=priority)
    assert fake.calls == []


# --- the dashboard route ------------------------------------------------------------------

def test_route_creates_the_issue_through_the_server_side_key(client, team, wf_path):
    resp = _post(client, wf_path)

    assert resp.status_code == 200, resp.get_data(as_text=True)
    data = resp.get_json()
    assert data["ok"] is True
    assert data["issue"] == {"identifier": "CMX-100", "title": "Add a thing",
                             "url": "https://linear.app/acme/issue/CMX-100"}
    assert team.inputs("create") == [{
        "teamId": "team-uuid", "title": "Add a thing", "description": "## Brief\n\nDo **it**.",
        "priority": 2, "stateId": "st-Todo",
    }]
    # The key came from chela.env, on the server.
    assert team.seen_keys == [SECRET]


def test_route_blocked_by_creates_the_relation(client, team, wf_path):
    resp = _post(client, wf_path, blocked_by=["CMX-1"])
    assert resp.get_json()["ok"] is True
    assert team.inputs("relation") == [
        {"issueId": "uuid-1", "relatedIssueId": "uuid-100", "type": "blocks"}]


@pytest.mark.parametrize("headers", [
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "https://relay.example.test"},
    {"Referer": "https://relay.example.test/join#abc"},
])
def test_route_refuses_a_share_guest(client, team, wf_path, monkeypatch, headers):
    monkeypatch.setattr(config, "COLLAB_RELAY", "wss://relay.example.test")
    resp = _post(client, wf_path, headers=headers)

    assert resp.status_code == 403
    assert resp.get_json()["ok"] is False
    assert team.calls == []                    # refused before Linear was touched


def test_route_shows_a_linear_error_and_creates_nothing(client, team, wf_path):
    team.fail_create = LinearError("graphql", "Title is too long")
    resp = _post(client, wf_path)

    assert resp.status_code == 502
    data = resp.get_json()
    assert data["ok"] is False
    assert "Title is too long" in data["error"]


def test_route_refuses_an_unknown_workflow_and_a_non_linear_one(client, team, wf_path, tmp_path):
    assert _post(client, tmp_path / "nope" / "WORKFLOW.md").status_code == 400
    wf_path.write_text("---\nproject_key: CMX\ntracker:\n  kind: markdown\n  path: TODO.md\n"
                       "---\nx\n")
    resp = _post(client, wf_path)
    assert resp.status_code == 400
    assert team.calls == []


def test_the_api_key_never_reaches_the_browser(client, team, wf_path, monkeypatch):
    """Every response this feature can send — success, Linear error (even one whose text
    carries the key), guest refusal, and the queue payload — is free of the key."""
    bodies = [_post(client, wf_path, blocked_by=["CMX-1"]).get_data(as_text=True)]
    team.fail_create = LinearError("graphql", f"Authorization {SECRET} is not valid")
    bodies.append(_post(client, wf_path).get_data(as_text=True))
    team.fail_create = None
    team.fail_relation = True
    bodies.append(_post(client, wf_path, blocked_by=["CMX-1"]).get_data(as_text=True))
    monkeypatch.setattr(config, "COLLAB_RELAY", "wss://relay.example.test")
    bodies.append(_post(client, wf_path, headers={"Sec-Fetch-Site": "cross-site"})
                  .get_data(as_text=True))
    bodies.append(client.get("/api/dispatcher").get_data(as_text=True))

    assert all(bodies)
    for body in bodies:
        assert SECRET not in body
    # The second body really was the error path (the redaction ran, not a skipped call).
    assert "[redacted]" in bodies[1]


def test_accepted_a_created_issue_appears_in_the_queue_on_the_next_refresh(
        client, team, wf_path):
    before = client.get("/api/dispatcher").get_json()
    [wf] = before["workflows"]
    assert wf["tracker_kind"] == "linear"
    assert "CMX-100" not in [t["id"] for t in wf["open_tasks"]]

    assert _post(client, wf_path, title="Fresh from the form").get_json()["ok"] is True

    [wf] = client.get("/api/dispatcher").get_json()["workflows"]
    new = [t for t in wf["open_tasks"] if t["id"] == "CMX-100"]
    assert new, wf["open_tasks"]
    assert new[0]["title"] == "Fresh from the form"
    assert new[0]["url"] == "https://linear.app/acme/issue/CMX-100"
