"""⏳ CMX-400 — the idle sweep: it starts when the queue and the suite slot are idle, yields the
slot to an agent between two mutations, and runs once a day on a fixed-time fallback when the
box never went quiet.

Scheduler tests drive :func:`chela.sweep_nightly.daemon_tick` with a fake idle probe and a
temp ``CHELA_DIR``; the yield tests run the REAL battery (:func:`chela.sweep.sweep`) holding
the real suite slot while a real second process asks for it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import pytest

from chela import judge, suite_guard, sweep, sweep_nightly
from chela import main as main_mod
from chela.workflow import load_workflow, parse_workflow
from tests.test_sweep_nightly import WEAK_MUT, Filer, _repo, _run, _workflow_text

ROOT = Path(__file__).resolve().parents[1]
NIGHT = datetime(2026, 11, 18, 1, 0)


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    got: list[tuple[str, str, dict]] = []
    monkeypatch.setattr("chela.inbox.enqueue",
                        lambda kind, summary, payload: got.append((kind, summary, payload)))
    return got


class _Child:
    def __init__(self, alive: bool = True):
        self.alive = alive

    def poll(self):
        return None if self.alive else 0


class _Box:
    """The idle probe: ``busy`` is the reason the box is busy, ``""`` when idle."""

    def __init__(self, busy: str = ""):
        self.busy = busy

    def __call__(self, _chela_dir=None):
        return (not self.busy, self.busy)


@pytest.fixture
def sched(tmp_path, monkeypatch):
    """A workflow file, a spawn spy, a fake box and a fresh idle watch."""
    p = tmp_path / "WORKFLOW.md"

    def write(extra=""):
        p.write_text(_workflow_text(tmp_path, extra=extra))
    write()
    spawned: list[tuple] = []
    monkeypatch.setattr(sweep_nightly, "spawn", lambda path, d=None, t=None: spawned.append(
        (path, t)) or _Child())
    box, watch, chela = _Box(), sweep_nightly.IdleWatch(), tmp_path / "chela"

    def tick(at, prev=None):
        return sweep_nightly.daemon_tick([p], prev, at, chela, probe=box, watch=watch)
    return type("S", (), {"path": p, "write": staticmethod(write), "spawned": spawned,
                          "box": box, "tick": staticmethod(tick), "chela": chela,
                          "watch": watch})


def _at(minutes: float, base: datetime = NIGHT) -> datetime:
    return datetime.fromtimestamp(base.timestamp() + minutes * 60)


def _finish(s, *, yielded="", seconds=600.0, status="unrun"):
    """The child's report back for today's run."""
    wf = load_workflow(s.path)
    sweep_nightly.finish_day(wf, {"at": NIGHT.timestamp(), "status": status,
                                  "yielded_to": yielded, "sweep_seconds": seconds,
                                  "trigger": sweep_nightly.IDLE}, s.chela)


def _day(s) -> dict:
    return json.loads((sweep_nightly.state_dir(s.chela) / "day.json").read_text())[
        str(load_workflow(s.path).path)]


# --- acceptance: start on idle, not on the clock ----------------------------------------------


def test_a_busy_queue_never_starts_and_ten_quiet_minutes_do(sched):
    sched.box.busy = "CMX-12 waiting for the suite slot"
    for m in (0, 5, 15, 30):
        assert sched.tick(_at(m)) is None
    sched.box.busy = ""
    assert sched.tick(_at(31)) is None              # idle, but not yet quiet for long
    assert sched.tick(_at(40.9)) is None            # 9.9 min quiet
    sched.box.busy = "the suite slot is held by CMX-13"
    assert sched.tick(_at(41)) is None              # a hand-in in between starts it over
    sched.box.busy = ""
    assert sched.tick(_at(42)) is None
    assert sched.tick(_at(51.9)) is None
    assert sched.tick(_at(52)) is not None          # 10 min quiet without a break
    assert sched.spawned == [(sched.path, "idle")]
    assert _day(sched) == {"date": "2026-11-18", "used": 0.0, "runs": 1, "pending": True,
                           "done": False, "trigger": "idle"}


def test_the_quiet_period_is_configurable(sched):
    sched.write("sweep:\n  quiet_seconds: 120\n")
    assert sched.tick(_at(0)) is None
    assert sched.tick(_at(1.9)) is None
    assert sched.tick(_at(2)) is not None


def test_a_running_child_holds_off_and_resets_the_quiet_clock(sched):
    child = sched.tick(_at(0)) or sched.tick(_at(10))
    assert child is not None and len(sched.spawned) == 1
    assert sched.tick(_at(20), child) is child and sched.watch.since is None


# --- acceptance: at most once a day ------------------------------------------------------------


def test_at_most_one_run_a_day(sched):
    sched.tick(_at(0))
    child = sched.tick(_at(10))
    assert child is not None
    child.alive = False
    assert sched.tick(_at(30), child) is child      # still pending: the child never reported
    _finish(sched)                                   # ran to its end, did not yield
    assert _day(sched)["done"] is True and _day(sched)["used"] == 600.0
    for m in (40, 60, 300, 600, 1000):
        assert sched.tick(_at(m), child) is child
    assert len(sched.spawned) == 1
    # The box stayed quiet past midnight: the next day's run starts on its first tick.
    assert sched.tick(datetime(2026, 11, 19, 0, 0)) is not None and len(sched.spawned) == 2


def test_a_yielded_day_resumes_when_idle_until_its_budget_is_spent(sched):
    sched.write("sweep:\n  budget_seconds: 1500\n")
    sched.tick(_at(0))
    assert sched.tick(_at(10)) is not None
    _finish(sched, yielded="CMX-12", seconds=1000)
    assert (_day(sched)["done"], _day(sched)["used"]) == (False, 1000.0)
    sched.tick(_at(20))
    assert sched.tick(_at(30)) is not None           # resumed the same day
    assert _day(sched)["runs"] == 2 and _day(sched)["used"] == 1000.0
    _finish(sched, yielded="CMX-13", seconds=500)
    assert _day(sched)["used"] == 1500.0
    sched.tick(_at(40))
    assert sched.tick(_at(60)) is None                # the day's total is spent
    assert len(sched.spawned) == 2


def test_a_child_that_never_reports_back_ends_its_day(sched):
    sched.tick(_at(0))
    child = sched.tick(_at(10))
    child.alive = False
    for m in (20, 40, 200):
        assert sched.tick(_at(m), child) is child
    assert len(sched.spawned) == 1


@pytest.mark.parametrize("make", [lambda p: p.write_text("[]"), lambda p: p.write_text("{x"),
                                  lambda p: p.mkdir()])
def test_an_unreadable_day_record_starts_nothing(sched, make):
    path = sweep_nightly.state_dir(sched.chela) / "day.json"
    path.parent.mkdir(parents=True)
    make(path)
    for m in (0, 10, 200):
        assert sched.tick(_at(m)) is None
    assert sched.spawned == []


def test_a_day_record_for_another_workflow_only_is_no_run(sched):
    path = sweep_nightly.state_dir(sched.chela) / "day.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"/other/WORKFLOW.md": {"date": "2026-11-18", "done": True}}))
    sched.tick(_at(0))
    assert sched.tick(_at(10)) is not None


def test_a_record_that_is_not_an_object_is_unreadable(sched):
    path = sweep_nightly.state_dir(sched.chela) / "day.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({str(load_workflow(sched.path).path): "2026-11-18"}))
    with pytest.raises(ValueError, match="malformed day"):
        sweep_nightly.read_day(load_workflow(sched.path), "2026-11-18", sched.chela)


# --- acceptance: the fixed-time fallback ------------------------------------------------------


def test_the_fallback_fires_once_when_never_idle(sched):
    sched.box.busy = "the suite slot is held by CMX-12"
    for m in (0, 60, 179, 239):                       # 01:00 .. 04:59
        assert sched.tick(_at(m)) is None
    child = sched.tick(_at(240))                      # 05:00 = nightly_at 02:00 + 3 h
    assert child is not None and sched.spawned == [(sched.path, "fallback")]
    child.alive = False
    _finish(sched, yielded="CMX-12", seconds=300)
    for m in (241, 300, 600, 1300):
        assert sched.tick(_at(m), child) is child
    assert len(sched.spawned) == 1


def test_no_fallback_after_an_idle_run_today(sched):
    sched.tick(_at(0))
    child = sched.tick(_at(10))
    _finish(sched, yielded="CMX-12", seconds=60)
    sched.box.busy = "CMX-12 waiting for the suite slot"
    child.alive = False
    assert sched.tick(_at(300), child) is child       # 06:00, busy: no fallback — it ran
    assert len(sched.spawned) == 1


@pytest.mark.parametrize("yaml_text, at", [
    ("", (5, 0)),
    ("sweep:\n  nightly_at: '03:30'\n", (6, 30)),
    ("sweep:\n  fallback_after_hours: 0\n", (2, 0)),
    ("sweep:\n  fallback_after_hours: 0.5\n", (2, 30)),
    ("sweep:\n  nightly_at: '23:00'\n", (23, 59)),       # never the next day
    ("sweep:\n  fallback_after_hours: -1\n", (5, 0)),    # garbage: the default, said
    ("sweep:\n  fallback_after_hours: soon\n", (5, 0)),
    ("sweep:\n  fallback_after_hours: true\n", (5, 0)),
    ("sweep:\n  nightly_at: 'off'\n", None),
])
def test_fallback_at(tmp_path, yaml_text, at):
    wf = parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(tmp_path, extra=yaml_text))
    assert sweep_nightly.fallback_at(wf) == at


def test_nightly_at_off_turns_the_idle_sweep_off_too(sched):
    sched.write("sweep:\n  nightly_at: 'off'\n")
    for m in (0, 20, 300):
        assert sched.tick(_at(m)) is None
    sched.box.busy = "x"
    assert sched.tick(_at(400)) is None and sched.spawned == []


def test_a_keeper_only_workflow_never_starts(sched):
    sched.write("dispatch:\n  enabled: false\n")
    for m in (0, 20, 300):
        assert sched.tick(_at(m)) is None
    assert sched.spawned == []


# --- config -----------------------------------------------------------------------------------


@pytest.mark.parametrize("yaml_text, mode, quiet, fb_budget", [
    ("", "idle", 600, 1800),
    ("sweep:\n  mode: fixed\n", "fixed", 600, 1800),
    ("sweep:\n  mode: ' Fixed '\n", "fixed", 600, 1800),
    ("sweep:\n  mode: idle\n  quiet_seconds: 60\n  fallback_budget_seconds: 900\n",
     "idle", 60, 900),
    ("sweep:\n  mode: sometimes\n  quiet_seconds: 0\n  fallback_budget_seconds: lots\n",
     "idle", 600, 1800),
])
def test_config_values_and_defaults(tmp_path, yaml_text, mode, quiet, fb_budget, caplog):
    wf = parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(tmp_path, extra=yaml_text))
    with caplog.at_level("WARNING", logger="chela.sweep_nightly"):
        got = (sweep_nightly.sweep_mode(wf), sweep_nightly.quiet_seconds(wf),
               sweep_nightly.fallback_budget_seconds(wf))
    assert got == (mode, quiet, fb_budget)
    assert bool(caplog.records) is ("sometimes" in yaml_text)


def test_fixed_mode_keeps_the_clock_and_ignores_idleness(sched):
    sched.write("sweep:\n  mode: fixed\n  nightly_at: '02:30'\n")
    for m in (0, 20, 60, 89):                         # idle all night, before 02:30
        assert sched.tick(_at(m)) is None
    child = sched.tick(_at(90))
    assert child is not None and sched.spawned == [(sched.path, "fixed")]
    child.alive = False
    assert sched.tick(_at(200), child) is child and len(sched.spawned) == 1


@pytest.mark.parametrize("trigger, used, want", [
    (None, 5000.0, 10800.0),                          # a hand run: the whole budget
    ("fixed", 5000.0, 10800.0),
    ("idle", 0.0, 10800.0),
    ("idle", 5000.0, 5800.0),                         # what is left of the day
    ("fallback", 0.0, 1800.0),                        # the reduced fallback budget
    ("fallback", 10000.0, 800.0),                     # ...never more than is left
])
def test_run_budget(tmp_path, trigger, used, want):
    wf = parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(
        tmp_path, extra="sweep:\n  budget_seconds: 10800\n  max_mutations: 40\n"))
    chela = tmp_path / "chela"
    sweep_nightly.mark_day_started(wf, NIGHT, "idle", chela)
    path = sweep_nightly.state_dir(chela) / "day.json"
    store = json.loads(path.read_text())
    store[str(wf.path)]["used"] = used
    path.write_text(json.dumps(store))
    got = sweep_nightly.run_budget(wf, trigger, NIGHT.timestamp(), chela)
    assert (got.seconds, got.max_mutations) == (want, 40)


# --- the idle probe -----------------------------------------------------------------------------


@pytest.fixture
def probe(monkeypatch, tmp_path):
    state = {"holders": [], "waiters": [], "avail": 10 * suite_guard.SUITE_PEAK_BYTES}
    monkeypatch.setattr(suite_guard, "holders", lambda: state["holders"])
    monkeypatch.setattr(suite_guard, "waiters", lambda exclude_pid=None: state["waiters"])
    monkeypatch.setattr(suite_guard, "mem_available_bytes", lambda: state["avail"])
    (tmp_path / "chela").mkdir()
    return state


@pytest.mark.parametrize("change, why", [
    ({}, ""),
    ({"holders": ["CMX-12"]}, "the suite slot is held by CMX-12"),
    ({"waiters": ["CMX-12", "CMX-13"]}, "CMX-12, CMX-13 waiting for the suite slot"),
    ({"avail": suite_guard.RESUME_MEM_BYTES - 1}, "memory headroom is below one suite's peak"),
    ({"avail": None}, "memory headroom is below one suite's peak"),
])
def test_box_idle(probe, tmp_path, change, why):
    probe.update(change)
    assert sweep_nightly.box_idle(tmp_path / "chela") == (not why, why)


def test_box_idle_at_exactly_one_suites_peak_is_idle(probe, tmp_path):
    probe["avail"] = suite_guard.RESUME_MEM_BYTES
    assert sweep_nightly.box_idle(tmp_path / "chela") == (True, "")


def test_a_fleet_pause_is_not_idle(probe, tmp_path):
    (tmp_path / "chela" / "fleet-pause.json").write_text("{}")
    assert sweep_nightly.box_idle(tmp_path / "chela") == (False, "a fleet pause is active")


def test_the_daemon_tick_reads_the_real_probe_by_default(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(sweep_nightly, "box_idle", lambda d=None: calls.append(d) or (False, "x"))
    p = tmp_path / "WORKFLOW.md"
    p.write_text(_workflow_text(tmp_path))
    assert sweep_nightly.daemon_tick([p], None, NIGHT, tmp_path / "c",
                                     watch=sweep_nightly.IdleWatch()) is None
    assert calls == [tmp_path / "c"]


# --- the waiters the yield reads ---------------------------------------------------------------


@pytest.fixture
def ledgers(monkeypatch, tmp_path):
    monkeypatch.setattr(suite_guard.config, "CHELA_DIR", tmp_path)
    waits = tmp_path / "suite-slots" / "waits"
    waits.mkdir(parents=True)
    return waits


def test_waiters_names_open_waits_oldest_first_except_mine(ledgers, monkeypatch):
    stats = {1: ("S", "a"), 2: ("R", "b"), 3: ("T", "c"), 4: ("S", "d"), 5: ("S", "e")}
    monkeypatch.setattr(suite_guard, "_proc_stat", lambda pid: stats.get(pid))
    for pid, cwd, since in ((1, "/w/CMX-13", 20.0), (2, "/w/CMX-12", 10.0),
                            (3, "/w/paused", 5.0), (4, "/w/holding", None),
                            (5, "/w/bad", "soon")):
        (ledgers / f"{pid}.json").write_text(json.dumps({
            "pid": pid, "start": stats[pid][1], "cwd": cwd, "waiting_since": since,
            "waited": 0}))
    assert suite_guard.waiters() == ["CMX-12", "CMX-13"]
    assert suite_guard.waiters(exclude_pid=2) == ["CMX-13"]


def test_slot_wanted_excludes_this_process(ledgers, monkeypatch):
    me = os.getpid()
    stats = {me: ("S", "m"), 7: ("S", "o")}
    monkeypatch.setattr(suite_guard, "_proc_stat", lambda pid: stats.get(pid))
    (ledgers / "me.json").write_text(json.dumps({"pid": me, "start": "m", "cwd": "/w/self",
                                                 "waiting_since": 1.0, "waited": 0}))
    assert sweep_nightly.slot_wanted() == ""
    (ledgers / "o.json").write_text(json.dumps({"pid": 7, "start": "o", "cwd": "/w/CMX-12",
                                                "waiting_since": 2.0, "waited": 0}))
    assert sweep_nightly.slot_wanted() == "CMX-12"


# --- acceptance: yield between mutations (the REAL battery and slot) -------------------------

GUARDS = ("def f(a, b, c, d):\n    if a:\n        return 1\n    if b:\n        return 2\n"
          "    if c:\n        return 3\n    if d:\n        return 4\n    return 0\n")
AGENT = """
import sys, time
from pathlib import Path
from chela import config, suite_guard
config.CHELA_DIR = Path(sys.argv[1])
with suite_guard.slot(sys.argv[2], max_wait=60, poll=0.02):
    Path(sys.argv[3]).write_text(repr(time.time()))
"""


@pytest.fixture
def one_slot(monkeypatch, tmp_path):
    monkeypatch.setattr(suite_guard.config, "CHELA_DIR", tmp_path / "chela")
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    monkeypatch.setenv("CHELA_SUITE_MEMORY_MAX", "0")
    monkeypatch.delenv("CHELA_SUITE_SLOT_MAX_WAIT", raising=False)
    monkeypatch.setattr(suite_guard, "_waited", 0.0)
    monkeypatch.setattr(suite_guard, "_thread_waited", {})
    monkeypatch.setattr(suite_guard, "SLOT_POLL_SECONDS", 0.02)
    return tmp_path


def _agent_mid_sweep(tmp_path: Path, monkeypatch, should_yield) -> dict:
    """The sweep holds the one slot; during its FIRST mutation an agent (a separate process)
    asks for the slot. Returns the sweep's report, when each mutation ended, when the agent
    got the slot, and the mutated file as it was after the sweep."""
    wt = tmp_path / "nightly-sweep"
    wt.mkdir()
    (wt / "m.py").write_text(GUARDS)
    got = tmp_path / "agent-got-slot"
    ends: list[float] = []
    real = sweep._apply_and_measure
    agent: list[subprocess.Popen] = []

    def measured(*a, **kw):
        if not agent:
            env = dict(os.environ, CHELA_SUITE_SLOTS="1", PYTHONPATH=str(ROOT))
            agent.append(subprocess.Popen(
                [sys.executable, "-c", AGENT, str(tmp_path / "chela"),
                 str(tmp_path / "CMX-12"), str(got)], cwd=str(ROOT), env=env))
            deadline = time.monotonic() + 20
            while "CMX-12" not in suite_guard.waiters() and time.monotonic() < deadline:
                time.sleep(0.02)
            assert "CMX-12" in suite_guard.waiters(), "the agent never asked for the slot"
        out = real(*a, **kw)
        ends.append(time.time())
        return out
    monkeypatch.setattr(sweep, "_apply_and_measure", measured)
    report = sweep.sweep(wt, "label", f"{sys.executable} -c pass", timeout=60,
                         select_tests=False, provision=False, lines={"m.py": {2, 4, 6, 8}},
                         should_yield=should_yield)
    agent[0].wait(60)
    return {"report": report, "ends": ends, "got": float(got.read_text()),
            "file": (wt / "m.py").read_text()}


def test_an_agent_asking_mid_sweep_gets_the_slot_after_the_current_mutation(one_slot,
                                                                              monkeypatch):
    out = _agent_mid_sweep(one_slot, monkeypatch, sweep_nightly.slot_wanted)
    rep = out["report"]
    assert rep.yielded == "CMX-12" and not rep.cannot_verify
    assert len(out["ends"]) == 1 and rep.measured == 1      # the current mutation finished...
    unrun = rep._with(sweep.UNRUN)
    assert len(unrun) == len(rep.results) - 1 >= 3          # ...and the rest is UNRUN (owed)
    assert all("yielded the suite slot to CMX-12 after 1 of" in r.reason for r in unrun)
    assert out["got"] >= out["ends"][0]                     # the slot went to the agent
    assert out["file"] == GUARDS                            # the mutated file is restored


def test_negative_control_without_the_yield_check_the_agent_waits_for_the_whole_sweep(
        one_slot, monkeypatch):
    out = _agent_mid_sweep(one_slot, monkeypatch, None)
    rep = out["report"]
    assert rep.yielded == "" and rep.measured == len(rep.results) >= 4
    assert len(out["ends"]) == rep.measured
    assert out["got"] >= out["ends"][-1]                    # it waited for every mutation
    # The acceptance assertion above, applied here, goes RED:
    assert not (len(out["ends"]) == 1 and rep.yielded == "CMX-12")


def test_nobody_waiting_runs_every_mutation(one_slot, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "m.py").write_text(GUARDS)
    asked = []
    rep = sweep.sweep(wt, "label", f"{sys.executable} -c pass", timeout=60,
                      select_tests=False, provision=False, lines={"m.py": {2, 4, 6, 8}},
                      should_yield=lambda: asked.append(1) or "")
    assert rep.yielded == "" and rep.measured == len(rep.results) >= 4
    # Asked after every measured mutation but the first — never before it.
    assert len(asked) == rep.measured - 1
    assert rep.as_dict()["yielded"] == ""


# --- the nightly run: wiring, owed, the day record, the notice --------------------------------


def _yielding_battery(monkeypatch, seen: dict, *, yielded="CMX-12", seconds=1200.0):
    def fake(worktree, base, test_cmd, **kw):
        seen.update(kw)
        late = sweep.Mutation("other.py", 3, "return-code", "        return 1",
                              "        return 0")
        return sweep.SweepReport(results=[sweep.SweepResult(WEAK_MUT, judge.KILLED),
                                          sweep.SweepResult(late, sweep.UNRUN)],
                                 yielded=yielded, seconds=seconds, battery_seconds=seconds)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)


def test_the_nightly_battery_is_handed_the_yield_check_and_the_days_budget(tmp_path,
                                                                           monkeypatch):
    repo, chela = _repo(tmp_path, extra="sweep:\n  budget_seconds: 3600\n"), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    sweep_nightly.mark_day_started(wf, datetime.now(), "idle", chela)
    seen: dict = {}
    _yielding_battery(monkeypatch, seen)
    res = _run(repo, chela, Filer(), trigger="idle")
    assert isinstance(seen["should_yield"], sweep_nightly.YieldCheck)
    assert seen["budget"].seconds == 3600.0
    assert (res["status"], res["yielded_to"], res["owed"]) == ("unrun", "CMX-12", 1)
    assert res["why"] == ("yielded the suite slot to CMX-12; 1 mutation(s) it did not reach "
                          "are owed to the next run")
    owed = json.loads((sweep_nightly.state_dir(chela) / "owed.json").read_text())[str(wf.path)]
    assert [(e["file"], e["line"], e["kind"]) for e in owed] == [("other.py", 3, "return-code")]
    day = json.loads((sweep_nightly.state_dir(chela) / "day.json").read_text())[str(wf.path)]
    assert (day["used"], day["pending"], day["done"], day["status"]) == (
        1200.0, False, False, "unrun")
    # Resumed the same day: only what is left of the day's budget.
    _run(repo, chela, Filer(), trigger="idle")
    assert seen["budget"].seconds == 2400.0


@pytest.mark.parametrize("trigger, says", [
    ("idle", " on idle: "),
    ("fallback", " on fallback (never idle): "),
])
def test_an_idle_or_fallback_run_reports_back_to_the_day_and_says_why_it_started(
        tmp_path, monkeypatch, notices, trigger, says):
    """Driven end to end through ``run()`` for EACH trigger the daemon starts it with: the
    trigger reaches the result and the notice, and the run's report lands in the day record
    (seconds used, pending cleared, a yielded day still open, the trigger kept)."""
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    sweep_nightly.mark_day_started(wf, datetime.now(), trigger, chela)
    _yielding_battery(monkeypatch, {}, seconds=900.0)
    res = _run(repo, chela, Filer(), trigger=trigger)
    assert res["trigger"] == trigger
    day = json.loads((sweep_nightly.state_dir(chela) / "day.json").read_text())[str(wf.path)]
    assert (day["used"], day["pending"], day["done"], day["trigger"], day["runs"]) == (
        900.0, False, False, trigger, 1)
    assert len(notices) == 1
    summary = notices[0][1]
    assert says in summary and "15 min used" in summary
    assert "yielded the suite slot to CMX-12" in summary


def test_a_run_that_did_not_yield_ends_the_day(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    sweep_nightly.mark_day_started(wf, datetime.now(), "idle", chela)
    _yielding_battery(monkeypatch, {}, yielded="")
    _run(repo, chela, Filer(), trigger="idle")
    day = json.loads((sweep_nightly.state_dir(chela) / "day.json").read_text())[str(wf.path)]
    assert (day["done"], day["pending"]) == (True, False)


def test_a_hand_run_never_touches_the_day_record(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    _yielding_battery(monkeypatch, {})
    _run(repo, chela, Filer())
    assert not (sweep_nightly.state_dir(chela) / "day.json").exists()


def test_a_spent_day_skips_without_sweeping(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path, extra="sweep:\n  budget_seconds: 600\n"), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    sweep_nightly.mark_day_started(wf, datetime.now(), "idle", chela)
    sweep_nightly.finish_day(wf, {"at": time.time(), "status": "unrun", "yielded_to": "x",
                                  "sweep_seconds": 600.0}, chela)
    sweep_nightly.mark_day_started(wf, datetime.now(), "idle", chela)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", lambda *a, **k: pytest.fail("swept"))
    res = _run(repo, chela, Filer(), trigger="idle")
    assert (res["status"], res["reason"]) == ("skipped", "budget_spent")


def test_an_unreadable_day_record_is_cannot_verify_for_an_idle_run(tmp_path, monkeypatch):
    repo, chela = _repo(tmp_path), tmp_path / "chela"
    sweep_nightly.state_dir(chela).mkdir(parents=True)
    (sweep_nightly.state_dir(chela) / "day.json").write_text("{x")
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", lambda *a, **k: pytest.fail("swept"))
    res = _run(repo, chela, Filer(), trigger="idle")
    assert res["status"] == "cannot_verify" and res["why"].startswith("the day record: ")


def test_finish_day_without_a_pending_record_starts_one_and_never_raises(tmp_path, monkeypatch):
    wf = parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(tmp_path))
    chela = tmp_path / "chela"
    sweep_nightly.finish_day(wf, {"at": NIGHT.timestamp(), "status": "clean",
                                  "sweep_seconds": 30.0, "trigger": "fallback"}, chela)
    day = json.loads((sweep_nightly.state_dir(chela) / "day.json").read_text())[str(wf.path)]
    assert day == {"date": "2026-11-18", "runs": 1, "used": 30.0, "pending": False,
                   "done": True, "trigger": "fallback", "status": "clean"}
    monkeypatch.setattr(sweep_nightly, "_write_json", lambda *a: (_ for _ in ()).throw(
        RuntimeError("disk")))
    sweep_nightly.finish_day(wf, {"at": NIGHT.timestamp(), "status": "clean"}, chela)


def _notice(**kw) -> str:
    res = {"workflow": "/x/repo/WORKFLOW.md", "at": NIGHT.timestamp(), "status": "unrun",
           "filed": [], "would_file": [], "already_filed": 0, "failed": [], "survivors": 0,
           "prs": ["a"], "groups": [], "warnings": [], "mutations": 9, "unrun": 7,
           "measured": 2, "battery_seconds": 1800.0, "per_hour": 4.0, "owed": 7}
    res.update(kw)
    return sweep_nightly.summary(res)


@pytest.mark.parametrize("kw, want", [
    ({"trigger": "idle", "yielded_to": "CMX-12", "sweep_seconds": 2400.0,
      "budget_seconds": 10800.0},
     "🧹🌙 nightly sweep (repo), started 01:00 on idle: UNRUN, "
     "not clean — nothing filed — 1 standard-tier PR(s), 9 mutation(s), 7 UNRUN — 2 measured "
     "in 0.5 h: 4 mutations/hour — 7 owed to the next night — ⏳ yielded the suite slot to "
     "CMX-12 — 40 min used of 180"),
    ({"trigger": "fallback", "sweep_seconds": 1800.0, "budget_seconds": 1800.0},
     "🧹🌙 nightly sweep (repo), started 01:00 on fallback (never idle): "
     "UNRUN, not clean — nothing filed — 1 standard-tier PR(s), 9 mutation(s), 7 UNRUN — 2 "
     "measured in 0.5 h: 4 mutations/hour — 7 owed to the next night — 30 min used of 30"),
    ({"trigger": "fixed", "sweep_seconds": 60.0},
     "🧹🌙 nightly sweep (repo), started 01:00 at the fixed time: UNRUN, "
     "not clean — nothing filed — 1 standard-tier PR(s), 9 mutation(s), 7 UNRUN — 2 measured "
     "in 0.5 h: 4 mutations/hour — 7 owed to the next night — 1 min used"),
    ({"sweep_seconds": 60.0, "yielded_to": ""},
     "🧹🌙 nightly sweep (repo): UNRUN, not clean — nothing filed — 1 standard-tier PR(s), 9 "
     "mutation(s), 7 UNRUN — 2 measured in 0.5 h: 4 mutations/hour — 7 owed to the next night"),
])
def test_the_notice_says_when_why_whether_it_yielded_and_minutes(kw, want):
    assert _notice(**kw) == want


def test_spawn_argv_carries_the_trigger(tmp_path):
    p = tmp_path / "WORKFLOW.md"
    assert sweep_nightly.spawn_argv(p, "idle") == [
        sys.executable, "-m", "chela.main", "judge", "nightly-sweep", "--workflow", str(p),
        "--trigger", "idle"]
    assert sweep_nightly.spawn_argv(p) == sweep_nightly.spawn_argv(p, None) == [
        sys.executable, "-m", "chela.main", "judge", "nightly-sweep", "--workflow", str(p)]


@pytest.mark.parametrize("argv, want", [([], None), (["--trigger", "idle"], "idle"),
                                        (["--trigger", "fallback"], "fallback"),
                                        (["--trigger", "fixed"], "fixed")])
def test_the_cli_passes_the_trigger_to_the_run(tmp_path, monkeypatch, argv, want):
    p = tmp_path / "WORKFLOW.md"
    p.write_text(_workflow_text(tmp_path))
    seen = []
    monkeypatch.setattr(sweep_nightly, "run", lambda wf, trigger=None: seen.append(trigger) or {
        "status": "clean"})
    monkeypatch.setattr(sys, "argv", ["chela", "judge", "nightly-sweep", "--workflow", str(p),
                                      *argv])
    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 0 and seen == [want]


def test_the_cli_refuses_an_unknown_trigger(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["chela", "judge", "nightly-sweep", "--workflow", "x",
                                      "--trigger", "noon"])
    with pytest.raises(SystemExit) as e:
        main_mod.main()
    assert e.value.code == 2


# --- review fixes: a passing skip keeps the day open; a spawn across midnight ------------------


@pytest.mark.parametrize("result, done", [
    ({"status": "skipped", "reason": "fleet_paused"}, False),
    ({"status": "skipped", "reason": "already_running"}, False),
    ({"status": "skipped", "reason": "judge_off"}, True),
    ({"status": "skipped", "reason": "no_merges"}, True),
    ({"status": "cannot_verify"}, True),
    ({"status": "clean"}, True),
    ({"status": "unrun", "yielded_to": "CMX-12"}, False),
])
def test_finish_day_ends_the_day_unless_it_yielded_or_skipped_for_a_passing_cause(
        tmp_path, result, done):
    wf = parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(tmp_path))
    chela = tmp_path / "chela"
    sweep_nightly.mark_day_started(wf, NIGHT, "idle", chela)
    sweep_nightly.finish_day(wf, {"at": NIGHT.timestamp(), **result}, chela)
    assert sweep_nightly.read_day(wf, "2026-11-18", chela)["done"] is done


def test_a_spawn_that_starts_after_midnight_is_charged_to_its_pending_day(tmp_path):
    wf = parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(
        tmp_path, extra="sweep:\n  budget_seconds: 3000\n"))
    chela = tmp_path / "chela"
    late = datetime(2026, 11, 18, 23, 59)
    sweep_nightly.mark_day_started(wf, late, "idle", chela)
    sweep_nightly.finish_day(wf, {"at": late.timestamp(), "status": "unrun",
                                  "yielded_to": "x", "sweep_seconds": 1000.0}, chela)
    sweep_nightly.mark_day_started(wf, late, "idle", chela)
    after = datetime(2026, 11, 19, 0, 1).timestamp()
    assert sweep_nightly.run_budget(wf, "idle", after, chela).seconds == 2000.0
