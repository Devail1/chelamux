"""POST /api/update/apply — the dashboard's Update control (CMX-199).

`chela doctor` and the hourly notify edge could both SAY the checkout was behind; neither
gave an operator anywhere to click, which is how five merged PRs sat unpulled for a full
day. This route is that click. It must: refuse to start a second run while one is already
in flight, never claim to have started when there is nothing to pull, and never run
`update.apply()` on the request thread (that call may restart THIS process via pm2).
"""

from __future__ import annotations

import threading
import time

import pytest

from chela import update
from chela.dashboard import app as dash


@pytest.fixture
def client():
    return dash.app.test_client()


@pytest.fixture(autouse=True)
def _reset_lock():
    # The lock is process-global (module state) — start and end every test unlocked
    # regardless of what a previous test's background thread did.
    yield
    if dash._update_apply_lock.locked():
        dash._update_apply_lock.release()


def test_apply_refuses_when_already_up_to_date(client, monkeypatch):
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=True, behind=0, ahead=0, branch="dev"))
    calls = []
    monkeypatch.setattr(update, "apply", lambda *a, **k: calls.append("apply"))

    resp = client.post("/api/update/apply")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"ok": True, "started": False, "detail": "already up to date"}
    # Never spawns the background thread when there is nothing to pull.
    time.sleep(0.05)
    assert calls == []


def test_apply_starts_a_background_run_when_behind(client, monkeypatch):
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=True, behind=3, ahead=0, branch="dev"))
    started = threading.Event()
    finished = threading.Event()

    def fake_apply():
        started.set()
        finished.wait(timeout=2)
        return update.ApplyResult(ok=True, step="done", behind_before=3, restarted=["chela-dashboard"])

    monkeypatch.setattr(update, "apply", fake_apply)

    resp = client.post("/api/update/apply")

    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"ok": True, "started": True, "behind": 3}
    # The response returned WITHOUT waiting for apply() to finish — proof this runs off
    # the request thread, which is the entire point (apply() can restart this process).
    assert started.wait(timeout=2), "update.apply() was never invoked"
    finished.set()


def test_apply_refuses_a_second_run_while_one_is_in_flight(client, monkeypatch):
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=True, behind=3, ahead=0, branch="dev"))
    release = threading.Event()

    def fake_apply():
        release.wait(timeout=2)
        return update.ApplyResult(ok=True, step="done", behind_before=3)

    monkeypatch.setattr(update, "apply", fake_apply)

    first = client.post("/api/update/apply")
    assert first.get_json()["started"] is True

    second = client.post("/api/update/apply")
    assert second.status_code == 409
    assert "already running" in second.get_json()["error"]

    release.set()


def test_apply_reports_dirty_tree_refusal_without_pulling(client, monkeypatch):
    """A dirty tree / diverged branch refusal from `update.apply()` must be logged, not
    silently swallowed — but the route's own HTTP contract stays "started: True": the
    refusal itself is `update.apply()`'s job to report (via the log), not this route's,
    since it already returned before apply() ran."""
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=True, behind=1, ahead=0, branch="dev"))
    done = threading.Event()

    def fake_apply():
        done.set()
        return update.ApplyResult(ok=False, step="dirty-check", error="working tree has uncommitted changes")

    monkeypatch.setattr(update, "apply", fake_apply)

    resp = client.post("/api/update/apply")
    assert resp.get_json()["started"] is True
    assert done.wait(timeout=2)


def test_apply_degrades_gracefully_on_a_pip_install(client, monkeypatch):
    def _boom(fetch=True):
        raise update.NotAGitCheckout("not a git checkout")
    monkeypatch.setattr(update, "commits_behind", _boom)

    resp = client.post("/api/update/apply")

    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False
