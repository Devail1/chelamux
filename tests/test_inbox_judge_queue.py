"""CMX-150: a run QUEUED behind a running judge (``judge_max_concurrent`` saturated) is not a
stall — it is silent, then says it is queued; the real stall notice still fires when nothing
is running, and its suggested command parses with the real CLI."""
from __future__ import annotations

import argparse
import sys
import time

import pytest

from chela import judge, main
from tests.test_inbox_cmx86 import _isolated, _queued, _run  # noqa: F401 — autouse fixture

WF = "/repo/WORKFLOW.md"
T0 = 1_000_000.0


@pytest.fixture(autouse=True)
def _knobs(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "1200")
    monkeypatch.setenv("CHELA_JUDGE_MAX_CONCURRENT", "1")
    monkeypatch.setenv("TZ", "UTC")
    time.tzset()
    yield
    monkeypatch.undo()
    time.tzset()


def _waiting(**over):
    return _run("awaiting_review", **{"pr_head_sha": "abc", "judge_state": "",
                                      "workflow_path": WF, **over})


def _judging(task="CMX-138", started="2026-10-16T10:05:00+00:00", wf=WF):
    return {"task_id": task, "status": "awaiting_review", "pr_state": "open",
            "pr_head_sha": "zzz", "judge_state": judge.J_RUNNING,
            "judge_started_at": started, "workflow_path": wf}


def _events(runs, store, now):
    from chela import inbox
    return inbox.judge_wait_events(runs, store, now)


def test_queued_behind_a_running_judge_is_no_stall_then_says_queued():
    from chela import inbox
    store, runs = inbox._empty(), [_waiting(), _judging()]
    assert _events(runs, store, T0) == []
    assert _events(runs, store, T0 + 1201) == []                  # past the stall bound
    assert _events(runs, store, T0 + 3599) == []
    fired = _events(runs, store, T0 + 3601)
    assert len(fired) == 1
    text = fired[0]["summary"]
    assert "queued for the judge behind CMX-138, judging since 10:05, 1 slot " in text
    assert "NO judge round" not in text
    assert fired[0]["payload"]["queued_behind"] == ["CMX-138"]
    assert _events(runs, store, T0 + 9000) == []                  # said once


def test_nothing_running_still_fires_the_stall_after_20m():
    from chela import inbox
    store, runs = inbox._empty(), [_waiting()]
    assert _events(runs, store, T0) == []
    assert _events(runs, store, T0 + 1199) == []
    fired = _events(runs, store, T0 + 1201)
    assert "NO judge round has started" in fired[0]["summary"]
    assert fired[0]["payload"]["queued_behind"] == []


@pytest.mark.parametrize("max_concurrent, fires", [("1", False), ("2", True)])
def test_a_free_slot_is_no_queue(monkeypatch, max_concurrent, fires):
    """One judge running: queued only when that saturates ``judge_max_concurrent``."""
    from chela import inbox
    monkeypatch.setenv("CHELA_JUDGE_MAX_CONCURRENT", max_concurrent)
    store, runs = inbox._empty(), [_waiting(), _judging()]
    _events(runs, store, T0)
    fired = _events(runs, store, T0 + 1201)
    assert [("NO judge round" in e["summary"]) for e in fired] == ([True] if fires else [])


def test_a_judge_of_ANOTHER_workflow_holds_no_slot_of_this_one():
    from chela import inbox
    store, runs = inbox._empty(), [_waiting(), _judging(wf="/other/WORKFLOW.md")]
    _events(runs, store, T0)
    fired = _events(runs, store, T0 + 1201)
    assert ["NO judge round" in e["summary"] for e in fired] == [True]


def test_the_stall_clock_restarts_when_the_queue_frees_and_a_stall_after_it_is_said():
    """🔴 GUARD: queued 61m (queued notice), then the slot frees and nothing starts — the
    stall gets its own notice, but only 20m after the LAST tick that saw it queued."""
    from chela import inbox
    store = inbox._empty()
    queued, free = [_waiting(), _judging()], [_waiting()]
    _events(queued, store, T0)
    assert len(_events(queued, store, T0 + 3660)) == 1            # the queued notice
    assert _events(free, store, T0 + 3720) == []
    assert _events(free, store, T0 + 3660 + 1199) == []
    fired = _events(free, store, T0 + 3660 + 1201)
    assert ["NO judge round" in e["summary"] for e in fired] == [True]
    assert _events(free, store, T0 + 99_999) == []


def test_the_queued_notice_is_kept_said_while_its_judge_runs():
    """Said queued, then this run's own judge starts and aborts on the SAME head: not new."""
    from chela import inbox
    store = inbox._empty()
    _events([_waiting(), _judging()], store, T0)
    assert len(_events([_waiting(), _judging()], store, T0 + 3601)) == 1
    _events([_waiting(judge_state=judge.J_RUNNING)], store, T0 + 3700)
    assert _events([_waiting(), _judging()], store, T0 + 3800) == []
    assert _events([_waiting(), _judging()], store, T0 + 3800 + 3601) == []   # not a new clock


def test_the_judge_named_is_the_earliest_started(monkeypatch):
    from chela import inbox
    runs = [_waiting(), _judging("CMX-2", "2026-10-16T11:00:00+00:00"),
            _judging("CMX-1", "2026-10-16T09:30:00+00:00")]
    monkeypatch.setenv("CHELA_JUDGE_MAX_CONCURRENT", "2")
    store = inbox._empty()
    _events(runs, store, T0)
    text = _events(runs, store, T0 + 3601)[0]["summary"]
    assert "behind CMX-1, judging since 09:30, 2 slots " in text


def test_the_suggested_command_is_the_literal_and_parses_with_the_real_cli(monkeypatch):
    from chela import inbox
    assert inbox.STALL_CHECK_ARGV == ("chela", "dispatch", "--hold-status")
    parsed = {}

    def capture(self, args=None, namespace=None):
        parsed["ns"] = real(self, list(inbox.STALL_CHECK_ARGV[1:]), namespace)
        raise SystemExit(0)

    real = argparse.ArgumentParser.parse_args
    monkeypatch.setattr(argparse.ArgumentParser, "parse_args", capture)
    monkeypatch.setattr(sys, "argv", ["chela"])
    with pytest.raises(SystemExit):
        main.main()
    assert parsed["ns"].command == "dispatch" and parsed["ns"].hold_status is True


def test_two_runs_in_review_with_one_judge_slot_through_the_tick(monkeypatch):
    """VERIFY: two runs reach awaiting_review with ``judge_max_concurrent=1``; the first is
    judging, the second waits — the orchestrator is told it is QUEUED, once, never a stall.
    (Bounds at 0 so the tick's real clock is past both.)"""
    from chela import inbox
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    runs = [_judging("CMX-138"), _waiting(task_id="CMX-149")]
    for _ in range(3):
        inbox.tick({}, runs=runs)
    said = [e["summary"] for e in _queued("run_judge_not_started")]
    assert len(said) == 1 and "NO judge round" not in said[0]
    assert "cmx-9 is queued for the judge behind CMX-138, judging since 10:05, 1 slot" in said[0]


def test_a_judge_with_no_start_time_is_still_named_without_a_clock():
    from chela import inbox
    store, runs = inbox._empty(), [_waiting(), _judging(started=None)]
    _events(runs, store, T0)
    text = _events(runs, store, T0 + 3601)[0]["summary"]
    assert "queued for the judge behind CMX-138, 1 slot " in text
    assert "judging since" not in text
