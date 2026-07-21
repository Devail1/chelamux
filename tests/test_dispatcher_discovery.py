"""The Dispatcher/Kanban view auto-discovers workflows so dogfood dispatch runs
show up without setting ``CHELA_DISPATCH_WORKFLOWS``.

``/api/dispatcher`` used to iterate only the env-configured ``DISPATCH_WORKFLOWS``
and return ``{configured: false, workflows: []}`` otherwise — even when the runs
DB already held runs. These tests lock in the union of three session-independent
sources (explicit config + repo-root ``WORKFLOW.md`` + every ``workflow_path``
with recorded runs) and prove the explicit config keeps working.
"""

from __future__ import annotations

import pytest

from chela.dashboard import app as dash


@pytest.fixture
def client():
    return dash.app.test_client()


def _run(workflow_path: str, *, task_id: str, status: str, branch: str, task_number=None):
    """A runs-DB row as list_runs() would return it (SELECT * → dict)."""
    return {
        "task_id": task_id,
        "workflow_path": workflow_path,
        "title": f"task {task_id}",
        "status": status,
        "window_name": None,
        "worktree_path": None,
        "branch_name": branch,
        "started_at": "2026-07-11T00:00:00+00:00",
        "ended_at": None,
        "attempt": 1,
        "last_error": None,
        "pr_url": None,
        "pr_state": None,
        "task_number": task_number,
    }


def _no_repo_workflow(monkeypatch):
    """Silence the real repo-root WORKFLOW.md so tests control the sources."""
    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: None)


# --- _project_key_from_runs -------------------------------------------------

@pytest.mark.parametrize(
    "branch,expected",
    [
        ("cmx-3", "CMX"),
        ("proj-12", "PROJ"),
        ("CMX-3", "CMX"),
        ("dogfood/abc123", None),   # pre-migration branch: no key
        ("", None),
        ("nodash", None),
        ("toolongkey-1", None),     # 10 chars > PROJECT_KEY_RE max of 5
    ],
)
def test_project_key_from_runs(branch, expected):
    assert dash._project_key_from_runs([_run("/x/WORKFLOW.md", task_id="t", status="running", branch=branch)]) == expected


def test_project_key_from_runs_scans_all_groups():
    # First group empty / unmatchable, key recovered from a later group.
    active: list[dict] = []
    awaiting = [_run("/x/WORKFLOW.md", task_id="t", status="awaiting_review", branch="dogfood/xyz")]
    recent = [_run("/x/WORKFLOW.md", task_id="u", status="done", branch="abc-7")]
    assert dash._project_key_from_runs(active, awaiting, recent) == "ABC"


# --- _discover_dispatch_workflows -------------------------------------------

def test_discovery_surfaces_run_only_workflow(monkeypatch, tmp_path):
    _no_repo_workflow(monkeypatch)
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [])
    wf = (tmp_path / "WORKFLOW.md").resolve()
    runs = [_run(str(wf), task_id="t1", status="running", branch="cmx-1", task_number=1)]
    discovered = dash._discover_dispatch_workflows(runs)
    assert wf in discovered


def test_discovery_unions_config_and_runs_without_dupes(monkeypatch, tmp_path):
    _no_repo_workflow(monkeypatch)
    cfg_wf = (tmp_path / "cfg" / "WORKFLOW.md").resolve()
    run_wf = (tmp_path / "run" / "WORKFLOW.md").resolve()
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [cfg_wf])
    runs = [
        # A run against the configured workflow must NOT duplicate it.
        _run(str(cfg_wf), task_id="a", status="done", branch="cmx-1", task_number=1),
        _run(str(run_wf), task_id="b", status="running", branch="abc-2", task_number=2),
    ]
    discovered = dash._discover_dispatch_workflows(runs)
    assert discovered.count(cfg_wf) == 1
    assert discovered == [cfg_wf, run_wf]  # config first, then run-discovered


def test_discovery_includes_repo_root_workflow(monkeypatch, tmp_path):
    repo_wf = (tmp_path / "WORKFLOW.md").resolve()
    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: repo_wf)
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [])
    assert repo_wf in dash._discover_dispatch_workflows([])


# --- /api/dispatcher end to end ---------------------------------------------

def test_api_run_only_workflow_appears(monkeypatch, client, tmp_path):
    """No env config, no repo workflow file — a run-discovered workflow whose
    file is absent still surfaces its runs, with configured flipped on."""
    _no_repo_workflow(monkeypatch)
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [])
    missing = str((tmp_path / "elsewhere" / "WORKFLOW.md").resolve())
    runs = [_run(missing, task_id="t9", status="awaiting_review", branch="cmx-9", task_number=9)]
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: runs)

    resp = client.get("/api/dispatcher")
    data = resp.get_json()
    assert data["configured"] is True
    assert len(data["workflows"]) == 1
    wf = data["workflows"][0]
    assert wf["path"] == missing
    assert wf["exists"] is False
    assert wf["error"] == "workflow file not found"
    # Run still shows, project_key recovered from the branch.
    assert wf["project_key"] == "CMX"
    assert [r["task_id"] for r in wf["awaiting_review_runs"]] == ["t9"]
    assert wf["awaiting_review_runs"][0]["project_key"] == "CMX"


def test_api_explicit_config_still_works(monkeypatch, client, tmp_path):
    """A configured workflow with a real file loads its project_key + open tasks
    (union does not replace the explicit config)."""
    _no_repo_workflow(monkeypatch)
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\nproject_key: XYZ\ntracker:\n  kind: markdown\n  path: TODO.md\n---\nprompt\n"
    )
    (repo / "TODO.md").write_text("## Open\n\n- [ ] ship it\n")
    wf_path = (repo / "WORKFLOW.md").resolve()
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [wf_path])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])

    resp = client.get("/api/dispatcher")
    data = resp.get_json()
    assert data["configured"] is True
    assert len(data["workflows"]) == 1
    wf = data["workflows"][0]
    assert wf["exists"] is True
    assert wf["error"] is None
    assert wf["project_key"] == "XYZ"
    assert [t["title"] for t in wf["open_tasks"]] == ["ship it"]


def test_api_empty_when_nothing_to_show(monkeypatch, client):
    """No config, no repo workflow, no runs → empty + configured False so the
    frontend renders its empty state."""
    _no_repo_workflow(monkeypatch)
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])
    data = client.get("/api/dispatcher").get_json()
    assert data == {"configured": False, "workflows": []}
