"""`claude agents --json` status cache — TTL, single-flight, failure safety.

`claude agents --json` is a ~165 MB process. The dashboard polls /api/agents in
bursts (multiple tabs × 30s refresh + 4s terminals tick + SSE deltas), served
concurrently by Flask. Without coalescing, a burst arriving while the command
runs each spawns their own — measured 8 stacked processes. These lock in the two
guards: a short TTL and a single-flight lock, plus the stale-but-safe failure
path (a transient timeout must not blank every status pill).

Exercised with an injected clock (``agent_manager.time``) and a fake
``subprocess.run`` — no live ``claude``.
"""
import json
import subprocess
import threading
import types
from concurrent.futures import ThreadPoolExecutor

import pytest

from chela import agent_manager


class _Clock:
    """A monkeypatchable stand-in for the ``time`` module (only ``.time()``)."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def time(self) -> float:
        return self.t


_RESET = dict(
    ts=0.0, by_pid={}, by_cwd={}, cwd_by_pid={}, down_since=None, escalated=False,
    last_success_ts=0.0, last_warning_ts=0.0,
)


@pytest.fixture(autouse=True)
def _reset_status_cache():
    agent_manager._status_cache.update(**_RESET)
    yield
    agent_manager._status_cache.update(**_RESET)


def _counting_run(payload="[]", returncode=0, counter=None):
    counter = counter if counter is not None else {"n": 0}

    def run(cmd, **kw):
        counter["n"] += 1
        return types.SimpleNamespace(returncode=returncode, stdout=payload, stderr="")

    return run, counter


_ONE_AGENT = json.dumps([{"pid": 4242, "cwd": "/home/x/proj", "status": "busy"}])


# --- TTL ---------------------------------------------------------------------

def test_ttl_coalesces_repeat_calls_within_window(monkeypatch):
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    run, n = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert n["n"] == 1
    assert m["by_pid"] == {4242: "busy"}
    assert m["cwd_by_pid"] == {4242: "/home/x/proj"}
    assert m["by_cwd"] == {"/home/x/proj": "busy"}

    clock.t += 0.1                              # still inside the TTL
    agent_manager.session_status_map()
    assert n["n"] == 1                          # served from cache, no new process

    clock.t += agent_manager._STATUS_TTL + 1.0  # now past the TTL
    agent_manager.session_status_map()
    assert n["n"] == 2


def test_force_bypasses_the_ttl(monkeypatch):
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    run, n = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    agent_manager.session_status_map()
    assert n["n"] == 1
    clock.t += 0.1                              # well within the TTL
    agent_manager.session_status_map(force=True)
    assert n["n"] == 2                          # force ignores the fresh cache


# --- by_cwd honesty (docs/AGENT_IDENTITY.md slice 1) --------------------------
# A cwd is not a session id: two live pids can share one (chela's own single-
# user box: every agent shares $HOME). `by_cwd` must never guess a status for
# an ambiguous cwd — it must omit the key rather than let one pid's status
# silently clobber another's.

def test_by_cwd_disagreement_omits_the_cwd(monkeypatch):
    payload = json.dumps([
        {"pid": 1, "cwd": "/home/x", "status": "busy"},
        {"pid": 2, "cwd": "/home/x", "status": "idle"},
    ])
    run, _n = _counting_run(payload)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert "/home/x" not in m["by_cwd"]


def test_by_cwd_agreement_keeps_the_cwd(monkeypatch):
    """Pins out the rejected "omit whenever >1 pid shares a cwd" rule: two pids
    sharing a cwd that AGREE must still resolve, not just get blanket-omitted."""
    payload = json.dumps([
        {"pid": 1, "cwd": "/home/x", "status": "busy"},
        {"pid": 2, "cwd": "/home/x", "status": "busy"},
    ])
    run, _n = _counting_run(payload)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert m["by_cwd"]["/home/x"] == "busy"


def test_by_cwd_none_status_counts_as_disagreement(monkeypatch):
    payload = json.dumps([
        {"pid": 1, "cwd": "/home/x", "status": "busy"},
        {"pid": 2, "cwd": "/home/x", "status": None},
    ])
    run, _n = _counting_run(payload)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert "/home/x" not in m["by_cwd"]


def test_by_cwd_sole_occupant_is_unaffected(monkeypatch):
    run, _n = _counting_run(_ONE_AGENT)  # single pid at /home/x/proj, status busy
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert m["by_cwd"] == {"/home/x/proj": "busy"}


# --- single-flight -----------------------------------------------------------

def test_single_flight_collapses_a_concurrent_burst(monkeypatch):
    # Real time here (threads actually sleep). A slow fake command gauges how many
    # run at once; the lock must hold that to exactly one.
    stats = {"running": 0, "max_running": 0, "total": 0}
    gauge = threading.Lock()

    def slow(cmd, **kw):
        with gauge:
            stats["running"] += 1
            stats["total"] += 1
            stats["max_running"] = max(stats["max_running"], stats["running"])
        threading.Event().wait(0.2)             # emulate the heavyweight command
        with gauge:
            stats["running"] -= 1
        return types.SimpleNamespace(returncode=0, stdout="[]", stderr="")

    monkeypatch.setattr(agent_manager.subprocess, "run", slow)

    barrier = threading.Barrier(8)

    def hit(_):
        barrier.wait()                          # all eight fire simultaneously
        agent_manager.session_status_map()

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(hit, range(8)))

    assert stats["total"] == 1                  # exactly one subprocess for the burst
    assert stats["max_running"] == 1


def test_non_force_call_never_blocks_while_a_refresh_holds_the_lock(monkeypatch):
    """CMX-179 round-2 fix: start_background_refresh holds `_status_lock` for up to
    `_STATUS_CMD_TIMEOUT` seconds. A non-force caller arriving mid-refresh must NOT
    join that wait — it must serve the cache as-is instead (stale but never blocking),
    or the request-path ceiling grows past what it was before the background thread
    existed. Simulate an in-flight refresh by holding the lock directly."""
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    run, n = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    agent_manager.session_status_map()          # seed a cache
    assert n["n"] == 1
    clock.t += agent_manager._STATUS_TTL + 1.0  # now stale — a free lock would refresh

    agent_manager._status_lock.acquire()        # simulate the background thread mid-refresh
    try:
        done = threading.Event()

        def call():
            agent_manager.session_status_map()  # not force
            done.set()

        t = threading.Thread(target=call)
        t.start()
        blocked = not done.wait(0.5)
    finally:
        agent_manager._status_lock.release()
    t.join(timeout=1.0)

    assert not blocked, "a non-force caller must never block while the lock is held"
    assert n["n"] == 1, "the shut-out caller must not have spawned its own subprocess"


# --- failure safety ----------------------------------------------------------

def test_failure_keeps_last_good_cache(monkeypatch, caplog):
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    run, _n = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert m["by_pid"] == {4242: "busy"}        # seed a good cache

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, agent_manager._STATUS_CMD_TIMEOUT)

    monkeypatch.setattr(agent_manager.subprocess, "run", boom)
    clock.t += agent_manager._STATUS_TTL + 1.0  # past TTL so it actually re-runs
    with caplog.at_level("WARNING"):
        m2 = agent_manager.session_status_map()

    # The timeout did NOT blank the statuses — last-good data is preserved…
    assert m2["by_pid"] == {4242: "busy"}
    # …and the trip was logged (not silently swallowed).
    assert any("timed out" in r.message for r in caplog.records)
    # ts was bumped so we back off for a TTL instead of retry-storming.
    assert clock.t - agent_manager._status_cache["ts"] < agent_manager._STATUS_TTL


def test_nonzero_exit_keeps_last_good_cache(monkeypatch, caplog):
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    run, _n = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)
    agent_manager.session_status_map()

    fail, _ = _counting_run("", returncode=1)
    monkeypatch.setattr(agent_manager.subprocess, "run", fail)
    clock.t += agent_manager._STATUS_TTL + 1.0
    with caplog.at_level("WARNING"):
        m = agent_manager.session_status_map()

    assert m["by_pid"] == {4242: "busy"}        # preserved across a nonzero exit
    assert any("exited 1" in r.message for r in caplog.records)


# --- sustained-failure visibility (CMX-179) -----------------------------------
# A single timeout is a blip and stays at WARNING. An outage that drags on past
# `_STATUS_SUSTAINED_FAILURE_S` escalates ONCE to ERROR — this is what makes a real
# regression (12 days of identical WARNINGs, unnoticed) impossible to miss again.

def test_sustained_failure_escalates_to_error_once(monkeypatch, caplog):
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    fail, _ = _counting_run("", returncode=1)
    monkeypatch.setattr(agent_manager.subprocess, "run", fail)

    with caplog.at_level("WARNING"):
        agent_manager.session_status_map()          # 1st failure: opens the episode
        assert not agent_manager._status_cache["escalated"]
        assert not any(r.levelname == "ERROR" for r in caplog.records)

        clock.t += agent_manager._STATUS_SUSTAINED_FAILURE_S + 5.0
        agent_manager.session_status_map(force=True)  # now past the sustained threshold
        assert agent_manager._status_cache["escalated"]
        errors = [r for r in caplog.records if r.levelname == "ERROR"]
        assert len(errors) == 1
        assert "failing for" in errors[0].message

        clock.t += agent_manager._STATUS_TTL + 1.0
        agent_manager.session_status_map(force=True)  # still down — must NOT re-escalate
        assert len([r for r in caplog.records if r.levelname == "ERROR"]) == 1


def test_recovery_after_sustained_failure_logs_and_resets(monkeypatch, caplog):
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    fail, _ = _counting_run("", returncode=1)
    monkeypatch.setattr(agent_manager.subprocess, "run", fail)

    with caplog.at_level("WARNING"):
        agent_manager.session_status_map()
        clock.t += agent_manager._STATUS_SUSTAINED_FAILURE_S + 5.0
        agent_manager.session_status_map(force=True)
        assert agent_manager._status_cache["escalated"]

        good, _ = _counting_run(_ONE_AGENT)
        monkeypatch.setattr(agent_manager.subprocess, "run", good)
        clock.t += agent_manager._STATUS_TTL + 1.0
        agent_manager.session_status_map(force=True)

    assert agent_manager._status_cache["down_since"] is None
    assert not agent_manager._status_cache["escalated"]
    assert any("recovered after" in r.message for r in caplog.records)


def test_native_status_health_reflects_outage(monkeypatch):
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)

    # ⭐ The exact CMX-179 bug, guarded directly: a cold cache — zero successful
    # refreshes, ever — must NOT report healthy. "no recorded failure" and "never
    # asked" are different states; only the first success may flip `ok` to True.
    assert agent_manager.native_status_health()["ok"] is False

    good, _ = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", good)
    agent_manager.session_status_map()
    assert agent_manager.native_status_health()["ok"] is True

    fail, _ = _counting_run("", returncode=1)
    monkeypatch.setattr(agent_manager.subprocess, "run", fail)
    clock.t += agent_manager._STATUS_TTL + 1.0
    agent_manager.session_status_map(force=True)

    health = agent_manager.native_status_health()
    assert health["ok"] is False
    assert health["down_since"] is not None
    # A real bound, not a tautology: down_for_s must track elapsed wall-clock time
    # since the failure, not just "some non-negative number".
    assert health["down_for_s"] == 0.0
    clock.t += 7.0
    assert agent_manager.native_status_health()["down_for_s"] == 7.0


def test_empty_and_never_populated_is_never_reported_as_healthy(monkeypatch):
    """Same invariant as above, phrased the way the brief states it: with zero
    successful refreshes, the status map is empty AND health.ok is False — the two
    can never disagree (an empty map + ok=True is indistinguishable on screen from a
    genuinely calm, all-idle fleet — the exact failure mode that hid for 12 days)."""
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    assert agent_manager._status_cache["by_pid"] == {}
    assert agent_manager.native_status_health()["ok"] is False


# --- CMX-179: the timeout floor -------------------------------------------------------
# The original regression, guarded directly: the bug was a bare constant with NO floor
# (10.0s, below the measured ~18s warm-start) and nothing that would have caught it
# going back down. This must go RED if the default is ever lowered below 45s again.

def test_default_status_cmd_timeout_has_a_floor():
    assert agent_manager._STATUS_CMD_TIMEOUT >= 45.0


# --- CMX-179: the per-call WARNING is throttled, not silenced -------------------------

def _failure_warnings(caplog):
    """Only the per-call failure WARNING ("... exited N; keeping last status cache") —
    excludes the separate "recovered after" WARNING, which is a different signal."""
    return [r for r in caplog.records if r.levelname == "WARNING" and "exited" in r.message]


def test_warning_throttles_within_an_episode_then_fires_again_after_the_window(monkeypatch, caplog):
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    fail, _ = _counting_run("", returncode=1)
    monkeypatch.setattr(agent_manager.subprocess, "run", fail)

    with caplog.at_level("WARNING"):
        agent_manager.session_status_map()              # 1st failure: always logs
        clock.t += agent_manager._STATUS_TTL + 1.0
        agent_manager.session_status_map(force=True)     # still inside the throttle window
        clock.t += agent_manager._STATUS_TTL + 1.0
        agent_manager.session_status_map(force=True)     # still inside the throttle window
    assert len(_failure_warnings(caplog)) == 1, "K failures inside the throttle window must emit ONE warning"
    caplog.clear()

    with caplog.at_level("WARNING"):
        clock.t += agent_manager._STATUS_WARN_THROTTLE_S + 1.0
        agent_manager.session_status_map(force=True)     # past the throttle window
    assert len(_failure_warnings(caplog)) == 1, "past the throttle window, the next failure must warn again"


def test_warning_fires_again_on_a_fail_to_ok_to_fail_transition(monkeypatch, caplog):
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    fail, _ = _counting_run("", returncode=1)
    good, _ = _counting_run(_ONE_AGENT)

    with caplog.at_level("WARNING"):
        monkeypatch.setattr(agent_manager.subprocess, "run", fail)
        agent_manager.session_status_map()                # 1st failure of episode 1: warns

        monkeypatch.setattr(agent_manager.subprocess, "run", good)
        clock.t += agent_manager._STATUS_TTL + 1.0
        agent_manager.session_status_map(force=True)       # recovers — closes the episode

        monkeypatch.setattr(agent_manager.subprocess, "run", fail)
        clock.t += agent_manager._STATUS_TTL + 1.0
        agent_manager.session_status_map(force=True)       # 1st failure of episode 2: warns again,
                                                             # even though still inside the old
                                                             # throttle window
    assert len(_failure_warnings(caplog)) == 2, "a fail -> ok -> fail transition must emit a fresh warning"


# --- CMX-179: a hung runner is still killed at the timeout -----------------------------

def test_a_hung_runner_is_actually_killed_at_the_configured_timeout(monkeypatch):
    """The timeout is not merely configured somewhere — it must actually reach the
    subprocess call. Assert the real kwarg, not just the resulting error message: a
    fix that drops `timeout=` from the `subprocess.run` call would still raise
    TimeoutExpired if something else killed it, but this catches THAT specific
    regression, not just "it eventually failed"."""
    seen = {}

    def hang(cmd, **kw):
        seen["timeout"] = kw.get("timeout")
        raise subprocess.TimeoutExpired(cmd, kw.get("timeout"))

    monkeypatch.setattr(agent_manager.subprocess, "run", hang)
    ok, detail = agent_manager.probe_native_status_feed()
    assert ok is False
    assert "timed out" in detail
    assert seen["timeout"] == agent_manager._STATUS_CMD_TIMEOUT


# --- probe_native_status_feed --------------------------------------------------

def test_probe_reports_ok_on_success(monkeypatch):
    good, n = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", good)
    ok, detail = agent_manager.probe_native_status_feed()
    assert ok is True
    assert n["n"] == 1


def test_probe_bypasses_the_ttl(monkeypatch):
    """A caller asking "is it alive RIGHT NOW" must not be satisfied by a cache that was
    healthy before an outage started."""
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    good, _ = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", good)
    agent_manager.session_status_map()               # fresh, healthy cache

    fail, n = _counting_run("", returncode=1)
    monkeypatch.setattr(agent_manager.subprocess, "run", fail)
    clock.t += 0.1                                    # well within the TTL
    ok, detail = agent_manager.probe_native_status_feed()
    assert ok is False
    assert n["n"] == 1                                # it really called out, not cache-served
    assert "exited 1" in detail


def test_probe_reports_timeout_detail(monkeypatch):
    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, agent_manager._STATUS_CMD_TIMEOUT)

    monkeypatch.setattr(agent_manager.subprocess, "run", boom)
    ok, detail = agent_manager.probe_native_status_feed()
    assert ok is False
    assert "timed out" in detail


# --- start_background_refresh (CMX-179 objective 2) -------------------------------------
# Real threads here (not the injected clock) — this is exactly the "off the request path"
# behaviour under test: a periodic refresh nobody's request has to wait on.

def test_background_refresh_keeps_the_cache_warm_without_any_request(monkeypatch):
    run, n = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    stop = threading.Event()
    t = agent_manager.start_background_refresh(interval=0.01, stop_event=stop)
    try:
        for _ in range(200):
            if n["n"] >= 3:
                break
            threading.Event().wait(0.01)
        assert n["n"] >= 3, "the background thread must refresh on its own timer"
        # No caller ever touched session_status_map()/force — the cache still filled in.
        assert agent_manager._status_cache["by_pid"] == {4242: "busy"}
    finally:
        stop.set()
        t.join(timeout=2.0)
        assert not t.is_alive(), "stop_event must actually stop the loop"
