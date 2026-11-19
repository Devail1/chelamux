"""⏳ CMX-436 — what holds the idle sweep off: a dispatch pause (``chela dispatch --pause``) and
``sweep.blocked_hours`` (default 03:00-04:30, the host's cron window). Neither starts a sweep, a
running one yields to either between two mutations, and the fallback (05:00 by default) fires
only inside its own window and never inside a blocked one.

Scheduler tests drive :func:`chela.sweep_nightly.daemon_tick` with a fake idle probe and a temp
``CHELA_DIR``; the yield tests run the REAL battery (:func:`chela.sweep.sweep`).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

from chela import config, hold, judge, suite_guard, sweep, sweep_nightly
from chela.workflow import load_workflow, parse_workflow
from tests.test_sweep_nightly import WEAK_MUT, Filer, _repo, _run, _workflow_text
from tests.test_sweep_nightly_idle import GUARDS, _at, _Box, _Child

NIGHT = datetime(2026, 11, 18, 1, 0)
HOLD_FILE = "dispatch-hold.json"          # chela.hold.HOLD_FILE_NAME — what --pause writes


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    got: list[tuple[str, str, dict]] = []
    monkeypatch.setattr("chela.inbox.enqueue",
                        lambda kind, summary, payload: got.append((kind, summary, payload)))
    return got


@pytest.fixture
def sched(tmp_path, monkeypatch):
    p = tmp_path / "WORKFLOW.md"

    def write(extra=""):
        p.write_text(_workflow_text(tmp_path, extra=extra))
    write()
    spawned: list[tuple] = []
    monkeypatch.setattr(sweep_nightly, "spawn", lambda path, d=None, t=None: spawned.append(
        (path, t)) or _Child())
    box, watch, chela = _Box(), sweep_nightly.IdleWatch(), tmp_path / "chela"
    chela.mkdir()

    def tick(at, prev=None):
        return sweep_nightly.daemon_tick([p], prev, at, chela, probe=box, watch=watch)
    return type("S", (), {"path": p, "write": staticmethod(write), "spawned": spawned,
                          "box": box, "tick": staticmethod(tick), "chela": chela})


@pytest.fixture
def one_slot(monkeypatch, tmp_path):
    """A temp suite slot of one, as in tests/test_sweep_nightly_idle.py."""
    monkeypatch.setattr(suite_guard.config, "CHELA_DIR", tmp_path / "chela")
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", "0")
    monkeypatch.delenv("CHELA_SUITE_SLOT_MAX_WAIT", raising=False)
    monkeypatch.setattr(suite_guard, "_waited", 0.0)
    monkeypatch.setattr(suite_guard, "_thread_waited", {})
    monkeypatch.setattr(suite_guard, "SLOT_POLL_SECONDS", 0.02)
    return tmp_path


def _pause(chela: Path, expires_at: float) -> None:
    (chela / HOLD_FILE).write_text(json.dumps({"reason": "gaming", "by": "liav", "pid": 1,
                                               "created_at": 0.0, "expires_at": expires_at}))


def _wf(tmp_path: Path, extra: str = ""):
    return parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(tmp_path, extra=extra))


# --- (a) a dispatch pause: no start, and a running sweep yields --------------------------------


def test_a_dispatch_pause_blocks_an_idle_start_until_it_is_released(sched):
    _pause(sched.chela, _at(60).timestamp())
    for m in (0, 10, 30, 59):                          # idle and quiet, but paused
        assert sched.tick(_at(m)) is None
    assert sched.spawned == []
    (sched.chela / HOLD_FILE).unlink()
    assert sched.tick(_at(60)) is not None             # quiet all along: starts once released
    assert sched.spawned == [(sched.path, "idle")]


def test_a_dispatch_pause_blocks_the_fallback_too(sched):
    sched.box.busy = "the suite slot is held by CMX-12"
    _pause(sched.chela, _at(10 * 60).timestamp())
    for m in (240, 241, 300):                          # 05:00 .. 06:00: the fallback window
        assert sched.tick(_at(m)) is None
    assert sched.spawned == []


def test_a_paused_queue_negative_control_ignoring_the_flag_starts_the_sweep(sched,
                                                                              monkeypatch):
    """The acceptance assertion above goes RED when the pause flag is ignored."""
    monkeypatch.setattr(sweep_nightly, "dispatch_paused", lambda *a, **k: False)
    _pause(sched.chela, _at(60).timestamp())
    sched.tick(_at(0))
    assert sched.tick(_at(10)) is not None
    assert sched.spawned == [(sched.path, "idle")]


def test_an_expired_pause_holds_nothing(sched):
    _pause(sched.chela, _at(5).timestamp())           # its TTL runs out at 01:05
    sched.tick(_at(0))
    assert sched.tick(_at(10)) is not None


def test_dispatch_paused_reads_what_chela_dispatch_pause_writes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    assert sweep_nightly.dispatch_paused(tmp_path) is False
    held = hold.take("gaming", ttl_seconds=600, by="liav")
    assert (tmp_path / HOLD_FILE).exists()
    assert sweep_nightly.dispatch_paused(tmp_path, held.created_at + 1) is True
    assert sweep_nightly.dispatch_paused(tmp_path, held.expires_at - 0.001) is True
    assert sweep_nightly.dispatch_paused(tmp_path, held.expires_at) is False   # its TTL
    assert sweep_nightly.dispatch_paused(None, held.created_at + 1) is True    # CHELA_DIR
    hold.release("liav")
    assert sweep_nightly.dispatch_paused(tmp_path) is False


@pytest.mark.parametrize("body", ["{x", "[]", json.dumps({"reason": "no expiry"}),
                                  json.dumps({"expires_at": "soon"})])
def test_an_unreadable_dispatch_hold_holds_the_sweep_off(tmp_path, body, caplog):
    (tmp_path / HOLD_FILE).write_text(body)
    assert sweep_nightly.dispatch_paused(tmp_path, 0.0) is True
    assert "unreadable" in caplog.text


def test_a_hold_path_that_cannot_be_read_holds_the_sweep_off(tmp_path):
    (tmp_path / HOLD_FILE).mkdir()                    # IsADirectoryError: an OSError
    assert sweep_nightly.dispatch_paused(tmp_path, 0.0) is True


def test_a_dispatch_pause_is_not_an_idle_box(tmp_path, monkeypatch):
    monkeypatch.setattr(suite_guard, "holders", lambda: [])
    monkeypatch.setattr(suite_guard, "waiters", lambda exclude_pid=None: [])
    monkeypatch.setattr(suite_guard, "mem_available_bytes",
                        lambda: 10 * suite_guard.SUITE_PEAK_BYTES)
    assert sweep_nightly.box_idle(tmp_path) == (True, "")
    _pause(tmp_path, 4e9)
    assert sweep_nightly.box_idle(tmp_path) == (False, "dispatch is paused")


def _battery(tmp_path: Path, monkeypatch, should_yield, on_first) -> sweep.SweepReport:
    """The REAL battery over four guards (under ``one_slot``: a temp suite slot); ``on_first``
    runs during the first mutation. Nobody waits for the slot."""
    wt = tmp_path / "nightly-sweep"
    wt.mkdir()
    (wt / "m.py").write_text(GUARDS)
    real, seen = sweep._apply_and_measure, []

    def measured(*a, **kw):
        if not seen:
            seen.append(1)
            on_first()
        return real(*a, **kw)
    monkeypatch.setattr(sweep, "_apply_and_measure", measured)
    monkeypatch.setattr(sweep_nightly, "slot_wanted", lambda: "")
    rep = sweep.sweep(wt, "label", f"{sys.executable} -c pass", timeout=60,
                      select_tests=False, provision=False, lines={"m.py": {2, 4, 6, 8}},
                      should_yield=should_yield)
    assert (wt / "m.py").read_text() == GUARDS          # every mutation restored
    return rep


def test_a_pause_that_starts_mid_sweep_yields_between_mutations(one_slot, monkeypatch):
    tmp_path, chela = one_slot, one_slot / "chela"
    chela.mkdir()
    check = sweep_nightly.YieldCheck(_wf(tmp_path), chela, clock=lambda: NIGHT)
    rep = _battery(tmp_path, monkeypatch, check, lambda: _pause(chela, 4e9))
    assert rep.yielded == "dispatch paused" and check.reason == "dispatch_paused"
    assert rep.measured == 1 and len(rep._with(sweep.UNRUN)) == len(rep.results) - 1 >= 3


def test_negative_control_a_check_that_ignores_the_pause_runs_the_whole_battery(
        one_slot, monkeypatch):
    tmp_path, chela = one_slot, one_slot / "chela"
    chela.mkdir()
    monkeypatch.setattr(sweep_nightly, "dispatch_paused", lambda *a, **k: False)
    check = sweep_nightly.YieldCheck(_wf(tmp_path), chela, clock=lambda: NIGHT)
    rep = _battery(tmp_path, monkeypatch, check, lambda: _pause(chela, 4e9))
    assert rep.yielded == "" and rep.measured == len(rep.results) >= 4


def test_the_yield_check_falls_through_to_the_suite_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep_nightly, "slot_wanted", lambda: "CMX-12")
    check = sweep_nightly.YieldCheck(_wf(tmp_path), tmp_path, clock=lambda: NIGHT)
    assert (check(), check.reason) == ("CMX-12", "")
    _pause(tmp_path, 4e9)
    assert (check(), check.reason) == ("dispatch paused", "dispatch_paused")


def _yield_on_call(monkeypatch, then):
    """A battery that asks its yield check once, after ``then`` ran — as the real one does
    between two mutations — and stops where it says."""
    seen: dict = {}

    def fake(worktree, base, test_cmd, **kw):
        then()
        seen["check"] = kw["should_yield"]
        wanted = kw["should_yield"]()
        late = sweep.Mutation("other.py", 3, "return-code", "        return 1",
                              "        return 0")
        return sweep.SweepReport(results=[sweep.SweepResult(WEAK_MUT, judge.KILLED),
                                          sweep.SweepResult(late, sweep.UNRUN)],
                                 yielded=wanted, seconds=300.0, battery_seconds=300.0)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)
    return seen


def test_a_paused_run_says_yielded_dispatch_paused_and_keeps_its_day_open(
        tmp_path, monkeypatch, notices):
    repo, chela = _repo(tmp_path, extra="sweep:\n  blocked_hours: []\n"), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    sweep_nightly.mark_day_started(wf, datetime.now(), "idle", chela)
    _yield_on_call(monkeypatch, lambda: _pause(chela, 4e9))
    res = _run(repo, chela, Filer(), trigger="idle")
    assert (res["yielded_to"], res["yield_reason"]) == ("dispatch paused", "dispatch_paused")
    assert res["why"] == ("yielded: dispatch paused; 1 mutation(s) it did not reach are owed "
                          "to the next run")
    assert " — ⏳ yielded: dispatch paused — " in notices[0][1]
    assert "yielded the suite slot" not in notices[0][1]
    day = json.loads((sweep_nightly.state_dir(chela) / "day.json").read_text())[str(wf.path)]
    assert (day["done"], day["pending"]) == (False, False)        # it resumes later


def test_a_hand_run_yields_only_to_the_slot(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    monkeypatch.setattr(sweep_nightly, "slot_wanted", lambda: "")
    seen = _yield_on_call(monkeypatch, lambda: None)
    res = _run(repo, chela, Filer())
    assert seen["check"] is sweep_nightly.slot_wanted and res["yield_reason"] == ""


@pytest.mark.parametrize("trigger", ["idle", "fallback", "fixed"])
def test_a_daemon_run_that_starts_into_a_pause_skips_and_says_so(tmp_path, monkeypatch,
                                                                notices, trigger):
    repo, chela = _repo(tmp_path, extra="sweep:\n  blocked_hours: []\n"), tmp_path / "chela"
    chela.mkdir()
    wf = load_workflow(repo / "WORKFLOW.md")
    sweep_nightly.mark_day_started(wf, datetime.now(), trigger, chela)
    _pause(chela, 4e9)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", lambda *a, **k: pytest.fail("swept"))
    res = _run(repo, chela, Filer(), trigger=trigger)
    assert (res["status"], res["reason"]) == ("skipped", "dispatch_paused")
    assert notices[0][1].startswith(
        "🧹🌙 nightly sweep (repo), started ")
    assert ": skipped — dispatch paused (`chela dispatch --pause`) — the box stays quiet" \
        in notices[0][1]
    if trigger != "fixed":
        day = json.loads((sweep_nightly.state_dir(chela) / "day.json").read_text())[
            str(wf.path)]
        assert day["done"] is False                    # a passing cause: the day stays open


def test_a_hand_run_during_a_pause_still_runs(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    chela.mkdir()
    _pause(chela, 4e9)
    monkeypatch.setattr(sweep_nightly, "slot_wanted", lambda: "")
    _yield_on_call(monkeypatch, lambda: None)
    res = _run(repo, chela, Filer())
    assert res.get("reason") != "dispatch_paused" and "mutations" in res


# --- (b) blocked hours: no start inside, a yield at the window's start ---------------------------


def test_blocked_hours_block_an_idle_start_and_it_starts_at_their_end(sched):
    night = datetime(2026, 11, 18, 2, 55)
    assert sched.tick(night) is None                                  # quiet from 02:55
    for at in ((3, 5), (3, 30), (4, 29)):                             # quiet, but 03:00-04:30
        assert sched.tick(datetime(2026, 11, 18, *at)) is None
    assert sched.spawned == []
    assert sched.tick(datetime(2026, 11, 18, 4, 30)) is not None
    assert sched.spawned == [(sched.path, "idle")]


def test_blocked_hours_negative_control_without_them_it_starts_at_0305(sched):
    sched.write("sweep:\n  blocked_hours: []\n")
    sched.tick(datetime(2026, 11, 18, 2, 55))
    assert sched.tick(datetime(2026, 11, 18, 3, 5)) is not None


@pytest.mark.parametrize("hm, blocked", [
    ((22, 59), False), ((23, 0), True), ((23, 59), True), ((0, 0), True), ((0, 59), True),
    ((1, 0), False), ((12, 0), False),
])
def test_a_window_that_wraps_midnight(tmp_path, hm, blocked):
    wf = _wf(tmp_path, "sweep:\n  blocked_hours: ['23:00-01:00']\n")
    now = datetime(2026, 11, 18, *hm)
    assert sweep_nightly.blocked_window(wf, now) == ("23:00-01:00" if blocked else "")
    assert sweep_nightly.hold_off(wf, now, tmp_path) == (
        ("blocked_hours", "blocked hours 23:00-01:00") if blocked else ("", ""))


def test_a_wrapping_window_blocks_the_daemon_across_midnight(sched):
    sched.write("sweep:\n  blocked_hours: ['23:00-01:00']\n")
    sched.tick(datetime(2026, 11, 18, 22, 45))
    assert sched.tick(datetime(2026, 11, 18, 23, 30)) is None
    assert sched.tick(datetime(2026, 11, 19, 0, 30)) is None
    assert sched.tick(datetime(2026, 11, 19, 1, 0)) is not None


def test_a_running_sweep_yields_at_the_windows_start(one_slot, monkeypatch):
    tmp_path = one_slot
    clock = iter([datetime(2026, 11, 18, 2, 59), datetime(2026, 11, 18, 3, 0)])
    check = sweep_nightly.YieldCheck(_wf(tmp_path), tmp_path, clock=lambda: next(clock))
    rep = _battery(tmp_path, monkeypatch, check, lambda: None)
    assert rep.yielded == "blocked hours 03:00-04:30" and check.reason == "blocked_hours"
    assert rep.measured == 2                       # 02:59 went on; 03:00 stopped it


def test_a_wrapping_window_yields_a_running_sweep_after_midnight(tmp_path):
    wf = _wf(tmp_path, "sweep:\n  blocked_hours: ['23:30-00:30']\n")
    check = sweep_nightly.YieldCheck(wf, tmp_path, clock=lambda: datetime(2026, 11, 19, 0, 10))
    assert (check(), check.reason) == ("blocked hours 23:30-00:30", "blocked_hours")


def test_a_run_held_by_blocked_hours_says_which(tmp_path, monkeypatch, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    at = datetime.now().replace(hour=3, minute=30, second=0, microsecond=0)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", lambda *a, **k: pytest.fail("swept"))
    res = _run(repo, chela, Filer(), trigger="idle", now=at.timestamp())
    assert (res["status"], res["reason"]) == ("skipped", "blocked_hours")
    assert res["why"] == "inside blocked hours 03:00-04:30 (`sweep.blocked_hours`)"
    assert "skipped — inside blocked hours 03:00-04:30 (`sweep.blocked_hours`)" in notices[0][1]


def test_a_blocked_yield_notice_says_which(tmp_path, monkeypatch, notices):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    sweep_nightly.mark_day_started(wf, datetime.now(), "idle", chela)
    monkeypatch.setattr(sweep_nightly, "blocked_window",
                        lambda wf, now: "03:00-04:30")
    real = sweep_nightly.hold_off
    calls = []

    def first_clear(wf, now, chela_dir=None):      # clear at the start, blocked at the yield
        calls.append(1)
        return ("", "") if len(calls) == 1 else real(wf, now, chela_dir)
    monkeypatch.setattr(sweep_nightly, "hold_off", first_clear)
    _yield_on_call(monkeypatch, lambda: None)
    res = _run(repo, chela, Filer(), trigger="idle")
    assert res["yield_reason"] == "blocked_hours"
    assert " — ⏳ yielded: blocked hours 03:00-04:30" in notices[0][1]


@pytest.mark.parametrize("yaml_text, want", [
    ("", [(180, 270)]),                                         # the default: 03:00-04:30
    ("sweep:\n  blocked_hours: []\n", []),
    ("sweep:\n  blocked_hours: 'off'\n", []),
    ("sweep:\n  blocked_hours: false\n", []),
    ("sweep:\n  blocked_hours: '12:00-13:00'\n", [(720, 780)]),
    ("sweep:\n  blocked_hours: ['1:00 - 2:15', '22:00-06:00']\n", [(60, 135), (1320, 360)]),
    ("sweep:\n  blocked_hours: ['03:00-03:00']\n", [(180, 270)]),     # empty: the default
    ("sweep:\n  blocked_hours: ['03:00-04:30', '25:00-26:00']\n", [(180, 270)]),
    ("sweep:\n  blocked_hours: ['3-4']\n", [(180, 270)]),
    ("sweep:\n  blocked_hours: ['x03:00-04:00']\n", [(180, 270)]),
    ("sweep:\n  blocked_hours: ['03:00-04:00x']\n", [(180, 270)]),
    ("sweep:\n  blocked_hours: [\"03:00-04:00\\n\"]\n", [(180, 240)]),  # strip()ped
    ("sweep:\n  blocked_hours: [300]\n", [(180, 270)]),
    ("sweep:\n  blocked_hours: {a: b}\n", [(180, 270)]),
])
def test_blocked_hours_config(tmp_path, yaml_text, want):
    assert sweep_nightly.blocked_hours(_wf(tmp_path, yaml_text)) == want


def test_unset_blocked_hours_is_the_default_without_a_warning(tmp_path, caplog):
    assert sweep_nightly.blocked_hours(_wf(tmp_path)) == [(180, 270)]
    assert "blocked_hours" not in caplog.text


def test_a_malformed_blocked_hours_is_said(tmp_path, caplog):
    sweep_nightly.blocked_hours(_wf(tmp_path, "sweep:\n  blocked_hours: ['3-4']\n"))
    assert "is not a list of HH:MM-HH:MM — using ['03:00-04:30']" in caplog.text


# --- (c) the fallback: 05:00, never inside a blocked window, only in its own window ------------


@pytest.mark.parametrize("yaml_text, at", [
    ("", (5, 0)),                                                     # 02:00 + 3 h
    ("sweep:\n  fallback_after_hours: 2\n", (4, 30)),                 # 04:00 → its end
    ("sweep:\n  fallback_after_hours: 1\n", (4, 30)),                 # 03:00 (its start)
    ("sweep:\n  fallback_after_hours: 2.5\n", (4, 30)),               # 04:30 is outside
    ("sweep:\n  fallback_after_hours: 0.9\n", (2, 54)),               # before the window
    ("sweep:\n  fallback_after_hours: 2\n  blocked_hours: ['03:00-04:30', '04:30-05:15']\n",
     (5, 15)),                                                        # chained windows
    ("sweep:\n  fallback_after_hours: 2\n  blocked_hours: ['04:30-05:15', '03:00-04:30']\n",
     (5, 15)),
    ("sweep:\n  fallback_after_hours: 2\n  blocked_hours: []\n", (4, 0)),
    ("sweep:\n  nightly_at: '23:00'\n  fallback_after_hours: 0.5\n"
     "  blocked_hours: ['23:15-23:45']\n", (23, 45)),
    ("sweep:\n  nightly_at: '23:00'\n  fallback_after_hours: 0.5\n"
     "  blocked_hours: ['23:15-01:00']\n", None),                     # its end is tomorrow
    ("sweep:\n  nightly_at: '00:30'\n  fallback_after_hours: 0\n"
     "  blocked_hours: ['23:00-01:00']\n", (1, 0)),                   # a wrapped window's end
])
def test_a_fallback_inside_a_blocked_window_moves_to_its_end(tmp_path, yaml_text, at):
    assert sweep_nightly.fallback_at(_wf(tmp_path, yaml_text)) == at


def test_a_fallback_skipped_by_a_hold_fires_again_in_its_window(sched):
    """The hold landed between the spawn and the start: the run swept nothing, so the day
    still had no run and the fallback may fire again once the hold passes."""
    sched.box.busy = "the suite slot is held by CMX-12"
    child = sched.tick(datetime(2026, 11, 18, 5, 0))
    assert child is not None
    child.alive = False
    wf = load_workflow(sched.path)
    sweep_nightly.finish_day(wf, {"at": datetime(2026, 11, 18, 5, 0).timestamp(),
                                  "status": "skipped", "reason": "dispatch_paused",
                                  "trigger": "fallback"}, sched.chela)
    assert sched.tick(datetime(2026, 11, 18, 5, 30), child) is not child
    assert sched.spawned == [(sched.path, "fallback")] * 2


def test_no_second_fallback_once_one_swept(sched):
    sched.box.busy = "the suite slot is held by CMX-12"
    child = sched.tick(datetime(2026, 11, 18, 5, 0))
    child.alive = False
    wf = load_workflow(sched.path)
    sweep_nightly.finish_day(wf, {"at": datetime(2026, 11, 18, 5, 0).timestamp(),
                                  "status": "unrun", "yielded_to": "CMX-12",
                                  "sweep_seconds": 1.0, "trigger": "fallback"}, sched.chela)
    assert sched.tick(datetime(2026, 11, 18, 5, 30), child) is child
    assert len(sched.spawned) == 1


def test_the_daemon_fires_a_moved_fallback_at_the_windows_end(sched):
    sched.write("sweep:\n  fallback_after_hours: 2\n")
    sched.box.busy = "the suite slot is held by CMX-12"
    for at in ((4, 0), (4, 15), (4, 29)):
        assert sched.tick(datetime(2026, 11, 18, *at)) is None
    assert sched.tick(datetime(2026, 11, 18, 4, 30)) is not None
    assert sched.spawned == [(sched.path, "fallback")]


@pytest.mark.parametrize("yaml_text, window", [
    ("", (300, 420)),                                            # 05:00-07:00
    ("sweep:\n  fallback_window_hours: 0.5\n", (300, 330)),
    ("sweep:\n  fallback_window_hours: 0\n", (300, 420)),        # not positive: the default
    ("sweep:\n  fallback_window_hours: soon\n", (300, 420)),
    ("sweep:\n  nightly_at: '20:30'\n", (1410, 1440)),           # at most midnight
    ("sweep:\n  nightly_at: 'off'\n", None),
])
def test_fallback_window(tmp_path, yaml_text, window):
    assert sweep_nightly.fallback_window(_wf(tmp_path, yaml_text)) == window


@pytest.mark.parametrize("hm, fires", [
    ((4, 59), False), ((5, 0), True), ((6, 59), True), ((7, 0), False), ((16, 35), False),
])
def test_the_fallback_fires_only_inside_its_own_window(sched, hm, fires):
    """11-18: the fallback fired at 16:35 ("after 04:00, nothing ran today") and took the slot
    from agents in working hours."""
    sched.box.busy = "the suite slot is held by CMX-12"
    got = sched.tick(datetime(2026, 11, 18, *hm))
    assert (got is not None) is fires
    assert sched.spawned == ([(sched.path, "fallback")] if fires else [])


def test_a_fixed_start_waits_out_a_hold_and_then_runs(sched):
    sched.write("sweep:\n  mode: fixed\n  nightly_at: '03:10'\n")
    assert sched.tick(datetime(2026, 11, 18, 3, 10)) is None          # blocked hours
    assert sched.tick(datetime(2026, 11, 18, 4, 30)) is not None
    assert sched.spawned == [(sched.path, "fixed")]
    sched.write("sweep:\n  mode: fixed\n  nightly_at: '05:00'\n  blocked_hours: []\n")
    _pause(sched.chela, datetime(2026, 11, 19, 6, 0).timestamp())
    assert sched.tick(datetime(2026, 11, 19, 5, 0)) is None           # paused
    assert sched.tick(datetime(2026, 11, 19, 6, 0)) is not None       # its TTL ran out


# --- the throughput figure ------------------------------------------------------------------------


@pytest.mark.parametrize("measured, seconds, rate, want", [
    (1, 0.16, 22885.0, " — 1 measured: too few to rate"),
    (9, 599.0, 54.1, " — 9 measured: too few to rate"),
    (10, 60.0, 600.0, " — 10 measured in 0.0 h: 600 mutations/hour"),
    (2, 600.0, 12.0, " — 2 measured in 0.2 h: 12 mutations/hour"),
    (25, 11520.0, 7.8, " — 25 measured in 3.2 h: 7.8 mutations/hour"),
    (0, 600.0, None, ""),
])
def test_the_rate_needs_a_minimum_sample(measured, seconds, rate, want):
    assert sweep_nightly.throughput({"measured": measured, "battery_seconds": seconds,
                                     "per_hour": rate}) == want
