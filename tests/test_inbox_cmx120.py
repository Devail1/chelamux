"""CMX-120 — the "self-check running past the ceiling" notice fires ONCE, not every tick.

10-13 ~07:20Z: after CMX-110 the orchestrator got "⏳ … judge self-check running for 1h00m
(ceiling 1h00m) — not stuck, still working" at 1h00m, again at 1h00m, then at 1h01m — once
per daemon tick. The latch was keyed on the self-check's float start (``btime +
starttime/HZ``, re-read every tick), and that float is not stable (WSL2 re-derives ``btime``
from a resynced clock), so every tick looked like a NEW self-check. Locked in here:

  * one self-check (one process) past the ceiling over N ticks is exactly ONE notice, even
    when its measured start jitters between ticks;
  * still running an hour later it may say so once more — never per tick;
  * a NEW self-check process (a re-run) is news again;
  * the latch clears when the self-check ends, and when the run leaves ``running``.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chela import inbox


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    monkeypatch.setenv("CHELA_SELFCHECK_ALERT_S", "3600")


START = 1_000_000.0
CEILING = 3600


def _run(status="running"):
    return {"task_id": "T9", "title": "fix the parser", "status": status,
            "branch_name": "cmx-9", "pr_url": "https://github.com/o/r/pull/9",
            "pr_state": "open"}


def _probe(pid=4242, since=START, what="judge self-check"):
    sc = {"what": what, "since": since, "pid": pid}
    return {"T9": {"activity": START, "markers": {}, "selfcheck": sc}}


def _tick(store, now, probe, status="running"):
    return [e["kind"] for e in inbox.stuck_events([_run(status)], store, probe, now)]


def test_one_self_check_past_the_ceiling_over_many_ticks_is_one_notice():
    """🔴 GUARD: the 10-13 flood — 30 ticks a minute apart past the ceiling, the measured
    start jittering by a fraction of a second each tick (same pid), is exactly ONE notice."""
    store, fired = inbox._empty(), []
    for i in range(30):
        jitter = (-1) ** i * 0.37 * (i % 3)
        fired += _tick(store, START + CEILING + 60 * i, _probe(since=START + jitter))
    assert fired == ["run_selfcheck_long"]


def test_without_a_pid_a_jittering_start_is_still_one_self_check():
    """🔴 GUARD: a probe that could not name the pid falls back to the start, with slack —
    a reading a second off is the same self-check, not a new one each tick."""
    store, fired = inbox._empty(), []
    for i in range(10):
        fired += _tick(store, START + CEILING + 60 * i,
                       _probe(pid=None, since=START + (i % 2) * 1.5))
    assert fired == ["run_selfcheck_long"]


def test_still_running_an_hour_past_the_ceiling_says_so_at_most_once_more():
    """🔴 GUARD: the coarse re-notify — +1h past the ceiling is ONE more notice, and the
    ticks around it stay silent."""
    store, fired = inbox._empty(), []
    for minute in range(0, 125):
        fired += _tick(store, START + CEILING + 60 * minute, _probe())
    # ceiling (minute 0), +1h (minute 60), +2h (minute 120) — never per tick.
    assert fired == ["run_selfcheck_long"] * 3


def test_a_new_self_check_process_is_news_again():
    """🔴 GUARD: a re-run is a different process — past the ceiling it gets its own notice."""
    store = inbox._empty()
    assert _tick(store, START + CEILING, _probe(pid=1)) == ["run_selfcheck_long"]
    assert _tick(store, START + CEILING + 60, _probe(pid=1)) == []
    rerun = START + 600
    assert _tick(store, rerun + CEILING, _probe(pid=2, since=rerun)) == ["run_selfcheck_long"]
    assert _tick(store, rerun + CEILING + 60, _probe(pid=2, since=rerun)) == []


def test_the_latch_clears_when_the_self_check_ends():
    """🔴 GUARD: a tick with no self-check drops the latch from the store."""
    store = inbox._empty()
    assert _tick(store, START + CEILING, _probe()) == ["run_selfcheck_long"]
    assert store["stuck"]["T9"].get("selfcheck")
    idle = {"T9": {"activity": START + CEILING, "markers": {}, "selfcheck": None}}
    _tick(store, START + CEILING + 60, idle)
    assert "selfcheck" not in store["stuck"]["T9"]


def test_the_latch_resets_when_the_run_leaves_running():
    """🔴 GUARD: a run that leaves ``running`` loses its latch; back in ``running`` with the
    same self-check still past the ceiling, it is announced again."""
    store = inbox._empty()
    assert _tick(store, START + CEILING, _probe()) == ["run_selfcheck_long"]
    assert _tick(store, START + CEILING + 60, _probe(), status="awaiting_review") == []
    assert "T9" not in store["stuck"]
    assert _tick(store, START + CEILING + 120, _probe()) == ["run_selfcheck_long"]


def test_the_probe_names_the_earliest_instance_pid():
    """🔴 GUARD: the self-check's identity is the pid of its earliest instance — the one the
    start is read from — so the latch keys on a stable process, not a float."""
    root = Path("/tmp").resolve()
    procs = [("judge self-check", root, 200.0, 77), ("judge self-check", root, 100.0, 55),
             ("pytest", root, 50.0, 11)]
    assert inbox._selfcheck_for(str(root), procs) == {
        "what": "judge self-check", "since": 100.0, "pid": 55}
