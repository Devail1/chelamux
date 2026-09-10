"""``chela wait`` — block until a delegated agent/task reaches done/blocked.

Issue #456. Exercises the two things this file actually adds (target resolution +
event-log polling with a deadline) and, above all, the counterweight guard: a wait on a
window id must NOT resolve using a DIFFERENT session's activity after a tmux restart
reissues that id — ``inbox.agent_events`` already refuses to attribute the stranger's
status to the old watch (it emits ``watch_epoch_lost`` instead), and this file's job is
to treat that as its own outcome rather than silently reading it as done/blocked.

Pure: tmux/`claude agents --json`/dispatcher DB are all stubbed, so no live session or
real ``~/.chela`` is touched (autouse isolation in ``tests/conftest.py``).
"""
from __future__ import annotations

import threading
import time

import pytest

from chela import dispatcher, event_log, inbox, wait

ORCH = "@1"
AGENT = "@2"


@pytest.fixture(autouse=True)
def store_file(monkeypatch):
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)


@pytest.fixture(autouse=True)
def no_session_identity(monkeypatch):
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "wid_for_session", lambda sid, pane_map=None: None)


@pytest.fixture(autouse=True)
def no_transcript_evidence(monkeypatch):
    """No real transcript exists for these synthetic wids — never let a test reach one
    on disk (mirrors ``tests/test_inbox.py``'s identically-named fixture)."""
    monkeypatch.setattr(inbox.sessions, "transcript_for_window", lambda wid: None)


@pytest.fixture(autouse=True)
def no_real_tmux_epoch(monkeypatch):
    """``epoch.current()`` shells out to real tmux — pin it to a fixed, known epoch so
    every test's watches are unambiguously stamped, instead of depending on whether this
    box happens to have a tmux server running."""
    monkeypatch.setattr(inbox.epoch, "current", lambda: "TEST-EPOCH")


@pytest.fixture
def windows(monkeypatch):
    live = {ORCH: "orchestrator", AGENT: "cmx-9"}
    monkeypatch.setattr(wait.discovery, "get_windows_by_id", lambda: dict(live))
    return live


def _statuses(monkeypatch, mapping):
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: dict(mapping))


def _tick(monkeypatch, runs=None, **status):
    """One inbox.tick() with the given statuses — how events actually get appended."""
    _statuses(monkeypatch, status)
    return inbox.tick({wid: inbox.BUSY for wid in status}, runs=runs or [])


def _emit_run_event(run):
    """Append whatever ``inbox.run_events`` currently says about ``run`` — the same two
    calls the daemon's own tick makes (``inbox.py`` `tick()`), without needing to stub
    the agent-status/window plumbing that ``agent_events`` alone cares about."""
    events, _ = inbox.run_events([run], {})
    for ev in events:
        event_log.from_inbox(ev)


def _run(task_id="T9", status="running", **over):
    return {"task_id": task_id, "title": "fix the parser", "status": status,
            "window_name": "cmx-9", "window_id": None, "window_epoch": None,
            "pr_url": None, "judge_state": "", **over}


# --- target resolution -----------------------------------------------------------

def test_resolve_target_wid_forms():
    assert wait.resolve_target("@12") == ("wid", "@12")
    assert wait.resolve_target("12") == ("wid", "@12")


def test_resolve_target_task_id():
    assert wait.resolve_target("21e75bc3d86c") == ("task", "21e75bc3d86c")


# --- wid targets: done / blocked -------------------------------------------------

def test_wait_wid_done_fires_on_a_clean_finish_not_just_a_failure(windows, monkeypatch):
    # issue #456 Problem 2: a CLEAN completion must satisfy `--until done` exactly like a
    # failed one — `finished` carries no verdict of its own.
    inbox.watch(AGENT, "do the thing", by=ORCH)
    monkeypatch.setattr(inbox, "IDLE_CONFIRM_SECONDS", 0)

    def deliver_finished():
        time.sleep(0.05)
        _tick(monkeypatch, **{ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    t = threading.Thread(target=deliver_finished)
    t.start()
    result = wait.wait_for(AGENT, wait.DONE, timeout=5)
    t.join()

    assert result["ok"] is True
    assert result["state"] == "done"
    assert result["event"]["type"] == "finished"


def test_wait_wid_blocked_is_satisfied_immediately_if_already_waiting(windows, monkeypatch):
    inbox.watch(AGENT, "", by=ORCH)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.WAITING})

    result = wait.wait_for(AGENT, wait.BLOCKED, timeout=1)

    assert result["ok"] is True
    assert result["state"] == "blocked"


def test_wait_wid_blocked_fires_on_the_busy_to_waiting_edge(windows, monkeypatch):
    inbox.watch(AGENT, "", by=ORCH)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    def deliver_blocked():
        time.sleep(0.05)
        _tick(monkeypatch, **{ORCH: inbox.IDLE, AGENT: inbox.WAITING})

    t = threading.Thread(target=deliver_blocked)
    t.start()
    result = wait.wait_for(AGENT, wait.BLOCKED, timeout=5)
    t.join()

    assert result["ok"] is True
    assert result["state"] == "blocked"
    assert result["event"]["type"] == "blocked"


def test_wait_wid_auto_watches_an_unwatched_window(windows, monkeypatch):
    # No prior `chela watch` — `chela wait` registers one itself, same as a dispatch does.
    assert AGENT not in inbox.watches()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.WAITING})

    result = wait.wait_for(AGENT, wait.BLOCKED, timeout=1)

    assert result["ok"] is True
    assert AGENT in inbox.watches()


def test_wait_wid_times_out_when_nothing_happens(windows, monkeypatch):
    inbox.watch(AGENT, "", by=ORCH)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    result = wait.wait_for(AGENT, wait.DONE, timeout=0.3)

    assert result["ok"] is False
    assert result["state"] == "timeout"


def test_wait_wid_not_live_is_an_error_not_a_guess(monkeypatch):
    monkeypatch.setattr(wait.discovery, "get_windows_by_id", lambda: {ORCH: "orchestrator"})

    result = wait.wait_for("@99", wait.DONE, timeout=0.1)

    assert result["ok"] is False
    assert result["state"] == "error"


# --- issue #478: the poll cadence itself is guarded, not just the outcome -------

def test_wait_wid_done_polls_at_a_bounded_cadence(windows, monkeypatch):
    """issue #478: raising POLL_INTERVAL to a `sleep 30/60` value must go RED — nothing
    in the suite previously depended on the constant. Counterweight: this asserts the
    OBSERVED interval `_poll` actually sleeps for, not the literal `POLL_INTERVAL == 0.5`
    (pinning the literal would pass for any implementation that keeps the constant but
    ignores it). No real multi-second wait either way: `time.sleep` is intercepted so
    each requested duration is recorded and bounded directly, never derived from a
    `time.time()` delta — see the guard against CI flake in the issue.
    """
    inbox.watch(AGENT, "do the thing", by=ORCH)
    monkeypatch.setattr(inbox, "IDLE_CONFIRM_SECONDS", 0)
    sleeps: list[float] = []
    real_sleep = time.sleep
    monkeypatch.setattr(wait.time, "sleep", lambda d: (sleeps.append(d), real_sleep(0.01)))

    def deliver_finished():
        real_sleep(0.05)
        _tick(monkeypatch, **{ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    t = threading.Thread(target=deliver_finished)
    t.start()
    result = wait.wait_for(AGENT, wait.DONE, timeout=5)
    t.join()

    assert result["ok"] is True
    assert sleeps, "expected _poll to sleep at least once before the event landed"
    assert all(d <= 2.0 for d in sleeps), (
        f"poll interval too coarse to be event-driven, not a disguised sleep loop: {sleeps}")


def test_wait_wid_done_still_resolves_correctly_at_a_slower_poll_interval(windows, monkeypatch):
    """Paired accept case for #478: a deliberately SLOWED interval (not the shipped 0.5s)
    must still produce the correct outcome — only when it fires moves, not whether it
    fires correctly. A guard that goes red for ANY interval change, not just a raised
    one, would be pinning a literal instead of the cadence property that matters.
    """
    inbox.watch(AGENT, "do the thing", by=ORCH)
    monkeypatch.setattr(inbox, "IDLE_CONFIRM_SECONDS", 0)
    monkeypatch.setattr(wait, "POLL_INTERVAL", 1.7)  # deliberately not the shipped 0.5
    real_sleep = time.sleep
    monkeypatch.setattr(wait.time, "sleep", lambda d: real_sleep(0.01))

    def deliver_finished():
        real_sleep(0.05)
        _tick(monkeypatch, **{ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    t = threading.Thread(target=deliver_finished)
    t.start()
    result = wait.wait_for(AGENT, wait.DONE, timeout=5)
    t.join()

    assert result["ok"] is True
    assert result["state"] == "done"
    assert result["event"]["type"] == "finished"


# --- the counterweight guard: a wid-reuse must NOT resolve the old wait ---------

def test_wait_wid_does_not_resolve_when_the_wid_is_reissued_to_a_stranger(
        windows, monkeypatch):
    """issue #456's guard, verbatim: drive a restart that reissues the SAME wid to a
    different session while a wait is outstanding, and assert it does NOT resolve as
    done/blocked for the original dispatch — it must come back `unknown` instead."""
    inbox.watch(AGENT, "do the thing", by=ORCH)  # stamped with the fixture's "TEST-EPOCH"

    def restart_and_reissue():
        time.sleep(0.05)
        # The tmux server restarted: a fresh epoch, and AGENT now names a STRANGER who
        # is busy→idle exactly like a real finish would look.
        monkeypatch.setattr(inbox.epoch, "current", lambda: "STRANGER-EPOCH")
        monkeypatch.setattr(inbox, "IDLE_CONFIRM_SECONDS", 0)
        _tick(monkeypatch, **{ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    t = threading.Thread(target=restart_and_reissue)
    t.start()
    result = wait.wait_for(AGENT, wait.DONE, timeout=5)
    t.join()

    assert result["state"] == "unknown"
    assert result["ok"] is False
    assert result["event"]["type"] == wait.EPOCH_LOST_KIND


# --- task targets ------------------------------------------------------------

def test_wait_task_done_is_satisfied_immediately_by_a_clean_judge_verdict(monkeypatch):
    # issue #456 Problem 2 again, for the task path: `run_judge_clean` — not a failure —
    # is exactly what `--until done` must catch.
    run = _run(status="awaiting_review", judge_state="clean", pr_url="https://x/pull/9")
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [run])

    result = wait.wait_for("T9", wait.DONE, timeout=1)

    assert result["ok"] is True
    assert result["state"] == "done"
    assert result["event"]["type"] == "run_judge_clean"


def test_wait_task_done_fires_on_a_fresh_failure_transition(monkeypatch):
    run = _run(status="running")
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [run])

    def fail_it():
        time.sleep(0.05)
        run["status"] = "failed"
        run["last_error"] = "boom"
        _emit_run_event(run)

    t = threading.Thread(target=fail_it)
    t.start()
    result = wait.wait_for("T9", wait.DONE, timeout=5)
    t.join()

    assert result["ok"] is True
    assert result["event"]["type"] == "run_failed"


def test_wait_task_changes_requested_is_neither_done_nor_blocked(monkeypatch):
    # Rework is automatic (the dispatcher's own next tick re-spawns it) — it is still
    # WORKING, not a state either `--until` should be satisfied by.
    run = _run(status="changes_requested")
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [run])

    result = wait.wait_for("T9", wait.DONE, timeout=0.3)

    assert result["ok"] is False
    assert result["state"] == "timeout"


def test_wait_task_needs_human_satisfies_blocked(monkeypatch):
    run = _run(status="needs_human", last_error="rework budget spent")
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [run])

    result = wait.wait_for("T9", wait.BLOCKED, timeout=1)

    assert result["ok"] is True
    assert result["state"] == "blocked"
    assert result["event"]["type"] == "run_needs_human"


def test_wait_task_unknown_id_is_an_error(monkeypatch):
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [])

    result = wait.wait_for("does-not-exist", wait.DONE, timeout=0.1)

    assert result["ok"] is False
    assert result["state"] == "error"
