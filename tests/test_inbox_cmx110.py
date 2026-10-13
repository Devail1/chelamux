"""CMX-110 — the stuck-agent notice must not cry wolf.

10-13: "🧊 … looks STUCK — no commit and no transcript activity for 20m … — no daemon
running". Both halves were false: the agent was 24 minutes into ``chela task-finished
--self-check-experiments`` (a full pytest per mutation — silent by design), and the daemon
had been up 92 minutes (the tick only looked it up when a marker was pending, so every
stuck notice read "no daemon running"). Locked in here:

  * a live ``chela task-finished`` / ``chela judge sweep`` / pytest in the run's worktree is
    activity — no stuck notice while it runs;
  * past the ceiling it is ONE notice that says exactly that ("task-finished self-check
    running for …"), never "stuck";
  * the daemon's state is MEASURED (state file + pid liveness): up says up, a dead pid says
    "no daemon running", and anything unreadable says "daemon state unknown".
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from chela import capabilities, inbox


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.delenv("CHELA_ACTOR", raising=False)
    monkeypatch.delenv("CHELA_WID", raising=False)
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    monkeypatch.setenv("CHELA_SELFCHECK_ALERT_S", "3600")
    monkeypatch.setattr(inbox, "status_snapshot", lambda: {})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {})
    monkeypatch.setattr(inbox.epoch, "current", lambda: None)
    state = tmp_path / "daemon.json"
    monkeypatch.setattr(capabilities, "state_file", lambda: state)
    return state


def _run(status="running", **over):
    return {"task_id": "T9", "title": "fix the parser", "status": status,
            "branch_name": "cmx-9", "pr_url": "https://github.com/o/r/pull/9",
            "pr_state": "open", **over}


def _queued(kind):
    return [e for e in inbox.load()["queue"] if e["kind"] == kind]


def _publish(state, pid, ts=None):
    state.write_text(json.dumps({"pid": pid, "ts": ts if ts is not None else time.time(),
                                 "capabilities": []}))


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


# --- 1. a running self-check is activity --------------------------------------------------

def test_a_running_self_check_with_a_quiet_transcript_is_not_stuck():
    """🔴 GUARD: 25m of transcript silence while ``chela task-finished`` runs in the worktree
    is the self-check doing its job — no ``run_stuck``."""
    now = 1_000_000.0
    probe = {"T9": {"activity": now - 25 * 60, "markers": {},
                    "selfcheck": {"what": "task-finished self-check", "since": now - 24 * 60}}}
    assert inbox.stuck_events([_run()], inbox._empty(), probe, now) == []


def test_a_self_check_past_the_ceiling_says_exactly_that_once():
    """🔴 GUARD: past the ceiling it is one ``run_selfcheck_long`` naming the command and
    how long it has run — not "STUCK" — and not repeated for the same self-check."""
    store, now = inbox._empty(), 1_000_000.0
    sc = {"what": "task-finished self-check", "since": now - 62 * 60}
    probe = {"T9": {"activity": now - 63 * 60, "markers": {}, "selfcheck": sc}}
    [event] = inbox.stuck_events([_run()], store, probe, now)
    assert event["kind"] == "run_selfcheck_long"
    assert "task-finished self-check running for 1h02m" in event["summary"]
    assert "STUCK" not in event["summary"]
    assert event["payload"]["selfcheck_running_s"] == 62 * 60
    assert inbox.stuck_events([_run()], store, probe, now + 600) == []


def test_a_self_check_under_the_ceiling_is_silent_even_at_the_boundary():
    now = 1_000_000.0
    sc = {"what": "pytest", "since": now - 3599}
    probe = {"T9": {"activity": now - 7200, "markers": {}, "selfcheck": sc}}
    assert inbox.stuck_events([_run()], inbox._empty(), probe, now) == []


def test_the_quiet_clock_restarts_when_the_self_check_ends():
    """🔴 GUARD: a self-check that just finished was activity until a moment ago — the next
    tick (no self-check, transcript still old) must not call the agent stuck at once."""
    store, now = inbox._empty(), 1_000_000.0
    sc = {"what": "task-finished self-check", "since": now - 30 * 60}
    busy = {"T9": {"activity": now - 40 * 60, "markers": {}, "selfcheck": sc}}
    assert inbox.stuck_events([_run()], store, busy, now) == []
    done = {"T9": {"activity": now - 40 * 60, "markers": {}, "selfcheck": None}}
    assert inbox.stuck_events([_run()], store, done, now + 60) == []
    assert [e["kind"] for e in inbox.stuck_events([_run()], store, done, now + 1300)] == [
        "run_stuck"]


@pytest.mark.parametrize("status,stale", [("running", False), ("awaiting_review", True)])
def test_a_queued_selfcheck_notice_rots_once_the_run_stops_running(status, stale):
    now = 1_000_000.0
    probe = {"T9": {"activity": now, "markers": {},
                    "selfcheck": {"what": "pytest", "since": now - 7200}}}
    [event] = inbox.stuck_events([_run()], inbox._empty(), probe, now)
    assert bool(inbox.stale_reason(event, [_run(status)])) is stale


@pytest.mark.parametrize("argv,label", [
    (["/usr/bin/chela", "task-finished", "CMX-1", "--self-check-experiments", "x.json"],
     "task-finished self-check"),
    (["python", "-m", "chela.main", "judge", "sweep"], "judge sweep"),
    (["/venv/bin/chela", "judge", "self-check", "--experiments", "x"], "judge self-check"),
    (["/venv/bin/python3", "-m", "pytest", "-q"], "pytest"),
    (["/venv/bin/pytest", "-q"], "pytest"),
    (["chela", "status"], None),
    (["vim", "task-finished"], None),
])
def test_selfcheck_label(argv, label):
    assert inbox._selfcheck_label(argv) == label


def test_stuck_probes_see_a_live_task_finished_in_the_worktree(tmp_path, monkeypatch):
    """🔴 GUARD: the probe reads the REAL process table — a ``chela task-finished`` whose cwd
    is the run's worktree is found; the same command elsewhere is not."""
    if not os.path.isdir("/proc/self"):
        pytest.skip("needs /proc")
    wt, other = tmp_path / "wt", tmp_path / "other"
    wt.mkdir()
    other.mkdir()
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    monkeypatch.setattr(inbox, "_git_last_commit", lambda w: None)
    argv = [sys.executable, "-c", "import time; time.sleep(60)", "chela", "task-finished", "T9"]
    proc = subprocess.Popen(argv, cwd=wt)
    try:
        deadline = time.time() + 5
        sc = None
        while time.time() < deadline and not sc:
            sc = inbox.stuck_probes([_run(worktree_path=str(wt))])["T9"]["selfcheck"]
        assert sc and sc["what"] == "task-finished self-check"
        assert abs(sc["since"] - time.time()) < 30
        assert inbox.stuck_probes([_run(worktree_path=str(other))])["T9"]["selfcheck"] is None
    finally:
        proc.kill()
        proc.wait()


# --- 2. the daemon's state is measured, and unknown is never "down" -----------------------

def test_daemon_state_measures_the_state_file_and_the_pid(_isolated):
    state = _isolated
    assert capabilities.daemon_state() == ("unknown", None)          # no file: can't tell
    state.write_text("{garbled")
    assert capabilities.daemon_state() == ("unknown", None)
    _publish(state, _dead_pid())
    assert capabilities.daemon_state() == ("down", None)
    _publish(state, os.getpid())
    assert capabilities.daemon_state()[0] == "up"


def _stuck_through_tick(monkeypatch):
    quiet_since = time.time() - 3600
    monkeypatch.setattr(inbox, "stuck_probes", lambda runs: {
        "T9": {"activity": quiet_since, "markers": {}, "selfcheck": None}})
    inbox.tick({}, runs=[_run()])
    [event] = _queued("run_stuck")
    return event


def test_a_stuck_notice_with_a_live_daemon_says_it_is_up(monkeypatch, _isolated):
    """🔴 GUARD: the 10-13 bug — a fresh daemon heartbeat (live pid) must read "daemon up",
    never "no daemon running", on a STUCK notice (no marker pending)."""
    _publish(_isolated, os.getpid(), ts=time.time() - 92 * 60)
    event = _stuck_through_tick(monkeypatch)
    assert "daemon up 1h32m" in event["summary"]
    assert "no daemon running" not in event["summary"]
    assert event["payload"]["daemon_state"] == "up"


def test_an_unreadable_heartbeat_says_unknown_not_down(monkeypatch, _isolated):
    """🔴 GUARD: a state file we cannot read is "daemon state unknown" — never "down"."""
    _isolated.write_text("{garbled")
    event = _stuck_through_tick(monkeypatch)
    assert "daemon state unknown" in event["summary"]
    assert "no daemon running" not in event["summary"]


def test_a_dead_daemon_pid_is_no_daemon_running(monkeypatch, _isolated):
    """🔴 GUARD: "no daemon running" is still said — when it was MEASURED (the pid is gone)."""
    _publish(_isolated, _dead_pid())
    event = _stuck_through_tick(monkeypatch)
    assert "no daemon running" in event["summary"]
    assert event["payload"]["daemon_state"] == "down"


def test_stuck_events_without_a_daemon_reading_never_claim_down():
    """🔴 GUARD: a caller that did not measure the daemon gets "unknown", not "down"."""
    now = 1_000_000.0
    probe = {"T9": {"activity": now - 3600, "markers": {}}}
    [event] = inbox.stuck_events([_run()], inbox._empty(), probe, now)
    assert "daemon state unknown" in event["summary"]
