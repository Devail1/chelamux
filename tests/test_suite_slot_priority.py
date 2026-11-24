"""⏳ CMX-445: the suite slot queue is ranked — judge rounds, then hand-ins by issue priority,
then the sweep — and a holder yields to a better-ranked waiter between two experiments.

11-19: a Medium self-check held the only slot for 2 h 08 m while an Urgent judge waited
1 h 13 m, and ``chela doctor`` called the queued judge PAST its wall."""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path

import pytest

from chela import dispatcher, judge, runtime_truth, suite_guard, sweep

MIN = 60.0


@pytest.fixture
def one_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(suite_guard.config, "CHELA_DIR", tmp_path)
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", "0")
    monkeypatch.delenv("CHELA_SUITE_SLOT_MAX_WAIT", raising=False)
    monkeypatch.setattr(suite_guard, "_waited", 0.0)
    monkeypatch.setattr(suite_guard, "_thread_waited", {})
    return tmp_path


# --- the rank of a hand-in, and of a ledger ----------------------------------------------

@pytest.mark.parametrize("priority,want", [
    (1, 1), (2, 2), (3, 3), (4, 4),           # Urgent, High, Medium, Low keep their number
    (0, 5), (None, 5), (5, 5), (6, 5), (99, 5), (-1, 5),   # none / unknown / garbage: after Low
    (True, 5), ("1", 5), (1.0, 5)])
def test_a_hand_ins_rank_is_its_tracker_priority(priority, want):
    assert suite_guard.handin_rank(priority) == want


@pytest.mark.parametrize("raw,want", [
    (0, 0), (3, 3), (6, 6), (None, 5), (7, 5), (-1, 5), (True, 5), ("0", 5)])
def test_a_ledger_rank_out_of_range_reads_as_an_unknown_hand_in(raw, want):
    assert suite_guard._rank(raw) == want


def test_the_rank_constants_are_the_documented_order():
    assert (suite_guard.RANK_JUDGE, suite_guard.RANK_URGENT, suite_guard.RANK_HIGH,
            suite_guard.RANK_MEDIUM, suite_guard.RANK_LOW, suite_guard.RANK_UNKNOWN,
            suite_guard.RANK_SWEEP) == (0, 1, 2, 3, 4, 5, 6)


# --- the waiter ordering -------------------------------------------------------------------

def _ledger(waits: Path, name: str, since: float | None, rank=None, *, pid=None, state=None):
    rec = {"pid": pid or os.getpid(), "start": suite_guard._proc_stat(os.getpid())[1],
           "cwd": f"/wt/{name}", "waiting_since": since, "waited": 0}
    if rank is not None:
        rec["rank"] = rank
    path = waits / f"{name}.json"
    path.write_text(json.dumps(rec))
    return path


def _admission_order(waits: Path, queued: dict) -> list[str]:
    """Who gets the slot, one free slot at a time: the one waiter nobody is ahead of."""
    paths = {n: _ledger(waits, n, since, rank) for n, (since, rank) in queued.items()}
    order = []
    while paths:
        free = [n for n, p in paths.items()
                if not suite_guard._must_queue(p, queued[n][0], suite_guard._rank(queued[n][1]))]
        assert len(free) == 1, free
        order.append(free[0])
        paths.pop(free[0]).unlink()
    return order


@pytest.fixture
def waits(one_slot):
    d = one_slot / "suite-slots" / "waits"
    d.mkdir(parents=True)
    return d


def test_the_waiters_go_judge_then_urgent_high_medium_low_then_the_sweep(waits):
    """Queued in the WORST possible arrival order — the sweep first, the judge last."""
    queued = {"sweep": (1.0, 6), "none": (2.0, 5), "low": (3.0, 4), "medium": (4.0, 3),
              "high": (5.0, 2), "urgent": (6.0, 1), "judge": (7.0, 0)}
    assert _admission_order(waits, queued) == ["judge", "urgent", "high", "medium", "low",
                                               "none", "sweep"]


@pytest.mark.parametrize("ahead,behind", [
    ("judge", "urgent"), ("urgent", "high"), ("high", "medium"), ("medium", "low"),
    ("low", "none"), ("none", "sweep")])
def test_each_better_rank_goes_first_even_when_it_came_later(waits, ahead, behind):
    rank = {"judge": 0, "urgent": 1, "high": 2, "medium": 3, "low": 4, "none": 5, "sweep": 6}
    queued = {behind: (1.0, rank[behind]), ahead: (2.0, rank[ahead])}
    assert _admission_order(waits, queued) == [ahead, behind]


def test_negative_control_equal_ranks_are_first_come_first_served(waits):
    """Today's order, kept among equals: two Medium hand-ins go oldest first — and with every
    rank equal, the Urgent judge that arrived last waits behind the Medium (the 11-19 order)."""
    assert _admission_order(waits, {"b": (2.0, 3), "a": (1.0, 3)}) == ["a", "b"]
    assert _admission_order(waits, {"medium": (1.0, 3), "judge": (2.0, 3)}) == ["medium",
                                                                                "judge"]


def test_a_legacy_ledger_without_a_rank_queues_as_an_unknown_hand_in(waits):
    old = _ledger(waits, "old", 1.0)                                   # no "rank" key
    assert suite_guard._must_queue(waits / "low.json", 2.0, 4) is False   # Low beats it
    assert suite_guard._must_queue(waits / "sw.json", 2.0, 6) is True     # the sweep does not
    assert suite_guard._must_queue(old, 1.0) is False


def test_a_waiter_with_a_garbage_since_is_skipped_not_fatal(waits):
    (waits / "bad.json").write_text(json.dumps({
        "pid": os.getpid(), "start": suite_guard._proc_stat(os.getpid())[1], "cwd": "/x",
        "waiting_since": "soon", "waited": 0, "rank": 0}))
    assert suite_guard._must_queue(waits / "me.json", 5.0, 6) is False


def test_the_ledger_publishes_its_rank(one_slot):
    led = suite_guard._Ledger("/wt/x", 2)
    led.write(12.0)
    assert json.loads(led.path.read_text())["rank"] == 2
    assert suite_guard._Ledger("/wt/y").rank == 5          # no rank given: unknown hand-in


# --- who a holder yields to ----------------------------------------------------------------

def test_outranking_names_only_strictly_better_live_waiters_best_first(waits, monkeypatch):
    _ledger(waits, "medium", 1.0, 3)
    _ledger(waits, "urgent", 3.0, 1)
    _ledger(waits, "judge", 4.0, 0)
    _ledger(waits, "high", 2.0, 2)
    _ledger(waits, "holding", None, 0)                 # holds a slot, is not asking
    assert suite_guard.outranking(3) == ["judge", "urgent", "high"]
    assert suite_guard.outranking(2) == ["judge", "urgent"]
    assert suite_guard.outranking(0) == []             # nothing outranks a judge
    assert suite_guard.outranking(3, exclude_pid=os.getpid()) == []


def test_a_stopped_waiter_is_not_yielded_to(waits, monkeypatch):
    _ledger(waits, "judge", 1.0, 0)
    for state, want in (("T", []), ("t", []), ("S", ["judge"])):
        monkeypatch.setattr(suite_guard, "_proc_stat",
                            lambda pid, _s=state: (_s, json.loads(
                                (waits / "judge.json").read_text())["start"]))
        assert suite_guard.outranking(3) == want


def test_outranking_skips_a_garbage_since(waits):
    (waits / "bad.json").write_text(json.dumps({
        "pid": os.getpid(), "start": suite_guard._proc_stat(os.getpid())[1], "cwd": "/x",
        "waiting_since": "soon", "waited": 0, "rank": 0}))
    assert suite_guard.outranking(3) == []


# --- yielding between experiments (the acceptance) -------------------------------------------

EXPERIMENT = 0.4          # one "experiment" — a stand-in for one full suite


def _medium_self_check(events, n=6, rank=3, *, yield_between=True):
    """A battery of ``n`` experiments holding the slot, yielding between two of them."""
    def body():
        with suite_guard.battery("/wt/CMX-443", "self-check", rank=rank, poll=0.01,
                                 max_wait=30):
            for i in range(n):
                if yield_between:
                    who = suite_guard.yield_slot()
                    if who:
                        events.append(("yielded", who, time.monotonic()))
                events.append(("experiment", i, time.monotonic()))
                time.sleep(EXPERIMENT)
            events.append(("done", None, time.monotonic()))
    return body


def _judge_battery(events, entered, rank=None):
    def body():
        with suite_guard.battery("/wt/CMX-440", "judge", rank=rank, poll=0.01, max_wait=30):
            events.append(("judge", None, time.monotonic()))
            entered.set()
            time.sleep(0.1)
    return body


def _race(rank_for_judge=None, *, yield_between=True):
    events, entered = [], threading.Event()
    holder = threading.Thread(target=_medium_self_check(events, yield_between=yield_between),
                              daemon=True)
    holder.start()
    deadline = time.monotonic() + 5
    while not any(e[0] == "experiment" for e in events) and time.monotonic() < deadline:
        time.sleep(0.01)
    asked = time.monotonic()
    judge_t = threading.Thread(target=_judge_battery(events, entered, rank_for_judge),
                               daemon=True)
    judge_t.start()
    assert entered.wait(15)
    holder.join(15)
    judge_t.join(15)
    return events, asked


def test_an_urgent_judge_gets_the_slot_within_one_experiment(one_slot):
    """ACCEPTANCE: a Medium self-check holds the only slot, an Urgent judge queues — the
    judge gets the slot within one experiment's time, and the self-check resumes after."""
    events, asked = _race()
    kinds = [e[0] for e in events]
    got = next(t for k, _w, t in events if k == "judge")
    assert got - asked < EXPERIMENT * 2          # one experiment (plus poll slack), not five
    assert kinds.index("judge") < kinds.index("done")
    assert ("yielded", "CMX-440") in [(k, w) for k, w, _t in events]
    assert sum(k == "experiment" for k in kinds) == 6        # it resumed and finished


def test_negative_control_an_equal_rank_waiter_waits_for_the_whole_battery(one_slot):
    """Today's first-come first-served: a waiter that does NOT outrank the holder waits out
    the whole battery — the 11-19 order."""
    events, _asked = _race(rank_for_judge=3)
    kinds = [e[0] for e in events]
    assert kinds.index("judge") > kinds.index("done")
    assert "yielded" not in kinds


def test_negative_control_a_battery_that_never_yields_keeps_the_slot(one_slot):
    events, _asked = _race(yield_between=False)
    kinds = [e[0] for e in events]
    assert kinds.index("judge") > kinds.index("done")


def test_yield_slot_outside_a_hold_is_a_no_op(one_slot, monkeypatch):
    monkeypatch.setattr(suite_guard, "outranking", lambda *a, **k: pytest.fail("asked"))
    assert suite_guard.yield_slot() == ""
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "0")
    with suite_guard.battery("/wt", "self-check"):
        assert suite_guard.yield_slot() == ""


def test_yield_slot_keeps_the_slot_when_nobody_outranks_it(one_slot, monkeypatch):
    with suite_guard.battery("/wt", "self-check", rank=3, max_wait=5):
        monkeypatch.setattr(suite_guard, "_acquire", lambda *a: pytest.fail("re-queued"))
        fd = suite_guard._held.fd
        assert suite_guard.yield_slot() == ""
        assert suite_guard._held.fd == fd


def _bounded(body, timeout=10.0):
    """Run ``body`` in a daemon thread and fail — never hang — if it does not finish: a
    re-acquire is unbounded, so a yield that never frees its slot would wait forever."""
    out = []

    def run():
        try:
            body()
            out.append(None)
        except BaseException as e:   # noqa: BLE001 — re-raised below
            out.append(e)
    t = threading.Thread(target=run, daemon=True)
    t.start()
    t.join(timeout)
    assert out, "hung: the yield never got its slot back"
    if out[0] is not None:
        raise out[0]


def test_yield_slot_asks_with_its_own_rank_and_requeues_at_it(one_slot, monkeypatch):
    seen = []
    real = suite_guard._acquire
    monkeypatch.setattr(suite_guard, "outranking",
                        lambda rank, exclude_pid=None: seen.append(rank) or ["CMX-1", "CMX-2"])
    claimed = []
    first = suite_guard._acquire
    monkeypatch.setattr(suite_guard, "_acquire",
                        lambda *a: claimed.append(a[7]) or first(*a))
    def body():
        with suite_guard.battery("/wt", "self-check", rank=4, poll=0.01, max_wait=7):
            monkeypatch.setattr(suite_guard, "_acquire",
                                lambda *a: seen.append(a[2:]) or real(*a))
            assert suite_guard.yield_slot() == "CMX-1, CMX-2"
            assert suite_guard.holders() == ["wt"]          # it holds the slot again
    _bounded(body)
    assert seen[0] == 4
    assert seen[1][:4] == ("/wt", 4, None, None)         # re-queued at its rank, unbounded
    assert seen[1][4] == 0.01 and seen[1][5] == claimed[0]   # ...at its ORIGINAL claim time
    assert not suite_guard.holders()


def test_yield_slot_re_queues_with_the_holds_own_wait_hook(one_slot, monkeypatch):
    """The re-wait is reported like the first one (a judge's status shows it queued): the
    hold's own ``on_wait`` rides into the re-acquire, not a dropped ``None``."""
    seen = []
    real = suite_guard._acquire
    monkeypatch.setattr(suite_guard, "outranking", lambda *a, **k: ["CMX-1"])

    def hook(_since):
        pass

    def body():
        with suite_guard.battery("/wt", "self-check", rank=3, poll=0.01, max_wait=5,
                                 on_wait=hook):
            monkeypatch.setattr(suite_guard, "_acquire",
                                lambda *a: seen.append(a) or real(*a))
            assert suite_guard.yield_slot() == "CMX-1"
    _bounded(body)
    [args] = seen
    assert args[4] is hook and args[5] is None               # its hook; no bound on the re-wait


def _queued(n, deadline=5.0):
    """Wait until ``n`` waiters have an OPEN wait in the ledger."""
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if sum(r.get("waiting_since") is not None for _p, r in suite_guard._ledgers()) >= n:
            return
        time.sleep(0.01)
    pytest.fail(f"{n} waiter(s) never queued")


def test_a_yielded_battery_takes_the_slot_back_before_a_later_equal(one_slot):
    """END TO END: a Medium battery holds the slot; a SECOND Medium queues behind it, then an
    Urgent judge. The battery yields to the judge — and when the judge is done, it resumes
    BEFORE the second Medium, which arrived after its claim but before its yield. Re-queued
    at "now" instead of its claim time, it would go behind."""
    order, judge_go = [], threading.Event()

    def judge_body():
        with suite_guard.battery("/wt/CMX-440", "judge", poll=0.01, max_wait=10):
            order.append("judge")
            judge_go.wait(10)

    def later_body():
        with suite_guard.battery("/wt/CMX-999", "self-check", rank=3, poll=0.01,
                                 max_wait=10):
            order.append("later")

    jt, lt = (threading.Thread(target=judge_body, daemon=True),
              threading.Thread(target=later_body, daemon=True))

    def body():
        with suite_guard.battery("/wt/CMX-443", "self-check", rank=3, poll=0.01,
                                 max_wait=10):
            order.append("first")
            lt.start()
            _queued(1)                                     # the later Medium is queued
            time.sleep(0.05)
            jt.start()
            _queued(2)                                     # ...and then the judge

            def release_judge():
                end = time.monotonic() + 10
                while "judge" not in order and time.monotonic() < end:
                    time.sleep(0.01)
                time.sleep(0.2)                            # both Mediums queued for it
                judge_go.set()
            threading.Thread(target=release_judge, daemon=True).start()
            assert suite_guard.yield_slot() == "CMX-440"
            order.append("first-resumed")
    _bounded(body)
    jt.join(10)
    lt.join(10)
    assert order == ["first", "judge", "first-resumed", "later"]


def test_a_failed_slot_record_write_frees_the_slot(one_slot, monkeypatch):
    """The slot is taken but its record cannot be written: the fd is closed, so the slot is
    free again — not leaked to a process that never ran."""
    d = one_slot / suite_guard.SLOTS_DIRNAME
    d.mkdir(parents=True, exist_ok=True)

    def boom(*_a):
        raise OSError("disk full")
    with monkeypatch.context() as m:
        m.setattr(suite_guard.os, "pwrite", boom)
        with pytest.raises(OSError):
            suite_guard._acquire(d, 1, "/wt", 3, None, 5, 0.01)
    fd = suite_guard._take(d, 1)
    assert fd is not None
    os.close(fd)


def test_yield_slot_frees_the_slot_for_the_waiter(one_slot, monkeypatch):
    """While it waits to take the slot back, the slot is FREE — not still held."""
    seen = []
    monkeypatch.setattr(suite_guard, "outranking", lambda *a, **k: ["CMX-1"])
    with suite_guard.battery("/wt", "self-check", rank=3, poll=0.01, max_wait=5):
        monkeypatch.setattr(suite_guard, "_acquire",
                            lambda d, k, *a: seen.append(suite_guard.holders())
                            or suite_guard._take(d, k))
        suite_guard.yield_slot()
    assert seen == [[]]


def test_a_failed_re_acquire_frees_cleanly(one_slot, monkeypatch):
    monkeypatch.setattr(suite_guard, "outranking", lambda *a, **k: ["CMX-1"])

    def never(*_a):
        raise suite_guard.SlotTimeout("no suite slot came free in 1s")
    with pytest.raises(suite_guard.SlotTimeout):
        with suite_guard.battery("/wt", "self-check", rank=3, max_wait=5):
            monkeypatch.setattr(suite_guard, "_acquire", never)
            suite_guard.yield_slot()
    assert suite_guard._held.fd is None and suite_guard._held.depth == 0
    assert not suite_guard.holders()


def test_yield_slot_says_who_it_yields_to(one_slot, monkeypatch, capsys):
    monkeypatch.setattr(suite_guard, "outranking", lambda *a, **k: ["CMX-440"])

    def body():
        with suite_guard.battery("/wt", "self-check", rank=3, poll=0.01, max_wait=5):
            suite_guard.yield_slot()
    _bounded(body)
    assert "yielding the suite slot to CMX-440 — it outranks this run (CMX-445)" in \
        capsys.readouterr().err


# --- each battery queues at the right rank -------------------------------------------------

@pytest.mark.parametrize("kind,rank,want", [
    # None (CMX-494): the rank of the run whose worktree this is, resolved while it queues.
    ("judge", None, 0), ("self-check", None, None), ("sweep", None, None),
    ("task-finished", None, None), ("task-finished", 1, 1), ("sweep", 6, 6), ("judge", 3, 3)])
def test_a_battery_queues_by_its_kind_or_its_explicit_rank(one_slot, monkeypatch, kind, rank,
                                                           want):
    got = []
    real = suite_guard._acquire
    monkeypatch.setattr(suite_guard, "_acquire", lambda *a: got.append(a[3]) or real(*a))
    with suite_guard.battery("/wt", kind, rank=rank, max_wait=5):
        pass
    assert got == [want]


def test_hand_in_battery_ranks_by_the_runs_priority(one_slot, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", one_slot / "scheduler.db")
    monkeypatch.chdir(one_slot)
    with dispatcher._db() as conn:
        for tid, prio in (("u", 1), ("m", 3), ("n", None), ("z", 0)):
            conn.execute(
                "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
                "worktree_path, branch_name, started_at, attempt, priority) "
                "VALUES (?, '', 'x', 'running', 'w', ?, 'b', '2026-11-19T00:00:00+00:00', 1, ?)",
                (tid, str(one_slot / tid), prio))
    got = []
    for tid in ("u", "m", "n", "z", "missing"):
        with dispatcher.hand_in_battery(tid):
            got += [h["rank"] for h in suite_guard.held()]
    assert got == [1, 3, 5, 5, 5]


# --- the yield points are wired --------------------------------------------------------------

def test_the_self_check_yields_before_every_experiment(monkeypatch, tmp_path):
    calls, ran = [], []
    answers = iter(["", "CMX-440", ""])
    monkeypatch.setattr(suite_guard, "yield_slot",
                        lambda: calls.append(len(ran)) or next(answers))
    monkeypatch.setattr(judge, "provision_suite_env", lambda wt: "")
    monkeypatch.setattr(judge, "ts_unparseable", lambda wt, items: "")
    green = judge.SuiteResult(True, 0, 1, 0, 0, "", "")
    monkeypatch.setattr(judge, "run_baseline", lambda *a, **k: (green, None, ""))

    def apply(_wt, _cmd, items, *_a):
        ran.append(items[0]["guard"])
        return [], ""
    monkeypatch.setattr(judge, "_apply_experiments", apply)
    from chela import sweep as sweep_mod
    rep = judge._self_check(tmp_path, "true", {"experiments": [
        {"guard": g, "file": "f.py", "before": "a", "after": "b"} for g in "xyz"]},
        timeout=30, select_tests=False, budget=sweep_mod.SweepBudget(900, 12),
        started=judge._now())
    assert ran == ["x", "y", "z"]
    assert calls == [0, 1, 2]                   # before each experiment, never mid-mutation
    assert rep.yielded_to == ["CMX-440"]


def _sweep_with(monkeypatch, tmp_path, *, should_yield=None, answers=("", "CMX-440", "")):
    calls, ran = [], []
    it = iter(answers)
    monkeypatch.setattr(suite_guard, "yield_slot",
                        lambda: calls.append(len(ran)) or next(it))
    muts = [sweep.Mutation(file="f.py", line=i, kind="k", before="a", after="b")
            for i in (1, 2, 3)]
    monkeypatch.setattr(sweep, "recover", lambda wt: ("", ""))
    monkeypatch.setattr(sweep, "collect_mutations", lambda wt, base, *a: (muts, ""))
    monkeypatch.setattr(sweep, "unsupported_files", lambda wt, base: ([], ""))
    monkeypatch.setattr(sweep, "generated_files", lambda wt, base: ([], ""))
    monkeypatch.setattr(sweep, "_start_guardian", lambda wt: None)
    monkeypatch.setattr(sweep, "_stop_guardian", lambda g: None)
    monkeypatch.setattr(sweep, "prioritise", lambda m: m)
    green = judge.SuiteResult(True, 0, 1, 0, 0, "", "")
    monkeypatch.setattr(sweep.judge, "run_baseline", lambda *a, **k: (green, None, ""))
    monkeypatch.setattr(sweep.judge, "provision_suite_env", lambda wt: "")

    def one(_wt, _cmd, mut, *_a):
        ran.append(mut.line)
        return sweep.SweepResult(mut, judge.KILLED, ""), ""
    monkeypatch.setattr(sweep, "_run_one", one)
    rep = sweep._sweep(tmp_path, "origin/dev", "true", timeout=30, select_tests=False,
                       progress=None, budget=sweep.SweepBudget(900, 12), started=sweep._now(),
                       should_yield=should_yield)
    return rep, calls, ran


def test_a_hand_in_sweep_pauses_for_a_better_waiter_and_resumes(monkeypatch, tmp_path):
    rep, calls, ran = _sweep_with(monkeypatch, tmp_path)
    assert ran == [1, 2, 3] and calls == [0, 1, 2]
    assert rep.paused_for == ["CMX-440"] and rep.yielded == ""


def test_the_nightly_sweep_stops_instead_and_never_pauses(monkeypatch, tmp_path):
    """A sweep with ``should_yield`` (the nightly one) gives the slot up for good — it never
    takes the pause-and-resume path."""
    rep, calls, ran = _sweep_with(monkeypatch, tmp_path, should_yield=lambda: "")
    assert ran == [1, 2, 3] and calls == []
    assert rep.paused_for == []


def test_the_sweep_queues_at_the_rank_it_is_given(monkeypatch, tmp_path):
    got = []

    @contextlib.contextmanager
    def spy(cwd, kind, on_wait=None, max_wait=None, poll=None, rank=None):
        got.append(rank)
        yield
    monkeypatch.setattr(suite_guard, "battery", spy)
    monkeypatch.setattr(sweep, "_sweep", lambda *a, **k: sweep.SweepReport())
    sweep.sweep(tmp_path, "origin/dev", "true", timeout=30)
    sweep.sweep(tmp_path, "origin/dev", "true", timeout=30, rank=6)
    assert got == [None, 6]




# --- the judge wall counts from the experiments, not the queue -------------------------------

def _doctor(r):
    [f] = runtime_truth._judge_runs_report(None, runtime_truth.observed([r]))
    return f


def _run(elapsed, **over):
    return {"task_id": "CMX-440", "pid": 1, "elapsed": elapsed, "detached": True,
            "log": None, **over}


def test_doctor_does_not_call_a_judge_queued_for_the_slot_past_its_wall():
    """11-19: 73 min in "baseline / setup", all of it queued — not a runaway."""
    now = time.time()
    f = _doctor(_run(73 * MIN, slot_waiting_since=now - 73 * MIN))
    assert f.level == runtime_truth.OK
    assert "PAST" not in f.detail
    assert "waiting for a suite slot (no experiment started yet)" in f.title
    assert "waited 73min for the suite slot" in f.title


def test_doctor_credits_a_finished_slot_wait_to_the_wall():
    f = _doctor(_run(80 * MIN, slot_wait_seconds=73 * MIN))
    assert f.level == runtime_truth.OK
    assert "waited 73min for the suite slot" in f.title
    assert "baseline / setup" in f.title


def test_negative_control_the_same_run_with_no_slot_wait_is_past_its_wall():
    f = _doctor(_run(80 * MIN))
    assert f.level == runtime_truth.WARN
    assert "PAST the 60min judge wall" in f.detail
    assert "waited" not in f.title


def test_doctor_still_warns_once_the_experiments_alone_pass_the_wall():
    f = _doctor(_run(140 * MIN, slot_wait_seconds=73 * MIN))
    assert f.level == runtime_truth.WARN


def test_a_short_wait_is_not_announced():
    f = _doctor(_run(5 * MIN, slot_wait_seconds=59))
    assert "waited" not in f.title


def test_a_wait_of_exactly_one_minute_is_announced():
    f = _doctor(_run(5 * MIN, slot_wait_seconds=60))
    assert "waited 1min for the suite slot" in f.title


def test_a_judge_queued_again_is_never_past_its_wall_however_long_it_ran_before():
    """The rule is "a run waiting for the slot is never past the wall" — not just "the queue
    time is subtracted": a run whose earlier experiments alone ran past the wall, and that is
    queued right now for 5 min, is reported as waiting, not as a runaway."""
    now = time.time()
    f = _doctor(_run(140 * MIN, slot_waiting_since=now - 5 * MIN, total=4, done=2))
    assert f.level == runtime_truth.OK
    assert "PAST" not in f.detail
    assert "waiting for a suite slot" in f.title
    assert "experiment 2/4" not in f.title


def test_a_judge_past_its_wall_names_its_progress_once_out_of_the_queue():
    f = _doctor(_run(140 * MIN, slot_wait_seconds=5 * MIN, total=4, done=2))
    assert f.level == runtime_truth.WARN
    assert "experiment 2/4" in f.title and "waiting for a suite slot" not in f.title


@pytest.mark.parametrize("status,want", [
    ({}, (False, 0.0)),
    (None, (False, 0.0)),
    ({"slot_wait_seconds": 120}, (False, 120.0)),
    ({"slot_waiting_since": 1000.0 - 30}, (True, 30.0)),
    ({"slot_waiting_since": 1000.0 - 30, "slot_wait_seconds": 60}, (True, 90.0)),
    ({"slot_waiting_since": 1000.0 + 30}, (True, 0.0)),          # a clock ahead: never < 0
    # ...and a clock ahead never eats into the time already queued
    ({"slot_waiting_since": 1000.0 + 30, "slot_wait_seconds": 60}, (True, 60.0)),
    ({"slot_wait_seconds": -45}, (False, 0.0)),                    # a garbage total: never < 0
    ({"slot_wait_seconds": True}, (False, 0.0)),                   # a bool is not a number
    ({"slot_waiting_since": "x", "slot_wait_seconds": "y"}, (False, 0.0)),
])
def test_judge_slot_wait(status, want):
    assert dispatcher.judge_slot_wait(status, 1000.0) == want


def test_a_yielded_holder_goes_back_ahead_of_an_equal_that_came_after_it(waits):
    """Re-queued at its ORIGINAL claim time: a Medium that yielded to a judge is not sent
    behind a Medium that arrived while it held the slot."""
    later = _ledger(waits, "later", 50.0, 3)
    me = _ledger(waits, "me", 10.0, 3)                       # claimed at 10, re-queued
    assert suite_guard._must_queue(me, 10.0, 3) is False
    assert suite_guard._must_queue(later, 50.0, 3) is True


def test_the_re_acquire_waits_past_max_wait(one_slot, monkeypatch):
    """A slow judge ahead of it does not time the yielded battery out: its experiments are
    kept, it just waits."""
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    seq = iter([["CMX-440"]])
    monkeypatch.setattr(suite_guard, "outranking", lambda *a, **k: next(seq, []))
    release, judge_in = threading.Event(), threading.Event()
    errors = []

    def judge_body():
        try:
            with suite_guard.battery("/wt/CMX-440", "judge", poll=0.01, max_wait=5):
                judge_in.set()
                release.wait(5)
        except BaseException as e:   # noqa: BLE001
            errors.append(e)
    t = threading.Thread(target=judge_body, daemon=True)

    def body():
        with suite_guard.battery("/wt/CMX-443", "self-check", rank=3, poll=0.01,
                                 max_wait=0.2):
            t.start()
            time.sleep(0.05)
            timer = threading.Timer(0.6, release.set)   # holds 3x the battery's max_wait
            timer.start()
            assert suite_guard.yield_slot() == "CMX-440"
            assert judge_in.is_set() and release.is_set()
    _bounded(body)
    t.join(5)
    assert not errors
