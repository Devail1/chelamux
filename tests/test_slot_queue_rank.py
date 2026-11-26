"""⏳ CMX-494: whatever runs a suite in a dispatched worktree queues at its issue's priority,
re-ranks when that priority changes, and a long wait is reported.

11-23: an Urgent task's ``chela judge sweep`` queued as "no priority" and sat 4 h behind a High
one; the stuck detector skips a slot waiter, so nothing said so."""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import (config, digest, dispatcher, inbox, judge, main, park, slot_alerts,
                   suite_guard, sweep)
from chela.sources import Task
from test_dispatcher_ci import _FakeGh, _row, _status, _wf

NOW = 1_800_000_000.0
MIN, HOUR = 60.0, 3600.0


@pytest.fixture
def box(monkeypatch, tmp_path):
    """One suite slot, an isolated state dir, and the run DB the rank is read from."""
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", "0")
    monkeypatch.setattr(suite_guard, "SLOT_POLL_SECONDS", 0.01)
    monkeypatch.setattr(suite_guard, "_waited", 0.0)
    monkeypatch.setattr(suite_guard, "_thread_waited", {})
    monkeypatch.setattr(suite_guard, "_last_rank", {})
    return tmp_path


def _add_run(box: Path, task="CMX-473", priority=1, worktree=None, **over) -> Path:
    wt = Path(worktree) if worktree else box / "wts" / task
    wt.mkdir(parents=True, exist_ok=True)
    with dispatcher._db() as conn:
        _row(conn, task, priority=priority, worktree_path=str(wt), status="running", **over)
    return wt


def _set_priority(task: str, priority) -> None:
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET priority=? WHERE task_id=?", (priority, task))


# --- run_rank: the worktree -> run -> priority lookup ----------------------------------------

@pytest.mark.parametrize("priority,want", [(1, 1), (2, 2), (3, 3), (4, 4), (0, 5), (None, 5)])
def test_a_worktree_ranks_by_its_runs_priority(box, priority, want):
    assert suite_guard.run_rank(_add_run(box, priority=priority)) == want


def test_a_directory_inside_the_worktree_is_the_run_too(box):
    wt = _add_run(box, priority=2)
    assert suite_guard.run_rank(wt / "sub" / "dir") == 2
    assert suite_guard.run_rank(str(wt)) == 2


def test_a_symlinked_worktree_is_found_by_either_name(box):
    real = _add_run(box, "CMX-1", priority=1, worktree=box / "real" / "CMX-1")
    (box / "link").symlink_to(box / "real")
    assert suite_guard.run_rank(box / "link" / "CMX-1") == 1
    _add_run(box, "CMX-2", priority=2, worktree=box / "link" / "CMX-2")
    assert suite_guard.run_rank(box / "link" / "CMX-2") == 2
    assert real.exists()


def test_negative_control_a_directory_no_run_owns_is_todays_rank_5(box):
    wt = _add_run(box, priority=1)
    assert suite_guard.run_rank(box / "elsewhere") == 5
    assert suite_guard.run_rank(wt.parent) == 5                  # the root is not a worktree
    assert suite_guard.run_rank(wt.parent / "CMX-4730") == 5     # nor a sibling by prefix


def test_the_deepest_worktree_wins(box):
    outer = _add_run(box, "CMX-1", priority=1)
    inner = _add_run(box, "CMX-2", priority=4, worktree=outer / "nested")
    assert suite_guard.run_rank(inner) == 4
    assert suite_guard.run_rank(outer) == 1


def test_the_newest_run_of_one_worktree_wins(box):
    wt = _add_run(box, "CMX-1", priority=4, started_at="2026-11-01T00:00:00+00:00")
    _add_run(box, "CMX-2", priority=1, worktree=wt, started_at="2026-11-23T00:00:00+00:00")
    assert suite_guard.run_rank(wt) == 1


def test_a_judges_checkout_ranks_by_its_task(box):
    wt = _add_run(box, "CMX-473", priority=2)
    assert judge.judge_worktree_path(_wf(box), "CMX-473").name == "judge-CMX-473"
    assert suite_guard.run_rank(wt.parent / "judge-CMX-473" / "sub") == 2
    # ...only beside that run's own worktree: the name alone proves nothing
    assert suite_guard.run_rank(box / "other" / "judge-CMX-473") == 5
    assert suite_guard.run_rank(wt.parent / "judge-CMX-999") == 5
    assert suite_guard.run_rank(wt.parent / "CMX-473-judge-") == 5


def test_no_run_db_yet_is_no_run(box):
    assert not (box / "scheduler.db").exists()
    assert suite_guard.run_rank("/wt") == 5
    assert not (box / "scheduler.db").exists()                   # a read never creates it


@pytest.mark.parametrize("err", [sqlite3.OperationalError("locked"), OSError("gone")])
def test_an_unreadable_db_keeps_the_last_known_rank(box, monkeypatch, err):
    wt = _add_run(box, priority=1)

    def boom(*a, **k):
        raise err
    with monkeypatch.context() as m:
        m.setattr(suite_guard.sqlite3, "connect", boom)
        assert suite_guard.run_rank(wt) == 5                     # never known: unknown
    assert suite_guard.run_rank(wt) == 1
    monkeypatch.setattr(suite_guard.sqlite3, "connect", boom)
    assert suite_guard.run_rank(wt) == 1


def test_the_last_known_rank_is_the_latest_and_its_own_worktrees(box, monkeypatch):
    urgent, low = _add_run(box, "CMX-1", priority=1), _add_run(box, "CMX-4", priority=4)
    assert [suite_guard.run_rank(w) for w in (urgent, low)] == [1, 4]
    _set_priority("CMX-4", 3)
    assert suite_guard.run_rank(low) == 3

    def boom(*a, **k):
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(suite_guard.sqlite3, "connect", boom)
    assert [suite_guard.run_rank(w) for w in (urgent, low, box / "new")] == [1, 3, 5]


def test_a_db_without_the_priority_column_is_unknown(box):
    with sqlite3.connect(box / "scheduler.db") as conn:
        conn.execute("CREATE TABLE runs (task_id TEXT, worktree_path TEXT, started_at TEXT)")
    conn.close()
    assert suite_guard.run_rank("/wt") == 5


def test_the_run_db_is_opened_read_only(box, monkeypatch):
    wt = _add_run(box)
    seen, real = [], sqlite3.connect
    monkeypatch.setattr(suite_guard.sqlite3, "connect",
                        lambda *a, **k: seen.append((a, k)) or real(*a, **k))
    suite_guard.run_rank(wt)
    assert seen == [((f"file://{box}/scheduler.db?mode=ro",), {"uri": True, "timeout": 1.0})]


@pytest.mark.parametrize("rank,want", [
    (None, (2, 2)),       # the run's own
    (0, (0, 2)),          # a judge: first, and its task's rank orders it among judges
    (6, (6, 6)), (3, (3, 3))])            # explicit: kept, the run is not asked
def test_ranks_of_an_acquirer(box, rank, want):
    assert suite_guard._ranks(str(_add_run(box, priority=2)), rank) == want


# --- the ledger of a real waiter -------------------------------------------------------------

class _Held:
    """The one slot, held by somebody else until :meth:`free`."""

    def __init__(self, box: Path):
        d = box / "suite-slots"
        d.mkdir(exist_ok=True)
        self.fd = os.open(d / "slot-0", os.O_RDWR | os.O_CREAT)
        fcntl.flock(self.fd, fcntl.LOCK_EX)

    def free(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


@pytest.fixture
def busy_slot(box):
    held = _Held(box)
    yield held
    held.free()


def _until(cond, what, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (got := cond()):
            return got
        time.sleep(0.01)
    raise AssertionError(f"never: {what}")


def _ledger_of(box: Path, wt: Path) -> dict | None:
    for f in (box / "suite-slots" / "waits").glob("*.json"):
        rec = json.loads(f.read_text())
        if rec["cwd"] == str(wt) and rec["waiting_since"] is not None:
            return rec
    return None


def _sweep_in(wt):
    sweep.sweep(wt, "origin/dev", "true", timeout=30)


def _self_check_in(wt):
    judge.self_check(wt, "true", [], timeout=30)


def _judge_in(wt):
    judge.run_experiments(wt, "true", [], timeout=30)


def _hand_in(_wt):
    with dispatcher.hand_in_battery("CMX-473"):
        pass


@pytest.fixture
def no_batteries(monkeypatch):
    monkeypatch.setattr(sweep, "_sweep", lambda *a, **k: sweep.SweepReport())
    monkeypatch.setattr(judge, "_self_check", lambda *a, **k: judge.Report())
    monkeypatch.setattr(judge, "_run_experiments", lambda *a, **k: judge.Report())


@pytest.mark.parametrize("start,priority,want", [
    (_sweep_in, 1, (1, 1)), (_self_check_in, 1, (1, 1)), (_hand_in, 1, (1, 1)),
    (_judge_in, 1, (0, 1)), (_judge_in, 3, (0, 3)),
    (_sweep_in, None, (5, 5))])            # negative control: today's rank 5
def test_every_acquirer_in_a_worktree_queues_at_its_tasks_rank(box, busy_slot, no_batteries,
                                                               start, priority, want):
    wt = _add_run(box, priority=priority)
    t = threading.Thread(target=start, args=(wt,), daemon=True)
    t.start()
    rec = _until(lambda: _ledger_of(box, wt), "a wait ledger")
    busy_slot.free()
    t.join(10)
    assert (rec["rank"], rec["task_rank"]) == want


def test_a_plain_suite_run_takes_the_runs_rank_too(box):
    wt = _add_run(box, priority=2)
    with suite_guard.slot(wt, max_wait=5):
        [h] = suite_guard.held()
    assert (h["rank"], h["task_rank"], h["name"]) == (2, 2, "CMX-473")


def test_raising_the_priority_re_ranks_a_queued_waiter(box, busy_slot, no_batteries):
    wt = _add_run(box, priority=None)
    t = threading.Thread(target=_sweep_in, args=(wt,), daemon=True)
    t.start()
    before = _until(lambda: _ledger_of(box, wt), "a wait ledger")
    assert before["rank"] == 5
    _set_priority("CMX-473", 1)
    after = _until(lambda: (r := _ledger_of(box, wt)) and r["rank"] == 1 and r, "the re-rank")
    busy_slot.free()
    t.join(10)
    assert after["waiting_since"] == before["waiting_since"]      # it keeps its place in age
    assert after["opened"] == before["opened"] and after["task_rank"] == 1


def test_raising_a_judged_tasks_priority_re_ranks_it_among_judges(box, busy_slot, no_batteries):
    wt = _add_run(box, priority=3)
    jdir = wt.parent / "judge-CMX-473"
    jdir.mkdir()
    t = threading.Thread(target=_judge_in, args=(jdir,), daemon=True)
    t.start()
    assert _until(lambda: _ledger_of(box, jdir), "a wait ledger")["task_rank"] == 3
    _set_priority("CMX-473", 1)
    rec = _until(lambda: (r := _ledger_of(box, jdir)) and r["task_rank"] == 1 and r, "re-rank")
    busy_slot.free()
    t.join(10)
    assert rec["rank"] == 0


def test_the_raised_waiter_is_admitted_first(box, busy_slot):
    """The order itself, not just the ledger: the older High would have gone first."""
    order = []
    threads = []

    def body(wt):
        with suite_guard.battery(wt, "sweep", max_wait=20):
            order.append(wt.name)
    for task, prio in (("CMX-482", 2), ("CMX-473", None)):
        wt = _add_run(box, task, priority=prio)
        threads.append(threading.Thread(target=body, args=(wt,), daemon=True))
        threads[-1].start()
        _until(lambda: _ledger_of(box, wt), "a wait ledger")
    _set_priority("CMX-473", 1)
    _until(lambda: _ledger_of(box, box / "wts" / "CMX-473")["rank"] == 1, "the re-rank")
    busy_slot.free()
    for t in threads:
        t.join(10)
    assert order == ["CMX-473", "CMX-482"]


def _admitted(box, busy_slot, starts, before_free=lambda: None):
    """The order REAL acquirers get the slot in: each is queued before the next one starts, so
    the first in ``starts`` is always the oldest wait."""
    order, errors, threads = [], [], []

    def body(wt, kind, rank):
        try:
            with suite_guard.battery(wt, kind, max_wait=8, rank=rank):
                order.append(wt.name)
        except BaseException as e:   # noqa: BLE001
            errors.append(e)
    for wt, kind, rank in starts:
        threads.append(threading.Thread(target=body, args=(wt, kind, rank), daemon=True))
        threads[-1].start()
        _until(lambda: _ledger_of(box, wt), f"{wt.name}'s wait ledger")
    before_free()
    busy_slot.free()
    for t in threads:
        t.join(20)
    assert not errors
    return order


def test_judge_rounds_are_admitted_by_their_tasks_rank(box, busy_slot):
    """Through the real acquirer, oldest wait last in rank: the order is never arrival's."""
    low, medium, urgent = (_add_run(box, f"CMX-{p}", priority=p) for p in (4, 3, 1))
    sweeper = _add_run(box, "CMX-9", priority=1)
    assert _admitted(box, busy_slot, [
        (sweeper, "sweep", suite_guard.RANK_SWEEP), (low, "judge", None),
        (medium, "judge", None), (urgent, "judge", None)]) == ["CMX-1", "CMX-3", "CMX-4", "CMX-9"]


def test_equal_judge_rounds_are_admitted_oldest_first(box, busy_slot):
    a, b = (_add_run(box, t, priority=2) for t in ("CMX-2", "CMX-1"))
    assert _admitted(box, busy_slot, [(a, "judge", None), (b, "judge", None)]) == [
        "CMX-2", "CMX-1"]


def test_a_raised_judge_round_is_admitted_first(box, busy_slot):
    high, raised = _add_run(box, "CMX-2", priority=2), _add_run(box, "CMX-4", priority=4)

    def re_rank():
        _set_priority("CMX-4", 1)
        _until(lambda: _ledger_of(box, raised)["task_rank"] == 1, "the re-rank")
    assert _admitted(box, busy_slot, [(high, "judge", None), (raised, "judge", None)],
                     re_rank) == ["CMX-4", "CMX-2"]


@pytest.mark.parametrize("kind,rank,priority,want,says", [
    ("judge", None, 1, (0, 1), "judge, Urgent"), ("judge", None, 4, (0, 4), "judge, Low"),
    ("judge", None, None, (0, 5), "judge, no priority"), ("sweep", None, 1, (1, 1), "Urgent"),
    ("sweep", 6, 1, (6, 6), "nightly sweep")])
def test_a_real_holders_record_says_whose_rank_it_holds_at(box, kind, rank, priority, want,
                                                           says):
    """The record the ACQUIRER wrote, read back by every surface that names the holder."""
    wt = _add_run(box, priority=priority)
    before = time.time()
    with suite_guard.battery(wt, kind, max_wait=5, rank=rank):
        [h] = suite_guard.held()
    assert before <= h.pop("since") <= time.time()
    assert h == {"name": "CMX-473", "cwd": str(wt), "pid": os.getpid(), "rank": want[0],
                 "task_rank": want[1]}
    assert slot_alerts.label(h) == f"CMX-473 ({says})"


def test_a_holder_that_queued_records_the_rank_it_was_re_ranked_to(box, busy_slot):
    wt = _add_run(box, priority=4)
    got = []

    def body():
        with suite_guard.battery(wt, "judge", max_wait=8):
            got.extend(suite_guard.held())
    t = threading.Thread(target=body, daemon=True)
    t.start()
    _until(lambda: _ledger_of(box, wt), "a wait ledger")
    _set_priority("CMX-473", 2)
    _until(lambda: _ledger_of(box, wt)["task_rank"] == 2, "the re-rank")
    busy_slot.free()
    t.join(10)
    assert [(h["rank"], h["task_rank"]) for h in got] == [(0, 2)]


def test_a_yielded_holder_re_queues_at_its_runs_rank_and_claim(box, monkeypatch):
    """The slot it takes back, and the wait in between, are both at the rank it has NOW."""
    wt = _add_run(box, priority=4)
    judged = _add_run(box, "CMX-1", priority=1)
    seen = {}
    release = threading.Event()

    def judge_body():
        with suite_guard.battery(judged, "judge", max_wait=8):
            release.wait(8)
    t = threading.Thread(target=judge_body, daemon=True)

    def watch():
        seen["wait"] = _until(lambda: _ledger_of(box, wt), "the yielded wait")
        _set_priority("CMX-473", 2)
        _until(lambda: _ledger_of(box, wt)["rank"] == 2, "the re-rank")
        # CMX-507: the yield hands the slot over, but the judge claims it on its own thread —
        # on a slow runner it is still an open wait here. Snapshot once it holds the slot.
        _until(lambda: _ledger_of(box, judged) is None
               and [h["name"] for h in suite_guard.held()] == ["CMX-1"], "the judge's claim")
        seen["queued"] = suite_guard.queued()
        release.set()
    w = threading.Thread(target=watch, daemon=True)
    try:
        with suite_guard.battery(wt, "self-check", max_wait=8):
            held_since = time.time()
            time.sleep(0.05)
            t.start()
            _until(lambda: _ledger_of(box, judged), "the judge's wait ledger")
            w.start()
            assert suite_guard.yield_slot() == "CMX-1"
            [h] = suite_guard.held()
    finally:
        release.set()
    t.join(10)
    w.join(10)
    assert (seen["wait"]["rank"], h["name"], h["rank"], h["task_rank"]) == (4, "CMX-473", 2, 2)
    assert seen["wait"]["waiting_since"] <= held_since            # its place: the claim
    [q] = seen["queued"]
    assert q["since"] > held_since                                # its wait: from the yield


@pytest.mark.parametrize("kind,rank,want", [
    ("judge", None, 0), ("sweep", 6, 6), ("self-check", None, 4)])
def test_a_holder_yields_by_its_queue_rank_not_its_tasks(box, monkeypatch, kind, rank, want):
    """A Low task's judge never yields to an Urgent hand-in; the nightly sweep always does."""
    wt = _add_run(box, priority=4)
    asked = []
    monkeypatch.setattr(suite_guard, "outranking", lambda rank, **k: asked.append(rank) or [])
    with suite_guard.battery(wt, kind, max_wait=5, rank=rank):
        assert suite_guard.yield_slot() == ""
    assert asked == [want]


def test_a_holder_yields_by_the_rank_its_run_has_now(box, monkeypatch):
    wt = _add_run(box, priority=4)
    asked = []
    monkeypatch.setattr(suite_guard, "outranking", lambda rank, **k: asked.append(rank) or [])
    with suite_guard.battery(wt, "self-check", max_wait=5):
        suite_guard.yield_slot()
        _set_priority("CMX-473", 1)
        suite_guard.yield_slot()
    assert asked == [4, 1]


# --- queued(): the order, and what it reads ----------------------------------------------------

def _write_ledger(box, name, rank, since, *, task_rank=None, opened=None, pid=None, **over):
    waits = box / "suite-slots" / "waits"
    waits.mkdir(parents=True, exist_ok=True)
    rec = {"pid": pid or os.getpid(), "start": suite_guard._proc_stat(os.getpid())[1],
           "cwd": f"/wts/{name}", "waiting_since": since, "waited": 0, "rank": rank, **over}
    if task_rank is not None:
        rec["task_rank"] = task_rank
    if opened is not None:
        rec["opened"] = opened
    (waits / f"{name}.json").write_text(json.dumps(rec))
    return waits / f"{name}.json"


def test_judge_rounds_queue_among_themselves_by_their_tasks_rank(box):
    late = _write_ledger(box, "judge-urgent", 0, 9.0, task_rank=1)
    early = _write_ledger(box, "judge-low", 0, 1.0, task_rank=4)
    legacy = _write_ledger(box, "judge-old", 0, 0.5)              # no task_rank: last of them
    assert suite_guard._must_queue(late, 9.0, 0, 1) is False
    assert suite_guard._must_queue(early, 1.0, 0, 4) is True
    assert suite_guard._must_queue(legacy, 0.5, 0) is True
    assert [w["name"] for w in suite_guard.queued()] == ["judge-urgent", "judge-low",
                                                         "judge-old"]


def test_a_task_rank_never_outranks_the_queue_rank(box):
    """Only a judge's sub-order is its task's: a Low hand-in claiming an Urgent task rank
    (a forged or garbled field) still queues at Low."""
    _write_ledger(box, "low", 4, 1.0, task_rank=1)
    _write_ledger(box, "medium", 3, 2.0)
    assert [(w["name"], w["task_rank"]) for w in suite_guard.queued()] == [("medium", 3),
                                                                           ("low", 4)]


def test_queued_lists_open_waits_in_the_order_they_are_served(box, monkeypatch):
    _write_ledger(box, "sweep", 6, 1.0)
    _write_ledger(box, "high", 2, 5.0)
    _write_ledger(box, "urgent", 1, 9.0, opened=50.0)
    _write_ledger(box, "urgent-old", 1, 8.0, opened=60.0)     # age is the CLAIM, not `opened`
    _write_ledger(box, "holding", 0, None)
    _write_ledger(box, "garbage", 1, "soon")
    got = suite_guard.queued()
    assert [w["name"] for w in got] == ["urgent-old", "urgent", "high", "sweep"]
    assert got[1] == {"name": "urgent", "cwd": "/wts/urgent", "pid": os.getpid(), "rank": 1,
                      "task_rank": 1, "since": 50.0, "stopped": False, "blocked_until": None}
    assert got[2]["since"] == 5.0                                  # a legacy ledger: its claim


@pytest.mark.parametrize("state,stopped", [("T", True), ("t", True), ("S", False)])
def test_a_paused_waiter_is_listed_as_stopped(box, monkeypatch, state, stopped):
    _write_ledger(box, "urgent", 1, 1.0)
    start = suite_guard._proc_stat(os.getpid())[1]
    monkeypatch.setattr(suite_guard, "_proc_stat", lambda pid: (state, start))
    assert [w["stopped"] for w in suite_guard.queued()] == [stopped]


def test_a_yielded_batterys_wait_starts_when_it_yields(box, monkeypatch):
    """It re-queues at its original claim; the time it HELD the slot is not waiting."""
    led = suite_guard._Ledger("/wts/x", 3)
    led.write(5.0)
    [w] = suite_guard.queued()
    assert abs(w["since"] - time.time()) < 60 and w["rank"] == 3


def test_held_names_what_a_record_says_and_nothing_it_does_not(box, busy_slot):
    (box / "suite-slots" / "slot-0").write_text(json.dumps(
        {"pid": 7, "cwd": "/wts/judge-CMX-1", "since": 5.0, "rank": 0, "task_rank": 2}))
    assert suite_guard.held() == [{"name": "judge-CMX-1", "cwd": "/wts/judge-CMX-1", "pid": 7,
                                   "since": 5.0, "rank": 0, "task_rank": 2}]
    for text in ("{not json", "[1]", '{"cwd": 3}'):
        (box / "suite-slots" / "slot-0").write_text(text)
        [h] = suite_guard.held()
        assert (h["name"], h["pid"], h["since"], h["rank"]) == ("unknown", None, None, 5)


# --- rank_label / park / dispatch-runs / chela slots --------------------------------------------

@pytest.mark.parametrize("rank,task_rank,want", [
    (0, 1, "judge, Urgent"), (0, 5, "judge, no priority"), (1, 1, "Urgent"), (2, 2, "High"),
    (3, 3, "Medium"), (4, 4, "Low"), (5, 5, "no priority"), (6, 6, "nightly sweep")])
def test_rank_label(rank, task_rank, want):
    assert suite_guard.rank_label(rank, task_rank) == want


def test_wait_rank_is_the_worktrees_first_served_open_wait(box):
    wt = box / "wt"
    (wt / "sub").mkdir(parents=True)
    _write_ledger(box, "a", 3, 1.0, cwd=str(wt))
    _write_ledger(box, "b", 2, 9.0, cwd=str(wt / "sub"))
    _write_ledger(box, "c", 1, 1.0, cwd=str(box / "wt2"))
    assert suite_guard.wait_rank(wt) == (2, 2)
    assert suite_guard.wait_rank(box / "none") is None


def test_dispatch_runs_shows_the_rank_next_to_the_slot_wait(box, busy_slot, capsys):
    (box / "suite-slots" / "slot-0").write_text(json.dumps({"cwd": "/wts/CMX-482"}))
    wt = _add_run(box, priority=1)
    _write_ledger(box, "x", 1, time.time() - 60, cwd=str(wt))
    main._print_park({"task_id": "CMX-473", "status": "running", "worktree_path": str(wt)})
    assert "waiting for a suite slot (rank: Urgent; holder: CMX-482)" in capsys.readouterr().out


def test_dispatch_runs_names_a_judge_waiters_task_rank(box, busy_slot, capsys):
    """Queue rank and task rank differ only for a judge; the worktree asked for is a symlink."""
    (box / "suite-slots" / "slot-0").write_text(json.dumps({"cwd": "/wts/CMX-482"}))
    wt = _add_run(box, priority=2)
    (box / "link").symlink_to(wt)
    _write_ledger(box, "x", 0, time.time() - 60, task_rank=2, cwd=str(wt))
    assert suite_guard.wait_rank(box / "link") == (0, 2)
    main._print_park({"task_id": "CMX-473", "status": "running",
                      "worktree_path": str(box / "link")})
    assert "(rank: judge, High; holder: CMX-482)" in capsys.readouterr().out


def test_a_wait_that_ended_mid_read_shows_no_rank(monkeypatch):
    monkeypatch.setattr(suite_guard, "waits_in", lambda w: {"waiting_since": 5.0, "waited": 0})
    monkeypatch.setattr(suite_guard, "wait_rank", lambda w: None)
    monkeypatch.setattr(suite_guard, "holders", lambda: ["CMX-7"])
    got = park.state({"status": "running", "worktree_path": "/wt"})
    assert got["reason"] == "waiting for a suite slot (holder: CMX-7)"


def _entry(name, rank, since, *, task_rank=None, pid=7, stopped=False, blocked_until=None):
    return {"name": name, "cwd": f"/wts/{name}", "pid": pid, "since": since, "rank": rank,
            "task_rank": rank if task_rank is None else task_rank, "stopped": stopped,
            "blocked_until": blocked_until}


def test_the_slots_lines():
    holders = [_entry("CMX-482", 2, NOW - 20 * MIN, pid=11),
               {**_entry("unknown", 5, None), "pid": None}]
    waiters = [_entry("judge-CMX-9", 0, NOW - 5 * MIN, task_rank=3, pid=12),
               _entry("CMX-473", 1, NOW - 130 * MIN, pid=13, stopped=True)]
    assert slot_alerts.lines(2, holders, waiters, NOW) == [
        "Suite slots: 2 of 2 held, 2 waiting",
        "  ▶ CMX-482 (High)  pid 11  held 20m",
        "  ▶ unknown (no priority)  pid ?  held ?",
        "  1. judge-CMX-9 (judge, Medium)  rank 0  pid 12  waiting 5m",
        "  2. CMX-473 (Urgent)  rank 1  pid 13  waiting 2h10m  (stopped)"]
    assert slot_alerts.lines(0, [], [], NOW) == [
        "Suite slots: off (CHELA_SUITE_SLOTS=0) — runs do not queue"]


def test_chela_slots_prints_the_live_queue(box, busy_slot, monkeypatch, capsys):
    (box / "suite-slots" / "slot-0").write_text(json.dumps(
        {"pid": 11, "cwd": "/wts/CMX-482", "since": time.time() - 120, "rank": 2}))
    _write_ledger(box, "CMX-473", 1, time.time() - 600)
    monkeypatch.setattr(sys, "argv", ["chela", "slots"])
    main.main()
    assert capsys.readouterr().out.splitlines() == [
        "Suite slots: 1 of 1 held, 1 waiting",
        "  ▶ CMX-482 (High)  pid 11  held 2m",
        f"  1. CMX-473 (Urgent)  rank 1  pid {os.getpid()}  waiting 10m"]


# --- the alert ---------------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _thresholds(monkeypatch):
    monkeypatch.delenv("CHELA_SLOT_WAIT_ALERT_S", raising=False)
    monkeypatch.delenv("CHELA_SLOT_WAIT_ANY_ALERT_S", raising=False)


def test_the_documented_thresholds(monkeypatch):
    assert (config.slot_wait_alert_s(), config.slot_wait_any_alert_s()) == (1800, 7200)
    assert slot_alerts.REPEAT_S == 3600
    monkeypatch.setenv("CHELA_SLOT_WAIT_ALERT_S", "60")
    monkeypatch.setenv("CHELA_SLOT_WAIT_ANY_ALERT_S", "90")
    assert (config.slot_wait_alert_s(), config.slot_wait_any_alert_s()) == (60, 90)


def _due(*waiters, store=None, holders=(), now=NOW):
    return slot_alerts.due({} if store is None else store, list(holders), list(waiters), now)


@pytest.mark.parametrize("rank,task_rank,waited,want", [
    (1, 1, 30 * MIN, False), (1, 1, 30 * MIN + 1, True),
    (2, 2, 30 * MIN, False), (2, 2, 30 * MIN + 1, True),
    (0, 1, 30 * MIN + 1, True), (0, 3, 30 * MIN + 1, False),     # a judge: by its task
    (3, 3, 2 * HOUR, False), (3, 3, 2 * HOUR + 1, True),
    (4, 4, 2 * HOUR + 1, True), (5, 5, 2 * HOUR + 1, True), (6, 6, 2 * HOUR + 1, True)])
def test_who_is_overdue(rank, task_rank, waited, want):
    assert bool(_due(_entry("CMX-1", rank, NOW - waited, task_rank=task_rank))) is want


def test_the_any_limit_caps_the_urgent_one(monkeypatch):
    monkeypatch.setenv("CHELA_SLOT_WAIT_ALERT_S", "9000")
    assert _due(_entry("CMX-1", 1, NOW - 7201))
    assert not _due(_entry("CMX-1", 1, NOW - 7200))


def test_a_stopped_waiter_is_never_reported():
    assert _due(_entry("CMX-1", 1, NOW - 9 * HOUR, stopped=True)) == []


def test_a_waiter_in_its_blocked_hours_is_listed_as_such_and_never_reported():
    w = _entry("CMX-1", 1, NOW - 9 * HOUR, blocked_until="04:30")
    assert _due(w) == []
    assert slot_alerts.lines(1, [], [w], NOW)[1].endswith(
        "waiting 9h00m  (blocked hours until 04:30)")


def test_queued_carries_blocked_until_only_while_it_still_holds(box, monkeypatch):
    _write_ledger(box, "urgent", 1, 1.0, blocked_until="04:30")
    for minute, want in ((29, "04:30"), (30, None)):
        monkeypatch.setattr(suite_guard, "_local_now", lambda m=minute: datetime(2026, 11, 23, 4, m))
        assert [w["blocked_until"] for w in suite_guard.queued()] == [want]


def test_the_notice_names_the_waiter_the_holder_and_the_rest_of_the_queue():
    urgent = _entry("CMX-473", 1, NOW - 130 * MIN)
    [d] = _due(urgent, _entry("CMX-479", 5, NOW - MIN), _entry("judge-CMX-2", 0, NOW, task_rank=2),
               holders=[_entry("CMX-482", 2, NOW)])
    assert d["text"] == ("⏳ CMX-473 (Urgent) has waited 2h10m for the suite slot. "
                         "Holder: CMX-482 (High), queued: CMX-479 (no priority), "
                         "judge-CMX-2 (judge, High)")
    assert d["waiter"] is urgent and d["waited"] == 130 * MIN
    [alone] = _due(urgent)
    assert alone["text"].endswith("Holder: none, queued: nobody else")


def test_it_repeats_at_most_hourly():
    store, w = {}, _entry("CMX-473", 1, NOW - 31 * MIN)
    fired = [bool(_due(w, store=store, now=NOW + i * MIN)) for i in range(121)]
    assert [i for i, f in enumerate(fired) if f] == [0, 60, 120]


def test_a_waiter_that_restarts_inside_the_hour_is_not_told_twice():
    store = {}
    assert _due(_entry("CMX-473", 1, NOW - 31 * MIN), store=store)
    assert _due(store=store, now=NOW + MIN) == []                       # its run was killed
    again = _entry("CMX-473", 1, NOW + 2 * MIN)
    assert _due(again, store=store, now=NOW + 40 * MIN) == []
    assert _due(again, store=store, now=NOW + 60 * MIN)


def test_a_latch_is_forgotten_once_the_waiter_is_gone_an_hour():
    store = {}
    w = _entry("CMX-473", 1, NOW - 31 * MIN)
    _due(w, store=store)
    assert store["slot_waits"] == {"/wts/CMX-473": NOW}
    _due(store=store, now=NOW + 59 * MIN)
    assert store["slot_waits"] == {"/wts/CMX-473": NOW}
    _due(store=store, now=NOW + 60 * MIN)
    assert store["slot_waits"] == {}


def test_two_overdue_waits_in_one_worktree_are_one_notice():
    w = _entry("CMX-473", 1, NOW - 40 * MIN)
    assert len(_due(w, {**w, "pid": 8})) == 1


# --- the inbox ---------------------------------------------------------------------------------

def _run(task="CMX-473", **over):
    return {"task_id": task, "status": "running", "branch_name": "cmx-473",
            "worktree_path": f"/wts/{task}", "window_id": "@5", **over}


@pytest.mark.parametrize("name,cwd,found", [
    ("CMX-473", "/wts/CMX-473", True), ("sub", "/wts/CMX-473/sub", True),
    ("judge-CMX-473", "/wts/judge-CMX-473", True), ("CMX-4730", "/wts/CMX-4730", False),
    ("nightly", "/tmp/nightly", False)])
def test_the_event_names_the_waiters_run(name, cwd, found):
    w = {**_entry(name, 1, NOW - 31 * MIN), "cwd": cwd}
    other = {"task_id": "CMX-1", "worktree_path": None, "status": "running"}
    [e] = inbox.slot_wait_events([other, {"task_id": None}, _run()], inbox._empty(),
                                 [_entry("CMX-482", 2, NOW)], [w], NOW)
    assert e["kind"] == "run_slot_wait"
    assert e["payload"].get("task_id") == ("CMX-473" if found else None)
    facts = {k: e["payload"][k] for k in ("worktree", "waited_s", "rank", "task_rank",
                                          "holders", "queued")}
    assert facts == {"worktree": cwd, "waited_s": 1860, "rank": 1, "task_rank": 1,
                     "holders": ["CMX-482"], "queued": [name]}


# (the run row's recorded window, the live tmux epoch) -> the window the notice belongs to
WID_CASES = [
    ({"window_id": "@5"}, None, "@5"),                                  # recorded at spawn
    ({"window_id": "@5", "window_epoch": "E2"}, "E2", "@5"),            # ...in this server
    ({"window_id": "@5", "window_epoch": "E1"}, "E2", "@9"),            # dead server: by name
    ({"window_id": "@5", "window_epoch": "E1", "window_name": "gone"}, "E2", None),
    ({"window_id": None}, "E2", "@9")]


@pytest.mark.parametrize("row,now_epoch,want", WID_CASES)
@pytest.mark.parametrize("name", ["CMX-473", "judge-CMX-473"])
def test_the_event_is_attributed_to_the_waiting_runs_window(row, now_epoch, want, name):
    runs = [_run("CMX-1", window_id="@3"), _run(**{"window_name": "cmx-473", **row})]
    [e] = inbox.slot_wait_events(runs, inbox._empty(), [], [_entry(name, 1, NOW - 31 * MIN)],
                                 NOW, windows={"@9": "cmx-473", "@3": "cmx-1"},
                                 now_epoch=now_epoch)
    assert e["wid"] == want


def test_a_waiter_no_run_owns_belongs_to_no_window():
    [e] = inbox.slot_wait_events([_run(window_name="nightly")], inbox._empty(), [],
                                 [_entry("nightly", 1, NOW - 31 * MIN)], NOW,
                                 windows={"@9": "nightly"}, now_epoch="E2")
    assert e["wid"] is None


def test_each_event_carries_its_own_waiters_facts():
    """Two overdue waiters whose every field differs, one of them a judge (rank != task rank)."""
    waiters = [_entry("judge-CMX-1", 0, NOW - 40 * MIN, task_rank=2),
               _entry("CMX-473", 1, NOW - 31 * MIN), _entry("CMX-7", 5, NOW)]
    holders = [_entry("CMX-482", 2, NOW), _entry("CMX-8", 3, NOW)]
    pr = "https://github.com/o/r/pull/5"
    events = inbox.slot_wait_events([_run("CMX-1", window_id="@1", pr_url=pr), _run()],
                                    inbox._empty(), holders, waiters, NOW)
    everyone = {"holders": ["CMX-482", "CMX-8"], "queued": ["judge-CMX-1", "CMX-473", "CMX-7"],
                "run_status": "running", "branch_name": "cmx-473", "pr_head_sha": None}
    assert [(e["wid"], e["payload"]) for e in events] == [
        ("@1", {"task_id": "CMX-1", "worktree": "/wts/judge-CMX-1", "waited_s": 2400, "rank": 0,
                "task_rank": 2, "pr_url": pr, "window_id": "@1", **everyone}),
        ("@5", {"task_id": "CMX-473", "worktree": "/wts/CMX-473", "waited_s": 1860, "rank": 1,
                "task_rank": 1, "pr_url": None, "window_id": "@5", **everyone})]
    assert [e["summary"].split(" has ")[0].split(" ", 1)[1] for e in events] == [
        inbox.sanitize_prompt("judge-CMX-1 (judge, High)"),
        inbox.sanitize_prompt("CMX-473 (Urgent)")]


def test_a_queued_notice_whose_wait_is_over_is_dropped():
    store = inbox._empty()
    gone = inbox._event("run_slot_wait", "x", {"worktree": "/wts/CMX-1"})
    still = inbox._event("run_slot_wait", "x", {"worktree": "/wts/CMX-2"})
    other = inbox._event("run_failed", "x", {"worktree": "/wts/CMX-1"})
    store["queue"] = [gone, still, other]
    inbox.slot_wait_events([], store, [], [_entry("CMX-2", 5, NOW)], NOW)
    assert store["queue"] == [still, other]


def test_the_notice_rots_once_its_pr_merged():
    e = inbox._event("run_slot_wait", "x", {"task_id": "CMX-473"})
    assert inbox.stale_reason(e, [_run(pr_state="merged")]) == "PR is merged"
    assert inbox.stale_reason(e, [_run()]) is None


def test_the_latch_survives_a_store_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    store = inbox._empty()
    store["slot_waits"] = {"/wts/x": 5.0}
    inbox.save(store)
    assert inbox.load()["slot_waits"] == {"/wts/x": 5.0}


def test_the_notice_is_never_held_for_the_digest():
    assert digest.route("run_slot_wait", digest.rules({"enabled": True})) == "interrupt"


def test_the_digest_names_the_notice_in_words():
    e = {"type": "run_slot_wait", "payload": {"task_id": "CMX-473"}, "summary": "x y z"}
    assert digest._what(e, {}) == "waits too long for a suite slot"


@pytest.mark.parametrize("row,now_epoch,want", WID_CASES)
def test_a_daemon_tick_queues_the_notice(box, busy_slot, monkeypatch, row, now_epoch, want):
    """End to end: the 11-23 shape — an Urgent waiter, 4 h in, behind a High holder — filed
    under the waiter's window, through the windows and tmux epoch the TICK read."""
    monkeypatch.setenv("CHELA_INBOX_FILE", str(box / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(box / "events.jsonl"))
    monkeypatch.setenv("CHELA_ORCHESTRATOR_WID", "@1")
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: {"@1": inbox.WAITING})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {"@1": "orchestrator", "@9": "cmx-473"})
    monkeypatch.setattr(inbox.epoch, "current", lambda: now_epoch)
    monkeypatch.setattr(inbox.messenger, "send_tmux", lambda wid, text: False)
    (box / "suite-slots" / "slot-0").write_text(json.dumps(
        {"pid": 11, "cwd": "/wts/CMX-482", "since": time.time(), "rank": 2}))
    _write_ledger(box, "CMX-473", 1, time.time() - 4 * HOUR)
    for _ in range(2):                                             # the second tick adds none
        inbox.tick({}, runs=[_run(**{"window_name": "cmx-473", **row})])
    [e] = [e for e in inbox.load()["queue"] if e["kind"] == "run_slot_wait"]
    assert e["wid"] == want
    assert "CMX-473" in e["summary"] and "has waited 4h00m for the suite slot" in e["summary"]
    assert e["payload"]["holders"] == ["CMX-482"]


# --- the tick refreshes runs.priority ------------------------------------------------------------

def _task(tid="CMX-473", priority=1):
    return Task(id=tid, title="t", file="", line_number=1, raw="", priority=priority)


def _priorities():
    with dispatcher._db() as conn:
        return {r["task_id"]: r["priority"] for r in conn.execute("SELECT * FROM runs")}


@pytest.mark.parametrize("stored,read,want,changed", [
    (3, 1, 1, 1), (None, 2, 2, 1), (1, 0, 0, 1), (1, 1, 1, 0),
    (1, None, 1, 0)])                        # the tracker gave no number: keep what we know
def test_refresh_priorities(box, stored, read, want, changed):
    _add_run(box, priority=stored, workflow_path="/w")
    with dispatcher._db() as conn:
        assert dispatcher._refresh_priorities(conn, "/w", [_task(priority=read)]) == changed
    assert _priorities() == {"CMX-473": want}


def test_refresh_priorities_stays_in_its_workflow_and_off_finished_runs(box):
    _add_run(box, "CMX-1", priority=3, workflow_path="/other")
    with dispatcher._db() as conn:
        for tid, status in (("CMX-2", "done"), ("CMX-3", "closed"), ("CMX-4", "failed"),
                            ("CMX-5", "awaiting_review")):
            _row(conn, tid, status=status, priority=3, workflow_path="/w")
        tasks = [_task(f"CMX-{i}") for i in range(1, 7)]          # CMX-6 has no run
        assert dispatcher._refresh_priorities(conn, "/w", tasks) == 1
    assert _priorities() == {"CMX-1": 3, "CMX-2": 3, "CMX-3": 3, "CMX-4": 3, "CMX-5": 1}


class _Source:
    def __init__(self, tasks, read_failed=False):
        self.tasks, self.read_failed = tasks, read_failed

    def list_open_tasks(self):
        return list(self.tasks)


@pytest.mark.parametrize("read_failed,want", [(False, 1), (True, 3)])
def test_a_tick_re_ranks_a_re_prioritized_run(box, read_failed, want):
    """...and is committed before the tick talks to GitHub, so no writer waits on the network."""
    wf = _wf(box)
    with dispatcher._db() as conn:
        _row(conn, "CMX-473", status="needs_human", priority=3, workflow_path=str(wf.path))
    seen = []

    def read_pr(*a):
        with sqlite3.connect(box / "scheduler.db") as other:       # another connection's view
            seen.append(other.execute("SELECT priority FROM runs").fetchone()[0])
        other.close()
        return None, None
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source",
                      return_value=_Source([_task(priority=1)], read_failed)), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_status", read_pr), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeGh().run):
        dispatcher.tick(wf.path)
    assert seen[0] == want
    assert _priorities() == {"CMX-473": want}
