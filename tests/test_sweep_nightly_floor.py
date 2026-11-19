"""⏳ CMX-439 — the fallback sweep's floor, and the owed backlog's day-over-day growth.

11-19 04:00: the queue was never idle, 620 mutations were due, the fallback yielded the slot
to three agents in turn and measured ONE. The fallback exists for exactly those days, so it
now holds the slot until it has measured a floor (N mutations or M minutes, whichever comes
first) before it yields to a waiter. And an owed backlog that only grows says so.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from chela import judge, suite_guard, sweep, sweep_nightly
from chela.workflow import load_workflow, parse_workflow
from tests.test_sweep_nightly import WEAK_MUT, Filer, _repo, _run, _workflow_text
from tests.test_sweep_nightly_idle import GUARDS

NIGHT = datetime(2026, 11, 19, 5, 0)
HOLD_FILE = "dispatch-hold.json"          # chela.hold.HOLD_FILE_NAME — what --pause writes


@pytest.fixture(autouse=True)
def notices(monkeypatch):
    got: list[tuple[str, str, dict]] = []
    monkeypatch.setattr("chela.inbox.enqueue",
                        lambda kind, summary, payload: got.append((kind, summary, payload)))
    return got


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
    (tmp_path / "chela").mkdir()
    return tmp_path


def _wf(tmp_path: Path, extra: str = ""):
    return parse_workflow(tmp_path / "WORKFLOW.md", _workflow_text(tmp_path, extra=extra))


def _busy_battery(tmp_path: Path, monkeypatch, should_yield) -> sweep.SweepReport:
    """The REAL battery over four guards, with a queue that is NEVER idle: someone is always
    waiting for the suite slot."""
    wt = tmp_path / "nightly-sweep"
    wt.mkdir()
    (wt / "m.py").write_text(GUARDS)
    monkeypatch.setattr(sweep_nightly, "slot_wanted", lambda: "CMX-12, CMX-13")
    rep = sweep.sweep(wt, "label", f"{sys.executable} -c pass", timeout=60,
                      select_tests=False, provision=False, lines={"m.py": {2, 4, 6, 8}},
                      should_yield=should_yield)
    assert (wt / "m.py").read_text() == GUARDS          # every mutation restored
    return rep


# --- the acceptance: a permanently busy queue still measures the floor ----------------------


def test_a_fallback_with_a_permanently_busy_queue_measures_at_least_the_floor(
        one_slot, monkeypatch):
    tmp_path = one_slot
    floor = sweep_nightly.Floor(mutations=3, seconds=1e9)
    check = sweep_nightly.YieldCheck(_wf(tmp_path), tmp_path / "chela", clock=lambda: NIGHT,
                                     floor=floor)
    rep = _busy_battery(tmp_path, monkeypatch, check)
    assert len(rep.results) > 3                           # there was more than the floor due
    assert rep.measured == 3                              # the floor, then it yielded
    assert rep.yielded == "CMX-12, CMX-13" and check.reason == ""
    assert check.held_off == ["CMX-12", "CMX-13"]


def test_negative_control_without_the_floor_a_busy_queue_measures_one(one_slot, monkeypatch):
    """Today's (CMX-400) behaviour: it yields after the first mutation — fewer than the floor."""
    tmp_path = one_slot
    check = sweep_nightly.YieldCheck(_wf(tmp_path), tmp_path / "chela", clock=lambda: NIGHT)
    rep = _busy_battery(tmp_path, monkeypatch, check)
    assert rep.measured == 1 < 3
    assert rep.yielded == "CMX-12, CMX-13" and check.held_off == []


def test_a_floor_above_the_battery_runs_it_all(one_slot, monkeypatch):
    tmp_path = one_slot
    check = sweep_nightly.YieldCheck(_wf(tmp_path), tmp_path / "chela", clock=lambda: NIGHT,
                                     floor=sweep_nightly.Floor(mutations=1000, seconds=1e9))
    rep = _busy_battery(tmp_path, monkeypatch, check)
    assert rep.yielded == "" and rep.measured == len(rep.results) >= 4


# --- the floor's two halves, and what still stops it ----------------------------------------


def _check(tmp_path, monkeypatch, floor, wanted="CMX-12"):
    now = {"t": 100.0}
    monkeypatch.setattr(sweep_nightly, "slot_wanted", lambda: wanted)
    check = sweep_nightly.YieldCheck(_wf(tmp_path), tmp_path, clock=lambda: NIGHT,
                                     floor=floor, timer=lambda: now["t"])
    return check, now


def test_the_mutation_floor_is_met_at_exactly_n(tmp_path, monkeypatch):
    check, _ = _check(tmp_path, monkeypatch, sweep_nightly.Floor(mutations=2, seconds=1e9))
    assert check() == ""                    # 1 measured: held
    assert check() == "CMX-12"              # 2 measured: the floor, yields


def test_the_time_floor_is_met_at_exactly_m_seconds_past_the_first_mutation(
        tmp_path, monkeypatch):
    check, now = _check(tmp_path, monkeypatch, sweep_nightly.Floor(mutations=1000, seconds=60))
    assert check() == ""                    # t=100: the floor's clock starts here
    now["t"] = 159.0
    assert check() == ""                    # 59 s held
    now["t"] = 160.0
    assert check() == "CMX-12"              # 60 s held: the floor, yields


def test_a_dispatch_pause_stops_a_fallback_under_its_floor(tmp_path, monkeypatch):
    check, _ = _check(tmp_path, monkeypatch, sweep_nightly.Floor(mutations=1000, seconds=1e9))
    (tmp_path / HOLD_FILE).write_text(json.dumps({"reason": "x", "by": "y", "pid": 1,
                                                  "created_at": 0.0, "expires_at": 4e9}))
    assert (check(), check.reason) == ("dispatch paused", "dispatch_paused")


def test_blocked_hours_stop_a_fallback_under_its_floor(tmp_path, monkeypatch):
    monkeypatch.setattr(sweep_nightly, "slot_wanted", lambda: "CMX-12")
    wf = _wf(tmp_path, "sweep:\n  blocked_hours: ['03:00-04:30']\n")
    check = sweep_nightly.YieldCheck(wf, tmp_path, clock=lambda: datetime(2026, 11, 19, 3, 10),
                                     floor=sweep_nightly.Floor(mutations=1000, seconds=1e9))
    assert (check(), check.reason) == ("blocked hours 03:00-04:30", "blocked_hours")


def test_nobody_waiting_holds_nothing_off(tmp_path, monkeypatch):
    check, _ = _check(tmp_path, monkeypatch, sweep_nightly.Floor(mutations=5, seconds=1e9),
                      wanted="")
    assert check() == "" and check.held_off == []


def test_a_waiter_is_named_once(tmp_path, monkeypatch):
    check, _ = _check(tmp_path, monkeypatch, sweep_nightly.Floor(mutations=5, seconds=1e9))
    check()
    check()
    assert check.held_off == ["CMX-12"]


@pytest.mark.parametrize("extra, want", [
    ("", (10, 900.0)),
    ("sweep:\n  fallback_floor_mutations: 25\n", (25, 900.0)),
    ("sweep:\n  fallback_floor_seconds: 120\n", (10, 120.0)),
    ("sweep:\n  fallback_floor_mutations: 4\n  fallback_floor_seconds: 30\n", (4, 30.0)),
    ("sweep:\n  fallback_floor_mutations: 0\n", (10, 900.0)),
    ("sweep:\n  fallback_floor_seconds: nope\n", (10, 900.0)),
])
def test_fallback_floor_config(tmp_path, extra, want):
    floor = sweep_nightly.fallback_floor(_wf(tmp_path, extra))
    assert (floor.mutations, floor.seconds) == want


def test_floor_describe():
    assert sweep_nightly.Floor(10, 900.0).describe() == "10 mutation(s) or 15 min"


# --- wiring: only a fallback run gets a floor -------------------------------------------------


def _fake_sweep(monkeypatch, *, unrun: int = 1, wanted="CMX-12"):
    """A battery that asks its yield check once (someone waiting) and leaves ``unrun``
    mutations UNRUN."""
    seen: dict = {}
    monkeypatch.setattr(sweep_nightly, "slot_wanted", lambda: wanted)

    def fake(worktree, base, test_cmd, **kw):
        seen["check"] = kw["should_yield"]
        got = kw["should_yield"]()
        late = [sweep.SweepResult(sweep.Mutation("other.py", 10 + i, "return-code",
                                                 "        return 1", "        return 0"),
                                  sweep.UNRUN) for i in range(unrun)]
        return sweep.SweepReport(results=[sweep.SweepResult(WEAK_MUT, judge.KILLED), *late],
                                 yielded=got, seconds=300.0, battery_seconds=300.0)
    monkeypatch.setattr(sweep_nightly.sweep, "sweep", fake)
    return seen


def test_a_fallback_run_holds_the_slot_and_the_notice_names_who_waited(
        tmp_path, monkeypatch, notices):
    repo, chela = _repo(tmp_path, extra="sweep:\n  blocked_hours: []\n"), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    sweep_nightly.mark_day_started(wf, datetime.now(), "fallback", chela)
    seen = _fake_sweep(monkeypatch)
    res = _run(repo, chela, Filer(), trigger="fallback")
    assert seen["check"].floor == sweep_nightly.Floor(10, 900.0)
    assert res["yielded_to"] == "" and res["held_off"] == ["CMX-12"]
    assert res["floor"] == "10 mutation(s) or 15 min"
    assert (" — held the suite slot for its floor (10 mutation(s) or 15 min) while CMX-12 "
            "waited") in notices[0][1]


@pytest.mark.parametrize("trigger", ["idle", "fixed"])
def test_an_idle_or_fixed_run_has_no_floor_and_yields_at_once(tmp_path, monkeypatch, trigger,
                                                              notices):
    repo, chela = _repo(tmp_path, extra="sweep:\n  blocked_hours: []\n"), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    if trigger == "idle":
        sweep_nightly.mark_day_started(wf, datetime.now(), trigger, chela)
    seen = _fake_sweep(monkeypatch)
    res = _run(repo, chela, Filer(), trigger=trigger)
    assert seen["check"].floor is None
    assert res["yielded_to"] == "CMX-12" and res["held_off"] == []
    assert "floor" not in res and "held the suite slot" not in notices[0][1]


# --- the owed backlog's growth -----------------------------------------------------------------


def _hist(*counts: int) -> list[tuple[str, int]]:
    return [(f"2026-11-{10 + i:02d}", n) for i, n in enumerate(counts)]


@pytest.mark.parametrize("history, want", [
    ([], (None, 0)),
    (_hist(5), (None, 0)),
    (_hist(5, 6), (1, 1)),
    (_hist(5, 6, 7, 8), (1, 3)),
    (_hist(1, 5, 6, 6, 9), (3, 1)),           # a flat day breaks the run
    (_hist(9, 8), (-1, 0)),
    (_hist(2, 3, 9, 8), (-1, 0)),             # a drop today: no run
])
def test_owed_growth(history, want):
    assert sweep_nightly.owed_growth(history) == want


def test_record_owed_keeps_the_days_last_count_and_other_workflows(tmp_path):
    wf, other = _wf(tmp_path), _wf(tmp_path / "x")
    sweep_nightly.record_owed(other, "2026-11-18", 7, tmp_path)
    sweep_nightly.record_owed(wf, "2026-11-18", 3, tmp_path)
    sweep_nightly.record_owed(wf, "2026-11-19", 4, tmp_path)
    assert sweep_nightly.record_owed(wf, "2026-11-19", 6, tmp_path) == [
        ("2026-11-18", 3), ("2026-11-19", 6)]
    data = json.loads((tmp_path / "sweep-nightly" / "owed-history.json").read_text())
    assert data[str(other.path)] == {"2026-11-18": 7}


def test_record_owed_keeps_the_newest_fourteen_days(tmp_path):
    wf, got = _wf(tmp_path), []
    for i in range(1, 17):
        got = sweep_nightly.record_owed(wf, f"2026-11-{i:02d}", i, tmp_path)
    assert len(got) == 14 and got[0] == ("2026-11-03", 3) and got[-1] == ("2026-11-16", 16)


@pytest.mark.parametrize("body", ["{not json", "[]", '{"%s": [1]}', '{"%s": {"d": "x"}}',
                                  '{"%s": {"d": true}}'])
def test_an_unreadable_owed_history_raises_and_is_left_alone(tmp_path, body):
    wf = _wf(tmp_path)
    path = tmp_path / "sweep-nightly" / "owed-history.json"
    path.parent.mkdir()
    text = body.replace("%s", str(wf.path))
    path.write_text(text)
    with pytest.raises(ValueError):
        sweep_nightly.record_owed(wf, "2026-11-19", 1, tmp_path)
    assert path.read_text() == text


def _seed_history(chela: Path, wf, counts: list[int]) -> None:
    today = date.today()
    days = {(today - timedelta(days=len(counts) - i)).isoformat(): n
            for i, n in enumerate(counts)}
    path = chela / "sweep-nightly" / "owed-history.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({str(wf.path): days}))


def test_owed_grown_three_days_running_is_said_in_the_summary(tmp_path, monkeypatch, notices):
    repo, chela = _repo(tmp_path, extra="sweep:\n  blocked_hours: []\n"), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    _seed_history(chela, wf, [1, 2, 3])
    _fake_sweep(monkeypatch, unrun=4, wanted="")
    res = _run(repo, chela, Filer())
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    assert (res["owed"], res["owed_delta"], res["owed_growth_days"]) == (4, 1, 3)
    assert (f" — 4 owed to the next night (+1 since {yesterday}) — ⚠️ owed has grown 3 days "
            "running: 1 → 2 → 3 → 4") in notices[0][1]


def test_owed_grown_two_days_is_a_delta_but_no_alarm(tmp_path, monkeypatch, notices):
    repo, chela = _repo(tmp_path, extra="sweep:\n  blocked_hours: []\n"), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    _seed_history(chela, wf, [3, 2, 3])
    _fake_sweep(monkeypatch, unrun=4, wanted="")
    res = _run(repo, chela, Filer())
    assert (res["owed_delta"], res["owed_growth_days"]) == (1, 2)
    assert "(+1 since " in notices[0][1] and "days running" not in notices[0][1]


def test_owed_shrinking_says_the_drop(tmp_path, monkeypatch, notices):
    repo, chela = _repo(tmp_path, extra="sweep:\n  blocked_hours: []\n"), tmp_path / "chela"
    wf = load_workflow(repo / "WORKFLOW.md")
    _seed_history(chela, wf, [9])
    _fake_sweep(monkeypatch, unrun=4, wanted="")
    _run(repo, chela, Filer())
    assert " — 4 owed to the next night (-5 since " in notices[0][1]


def test_an_unreadable_owed_history_is_a_warning_never_no_growth(tmp_path, monkeypatch,
                                                                  notices):
    repo, chela = _repo(tmp_path, extra="sweep:\n  blocked_hours: []\n"), tmp_path / "chela"
    path = chela / "sweep-nightly" / "owed-history.json"
    path.parent.mkdir(parents=True)
    path.write_text("{torn")
    _fake_sweep(monkeypatch, unrun=4, wanted="")
    res = _run(repo, chela, Filer())
    assert "owed_delta" not in res and "owed_growth_days" not in res
    assert any("the owed history could not be kept" in w and "UNKNOWN" in w
               for w in res["warnings"])
    assert "⚠️ 1 warning(s): the owed history could not be kept" in notices[0][1]
    assert path.read_text() == "{torn"


def test_the_first_recorded_day_has_no_delta(tmp_path, monkeypatch, notices):
    repo, chela = _repo(tmp_path, extra="sweep:\n  blocked_hours: []\n"), tmp_path / "chela"
    _fake_sweep(monkeypatch, unrun=2, wanted="")
    res = _run(repo, chela, Filer())
    assert "owed_delta" not in res and res["owed_growth_days"] == 0
    assert " — 2 owed to the next night — " in notices[0][1] + " — "
