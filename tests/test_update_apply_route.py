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


def _wait_for_release_then_clear(lock, timeout=2):
    """The `_reset_lock` teardown body, factored out so `test_teardown_waits_for_the_leaked_
    threads_own_release` below can exercise this exact code — not a copy of it — directly
    against a throwaway lock.

    It must be a WAIT, not a check-then-release: `_run`'s `finally: release()` fires on its
    own background thread, asynchronously, after the test body that started it has already
    returned (several tests in this file set their release/finished event and move on
    without joining that thread). A bare `if locked(): release()` races that thread —
    observed flaky on PR #287 (test_apply_refuses_a_second_run_while_one_is_in_flight, green
    in isolation, red only in the full file): a thread LEAKED from an earlier test would
    reach its own `release()` mid-way through a LATER test, after that later test had
    legitimately re-acquired the lock for itself — freeing it early and turning an expected
    409 ("already running") into a 200, or, if the earlier test's teardown forced a release
    while the leaked thread still owned the lock, the leaked thread's own later `release()`
    lands on an already-free lock and raises `RuntimeError: release unlocked lock`.

    `acquire(timeout=...)` blocks until the background thread's own release() lands and
    hands the lock back free — no polling granularity, no spin, and it distinguishes "the
    thread released it" (we now hold it, so drop it) from "the thread is genuinely hung"
    (still held after the timeout, so force it as a last resort) instead of inferring the
    latter from a bare deadline.
    """
    if lock.acquire(timeout=timeout):
        lock.release()
    else:
        lock.release()


@pytest.fixture(autouse=True)
def _reset_lock():
    # The lock is process-global (module state) — start and end every test unlocked
    # regardless of what a previous test's background thread did. See
    # `_wait_for_release_then_clear` for why this must be a wait, not a check-then-release.
    yield
    _wait_for_release_then_clear(dash._update_apply_lock)


def test_teardown_waits_for_the_leaked_threads_own_release():
    """Regression for `_wait_for_release_then_clear` itself (the teardown, not the route):
    a thread that still owns the lock when a test body returns must have ITS OWN release()
    observed by the next teardown, not raced. Reproduces the exact interleaving that
    produced `RuntimeError: release unlocked lock` on a full-file run — sequenced with an
    `Event`, not timing, so it is deterministic on every run.

    The OLD teardown (`if locked(): release()`) is reproduced inline below purely as the
    counterfactual this test is pinned against: run it, and the leaked thread's own release
    (which fires only once we set `release_now`, guaranteed after the old teardown already
    ran while the thread was still blocked on the event) lands on an already-freed lock —
    a double release, `RuntimeError`. `_wait_for_release_then_clear` — the actual code the
    real fixture calls — is run against the same interleaving directly below it and must
    end the lock free with no error, proving it does not race the thread.
    """

    def leak_a_thread(lock, release_now, errors):
        def _run():
            release_now.wait(timeout=2)
            try:
                lock.release()  # stands in for `_run`'s `finally: release()`
            except RuntimeError as e:
                errors.append(e)

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return t

    # --- counterfactual: the OLD check-then-act teardown races the leaked thread ---
    old_lock = threading.Lock()
    old_lock.acquire()
    old_release_now = threading.Event()
    old_errors = []
    old_thread = leak_a_thread(old_lock, old_release_now, old_errors)

    assert old_lock.locked(), "thread must still own the lock when the old teardown runs"
    if old_lock.locked():  # the old, buggy teardown body
        old_lock.release()
    old_release_now.set()  # only now does the leaked thread run its own release
    old_thread.join(timeout=2)
    assert old_errors, "the old teardown should have raced the thread into a double release"
    assert not old_lock.locked()

    # --- the real fix: `_wait_for_release_then_clear` waits for that same release instead ---
    new_lock = threading.Lock()
    new_lock.acquire()
    new_release_now = threading.Event()
    new_errors = []
    new_thread = leak_a_thread(new_lock, new_release_now, new_errors)
    new_release_now.set()  # let the leaked thread release on its own schedule

    _wait_for_release_then_clear(new_lock)
    new_thread.join(timeout=2)
    assert not new_errors, "the fixed teardown must not race the leaked thread's own release"
    assert not new_lock.locked(), "the lock must end free"


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


def test_apply_never_fetches(client, monkeypatch):
    """`api_update_apply`'s own docstring/comment promise the same 'never a network call'
    guarantee as `_update_status_payload` (see test_settings_status.py::
    test_update_payload_never_fetches) — the checkout-behind check that gates whether the
    route even considers pulling must read only the local remote-tracking ref. Every other
    test in this file fakes `commits_behind` with a `fetch=True` DEFAULT or `lambda *a,
    **k:`, both of which accept (and silently discard) whatever value `fetch` is called
    with, so none of them would catch this route calling `commits_behind(fetch=True)`
    instead — a `fetch` kwarg with no default is what actually forces the call site to
    pass one, and recording it is what proves which value it passed."""
    calls = []

    def fake_commits_behind(repo=None, *, fetch):
        calls.append(fetch)
        return update.UpdateStatus(ok=True, behind=0, ahead=0, branch="dev")

    monkeypatch.setattr(update, "commits_behind", fake_commits_behind)
    client.post("/api/update/apply")
    assert calls == [False]


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


def test_apply_refusal_is_logged_not_reported_as_a_success(client, monkeypatch, caplog):
    """The log line is the ONLY place a refused `update.apply()` outcome ever surfaces — the
    route already replied `started: True` before apply() ran (see the test above). Judge
    round 5 (PR #260) found `if not result.ok:` corrupted to `if False and not result.ok:`
    left the suite green: nothing pinned that the refusal path logs at all, only that
    `apply()` ran."""
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=True, behind=1, ahead=0, branch="dev"))
    done = threading.Event()

    def fake_apply():
        done.set()
        return update.ApplyResult(ok=False, step="dirty-check", error="working tree has uncommitted changes")

    monkeypatch.setattr(update, "apply", fake_apply)

    with caplog.at_level("ERROR", logger=dash.log.name):
        client.post("/api/update/apply")
        assert done.wait(timeout=2)
        time.sleep(0.05)   # the log call happens in the background thread, after the response

    refusals = [r for r in caplog.records if "refused" in r.message]
    assert refusals, "a refused update.apply() was never logged as a refusal"
    assert "dirty-check" in refusals[0].message
    assert "working tree has uncommitted changes" in refusals[0].message
    # Counterweight: the success path must NOT also log as a refusal (else the assertion
    # above would pass for any log call regardless of outcome).
    successes = [r for r in caplog.records if "applied" in r.message]
    assert not successes


def test_apply_lock_is_released_once_the_run_finishes(client, monkeypatch):
    """`_update_apply_lock` is non-reentrant on purpose — a second click mid-run would race
    the same working tree — but it must be RELEASED once that run ends, or the control
    becomes one-shot for the life of the process. Judge round 5 (PR #260) found
    `_update_apply_lock.release()` corrupted to `pass` left the suite green: the only
    existing in-flight test (`test_apply_refuses_a_second_run_while_one_is_in_flight`)
    never lets the first run actually FINISH before posting again."""
    monkeypatch.setattr(update, "commits_behind",
                        lambda fetch=True: update.UpdateStatus(ok=True, behind=1, ahead=0, branch="dev"))
    entered = threading.Event()

    def fake_apply():
        entered.set()
        return update.ApplyResult(ok=True, step="done", behind_before=1)

    monkeypatch.setattr(update, "apply", fake_apply)

    first = client.post("/api/update/apply")
    assert first.get_json()["started"] is True
    assert entered.wait(timeout=2), "update.apply() was never invoked"

    # `_run`'s `finally` releases the lock immediately after apply() returns (fake_apply
    # above does no work of its own) — poll rather than guess a fixed sleep.
    deadline = time.monotonic() + 2
    while dash._update_apply_lock.locked() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert not dash._update_apply_lock.locked(), "the background run never released the lock"

    second = client.post("/api/update/apply")
    assert second.status_code == 200, "the lock was never handed back after the first run ended"
    assert second.get_json()["started"] is True


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
    monkeypatch.setattr(update, "services_running_stale_code",
                        lambda *a, **k: update.ServiceFreshness(ok=True, stale=[]))

    if surface == "payload":
        data = dash._update_status_payload()
        assert data["ok"] is True and data["behind"] == 0
    else:
        resp = client.post("/api/update/apply")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
