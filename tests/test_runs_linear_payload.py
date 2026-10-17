"""CMX-94 — a Linear open task's payload: URL, state, risk, section; no file:line.
Rendering: tests/runs_linear.test.mjs."""
from __future__ import annotations

from pathlib import Path

import pytest

from chela.dashboard import app as dash
from chela.sources import Task

URL = "https://linear.app/acme/issue/CMX-12/tighten-top-row"
COLUMNS = [{"name": n, "type": t, "hidden": False} for n, t in (
    ("Backlog", "backlog"), ("Todo", "unstarted"), ("In Progress", "started"),
    ("In Review", "started"))]


def _task(n, state, **kw):
    return Task(id=f"CMX-{n}", title=f"task {n}", file="", line_number=n,
                raw=f"https://linear.app/acme/issue/CMX-{n}", tracker_state=state, **kw)


class _LinearSource:
    """The slice of LinearSource /api/dispatcher reads: Todo is the ready state."""
    def __init__(self, tasks):
        self.list_open_tasks = lambda: list(tasks)
        self.workflow_states = lambda: COLUMNS

    def claimable(self, tasks):
        return [t for t in tasks if t.tracker_state == "Todo"], set()


def _workflow(monkeypatch, tmp_path: Path, source) -> dict:
    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: None)
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\n---\nprompt\n")
    wf_path = (repo / "WORKFLOW.md").resolve()
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [wf_path])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(dash, "get_source", lambda wf: source)
    return dash.app.test_client().get("/api/dispatcher").get_json()["workflows"][0]


def test_a_linear_open_task_carries_url_state_risk_and_group(monkeypatch, tmp_path):
    t = _task(12, "Todo", url=URL, risk="high", risk_reason="label")
    wf = _workflow(monkeypatch, tmp_path, _LinearSource([t]))
    assert wf["tracker_kind"] == "linear"
    got = wf["open_tasks"][0]
    assert (got["id"], got["tracker_url"], got["tracker_state"], got["risk"],
            got["risk_reason"], got["group"]) == ("CMX-12", URL, "Todo", "high", "label", "ready")
    # A Linear issue has no file:line — nothing a source-line delete could act on.
    assert got["file"] == ""


def test_open_tasks_are_grouped_ready_started_backlog(monkeypatch, tmp_path):
    tasks = [_task(1, "Todo"), _task(2, "In Progress"), _task(3, "In Review"),
             _task(4, "Backlog"), _task(5, "Todo")]
    wf = _workflow(monkeypatch, tmp_path, _LinearSource(tasks))
    assert {t["id"]: t["group"] for t in wf["open_tasks"]} == {
        "CMX-1": "ready", "CMX-2": "in_progress", "CMX-3": "in_progress",
        "CMX-4": "backlog", "CMX-5": "ready"}


@pytest.mark.parametrize("ready_ids, state, expected", [
    ({"CMX-1"}, "Todo", "ready"),
    ({"CMX-1"}, "In Progress", "ready"),      # the dispatcher's own ready read wins
    (set(), "In Progress", "in_progress"),
    (set(), "Todo", "backlog"),               # not claimable ⇒ never counted as ready
    (None, "Todo", "backlog"),                # read failed ⇒ unknown is never ready
    (set(), "Backlog", "backlog"),
    (set(), None, "backlog"),
])
def test_open_task_group_table(ready_ids, state, expected):
    types = {c["name"]: c["type"] for c in COLUMNS}
    assert dash._open_task_group(_task(1, state), ready_ids, types) == expected
