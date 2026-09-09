"""``/api/dispatcher`` joins each run to its own task-list progress (issue #462).

End-to-end through the real Flask route, driving ``dispatcher.list_runs``,
``sessionids.entries`` and ``epoch.current`` — the same three session-independent
sources ``chela.tasklists.progress_for_run`` reads — plus a real ``tasks/<sid>/N.json``
fixture tree under a temp dir monkeypatched in as ``tasklists.TASKS_DIR``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chela.dashboard import app as dash


@pytest.fixture
def client():
    return dash.app.test_client()


def _run(**overrides) -> dict:
    row = {
        "task_id": "t1",
        "workflow_path": "/x/WORKFLOW.md",
        "title": "a task",
        "status": "running",
        "window_name": "agent-1",
        "window_id": None,
        "window_epoch": None,
        "worktree_path": None,
        "branch_name": "cmx-1",
        "started_at": "2026-09-09T00:00:00+00:00",
        "ended_at": None,
        "attempt": 1,
        "last_error": None,
        "pr_url": None,
        "pr_state": None,
        "task_number": 1,
    }
    row.update(overrides)
    return row


def _no_repo_workflow(monkeypatch):
    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: None)


def _write_task(root: Path, name: str, **fields) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_text(json.dumps(fields))


def _setup(monkeypatch, tmp_path, runs, *, entries, current_epoch):
    _no_repo_workflow(monkeypatch)
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: runs)
    monkeypatch.setattr(dash.sessionids, "entries", lambda: entries)
    monkeypatch.setattr(dash.epoch, "current", lambda: current_epoch)
    monkeypatch.setattr(dash.tasklists, "TASKS_DIR", tmp_path / "tasks")


# --- counterweight: no window recorded yet -> renders exactly as before --------

def test_a_run_with_no_window_yet_carries_no_task_data(monkeypatch, client, tmp_path):
    runs = [_run(status="claimed", window_id=None, window_epoch=None)]
    _setup(monkeypatch, tmp_path, runs, entries={}, current_epoch="111-222")

    data = client.get("/api/dispatcher").get_json()
    active = data["workflows"][0]["active_runs"]
    assert len(active) == 1
    assert active[0]["tasks"] is None


# --- counterweight: a session with NO task directory -> None, not "0/0" --------

def test_a_live_session_with_no_task_directory_carries_no_task_data(monkeypatch, client, tmp_path):
    runs = [_run(status="running", window_id="@5", window_epoch="111-222")]
    entries = {"@5": {"session_id": "sid-quiet", "epoch": "111-222"}}
    _setup(monkeypatch, tmp_path, runs, entries=entries, current_epoch="111-222")

    data = client.get("/api/dispatcher").get_json()
    active = data["workflows"][0]["active_runs"]
    assert active[0]["tasks"] is None


# --- the join actually works end to end -----------------------------------------

def test_a_live_session_with_a_task_directory_joins_its_progress(monkeypatch, client, tmp_path):
    runs = [_run(status="running", window_id="@5", window_epoch="111-222")]
    entries = {"@5": {"session_id": "sid-live", "epoch": "111-222"}}
    _setup(monkeypatch, tmp_path, runs, entries=entries, current_epoch="111-222")
    _write_task(tmp_path / "tasks" / "sid-live", "1.json",
                id="1", subject="first", status="completed", blockedBy=[])
    _write_task(tmp_path / "tasks" / "sid-live", "2.json",
                id="2", subject="wire the in-flight refusal", status="in_progress", blockedBy=[])

    data = client.get("/api/dispatcher").get_json()
    tasks = data["workflows"][0]["active_runs"][0]["tasks"]
    assert tasks["total"] == 2
    assert tasks["done"] == 1
    assert tasks["in_progress"] == {"id": "2", "subject": "wire the in-flight refusal"}


# --- 🔴 GUARD: a reused window id must NOT attach a dead run to a live agent's tasks ---

def test_a_reused_window_id_does_not_attach_a_stale_run_to_the_new_occupants_tasks(
    monkeypatch, client, tmp_path,
):
    # Run A was dispatched onto @5 under epoch "111-222" and is still sitting in the
    # DB as `running` (never reaped). tmux then restarted (epoch "999-888") and @5
    # was handed to an unrelated agent, whose spawn overwrote the session-ids.json
    # row for @5 with the NEW session + epoch. Run A's own `window_epoch` column
    # was never touched, so it still reads "111-222" — that mismatch against the
    # CURRENT epoch is what must refuse the join.
    runs = [_run(task_id="run-A", status="running", window_id="@5", window_epoch="111-222")]
    entries = {"@5": {"session_id": "sid-of-new-unrelated-agent", "epoch": "999-888"}}
    _setup(monkeypatch, tmp_path, runs, entries=entries, current_epoch="999-888")
    # The new occupant DOES have a real task list — proves a naive wid lookup would
    # have found and misattributed real data, not merely an absent directory.
    _write_task(tmp_path / "tasks" / "sid-of-new-unrelated-agent", "1.json",
                id="1", subject="unrelated work", status="in_progress", blockedBy=[])

    data = client.get("/api/dispatcher").get_json()
    active = data["workflows"][0]["active_runs"]
    assert [r["task_id"] for r in active] == ["run-A"]
    assert active[0]["tasks"] is None


# --- 🔴 GUARD: the join runs on EVERY bucket, not only active_runs -------------

def test_a_recent_completed_run_also_carries_its_task_progress(monkeypatch, client, tmp_path):
    # docs/defeat_shapes 352d: every OTHER test in this file reads only
    # data["workflows"][0]["active_runs"]. app.py actually loops
    # `for r in (*active, *awaiting, *recent)` — kanban.js builds cards from all
    # three buckets (active_runs, awaiting_review_runs, recent_runs) off this same
    # payload, so a run that has finished must carry `tasks` too. A judge mutation
    # scoped the join to `... if r in active else None`, which corrupts nothing this
    # file's own (active-only) assertions ever read, and the suite stayed green.
    runs = [_run(status="done", window_id="@5", window_epoch="111-222")]
    entries = {"@5": {"session_id": "sid-live", "epoch": "111-222"}}
    _setup(monkeypatch, tmp_path, runs, entries=entries, current_epoch="111-222")
    _write_task(tmp_path / "tasks" / "sid-live", "1.json",
                id="1", subject="first", status="completed", blockedBy=[])

    data = client.get("/api/dispatcher").get_json()
    recent = data["workflows"][0]["recent_runs"]
    assert len(recent) == 1
    assert recent[0]["tasks"] == {
        "total": 1, "done": 1, "in_progress": None, "blocked": [],
    }


def test_an_awaiting_review_run_also_carries_its_task_progress(monkeypatch, client, tmp_path):
    # Same guard as above, the OTHER non-active bucket the join must still reach.
    runs = [_run(status="awaiting_review", window_id="@5", window_epoch="111-222")]
    entries = {"@5": {"session_id": "sid-live", "epoch": "111-222"}}
    _setup(monkeypatch, tmp_path, runs, entries=entries, current_epoch="111-222")
    _write_task(tmp_path / "tasks" / "sid-live", "1.json",
                id="1", subject="first", status="in_progress", blockedBy=[])

    data = client.get("/api/dispatcher").get_json()
    awaiting = data["workflows"][0]["awaiting_review_runs"]
    assert len(awaiting) == 1
    assert awaiting[0]["tasks"]["total"] == 1


# --- 🔴 GUARD: session_entries/current_epoch are fetched ONCE per request, not per run ---

def test_session_entries_and_current_epoch_are_fetched_once_for_the_whole_request_not_per_run(
    monkeypatch, client, tmp_path,
):
    # app.py's own comment states the reason: sessionids.entries() is one file read and
    # epoch.current() one tmux round trip, paid for once per request and reused by every
    # run below — never re-fetched per row. A judge mutation swapped the reused local
    # variables for fresh sessionids.entries()/epoch.current() calls at the per-run call
    # site (still functionally correct with two runs and one shared fixture), and the
    # suite stayed green because nothing counted how many times either was actually
    # called. Two runs here means a per-run re-fetch calls each function twice; the fix
    # must call each exactly once regardless of run count.
    runs = [
        _run(task_id="run-A", status="running", window_id="@5", window_epoch="111-222"),
        _run(task_id="run-B", status="running", window_id="@6", window_epoch="111-222"),
    ]
    entries = {
        "@5": {"session_id": "sid-A", "epoch": "111-222"},
        "@6": {"session_id": "sid-B", "epoch": "111-222"},
    }
    _setup(monkeypatch, tmp_path, runs, entries=entries, current_epoch="111-222")

    entries_calls = []
    epoch_calls = []
    monkeypatch.setattr(dash.sessionids, "entries", lambda: (entries_calls.append(1), entries)[1])
    monkeypatch.setattr(dash.epoch, "current", lambda: (epoch_calls.append(1), "111-222")[1])

    data = client.get("/api/dispatcher").get_json()

    assert len(data["workflows"][0]["active_runs"]) == 2
    assert len(entries_calls) == 1, (
        f"sessionids.entries() was called {len(entries_calls)} times for a 2-run request "
        "— it must be fetched once per request, not once per run"
    )
    assert len(epoch_calls) == 1, (
        f"epoch.current() was called {len(epoch_calls)} times for a 2-run request — it "
        "must be fetched once per request, not once per run"
    )


# --- MUST STILL PASS: shape unchanged with zero runs, zero task data -----------

def test_api_dispatcher_shape_is_unchanged_with_no_runs(monkeypatch, client, tmp_path):
    _no_repo_workflow(monkeypatch)
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(dash.epoch, "current", lambda: None)

    data = client.get("/api/dispatcher").get_json()
    assert data == {"configured": False, "workflows": [], "dispatch_hold": None}
