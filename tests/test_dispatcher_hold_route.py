"""POST /api/dispatcher/pause and /api/dispatcher/resume — the dashboard's Pause/Resume
control for `chela dispatch --pause` (CMX-206).

`chela dispatch --pause` is genuinely operational — it stops the dispatcher from
claiming new tasks so a batch of merges can land without racing a new claim — but until
now the only way to reach it was SSH + the CLI. These routes are a second front door
onto the exact same `chela.hold` file the CLI's `--pause`/`--resume` write, plus the
`dispatch_hold` twin on `/api/dispatcher` that the Board's button reads its state from.
"""

from __future__ import annotations

import pytest

from chela import hold
from chela.dashboard import app as dash


@pytest.fixture
def client():
    return dash.app.test_client()


def _no_repo_workflow(monkeypatch):
    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: None)


def test_pause_takes_a_hold_the_cli_would_also_see(client):
    resp = client.post("/api/dispatcher/pause", json={"reason": "batch merge"})

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["dispatch_hold"]["reason"] == "batch merge"
    # The same file `chela dispatch --hold-status` reads — a second front door onto the
    # SAME hold, not a parallel one.
    held = hold.active()
    assert held is not None and held.reason == "batch merge"


def test_pause_defaults_reason_and_ttl_when_none_given(client):
    resp = client.post("/api/dispatcher/pause")

    assert resp.status_code == 200
    held = hold.active()
    assert held is not None
    assert held.reason == "dashboard"
    assert held.remaining() <= hold.DEFAULT_TTL_SECONDS + 1


def test_pause_rejects_an_unparseable_ttl_without_taking_a_hold(client):
    resp = client.post("/api/dispatcher/pause", json={"ttl": "not-a-duration"})

    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
    # ⛔ A rejected request must not silently take the hold anyway.
    assert hold.active() is None


def test_resume_releases_an_active_hold(client):
    hold.take(reason="rewriting the queue", ttl_seconds=600)

    resp = client.post("/api/dispatcher/resume")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["released"]["reason"] == "rewriting the queue"
    assert hold.active() is None


def test_resume_is_idempotent_when_nothing_is_held(client):
    assert hold.active() is None

    resp = client.post("/api/dispatcher/resume")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["released"] is None


def test_dispatcher_payload_carries_the_hold_for_the_board_button(client, monkeypatch):
    """The Board's Pause/Resume button reads its state off `/api/dispatcher` (the poll it
    already runs every 30s), not a second endpoint — this pins that field exists and
    reflects reality on both sides of pause/resume."""
    _no_repo_workflow(monkeypatch)

    assert client.get("/api/dispatcher").get_json()["dispatch_hold"] is None

    client.post("/api/dispatcher/pause", json={"reason": "batch merge"})
    data = client.get("/api/dispatcher").get_json()
    assert data["dispatch_hold"] is not None
    assert data["dispatch_hold"]["reason"] == "batch merge"

    client.post("/api/dispatcher/resume")
    assert client.get("/api/dispatcher").get_json()["dispatch_hold"] is None


def test_pause_does_not_touch_the_task_queue(client, monkeypatch):
    """The hold pauses CLAIMS only — CMX-53's rule (see chela/hold.py). Reconciliation
    (and therefore anything the /api/dispatcher payload reports about open tasks) must be
    completely unaffected by taking the hold from the dashboard."""
    _no_repo_workflow(monkeypatch)
    before = client.get("/api/dispatcher").get_json()

    client.post("/api/dispatcher/pause")
    after = client.get("/api/dispatcher").get_json()

    assert after["configured"] == before["configured"]
    assert after["workflows"] == before["workflows"]
