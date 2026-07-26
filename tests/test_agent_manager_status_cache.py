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


_RESET = dict(ts=0.0, by_pid={}, by_cwd={}, cwd_by_pid={}, down_since=None, escalated=False)


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

    clock.t += 1.0                              # still inside the 2s TTL
    agent_manager.session_status_map()
    assert n["n"] == 1                          # served from cache, no new process

    clock.t += 2.0                              # now past the TTL
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
    clock.t += 5.0                              # past TTL so it actually re-runs
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
    clock.t += 5.0
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
    assert health["down_for_s"] == 0.0 or health["down_for_s"] >= 0.0
    assert health["down_since"] is not None


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
