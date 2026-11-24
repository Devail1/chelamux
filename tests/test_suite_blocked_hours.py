"""⏳ CMX-479: the suite slot is not granted inside ``sweep.blocked_hours`` (default
03:00-04:30, the host's nautilus crons); a holder yields between two experiments when the
window opens; and an agent's battery killed while only WAITING is re-queued once."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from chela import dispatcher, judge, messenger, park, procguard, suite_guard


@pytest.fixture
def one_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(suite_guard.config, "CHELA_DIR", tmp_path)
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", "0")       # no systemd scope in a unit test
    monkeypatch.delenv("CHELA_SUITE_SLOT_MAX_WAIT", raising=False)
    monkeypatch.setattr(suite_guard, "_waited", 0.0)
    monkeypatch.setattr(suite_guard, "_thread_waited", {})
    return tmp_path


class FakeClock:
    """The slot's local wall clock; every ``time.sleep`` in suite_guard moves it ``step``."""

    def __init__(self, start: datetime, step: timedelta = timedelta(minutes=10)):
        self.now, self.step, self.sleeps = start, step, []

    def sleep(self, _s):
        self.sleeps.append(self.now)
        self.now += self.step


@pytest.fixture
def clock(monkeypatch):
    c = FakeClock(datetime(2026, 11, 23, 3, 10))
    monkeypatch.setattr(suite_guard, "_local_now", lambda: c.now)
    monkeypatch.setattr(suite_guard.time, "sleep", c.sleep)
    return c


def _records(root: Path) -> list[dict]:
    d = root / "suite-slots" / "batteries"
    return [json.loads(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []


def _ledger(root: Path) -> dict:
    [rec] = [json.loads(p.read_text()) for p in (root / "suite-slots" / "waits").glob("*.json")]
    return rec


# --- the acceptance: a task-finished hand-in queued at 03:10 waits, then starts by itself ----

@pytest.fixture
def tf_row(one_slot, monkeypatch, tmp_path):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    wt = tmp_path / "CMX-9"
    wt.mkdir()
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: TEST\njudge:\n  test_cmd: \"true\"\n"
                  "  suite_timeout_seconds: 45\n---\nbody\n")
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, window_id, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES ('t9', ?, 'x', 'running', 'cmx-9', '@9', ?, 'b', "
            "'2026-11-23T00:00:00+00:00', 1, 9)", (str(wf), str(wt)))
        conn.commit()
    return wt


def test_a_hand_in_inside_blocked_hours_waits_then_starts_by_itself(tf_row, clock, monkeypatch,
                                                                    capsys):
    ran, seen = [], {}

    def fake_shell(cmd, cwd, env, timeout, frozen=None):
        ran.append(clock.now)
        return SimpleNamespace(returncode=0, stdout="1 passed", stderr="")

    def sleep(s):
        if "ledger" not in seen:
            seen["ledger"] = _ledger(tf_row.parent)
            seen["records"] = _records(tf_row.parent)
            seen["park"] = park.state({"status": "running", "worktree_path": str(tf_row)})
        FakeClock.sleep(clock, s)

    monkeypatch.setattr(procguard, "run_shell_group", fake_shell)
    monkeypatch.setattr(suite_guard.time, "sleep", sleep)
    with dispatcher.hand_in_battery("t9"):
        assert [r["state"] for r in _records(tf_row.parent)] == ["running"]
        assert judge.run_suite("true", tf_row, timeout=30).green
    # Not one suite ran inside the window; the first ran at 04:30, on its own.
    assert ran == [datetime(2026, 11, 23, 4, 30)]
    assert clock.sleeps[0] == datetime(2026, 11, 23, 3, 10)
    assert seen["ledger"]["blocked_until"] == "04:30"
    assert seen["ledger"]["waiting_since"] is not None
    assert [r["state"] for r in seen["records"]] == ["waiting"]      # written BEFORE the wait
    assert seen["park"]["reason"] == ("waiting for a suite slot: blocked hours until 04:30 "
                                      "(`sweep.blocked_hours`)")
    err = capsys.readouterr().err
    assert err.count("⏳ blocked hours until 04:30 — the suite slot is not granted inside "
                     "them (`sweep.blocked_hours`, CMX-479)") == 1
    assert _records(tf_row.parent) == []                 # a battery that ended leaves none
    assert _ledger(tf_row.parent)["blocked_until"] is None


def test_outside_blocked_hours_the_slot_is_granted_at_once(one_slot, clock):
    clock.now = datetime(2026, 11, 23, 4, 30)            # the window's end is exclusive
    with suite_guard.slot(one_slot / "CMX-1", max_wait=1):
        pass
    assert clock.sleeps == []


def test_a_judge_round_at_0310_waits_too(one_slot, clock):
    with suite_guard.battery(one_slot / "PR-5", "judge", max_wait=60):
        assert clock.now == datetime(2026, 11, 23, 4, 30)


def test_admission_control_off_is_not_gated(one_slot, clock, monkeypatch):
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "0")
    with suite_guard.slot(one_slot / "CMX-1", max_wait=1):
        pass
    assert clock.sleeps == []


def test_blocked_time_does_not_count_toward_the_slot_timeout(one_slot, monkeypatch):
    """Blocked for ~0.5 s of real time, then the slot is busy for two polls: a 0.3 s
    ``max_wait`` that counted the blocked stretch would time out."""
    polls = {"n": 0}
    monkeypatch.setattr(suite_guard, "_local_now",
                        lambda: datetime(2026, 11, 23, 3, 10) if polls["n"] < 50
                        else datetime(2026, 11, 23, 4, 40))
    real_sleep = suite_guard.time.sleep

    def sleep(s):
        polls["n"] += 1
        real_sleep(0.01)

    busy = {"n": 0}

    def must_queue(*_a):
        busy["n"] += 1
        return busy["n"] <= 2

    monkeypatch.setattr(suite_guard.time, "sleep", sleep)
    monkeypatch.setattr(suite_guard, "_must_queue", must_queue)
    with suite_guard.slot(one_slot / "CMX-1", max_wait=0.3, poll=0.01):
        pass
    assert polls["n"] >= 50 and busy["n"] == 3


# --- which windows hold the slot -----------------------------------------------------------

def _wf(root: Path, sweep_block: str) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "WORKFLOW.md").write_text(f"---\nproject_key: TEST\n{sweep_block}---\nbody\n")
    return root


@pytest.mark.parametrize("block,at,until", [
    ("", (3, 30), "04:30"),                                   # no WORKFLOW key: the default
    ("sweep:\n  blocked_hours: [\"01:00-02:00\"]\n", (1, 30), "02:00"),
    ("sweep:\n  blocked_hours: [\"01:00-02:00\"]\n", (3, 30), ""),   # the workflow's, not ours
    ("sweep:\n  blocked_hours: [\"23:00-01:15\"]\n", (0, 30), "01:15"),   # wraps midnight
    ("sweep:\n  blocked_hours: [\"23:00-01:15\"]\n", (22, 59), ""),
    ("sweep:\n  blocked_hours: off\n", (3, 30), ""),
    ("sweep:\n  blocked_hours: [\"nonsense\"]\n", (3, 30), "04:30"),  # a typo never unblocks
])
def test_the_worktrees_workflow_decides_the_window(tmp_path, block, at, until):
    wt = _wf(tmp_path / "wt", block)
    now = datetime(2026, 11, 23, *at)
    assert suite_guard.blocked_until(suite_guard.blocked_windows(wt), now) == until


def test_no_workflow_is_the_default_window(tmp_path):
    assert suite_guard.blocked_windows(tmp_path) == [(180, 270)]


def test_an_unreadable_workflow_is_the_default_window_said_in_the_log(tmp_path, caplog):
    (tmp_path / "WORKFLOW.md").write_text("---\nproject_key: [unclosed\n")
    with caplog.at_level("WARNING", logger="chela.suite_guard"):
        assert suite_guard.blocked_windows(tmp_path) == [(180, 270)]
    assert "could not be read" in caplog.text and "03:00-04:30" in caplog.text


def test_blocked_until_reads_the_slot_clock_by_default(monkeypatch):
    monkeypatch.setattr(suite_guard, "_local_now", lambda: datetime(2026, 11, 23, 3, 0))
    assert suite_guard.blocked_until([(180, 270)]) == "04:30"
    monkeypatch.setattr(suite_guard, "_local_now", lambda: datetime(2026, 11, 23, 2, 59))
    assert suite_guard.blocked_until([(180, 270)]) == ""


# --- a holder yields when the window opens ------------------------------------------------

def test_a_holder_yields_when_blocked_hours_open_and_resumes_after(one_slot, clock, monkeypatch,
                                                                  capsys):
    clock.now = datetime(2026, 11, 23, 2, 55)
    held_during = []
    real = clock.sleep

    def sleep(s):
        held_during.append(suite_guard.holders())
        real(s)

    monkeypatch.setattr(suite_guard.time, "sleep", sleep)
    with suite_guard.battery(one_slot / "CMX-7", "self-check", max_wait=60):
        assert suite_guard.yield_slot() == ""                # 02:55: nothing to yield to
        clock.now = datetime(2026, 11, 23, 3, 5)             # the window opened mid-battery
        assert suite_guard.yield_slot() == "blocked hours until 04:30"
        assert clock.now >= datetime(2026, 11, 23, 4, 30)
        assert suite_guard.holders() == ["CMX-7"]            # it has the slot back
    assert held_during and all(h == [] for h in held_during)  # really let go while blocked
    assert "yielding the suite slot after this experiment" in capsys.readouterr().err


def test_a_battery_waiting_after_a_yield_is_recorded_as_waiting(one_slot, clock, monkeypatch):
    clock.now = datetime(2026, 11, 23, 2, 55)
    states = []

    def sleep(s):
        states.extend(r["state"] for r in _records(one_slot))
        clock.sleep(s)

    monkeypatch.setattr(suite_guard.time, "sleep", sleep)
    with suite_guard.battery(one_slot / "CMX-7", "self-check", max_wait=60):
        clock.now = datetime(2026, 11, 23, 3, 5)
        suite_guard.yield_slot()
        assert [r["state"] for r in _records(one_slot)] == ["running"]
    assert set(states) == {"waiting"}
    assert suite_guard._held.battery is None
    with suite_guard.slot(one_slot / "CMX-7", max_wait=1):     # no battery: no record
        clock.now = datetime(2026, 11, 23, 3, 5)
        suite_guard.yield_slot()
    assert _records(one_slot) == []


def test_a_state_that_cannot_be_written_drops_the_stale_waiting_record(one_slot, monkeypatch):
    real = suite_guard._battery_record
    monkeypatch.setattr(suite_guard, "_battery_record",
                        lambda c, k, s: None if s == "running" else real(c, k, s))
    with suite_guard.battery(one_slot / "CMX-7", "sweep", max_wait=1):
        assert _records(one_slot) == []


def test_a_blocked_waiter_holds_no_line_for_a_repo_that_is_not_blocked(one_slot):
    waits = one_slot / "suite-slots" / "waits"
    waits.mkdir(parents=True)
    rec = {"pid": os.getpid(), "start": suite_guard._proc_stat(os.getpid())[1], "cwd": "/w/A",
           "waiting_since": 1.0, "waited": 0, "rank": 0, "blocked_until": None}
    (waits / "a.json").write_text(json.dumps(rec))
    me = waits / "me.json"
    assert suite_guard._must_queue(me, 2.0, 3) is True
    assert suite_guard.outranking(3) == ["A"]
    (waits / "a.json").write_text(json.dumps({**rec, "blocked_until": "04:30"}))
    assert suite_guard._must_queue(me, 2.0, 3) is False
    assert suite_guard.outranking(3) == []


def _script_sleeps(monkeypatch, clock, steps):
    """Each slot poll runs the next of ``steps`` (a callable); one poll too many fails."""
    todo = iter(steps)

    def sleep(_s):
        step = next(todo, None)
        assert step is not None, f"still polling at {clock.now}: the wait never ended"
        step()

    monkeypatch.setattr(suite_guard.time, "sleep", sleep)


def _hold_the_slot(root: Path) -> int:
    d = root / "suite-slots"
    d.mkdir(parents=True, exist_ok=True)
    fd = suite_guard._take(d, 1)
    assert fd is not None
    return fd


def test_a_waiter_queued_before_the_window_is_held_by_it_too(one_slot, clock, monkeypatch):
    """Queued behind a holder at 02:55, the holder leaves at 03:05 — inside the window — and
    the free slot is still not granted before 04:30; then it is, and then a second window."""
    fd = _hold_the_slot(one_slot)
    clock.now = datetime(2026, 11, 23, 2, 55)
    seen = []

    def to(h, m, free=False):
        def step():
            seen.append((clock.now.strftime("%H:%M"), _ledger(one_slot)["blocked_until"]))
            clock.now = datetime(2026, 11, 23, h, m)
            if free:
                os.close(fd)
        return step

    _script_sleeps(monkeypatch, clock, [to(3, 5, free=True), to(4, 29), to(4, 30)])
    with suite_guard.slot(one_slot / "CMX-1"):
        assert clock.now == datetime(2026, 11, 23, 4, 30)
    assert seen == [("02:55", None), ("03:05", "04:30"), ("04:29", "04:30")]


def test_a_waiter_still_queued_after_the_window_holds_its_place_again(one_slot, clock,
                                                                     monkeypatch):
    """The slot is still taken at 04:30: the waiter's ledger stops saying blocked, so a
    later arrival queues behind it and a lower-ranked holder sees it; a second window
    (the next night's) says blocked again."""
    fd = _hold_the_slot(one_slot)
    seen = []

    def step(day, h, m, free=False):
        def run():
            rec = _ledger(one_slot)
            assert rec["waiting_since"] is not None
            other = one_slot / "suite-slots" / "waits" / "later.json"
            seen.append((rec["blocked_until"],
                         suite_guard._must_queue(other, rec["waiting_since"] + 1, 3),
                         suite_guard.outranking(3)))
            clock.now = datetime(2026, 11, day, h, m)
            if free:
                os.close(fd)
        return run

    _script_sleeps(monkeypatch, clock, [step(23, 4, 30), step(24, 3, 0),
                                        step(24, 4, 30, free=True)])
    with suite_guard.slot(one_slot / "CMX-1", rank=0):
        pass
    held, queued = ("04:30", False, []), (None, True, ["CMX-1"])
    assert seen == [held, queued, held]
    assert _ledger(one_slot)["blocked_until"] is None


@pytest.mark.parametrize("until,at,held", [
    ("04:30", (4, 29), True), ("04:30", (4, 30), False), ("04:30", (4, 34), False),
    ("04:30", (4, 35), True), ("23:58", (0, 2), False), ("23:58", (0, 3), True),
    ("00:00", (23, 59), True), ("00:00", (0, 0), False),
    ("soon", (4, 30), True), ("a:b:c", (4, 30), True), (None, (4, 0), False)])
def test_a_window_that_just_ended_hides_no_waiter(one_slot, clock, until, at, held):
    """Whoever polls first at 04:30 must not pass a better-ranked one still saying blocked."""
    waits = one_slot / "suite-slots" / "waits"
    waits.mkdir(parents=True)
    (waits / "a.json").write_text(json.dumps({
        "pid": os.getpid(), "start": suite_guard._proc_stat(os.getpid())[1], "cwd": "/w/A",
        "waiting_since": 1.0, "waited": 0, "rank": 1, "blocked_until": until}))
    clock.now = datetime(2026, 11, 23, *at)
    assert suite_guard._must_queue(waits / "me.json", 2.0, 3) is not held
    assert suite_guard.outranking(3) == ([] if held else ["A"])


def test_a_waiter_raised_inside_the_window_is_re_ranked_before_it_ends(one_slot, clock,
                                                                      monkeypatch):
    rank = {"now": 5}
    monkeypatch.setattr(suite_guard, "run_rank", lambda cwd: rank["now"])
    seen = []

    def poll(new, h, m):
        def step():
            seen.append((_ledger(one_slot)["rank"], _ledger(one_slot)["blocked_until"]))
            rank["now"] = new
            clock.now = datetime(2026, 11, 23, h, m)
        return step

    _script_sleeps(monkeypatch, clock, [poll(1, 4, 0), poll(1, 4, 30)])
    with suite_guard.slot(one_slot / "CMX-1"):
        assert suite_guard.held()[0]["rank"] == 1
    assert seen == [(5, "04:30"), (1, "04:30")]


def test_the_blocked_stretch_is_not_part_of_a_reported_wait(one_slot, clock, monkeypatch):
    """Queued 60 s before the window, blocked 5400 s, 60 s after it: a 120 s wait."""
    fd = _hold_the_slot(one_slot)
    clock.now = datetime(2026, 11, 23, 2, 59)
    real = {"t": 1000.0}
    monkeypatch.setattr(suite_guard.time, "time", lambda: real["t"])
    monkeypatch.setattr(suite_guard.time, "monotonic", lambda: real["t"])
    waited = []

    def after(seconds, h, m, free=False):
        def step():
            waited.append(real["t"] - suite_guard.queued()[0]["since"])
            real["t"] += seconds
            clock.now = datetime(2026, 11, 23, h, m)
            if free:
                os.close(fd)
        return step

    _script_sleeps(monkeypatch, clock, [after(60, 3, 0), after(5400, 4, 30), after(60, 4, 31),
                                        after(0, 4, 31, free=True)])
    with suite_guard.slot(one_slot / "CMX-1"):
        pass
    assert waited == [0, 60, 60, 120]


def test_after_the_window_the_slot_timeout_still_runs(one_slot, clock, monkeypatch):
    """Only the blocked stretch is excused: once it ends, a slot that stays busy times out."""
    fd = _hold_the_slot(one_slot)
    real = {"t": 1000.0}
    monkeypatch.setattr(suite_guard.time, "time", lambda: real["t"])
    monkeypatch.setattr(suite_guard.time, "monotonic", lambda: real["t"])

    def after(seconds, h, m):
        def step():
            real["t"] += seconds
            clock.now = datetime(2026, 11, 23, h, m)
        return step

    _script_sleeps(monkeypatch, clock, [after(4800, 4, 30), after(59, 4, 31), after(1, 4, 32)])
    with pytest.raises(suite_guard.SlotTimeout):
        with suite_guard.slot(one_slot / "CMX-1", max_wait=60):
            pass
    assert clock.now == datetime(2026, 11, 23, 4, 32)
    os.close(fd)


def test_a_wait_interrupted_inside_the_window_leaves_no_blocked_ledger(one_slot, clock,
                                                                       monkeypatch):
    def interrupt(_s):
        assert _ledger(one_slot)["blocked_until"] == "04:30"
        raise KeyboardInterrupt

    monkeypatch.setattr(suite_guard.time, "sleep", interrupt)
    with pytest.raises(KeyboardInterrupt):
        with suite_guard.slot(one_slot / "CMX-1"):
            pass
    rec = _ledger(one_slot)
    assert (rec["blocked_until"], rec["waiting_since"]) == (None, None)


def test_a_holder_yields_to_the_window_whoever_is_waiting_and_every_night(one_slot, clock,
                                                                         monkeypatch):
    """The window decides even with a better-ranked waiter in the line, and again the next
    night; a holder that never waited and one that already yielded both give way."""
    clock.now = datetime(2026, 11, 23, 2, 55)
    waits = one_slot / "suite-slots" / "waits"
    waits.mkdir(parents=True)
    peer = waits / "peer.json"
    rec = {"pid": os.getpid(), "start": suite_guard._proc_stat(os.getpid())[1], "cwd": "/w/A",
           "waiting_since": 1.0, "waited": 0, "rank": 0, "blocked_until": None}
    real = clock.sleep

    def sleep(s):
        peer.unlink(missing_ok=True)
        real(s)

    with suite_guard.slot(one_slot / "CMX-7", rank=3):
        monkeypatch.setattr(suite_guard.time, "sleep", sleep)
        for day in (23, 24):
            clock.now = datetime(2026, 11, day, 3, 0)
            peer.write_text(json.dumps(rec))
            assert suite_guard.outranking(3) == ["A"]
            assert suite_guard.yield_slot() == "blocked hours until 04:30"
            assert clock.now == datetime(2026, 11, day, 4, 30)


def test_the_judge_yields_between_experiments_never_before_the_first(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(suite_guard, "yield_slot", lambda: calls.append(1) or "")
    outcomes, err = judge._apply_each(tmp_path, "true", [{}, {}, {}], None, 30, None, None,
                                      progress=None, heartbeat=None)
    assert len(outcomes) == 3 and err == ""
    assert calls == [1, 1]


# --- the ledger and the park say why ------------------------------------------------------

def test_waits_in_reports_blocked_until_of_an_open_wait_only(one_slot, tmp_path):
    wt = tmp_path / "CMX-3"
    waits = one_slot / "suite-slots" / "waits"
    waits.mkdir(parents=True)
    me = suite_guard._proc_stat(os.getpid())[1]
    (waits / "a.json").write_text(json.dumps({
        "pid": os.getpid(), "start": me, "cwd": str(wt), "waiting_since": None, "waited": 3,
        "blocked_until": "09:99"}))
    assert suite_guard.waits_in(wt)["blocked_until"] is None     # a closed wait says nothing
    (waits / "b.json").write_text(json.dumps({
        "pid": os.getpid(), "start": me, "cwd": str(wt), "waiting_since": 5.0, "waited": 0,
        "blocked_until": 430}))
    assert suite_guard.waits_in(wt)["blocked_until"] is None     # not a time: ignored
    (waits / "c.json").write_text(json.dumps({
        "pid": os.getpid(), "start": me, "cwd": str(wt), "waiting_since": 6.0, "waited": 0,
        "blocked_until": "04:30"}))
    assert suite_guard.waits_in(wt)["blocked_until"] == "04:30"


@pytest.mark.parametrize("wait,reason", [
    ({"waiting_since": 1.0, "blocked_until": "04:30"},
     "waiting for a suite slot: blocked hours until 04:30 (`sweep.blocked_hours`)"),
    ({"waiting_since": 1.0, "blocked_until": None}, "waiting for a suite slot (holder: CMX-1)"),
    ({"waiting_since": 1.0}, "waiting for a suite slot (holder: CMX-1)"),
])
def test_the_park_names_blocked_hours_instead_of_a_holder(wait, reason):
    assert park.slot({"status": "running"}, wait, ["CMX-1"])["reason"] == reason


# --- a waiter the reaper killed is re-queued ONCE -----------------------------------------

def _dead_pid() -> int:
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def _stopped(root: Path, cwd: Path, kind: str = "task-finished", state: str = "waiting",
             name: str = "r") -> Path:
    d = root / "suite-slots" / "batteries"
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{name}.json"
    rec = {"pid": _dead_pid(), "start": "1", "cwd": str(cwd), "kind": kind, "since": 1.0}
    if state is not None:
        rec["state"] = state
    path.write_text(json.dumps(rec))
    return path


@pytest.fixture
def reaped(tf_row, monkeypatch):
    """A dead ``task-finished`` waiter in t9's worktree; memory back; noon; a peer that
    takes the message."""
    monkeypatch.setattr(suite_guard, "mem_available_bytes", lambda *a: 10 * 2**30)
    sent = []
    monkeypatch.setattr(messenger, "send_peer",
                        lambda wid, frm, text: sent.append((wid, frm, text))
                        or messenger.PeerSendResult(True, "sent"))
    return SimpleNamespace(path=_stopped(tf_row.parent, tf_row), wt=tf_row, sent=sent,
                           root=tf_row.parent)


def test_a_reaped_waiter_is_re_queued_once_through_its_own_agent(reaped):
    assert dispatcher.requeue_reaped_waiters() == ["t9"]
    [(wid, frm, text)] = reaped.sent
    assert (wid, frm) == ("@9", "chela")
    assert text.startswith("🧠 chela: your `chela task-finished` was killed (most likely by the "
                           "low-memory reaper) while it was WAITING for the suite slot")
    assert "once" in text and "if it is killed again, stop and report" in text
    assert not reaped.path.exists()
    assert suite_guard.requeued_recently(reaped.wt)
    # Killed again: NOT re-queued a second time — left for the stopped-runs notice.
    again = _stopped(reaped.root, reaped.wt, name="r2")
    assert dispatcher.requeue_reaped_waiters() == []
    assert len(reaped.sent) == 1 and again.exists()


@pytest.mark.parametrize("kind,cmd", [("sweep", "chela judge sweep"),
                                      ("self-check", "chela judge self-check")])
def test_each_agent_battery_is_re_queued_by_its_own_command(reaped, kind, cmd):
    reaped.path.unlink()
    _stopped(reaped.root, reaped.wt / "sub", kind=kind)     # a cwd inside the worktree
    assert dispatcher.requeue_reaped_waiters() == ["t9"]
    assert f"your `{cmd}` was killed" in reaped.sent[0][2]


@pytest.mark.parametrize("kind,state", [("judge", "waiting"), ("task-finished", "running"),
                                        ("task-finished", None), ("bogus", "waiting")])
def test_only_an_agent_battery_killed_while_waiting_is_re_queued(reaped, kind, state):
    reaped.path.unlink()
    path = _stopped(reaped.root, reaped.wt, kind=kind, state=state)
    assert dispatcher.requeue_reaped_waiters() == []
    assert reaped.sent == [] and path.exists()


def test_not_re_queued_inside_blocked_hours_nor_announced(reaped, monkeypatch):
    monkeypatch.setattr(suite_guard, "_local_now", lambda: datetime(2026, 11, 23, 3, 40))
    monkeypatch.setattr(suite_guard, "resumable", lambda: True)
    from chela import inbox
    monkeypatch.setattr(inbox, "enqueue", lambda *a: pytest.fail("announced inside the window"))
    assert dispatcher.requeue_reaped_waiters() == []
    assert dispatcher.notice_stopped_batteries() is None
    assert reaped.sent == [] and reaped.path.exists()
    monkeypatch.setattr(suite_guard, "_local_now", lambda: datetime(2026, 11, 23, 4, 30))
    assert dispatcher.requeue_reaped_waiters() == ["t9"]


def test_a_running_battery_is_still_announced_inside_blocked_hours(reaped, monkeypatch):
    monkeypatch.setattr(suite_guard, "_local_now", lambda: datetime(2026, 11, 23, 3, 40))
    monkeypatch.setattr(suite_guard, "resumable", lambda: True)
    reaped.path.unlink()
    _stopped(reaped.root, reaped.wt, state="running")
    from chela import inbox
    queued = []
    monkeypatch.setattr(inbox, "enqueue", lambda *a: queued.append(a) or True)
    assert dispatcher.notice_stopped_batteries()["runs"][0]["worktree"] == "CMX-9"
    assert len(queued) == 1


@pytest.mark.parametrize("avail", [None, suite_guard.RESUME_MEM_BYTES - 1])
def test_not_re_queued_until_memory_is_back(reaped, monkeypatch, avail):
    monkeypatch.setattr(suite_guard, "mem_available_bytes", lambda *a: avail)
    assert dispatcher.requeue_reaped_waiters() == []
    assert reaped.sent == [] and reaped.path.exists()


def test_re_queued_at_exactly_one_suites_peak(reaped, monkeypatch):
    monkeypatch.setattr(suite_guard, "mem_available_bytes",
                        lambda *a: suite_guard.RESUME_MEM_BYTES)
    assert dispatcher.requeue_reaped_waiters() == ["t9"]


@pytest.mark.parametrize("result", [messenger.PeerSendResult(False, None),
                                    messenger.PeerSendResult(True, "held"),
                                    messenger.PeerSendResult(True, "denied")])
def test_a_send_that_does_not_land_leaves_it_for_the_notice(reaped, monkeypatch, result,
                                                            caplog):
    monkeypatch.setattr(messenger, "send_peer", lambda *a: result)
    with caplog.at_level("WARNING", logger="chela.dispatcher"):
        assert dispatcher.requeue_reaped_waiters() == []
    assert reaped.path.exists() and not suite_guard.requeued_recently(reaped.wt)
    assert "could not re-queue t9's reaped chela task-finished" in caplog.text


def test_a_message_delivered_with_a_receipt_counts(reaped, monkeypatch):
    monkeypatch.setattr(messenger, "send_peer",
                        lambda *a: messenger.PeerSendResult(True, "delivered"))
    assert dispatcher.requeue_reaped_waiters() == ["t9"]


@pytest.mark.parametrize("sql", ["UPDATE runs SET status='awaiting_review'",
                                 "UPDATE runs SET window_id=NULL",
                                 "UPDATE runs SET worktree_path=NULL"])
def test_no_live_run_with_a_window_no_re_queue(reaped, sql):
    with dispatcher._db() as conn:
        conn.execute(sql)
        conn.commit()
    assert dispatcher.requeue_reaped_waiters() == []
    assert reaped.sent == [] and reaped.path.exists()


def test_a_run_without_a_worktree_does_not_hide_the_one_that_matches(reaped):
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET worktree_path=NULL")          # t9 comes first, no worktree
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, window_id, "
            "worktree_path, branch_name, started_at, attempt, task_number) "
            "VALUES ('t10', 'w', 'x', 'running', 'cmx-10', '@10', ?, 'b', "
            "'2026-11-23T00:00:00+00:00', 1, 10)", (str(reaped.wt),))
        conn.commit()
    assert dispatcher.requeue_reaped_waiters() == ["t10"]
    assert reaped.sent[0][0] == "@10"


def test_a_record_for_another_directory_is_not_this_runs(reaped, tmp_path):
    reaped.path.unlink()
    _stopped(reaped.root, tmp_path / "CMX-99")
    assert dispatcher.requeue_reaped_waiters() == []


@pytest.mark.parametrize("sub,resumed", [("", True), ("sub", True), ("../CMX-99", False)])
def test_a_command_its_agent_already_re_ran_is_not_asked_for_again(reaped, sub, resumed):
    d = reaped.path.parent
    (d / "0-junk.json").write_text("{not json")
    (d / "1-live.json").write_text(json.dumps({
        "pid": os.getpid(), "start": suite_guard._proc_stat(os.getpid())[1],
        "cwd": str(reaped.wt / sub), "kind": "sweep", "state": "waiting", "since": 2.0}))
    assert suite_guard.live_battery_in(reaped.wt) is resumed
    assert dispatcher.requeue_reaped_waiters() == ([] if resumed else ["t9"])
    assert len(reaped.sent) == (0 if resumed else 1)
    assert not reaped.path.exists() and (d / "1-live.json").exists()


def test_a_record_without_a_cwd_is_skipped(reaped):
    reaped.path.write_text(json.dumps({"pid": _dead_pid(), "start": "1", "cwd": None,
                                       "kind": "sweep", "state": "waiting", "since": 1.0}))
    assert dispatcher.requeue_reaped_waiters() == []


def test_the_re_queue_never_raises(one_slot, monkeypatch, caplog):
    monkeypatch.setattr(suite_guard, "mem_available_bytes", lambda *a: 10 * 2**30)

    def boom():
        raise RuntimeError("disk")
    monkeypatch.setattr(suite_guard, "stopped_batteries", boom)
    with caplog.at_level("ERROR", logger="chela.dispatcher"):
        assert dispatcher.requeue_reaped_waiters() == []
    assert "could not re-queue the reaped suite waiters" in caplog.text


def test_every_tick_re_queues_before_it_announces(monkeypatch, tmp_path):
    """Driven, not read: on a healthy tick and on one running on the last good config."""
    calls = []
    monkeypatch.setattr(dispatcher, "requeue_reaped_waiters", lambda: calls.append("requeue"))
    monkeypatch.setattr(dispatcher, "notice_stopped_batteries", lambda: calls.append("announce"))

    class Stop(Exception):
        pass

    def stop(_wf):
        raise Stop

    monkeypatch.setattr(dispatcher, "get_source", stop)
    wf = tmp_path / "WORKFLOW.md"
    good = (f"---\nproject_key: TEST\nworkspace:\n  root: {tmp_path / '.chela' / 'wts'}\n"
            "---\nbody\n")
    for n, (text, healthy) in enumerate([(good, True), (good, True),
                                         ("---\nproject_key: [unclosed\n", False)], start=1):
        wf.write_text(text)
        assert (dispatcher.load_workflow_cached(wf).error is None) is healthy
        with pytest.raises(Stop):
            dispatcher.tick(wf)
        assert calls == ["requeue", "announce"] * n


# --- once means once ------------------------------------------------------------------------

def test_requeued_recently_window(one_slot, tmp_path):
    wt = tmp_path / "CMX-1"
    assert suite_guard.requeued_recently(wt) is False
    suite_guard.mark_requeued(wt, now=1000.0)
    assert suite_guard.requeued_recently(wt, now=1000.0 + 12 * 3600 - 1) is True
    assert suite_guard.requeued_recently(wt, now=1000.0 + 12 * 3600) is False
    assert suite_guard.requeued_recently(tmp_path / "CMX-2", now=1001.0) is False


def test_mark_requeued_drops_old_entries(one_slot, tmp_path):
    suite_guard.mark_requeued(tmp_path / "old", now=0.0)
    suite_guard.mark_requeued(tmp_path / "new", now=12 * 3600.0)
    data = json.loads((one_slot / "suite-slots" / "requeued.json").read_text())
    assert list(data) == [str((tmp_path / "new").resolve())]


@pytest.mark.parametrize("raw", ["{not json", "[1, 2]", json.dumps({"X": "soon"})])
def test_an_unreadable_requeue_record_never_buys_a_second_re_queue(one_slot, tmp_path, raw):
    path = one_slot / "suite-slots" / "requeued.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw.replace("X", str((tmp_path / "CMX-1").resolve())))
    assert suite_guard.requeued_recently(tmp_path / "CMX-1") is True


def test_mark_requeued_that_cannot_write_says_so(one_slot, tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(suite_guard, "_requeued_path", lambda: one_slot / "f" / "requeued.json")
    (one_slot / "f").write_text("a file, not a dir")
    with caplog.at_level("WARNING", logger="chela.suite_guard"):
        suite_guard.mark_requeued(tmp_path / "CMX-1")
    assert "could not record the re-queue" in caplog.text
