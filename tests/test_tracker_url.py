"""🔗↗️ CMX-5 — a Linear-backed Work card links to its Linear issue.

The URL travels three hops, and each is guarded here: the Linear adapter reads GraphQL
`url` onto ``Task.url`` (never guessed from a workspace slug); ``dispatcher._spawn``
copies it onto the run row at claim (``runs.tracker_url``) so a running / in-review /
done card still has it after the task leaves ``open_tasks``; ``/api/dispatcher`` hands
it to the board on open tasks and runs alike. A markdown task carries none at every hop.
The card rendering itself is guarded in tests/kanban_tracker_link.test.mjs.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chela.dashboard import app as dash
from chela.sources import Task

# Reuse the brief-at-claim fixtures (a real git repo + faked tmux) rather than a copy.
from tests.test_linear_source import FakeLinear, _src, issue
from tests.test_linear_source import _fresh_module_state  # noqa: F401 — autouse: isolates CHELA_DIR
from tests.test_taskmodal_data import _conn, _repo, _spawn, _wf

URL = "https://linear.app/acme/issue/CMX-12/tighten-top-row"


# --- hop 1: the Linear adapter ----------------------------------------------------

def test_a_linear_task_carries_the_issue_url_linear_returned(tmp_path):
    node = issue(12, "Tighten top row")
    node["url"] = URL
    t = _src(tmp_path, FakeLinear([node])).list_open_tasks()[0]
    assert t.url == URL


@pytest.mark.parametrize("bad", [None, "", "javascript:alert(1)", "http://linear.app/x", 42])
def test_a_missing_or_non_https_url_is_no_url_never_a_guess(tmp_path, bad):
    node = issue(12)
    node["url"] = bad
    t = _src(tmp_path, FakeLinear([node])).list_open_tasks()[0]
    assert t.url is None


# --- hop 2: the run row, at claim -------------------------------------------------

def test_spawn_persists_the_tracker_url_on_the_run_row(tmp_path):
    conn = _conn()
    task = Task(id="CMX-12", title="Tighten top row", file="", line_number=12, raw=URL,
                url=URL)
    assert _spawn(_wf(_repo(tmp_path), tmp_path / "wt"), task, conn) is True
    row = conn.execute("SELECT tracker_url FROM runs WHERE task_id='CMX-12'").fetchone()
    assert row["tracker_url"] == URL


def test_a_retry_rewrites_the_tracker_url_via_on_conflict(tmp_path):
    repo, root, conn = _repo(tmp_path), tmp_path / "wt", _conn()
    task = Task(id="CMX-12", title="Tighten top row", file="", line_number=12, raw=URL,
                url=URL)
    assert _spawn(_wf(repo, root), task, conn, attempt=1) is True
    conn.execute("UPDATE runs SET status='failed', tracker_url=NULL WHERE task_id='CMX-12'")
    conn.commit()
    assert _spawn(_wf(repo, root), task, conn, attempt=2) is True
    row = conn.execute("SELECT tracker_url FROM runs WHERE task_id='CMX-12'").fetchone()
    assert row["tracker_url"] == URL


def test_a_markdown_task_claims_with_no_tracker_url(tmp_path):
    conn = _conn()
    task = Task(id="md-task", title="ship it", file="TODO.md", line_number=3,
                raw="- [ ] ship it")
    assert _spawn(_wf(_repo(tmp_path), tmp_path / "wt"), task, conn) is True
    row = conn.execute("SELECT tracker_url FROM runs WHERE task_id='md-task'").fetchone()
    assert row["tracker_url"] is None


# --- hop 3: /api/dispatcher ---------------------------------------------------------

@pytest.fixture
def client():
    return dash.app.test_client()


def _workflow(monkeypatch, tmp_path: Path, runs: list[dict], tasks: list[Task] | None):
    """A markdown WORKFLOW.md on disk; `tasks` (when given) replaces what its source lists."""
    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: None)
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\nproject_key: CMX\ntracker:\n  kind: markdown\n  path: TODO.md\n---\nprompt\n")
    (repo / "TODO.md").write_text("## Open\n\n- [ ] ship it\n")
    wf_path = (repo / "WORKFLOW.md").resolve()
    for r in runs:
        r.setdefault("workflow_path", str(wf_path))
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [wf_path])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [dict(r) for r in runs])
    if tasks is not None:
        class _Source:
            def list_open_tasks(self):
                return list(tasks)
        monkeypatch.setattr(dash, "get_source", lambda wf: _Source())


def _run(task_id, status, tracker_url=None):
    return {"task_id": task_id, "title": task_id, "status": status, "attempt": 1,
            "started_at": "2026-10-01T00:00:00Z", "ended_at": "2026-10-01T01:00:00Z",
            "tracker_url": tracker_url}


def test_open_linear_tasks_carry_their_tracker_url(monkeypatch, client, tmp_path):
    _workflow(monkeypatch, tmp_path, [], [
        Task(id="CMX-12", title="Tighten top row", file="", line_number=12, raw=URL, url=URL)])
    wf = client.get("/api/dispatcher").get_json()["workflows"][0]
    assert [(t["id"], t["tracker_url"]) for t in wf["open_tasks"]] == [("CMX-12", URL)]


def test_a_markdown_open_task_has_no_tracker_url(monkeypatch, client, tmp_path):
    _workflow(monkeypatch, tmp_path, [], None)
    wf = client.get("/api/dispatcher").get_json()["workflows"][0]
    assert len(wf["open_tasks"]) == 1
    assert wf["open_tasks"][0]["tracker_url"] is None


def test_runs_carry_the_url_recorded_at_claim(monkeypatch, client, tmp_path):
    done_url = "https://linear.app/acme/issue/CMX-3/shipped"
    _workflow(monkeypatch, tmp_path, [_run("CMX-3", "done", done_url)], [])
    wf = client.get("/api/dispatcher").get_json()["workflows"][0]
    assert [r["tracker_url"] for r in wf["recent_runs"]] == [done_url]


def test_a_pre_migration_run_borrows_its_open_tasks_url(monkeypatch, client, tmp_path):
    """A run claimed before `runs.tracker_url` existed reads NULL there; while its issue is
    still open, the URL this request's tracker read returned fills it in. A run whose task
    the tracker did NOT return stays None — never guessed."""
    _workflow(monkeypatch, tmp_path, [_run("CMX-12", "running"), _run("CMX-99", "running")], [
        Task(id="CMX-12", title="Tighten top row", file="", line_number=12, raw=URL, url=URL)])
    wf = client.get("/api/dispatcher").get_json()["workflows"][0]
    got = {r["task_id"]: r["tracker_url"] for r in wf["active_runs"]}
    assert got == {"CMX-12": URL, "CMX-99": None}
