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

from chela import agent_manager, sessions


class _Clock:
    """A monkeypatchable stand-in for the ``time`` module (only ``.time()``)."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def time(self) -> float:
        return self.t


_RESET = dict(
    ts=0.0, by_pid={}, by_cwd={}, cwd_by_pid={}, session_by_pid={}, started_by_pid={},
    down_since=None, escalated=False, last_success_ts=0.0, last_warning_ts=0.0,
)


@pytest.fixture(autouse=True)
def _reset_status_cache():
    agent_manager._status_cache.update(**_RESET)
    yield
    agent_manager._status_cache.update(**_RESET)


@pytest.fixture(autouse=True)
def _no_real_proc_started(monkeypatch):
    """CMX-219: `_refresh_status_locked` now calls `sessions.proc_started` per live pid.
    None of the pids in this suite's canned payloads are real processes, so without this a
    fast-path /proc miss falls to the `ps` subprocess fallback on every refresh — a real,
    unstubbed subprocess call this suite otherwise takes pains to avoid. A test that wants
    started_by_pid populated overrides this explicitly."""
    monkeypatch.setattr(sessions, "proc_started", lambda pid: None)


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


# --- session_by_pid (CMX-184) --------------------------------------------------
# `claude agents --json` reports a `sessionId` for every pid it lists, alongside
# `status`/`cwd` — the refresh used to parse it and throw it away. This is the tier
# `chela.sessions.resolve_window` needed for a window that fired no hook and was never
# --resume'd: chela.sessions.session_and_cwd_for_pid reads it as a pure cache lookup,
# never a subprocess call of its own.
#
# The feed's `startedAt` field is intentionally NOT captured. It looked like a natural
# bound (pid recycling guard) and was tried as one, but it is the SESSION's start time,
# not the process's fork time — measured against real /proc start times on a live box it
# disagreed by up to 113 days, in both directions, on processes nobody recycled. `cwd` is
# the bound that actually means the same thing on both sides — see chela/sessions.py.

_WITH_SESSION = json.dumps([{
    "pid": 1339280, "cwd": "/home/liavedunix", "status": "idle",
    "sessionId": "aaef8ff8-9b43-4416-a745-825a694e031a", "startedAt": 1785073750696,
}])


def test_refresh_captures_sessionid_per_pid(monkeypatch):
    run, _n = _counting_run(_WITH_SESSION)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert m["session_by_pid"] == {1339280: "aaef8ff8-9b43-4416-a745-825a694e031a"}
    # cwd_by_pid already existed pre-CMX-184; session_and_cwd_for_pid reads both maps.
    assert m["cwd_by_pid"] == {1339280: "/home/liavedunix"}
    # The feed's own `startedAt` (in the payload above) is still not captured — see the
    # comment on _status_cache's `started_by_pid` key. Absent a real /proc read (stubbed to
    # None for this pid by the autouse fixture), started_by_pid stays empty.
    assert m["started_by_pid"] == {}


# --- started_by_pid (CMX-219) --------------------------------------------------
# `chela.sessions` tier 3 needs a SECOND witness — independent of the feed's own cwd —
# to tell "this pid legitimately cd'ed" apart from "this pid was recycled". A pid's own
# /proc start time, read by this same refresh, is that witness (not the feed's
# `startedAt`, which is the session's start time and disagrees with /proc's process fork
# time by up to 113 days — see the `session_by_pid` comment above).

def test_refresh_captures_started_time_per_pid(monkeypatch):
    monkeypatch.setattr(sessions, "proc_started", lambda pid: 1785074373.8 if pid == 1339280 else None)
    run, _n = _counting_run(_WITH_SESSION)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert m["started_by_pid"] == {1339280: 1785074373.8}


def test_started_for_pid_reads_the_captured_map(monkeypatch):
    monkeypatch.setattr(sessions, "proc_started", lambda pid: 1785074373.8 if pid == 1339280 else None)
    run, _n = _counting_run(_WITH_SESSION)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)
    agent_manager.session_status_map()

    assert agent_manager.started_for_pid(1339280) == 1785074373.8


def test_started_for_pid_is_none_for_an_unknown_or_absent_pid(monkeypatch):
    monkeypatch.setattr(sessions, "proc_started", lambda pid: 1785074373.8 if pid == 1339280 else None)
    run, _n = _counting_run(_WITH_SESSION)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)
    agent_manager.session_status_map()

    assert agent_manager.started_for_pid(99999) is None
    assert agent_manager.started_for_pid(None) is None


def test_a_pid_whose_proc_start_time_cannot_be_read_is_absent_from_started_by_pid(monkeypatch):
    """A dead-between-listing-and-read pid, or a host without /proc and no `ps` on PATH —
    unknown is not a pass; the pid simply carries no entry, same as `cwd_by_pid` does."""
    run, _n = _counting_run(_WITH_SESSION)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert 1339280 not in m["started_by_pid"]


def test_started_for_pid_never_spawns_a_subprocess(monkeypatch):
    """Same budget guard as `session_and_cwd_for_pid` (CMX-184/CMX-219):
    `sessions.resolve_window` runs on the hook path with an agent BLOCKED on it, and
    `started_for_pid` is documented as a pure cache read — it must never itself trigger
    `claude agents --json`, cold cache or not."""
    calls = []

    def boom(cmd, **kw):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(agent_manager.subprocess, "run", boom)

    assert agent_manager.started_for_pid(1339280) is None
    assert calls == [], "started_for_pid must never spawn a subprocess"


def test_session_and_cwd_for_pid_reads_the_captured_map(monkeypatch):
    run, _n = _counting_run(_WITH_SESSION)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)
    agent_manager.session_status_map()

    sid, cwd = agent_manager.session_and_cwd_for_pid(1339280)
    assert sid == "aaef8ff8-9b43-4416-a745-825a694e031a"
    assert cwd == "/home/liavedunix"


def test_session_and_cwd_for_pid_is_none_none_for_an_unknown_or_absent_pid(monkeypatch):
    run, _n = _counting_run(_WITH_SESSION)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)
    agent_manager.session_status_map()

    assert agent_manager.session_and_cwd_for_pid(99999) == (None, None)
    assert agent_manager.session_and_cwd_for_pid(None) == (None, None)


def test_a_pid_with_no_sessionid_in_the_payload_is_absent_from_the_map(monkeypatch):
    """An entry lacking `sessionId` must not poison the map with a fabricated key —
    pins the loop's `isinstance` guard, not just dict.get's default behaviour."""
    run, _n = _counting_run(_ONE_AGENT)  # _ONE_AGENT carries no sessionId
    monkeypatch.setattr(agent_manager.subprocess, "run", run)

    m = agent_manager.session_status_map()
    assert 4242 not in m["session_by_pid"]


def test_session_and_cwd_for_pid_never_spawns_a_subprocess(monkeypatch):
    """The tier-3 budget guard (docs/AGENT_IDENTITY.md, CMX-184): `sessions.resolve_window`
    runs on the hook path with an agent BLOCKED on it and must never trigger `claude
    agents --json` itself — this accessor is a pure read of whatever the cache already
    holds, cold or not."""
    calls = []

    def boom(cmd, **kw):
        calls.append(cmd)
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(agent_manager.subprocess, "run", boom)

    assert agent_manager.session_and_cwd_for_pid(1339280) == (None, None)
    assert calls == [], "session_and_cwd_for_pid must never spawn a subprocess"


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


def test_missing_claude_binary_logs_a_warning_not_a_traceback(monkeypatch, caplog):
    # Real incident, 2026-07-27: a box with no `claude` on PATH (CI, a fresh dev
    # machine before the CLI is installed) hit this via `subprocess.run` and fell
    # into the catch-all `except Exception`, which `log.exception`s a full traceback
    # — tripping test_graceful_shutdown.py's "daemon shuts down with no traceback"
    # invariant even though nothing here actually crashed. FileNotFoundError is an
    # expected, quiet failure mode everywhere else in this module; this call must
    # treat it the same way — a plain WARNING, no traceback.
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    run, _n = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)
    agent_manager.session_status_map()

    def boom(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory", "claude")

    monkeypatch.setattr(agent_manager.subprocess, "run", boom)
    clock.t += agent_manager._STATUS_TTL + 1.0
    with caplog.at_level("WARNING"):
        m = agent_manager.session_status_map()

    assert m["by_pid"] == {4242: "busy"}        # preserved across the missing binary
    assert any(
        r.levelname == "WARNING" and "FileNotFoundError" in r.message
        for r in caplog.records
    )
    assert not any(r.exc_info for r in caplog.records), "must not log a traceback"


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


# --- native_status_ever_fetched (CMX-189) -------------------------------------

def test_native_status_ever_fetched_is_false_until_the_first_success(monkeypatch):
    """The exact distinction `chela.sessions.resolve_window`'s tier 3 needs: a cache
    that has never completed a fetch (down_since is also None here — no failure has
    been recorded either, this cache has simply never been asked) must read as
    NOT-fetched, not conflated with a healthy-but-quiet one."""
    assert agent_manager.native_status_ever_fetched() is False

    good, _ = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", good)
    agent_manager.session_status_map()

    assert agent_manager.native_status_ever_fetched() is True


def test_native_status_ever_fetched_stays_true_through_a_later_outage(monkeypatch):
    """A later failed refresh must not un-ring the bell: the process HAS fetched
    successfully before, so a pid absent from the (now stale) cache is still "the feed
    answered and doesn't have it", not "never asked" — `native_status_health().ok` is
    what tracks the outage itself; this only tracks whether a fetch ever completed."""
    clock = _Clock(1000.0)
    monkeypatch.setattr(agent_manager, "time", clock)
    good, _ = _counting_run(_ONE_AGENT)
    monkeypatch.setattr(agent_manager.subprocess, "run", good)
    agent_manager.session_status_map()
    assert agent_manager.native_status_ever_fetched() is True

    fail, _ = _counting_run("", returncode=1)
    monkeypatch.setattr(agent_manager.subprocess, "run", fail)
    clock.t += agent_manager._STATUS_TTL + 1.0
    agent_manager.session_status_map(force=True)

    assert agent_manager.native_status_health()["ok"] is False   # the outage IS visible
    assert agent_manager.native_status_ever_fetched() is True    # but this stays True


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


def test_started_for_pid_returns_the_CACHED_value_not_a_live_proc_read(monkeypatch):
    """🔴 The two existing guards cannot tell a cache read from a live one.

    `..._reads_the_captured_map` leaves its `sessions.proc_started` stub installed, so a
    live read returns the very same number the cache holds and the assertion passes either
    way. `..._never_spawns_a_subprocess` only watches `agent_manager.subprocess`, and a
    live `/proc` read spawns nothing at all when `/proc` is readable.

    This one moves the live answer AFTER the refresh: the cache holds the value captured at
    refresh time, so `started_for_pid` must keep returning that, not the new one.

    ⛔ Why it matters beyond tidiness: `resolve_window` compares this against
    `pane.started`, which `sessions._load_panes` obtained from the SAME `proc_started()` on
    the SAME pid. A live read therefore makes `same_process` True for EVERY live pid, and
    every cwd mismatch is trusted — the recycling guard silently becomes a no-op.
    """
    at_refresh = 1785074373.8
    monkeypatch.setattr(sessions, "proc_started", lambda pid: at_refresh if pid == 1339280 else None)
    run, _n = _counting_run(_WITH_SESSION)
    monkeypatch.setattr(agent_manager.subprocess, "run", run)
    agent_manager.session_status_map()

    # The pid is recycled: /proc would now answer with a different process's start time.
    monkeypatch.setattr(sessions, "proc_started", lambda pid: 9999999999.0)

    assert agent_manager.started_for_pid(1339280) == at_refresh, (
        "started_for_pid returned the LIVE /proc value, not the one captured at refresh — "
        "that makes same_process true for every live pid and defeats the recycling guard"
    )


def test_started_for_pid_never_calls_proc_started_at_all(monkeypatch):
    """The budget half, pinned on the function itself rather than on `subprocess`:
    `proc_started` falls back to a `ps` subprocess when `/proc` is unreadable, and
    `resolve_window` runs on the hook path with an agent BLOCKED on it."""
    calls = []
    monkeypatch.setattr(sessions, "proc_started", lambda pid: calls.append(pid))

    agent_manager.started_for_pid(1339280)

    assert calls == [], "started_for_pid must read the cache, never call proc_started"
