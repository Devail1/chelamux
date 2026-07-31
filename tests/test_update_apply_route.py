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

from chela import dispatcher, update
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


def test_apply_refuses_while_a_dispatched_run_is_in_flight(client, monkeypatch):
    """The brief's guard: a dispatched agent run (claimed/running), not a second click of
    this same route — those are separate hazards with separate tests."""
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=True, behind=3, ahead=0, branch="dev"))
    monkeypatch.setattr(dispatcher, "list_runs",
                        lambda: [{"task_id": "cmx-199-abc12", "status": "running"}])
    calls = []
    monkeypatch.setattr(update, "apply", lambda *a, **k: calls.append("apply"))

    resp = client.post("/api/update/apply")

    assert resp.status_code == 409
    data = resp.get_json()
    assert data["ok"] is False
    assert "cmx-199-abc12" in data["error"]
    # Never queued, never applied — apply() must not run now or later.
    time.sleep(0.05)
    assert calls == []


def test_apply_proceeds_when_no_dispatched_run_is_active(client, monkeypatch):
    """Counterweight to the guard above: a run table that's empty or all-terminal must
    not make the route refuse unconditionally."""
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=True, behind=3, ahead=0, branch="dev"))
    monkeypatch.setattr(dispatcher, "list_runs",
                        lambda: [{"task_id": "cmx-198-old1", "status": "done"},
                                 {"task_id": "cmx-197-old2", "status": "failed"}])
    started = threading.Event()
    monkeypatch.setattr(update, "apply", lambda: (started.set(), update.ApplyResult(
        ok=True, step="done", behind_before=3))[1])

    resp = client.post("/api/update/apply")

    assert resp.status_code == 200
    assert resp.get_json()["started"] is True
    assert started.wait(timeout=2), "update.apply() was never invoked"


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


# --- ⛔ AN UNREADABLE CHECKOUT MUST NEVER RENDER AS HEALTHY -------------------------
#
# CMX-199 exists because "in sync" was printed on a state nobody had checked. The judge
# found the same defect one layer up, in TWO places at once: corrupting `if not status.ok:`
# to `if False and not status.ok:` left the suite green in both `_update_status_payload`
# and `api_update_apply`, so a `commits_behind` that CANNOT ANSWER falls through to the
# `behind == 0` arm — the drawer renders "Up to date" and the route replies "already up to
# date" about a checkout it failed to read.
#
# ⛔ These two surfaces make the SAME promise on the SAME condition, so they are pinned by
# ONE parametrize rather than two hand-written tests. Two sites guarded by a remembered
# rule is how "you applied your own rule everywhere except here" keeps happening.

_UNREADABLE = update.UpdateStatus(ok=False, behind=0, ahead=0, branch="", error="git exploded")


@pytest.mark.parametrize("surface", ["payload", "route"])
def test_an_unreadable_checkout_is_never_reported_as_up_to_date(client, monkeypatch, surface):
    monkeypatch.setattr(update, "commits_behind", lambda *a, **k: _UNREADABLE)
    applied = []
    monkeypatch.setattr(update, "apply", lambda *a, **k: applied.append("apply"))

    if surface == "payload":
        data, status_code = dash._update_status_payload(), None
    else:
        resp = client.post("/api/update/apply")
        data, status_code = resp.get_json(), resp.status_code

    assert data["ok"] is False, "an unreadable checkout reported as OK"
    # ⛔ The VALUE, not just that a key exists: the operator has to be told what broke.
    assert "git exploded" in (data.get("error") or ""), "the read failure never reached the operator"
    # ⛔ The false-green shape this guards: never the behind==0 "nothing to do" arm.
    assert "up to date" not in str(data).lower()
    if status_code is not None:
        assert status_code == 400, "an unreadable checkout must refuse, not 200"
    assert applied == [], "update.apply() ran against a checkout that could not be read"


@pytest.mark.parametrize("surface", ["payload", "route"])
def test_a_readable_checkout_still_succeeds(client, monkeypatch, surface):
    """Counterweight — without it, 'always refuse' satisfies the guard above."""
    monkeypatch.setattr(update, "commits_behind",
                        lambda *a, **k: update.UpdateStatus(ok=True, behind=0, ahead=0, branch="dev"))

    if surface == "payload":
        data = dash._update_status_payload()
        assert data["ok"] is True and data["behind"] == 0
    else:
        resp = client.post("/api/update/apply")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
