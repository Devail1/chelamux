"""CMX-318: a parked run — held on purpose by the orchestrator, or waiting for a suite slot —
is not reported STUCK; a park that outlives itself is reported ONCE; an agent cannot park
itself. 10-22/23: six "looks STUCK" notices for CMX-303, which the orchestrator had parked."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from chela import config, dispatcher, inbox, main, park, suite_guard

NOW = 1_800_000_000.0
NOW_DT = datetime.fromtimestamp(NOW, timezone.utc)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat()


def _run(task="T1", status="running", **park_cols) -> dict:
    return {"task_id": task, "status": status, "window_name": "cmx-1", **park_cols}


def _parked(task="T1", status="running", at=NOW - 60, reason="waiting for the flaky fix",
            until=None, by="orchestrator") -> dict:
    return _run(task, status, parked_at=_iso(at) if isinstance(at, float) else at,
                parked_reason=reason, parked_until=until, parked_by=by)


def _idle(task="T1") -> dict:
    return {task: {"activity": NOW - 7200, "markers": {}}}     # quiet for 2h


@pytest.fixture(autouse=True)
def _limits(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    monkeypatch.setenv("CHELA_PARK_MAX_S", "21600")


def _kinds(events):
    return [e["kind"] for e in events]


# --- the stuck detector ---------------------------------------------------------------

def test_a_parked_run_gets_no_stuck_notice_for_n_ticks():
    store, run = inbox._empty(), _parked()
    for i in range(12):                                       # 12 ticks x 10 min = 2h
        assert inbox.stuck_events([run], store, _idle(), NOW + i * 600) == []


def test_negative_control_the_same_idle_run_unparked_is_stuck():
    events = inbox.stuck_events([_run()], inbox._empty(), _idle(), NOW)
    assert _kinds(events) == ["run_stuck"]
    assert "looks STUCK" in events[0]["summary"]


def test_a_park_that_outlives_the_max_is_exactly_one_notice():
    store, run = inbox._empty(), _parked(at=NOW - 21600 + 1200)
    assert inbox.stuck_events([run], store, _idle(), NOW) == []                 # 5h40m
    fired = [inbox.stuck_events([run], store, _idle(), NOW + 1200 + i * 600) for i in range(6)]
    assert [_kinds(f) for f in fired] == [["run_park_overdue"]] + [[]] * 5
    event = fired[0][0]
    assert "has been parked 6h" in event["summary"]
    assert "past the 6h park max" in event["summary"]
    assert "reason: waiting for the flaky fix" in event["summary"]
    assert "chela unpark T1 or re-park it" in event["summary"]
    assert event["payload"]["parked_at"] == run["parked_at"]
    assert event["payload"]["task_id"] == "T1"


def test_the_max_reads_its_env_value(monkeypatch):
    monkeypatch.setenv("CHELA_PARK_MAX_S", "3600")
    events = inbox.stuck_events([_parked(at=NOW - 3600)], inbox._empty(), _idle(), NOW)
    assert _kinds(events) == ["run_park_overdue"]
    assert "past the 1h park max" in events[0]["summary"]


def test_the_max_defaults_to_six_hours(monkeypatch):
    monkeypatch.delenv("CHELA_PARK_MAX_S")
    assert config.park_max_s() == 21600


def test_a_re_park_is_a_new_park_and_may_be_reported_again():
    store = inbox._empty()
    first = _parked(at=NOW - 30000)
    assert _kinds(inbox.stuck_events([first], store, _idle(), NOW)) == ["run_park_overdue"]
    again = _parked(at=NOW - 25000)
    assert _kinds(inbox.stuck_events([again], store, _idle(), NOW)) == ["run_park_overdue"]


def test_a_park_past_its_until_time_is_one_notice():
    store, run = inbox._empty(), _parked(until=_iso(NOW - 1))
    events = inbox.stuck_events([run], store, _idle(), NOW)
    assert _kinds(events) == ["run_park_overdue"]
    assert f"past its --until {_iso(NOW - 1)}" in events[0]["summary"]
    assert inbox.stuck_events([run], store, _idle(), NOW + 600) == []


def test_a_park_until_a_run_ends_is_reported_once_that_run_is_done():
    store = inbox._empty()
    parked = _parked(until="run:T2")
    other = _run("T2", status="awaiting_review")
    assert inbox.stuck_events([parked, other], store, _idle(), NOW) == []
    events = inbox.stuck_events([parked, {**other, "status": "done"}], store, _idle(), NOW)
    assert _kinds(events) == ["run_park_overdue"]
    assert "its --until run T2 is done" in events[0]["summary"]


def test_unparking_restarts_the_stuck_clock():
    store = inbox._empty()
    assert inbox.stuck_events([_parked()], store, _idle(), NOW) == []
    assert inbox.stuck_events([_run()], store, _idle(), NOW + 1199) == []
    assert _kinds(inbox.stuck_events([_run()], store, _idle(), NOW + 1200)) == ["run_stuck"]


def test_a_parked_run_with_no_probe_is_still_reported_when_overdue():
    events = inbox.stuck_events([_parked(at=NOW - 30000)], inbox._empty(), {}, NOW)
    assert _kinds(events) == ["run_park_overdue"]


@pytest.mark.parametrize("status", ["awaiting_review", "changes_requested", "done"])
def test_a_park_left_on_a_run_that_left_running_is_ignored(status):
    assert park.manual(_parked(status=status)) is None
    assert inbox.stuck_events([_parked(status=status)], inbox._empty(), _idle(), NOW) == []


def test_a_claimed_run_can_be_parked():
    assert park.manual(_parked(status="claimed"))["kind"] == "manual"


def test_a_slot_waiting_run_is_waiting_not_stuck():
    """CMX-119's wait, read as a park: no stuck notice while it waits."""
    probe = {"T1": {**_idle()["T1"], "slot_wait": {"waiting_since": NOW - 7000, "waited": 0}}}
    assert inbox.stuck_events([_run()], inbox._empty(), probe, NOW) == []


# --- a queued overdue notice rots -----------------------------------------------------

def _overdue_event(run):
    return inbox.stuck_events([run], inbox._empty(), _idle(), NOW)[0]


def test_a_queued_overdue_notice_is_still_true_while_the_same_park_holds():
    run = _parked(at=NOW - 30000)
    assert inbox.stale_reason(_overdue_event(run), [run]) is None


def test_a_queued_overdue_notice_rots_once_unparked_or_re_parked():
    run = _parked(at=NOW - 30000)
    event = _overdue_event(run)
    assert inbox.stale_reason(event, [_run()]) == "the park it reported was lifted or replaced"
    assert inbox.stale_reason(event, [_parked(at=NOW)]) == (
        "the park it reported was lifted or replaced")


def test_a_queued_overdue_notice_rots_once_the_run_stops_running():
    run = _parked(at=NOW - 30000)
    event = _overdue_event(run)
    assert inbox.stale_reason(event, [{**run, "status": "awaiting_review"}]) == (
        "run is now 'awaiting_review', no longer running")


# --- park.parse_until -----------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("90m", NOW_DT + timedelta(minutes=90)),
    ("2h", NOW_DT + timedelta(hours=2)),
    ("1d", NOW_DT + timedelta(days=1)),
    ("30s", NOW_DT + timedelta(seconds=30)),
    (" 2h ", NOW_DT + timedelta(hours=2)),
    ("2099-01-01T00:00:00+00:00", datetime(2099, 1, 1, tzinfo=timezone.utc)),
    ("2099-01-01T02:00:00+02:00", datetime(2099, 1, 1, tzinfo=timezone.utc)),
])
def test_until_times_are_stored_as_utc_iso(raw, expected):
    assert park.parse_until(raw, NOW_DT) == expected.isoformat()


def test_a_naive_until_is_local_time():
    naive = (NOW_DT + timedelta(hours=3)).astimezone().replace(tzinfo=None)
    assert park.parse_until(naive.isoformat(), NOW_DT) == (NOW_DT + timedelta(hours=3)).isoformat()


@pytest.mark.parametrize("raw", ["run:CMX-12", "run:abc"])
def test_until_a_run_is_kept_verbatim(raw):
    assert park.parse_until(raw, NOW_DT) == raw


@pytest.mark.parametrize("raw", [None, "", "  "])
def test_no_until_is_none(raw):
    assert park.parse_until(raw, NOW_DT) is None


@pytest.mark.parametrize("raw, words", [
    ("0m", "not in the future"),
    ("2020-01-01T00:00:00+00:00", "not in the future"),
    (NOW_DT.isoformat(), "not in the future"),
    ("run:", "is not a duration"),
    ("xrun:A", "is not a duration"),
    ("run:A B", "is not a duration"),
    ("2hx", "is not a duration"),
    ("2w", "is not a duration"),
    ("-2h", "is not a duration"),
    ("after the flaky fix", "is not a duration"),
])
def test_an_until_chela_cannot_check_is_refused(raw, words):
    with pytest.raises(ValueError, match=words):
        park.parse_until(raw, NOW_DT)


# --- park.overdue ---------------------------------------------------------------------

def _p(**kw):
    return {"kind": "manual", "reason": "r", "since": NOW - 60, "at": "a", "until": None, **kw}


@pytest.mark.parametrize("park_, runs, expected", [
    (_p(), {}, None),
    (_p(since=NOW - 21599), {}, None),
    (_p(since=NOW - 21600), {}, "it is past the 6h park max"),
    (_p(since=None, at="garbage"), {}, "its start ('garbage') cannot be read"),
    (_p(until=_iso(NOW + 1)), {}, None),
    (_p(until=_iso(NOW)), {}, f"it is past its --until ({_iso(NOW)})"),
    (_p(until="not-a-time"), {}, "its --until ('not-a-time') cannot be read"),
    (_p(until="run:T2"), {"T2": {"status": "running"}}, None),
    (_p(until="run:T2"), {"T2": {"status": "needs_human"}}, None),
    (_p(until="run:T2"), {"T2": {"status": "closed"}}, "its --until run T2 is closed"),
    (_p(until="run:T2"), {"T2": {"status": "failed"}}, "its --until run T2 is failed"),
    (_p(until="run:T2"), {}, "its --until run T2 is not a known run"),
    (_p(until="run:T2", since=NOW - 21600), {"T2": {"status": "running"}},
     "it is past the 6h park max"),
    (_p(until=_iso(NOW + 99999), since=NOW - 21600), {}, "it is past the 6h park max"),
])
def test_overdue_table(park_, runs, expected):
    assert park.overdue(park_, NOW, runs, 21600) == expected


def test_an_unreadable_parked_at_is_reported_not_trusted():
    events = inbox.stuck_events([_parked(at="not-a-time")], inbox._empty(), _idle(), NOW)
    assert _kinds(events) == ["run_park_overdue"]
    assert "parked for an unknown time" in events[0]["summary"]


# --- park.state / slot ----------------------------------------------------------------

def test_slot_reason_names_the_holders():
    assert park.slot_reason(["CMX-7", "CMX-9-r2"]) == (
        "waiting for a suite slot (holder: CMX-7, CMX-9-r2)")
    assert park.slot_reason([]) == "waiting for a suite slot (holder: unknown)"


def test_state_reads_a_manual_park_first(monkeypatch):
    monkeypatch.setattr(suite_guard, "waits_in", lambda w: {"waiting_since": 5.0, "waited": 0})
    got = park.state({**_parked(), "worktree_path": "/wt"})
    assert (got["kind"], got["reason"], got["by"]) == ("manual", "waiting for the flaky fix",
                                                       "orchestrator")
    assert got["since"] == pytest.approx(NOW - 60)


def test_state_reads_an_open_slot_wait_as_a_park(monkeypatch):
    seen = []
    monkeypatch.setattr(suite_guard, "waits_in",
                        lambda w: seen.append(w) or {"waiting_since": 5.0, "waited": 1.0})
    monkeypatch.setattr(suite_guard, "holders", lambda: ["CMX-7"])
    got = park.state({**_run(), "worktree_path": "/wt/T1"})
    assert seen == ["/wt/T1"]
    assert got == {"kind": "slot", "reason": "waiting for a suite slot (holder: CMX-7)",
                   "since": 5.0, "at": None, "until": None, "by": "suite slot"}


@pytest.mark.parametrize("wait", [None, {"waiting_since": None, "waited": 30.0}])
def test_state_with_no_open_wait_is_none(monkeypatch, wait):
    monkeypatch.setattr(suite_guard, "waits_in", lambda w: wait)
    monkeypatch.setattr(suite_guard, "holders", lambda: pytest.fail("holders read for no wait"))
    assert park.state({**_run(), "worktree_path": "/wt/T1"}) is None


def test_state_of_a_run_out_of_running_never_reads_the_ledger(monkeypatch):
    monkeypatch.setattr(suite_guard, "waits_in", lambda w: pytest.fail("ledger read"))
    assert park.state({**_run(status="awaiting_review"), "worktree_path": "/wt"}) is None
    assert park.state(_run()) is None                    # no worktree: nothing to read


def test_a_slot_wait_out_of_running_is_not_a_park():
    assert park.slot(_run(status="done"), {"waiting_since": 5.0}, ["x"]) is None


# --- suite_guard.holders ----------------------------------------------------------------

def test_holders_names_only_slots_whose_lock_is_held(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "3")
    d = tmp_path / suite_guard.SLOTS_DIRNAME
    d.mkdir()
    for i, name in enumerate(["CMX-7", "CMX-8", "CMX-9"]):
        (d / f"slot-{i}").write_text(json.dumps({"pid": 1, "cwd": f"/wts/{name}", "since": 1}))
    (d / "slot-2").write_text("{not json")
    held = []
    for i in (0, 2):
        fd = os.open(d / f"slot-{i}", os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held.append(fd)
    try:
        # slot-1's record is stale (its lock is free); slot-2 is held but unreadable.
        assert suite_guard.holders() == ["CMX-7", "unknown"]
    finally:
        for fd in held:
            os.close(fd)
    assert suite_guard.holders() == []


def test_holders_with_no_slot_files_is_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "2")
    assert suite_guard.holders() == []


def test_holders_inside_a_real_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    monkeypatch.setenv("CHELA_SUITE_SLOTS", "1")
    with suite_guard.slot(tmp_path / "CMX-42"):
        assert suite_guard.holders() == ["CMX-42"]


# --- park.refusal: an agent cannot park itself -------------------------------------------

def test_a_process_in_the_runs_own_worktree_is_refused_even_as_orchestrator(tmp_path):
    wt = tmp_path / "CMX-5"
    (wt / "sub").mkdir(parents=True)
    run = {"task_id": "CMX-5", "worktree_path": str(wt)}
    for cwd in (wt, wt / "sub"):
        why = park.refusal(run, is_orchestrator=True, env={}, cwd=str(cwd))
        assert why and "inside CMX-5's own worktree — an agent cannot park itself" in why


def test_a_sibling_directory_is_not_the_worktree(tmp_path):
    (tmp_path / "CMX-5").mkdir()
    (tmp_path / "CMX-50").mkdir()
    run = {"task_id": "CMX-5", "worktree_path": str(tmp_path / "CMX-5")}
    assert park.refusal(run, is_orchestrator=False, env={},
                        cwd=str(tmp_path / "CMX-50")) is None


@pytest.mark.parametrize("is_orch, env, refused", [
    (True, {"CLAUDECODE": "1"}, False),        # the orchestrator is a Claude session
    (False, {"CLAUDECODE": "1"}, True),        # any other Claude session is an agent
    (False, {}, False),                        # the operator in a plain terminal
    (True, {}, False),
])
def test_who_may_park(tmp_path, is_orch, env, refused):
    run = {"task_id": "CMX-5", "worktree_path": str(tmp_path / "CMX-5")}
    why = park.refusal(run, is_orchestrator=is_orch, env=env, cwd=str(tmp_path))
    assert (why is not None) is refused
    if refused:
        assert "not the orchestrator" in why


def test_refusal_reads_the_process_env_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.chdir(tmp_path)
    assert park.refusal({"task_id": "X"}, is_orchestrator=False) is not None
    monkeypatch.delenv("CLAUDECODE")
    assert park.refusal({"task_id": "X"}, is_orchestrator=False) is None


def test_refusal_reads_the_process_cwd_by_default(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    why = park.refusal({"task_id": "X", "worktree_path": str(tmp_path)}, is_orchestrator=True,
                       env={})
    assert why and "own worktree" in why


def test_a_worktree_that_cannot_be_resolved_fails_closed(tmp_path, monkeypatch):
    def boom(self, *a, **k):
        raise PermissionError("denied")
    monkeypatch.setattr(park.Path, "resolve", boom)
    why = park.refusal({"task_id": "X", "worktree_path": "/wt"}, is_orchestrator=True,
                       env={}, cwd="/elsewhere")
    assert why == "cannot tell whether this process runs inside the run's own worktree"


# --- dispatcher.park_run / unpark_run -------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))

    def seed(task="CMX-5", status="running", **cols):
        fields = {"task_id": task, "workflow_path": "/repo/WORKFLOW.md", "title": "t",
                  "status": status, "worktree_path": f"/wt/{task}", "branch_name": task.lower(),
                  "started_at": "2026-10-22T10:00:00+00:00", **cols}
        with dispatcher._db() as conn:
            conn.execute(f"INSERT INTO runs ({', '.join(fields)}) VALUES "
                         f"({', '.join('?' * len(fields))})", tuple(fields.values()))
    return seed


def _row(task="CMX-5") -> dict:
    with dispatcher._db() as conn:
        return dict(conn.execute("SELECT * FROM runs WHERE task_id=?", (task,)).fetchone())


@pytest.mark.parametrize("status", ["claimed", "running"])
def test_park_run_writes_the_four_columns(db, status):
    db(status=status)
    got = dispatcher.park_run("CMX-5", "  waiting for CMX-9  ", until="run:CMX-9", by="orchestrator")
    assert got["ok"] and got["task_id"] == "CMX-5"
    row = _row()
    assert (row["parked_reason"], row["parked_until"], row["parked_by"]) == (
        "waiting for CMX-9", "run:CMX-9", "orchestrator")
    assert datetime.fromisoformat(row["parked_at"]).tzinfo is not None
    assert row["status"] == status                      # keeps its slot and window
    logged = [json.loads(line) for line in
              Path(os.environ["CHELA_EVENTS_FILE"]).read_text().splitlines()]
    assert [e["type"] for e in logged] == ["run_parked"]


@pytest.mark.parametrize("status", ["awaiting_review", "changes_requested", "needs_human",
                                    "done", "failed", "closed"])
def test_only_a_claimed_or_running_run_can_be_parked(db, status):
    db(status=status)
    got = dispatcher.park_run("CMX-5", "why")
    assert not got["ok"]
    assert f"is {status!r} — only a claimed or running run's agent can be parked" in got["error"]
    assert _row()["parked_at"] is None


@pytest.mark.parametrize("reason", ["", "   ", None])
def test_a_park_needs_a_reason(db, reason):
    db()
    assert dispatcher.park_run("CMX-5", reason)["error"] == "a park needs a --reason"
    assert _row()["parked_at"] is None


def test_park_and_unpark_an_unknown_run(db):
    assert "no run matches 'nope'" in dispatcher.park_run("nope", "r")["error"]
    assert "no run matches 'nope'" in dispatcher.unpark_run("nope")["error"]


def test_unpark_clears_the_park(db):
    db()
    dispatcher.park_run("CMX-5", "r", until="run:X", by="me")
    assert dispatcher.unpark_run("cmx-5", by="me") == {"ok": True, "task_id": "CMX-5"}
    row = _row()
    assert [row[c] for c in ("parked_at", "parked_reason", "parked_until", "parked_by")] == [
        None] * 4
    assert dispatcher.unpark_run("CMX-5")["error"] == "CMX-5 is not parked"


# --- the CLI ------------------------------------------------------------------------

def _ns(**kw):
    return argparse.Namespace(**kw)


@pytest.fixture
def orch(monkeypatch):
    who = {"orch": True}
    monkeypatch.setattr(inbox, "caller_is_orchestrator", lambda store=None: who["orch"])
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.setenv("USER", "op")
    return who


def test_cli_park_records_the_orchestrator_and_the_until(db, orch, capsys, tmp_path,
                                                         monkeypatch):
    monkeypatch.chdir(tmp_path)
    db()
    main.cmd_park(_ns(run="CMX-5", reason="judge round frees memory", until="run:CMX-9"))
    out = capsys.readouterr().out
    assert "⏸️ CMX-5 parked by orchestrator: judge round frees memory — until run:CMX-9" in out
    assert "one notice past --until or 6h" in out
    row = _row()
    assert (row["parked_by"], row["parked_until"]) == ("orchestrator", "run:CMX-9")


def test_cli_park_by_the_operator_records_their_name(db, orch, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    orch["orch"] = False
    db()
    main.cmd_park(_ns(run="CMX-5", reason="r", until=None))
    assert _row()["parked_by"] == "op"
    assert "one notice past 6h" in capsys.readouterr().out


def test_cli_park_from_an_agent_session_is_refused(db, orch, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    orch["orch"] = False
    monkeypatch.setenv("CLAUDECODE", "1")
    db()
    with pytest.raises(SystemExit) as exc:
        main.cmd_park(_ns(run="CMX-5", reason="dodge the detector", until=None))
    assert exc.value.code == 1
    assert "park: refused — this process runs inside a Claude Code session" in (
        capsys.readouterr().out)
    assert _row()["parked_at"] is None


def test_cli_park_from_the_runs_own_worktree_is_refused(db, orch, capsys, tmp_path, monkeypatch):
    wt = tmp_path / "CMX-5"
    wt.mkdir()
    monkeypatch.chdir(wt)
    db(worktree_path=str(wt))
    with pytest.raises(SystemExit):
        main.cmd_park(_ns(run="CMX-5", reason="r", until=None))
    assert "own worktree" in capsys.readouterr().out
    assert _row()["parked_at"] is None


def test_cli_park_when_the_orchestrator_check_fails_is_not_the_orchestrator(
        db, orch, capsys, tmp_path, monkeypatch):
    def boom(store=None):
        raise RuntimeError("inbox unreadable")
    monkeypatch.setattr(inbox, "caller_is_orchestrator", boom)
    monkeypatch.setenv("CLAUDECODE", "1")
    monkeypatch.chdir(tmp_path)
    db()
    with pytest.raises(SystemExit):
        main.cmd_park(_ns(run="CMX-5", reason="r", until=None))
    assert "not the orchestrator" in capsys.readouterr().out


@pytest.mark.parametrize("until", ["soon", "0m"])
def test_cli_park_with_a_bad_until_changes_nothing(db, orch, capsys, tmp_path, monkeypatch, until):
    monkeypatch.chdir(tmp_path)
    db()
    with pytest.raises(SystemExit) as exc:
        main.cmd_park(_ns(run="CMX-5", reason="r", until=until))
    assert exc.value.code == 1
    assert capsys.readouterr().out.startswith(f"park: --until {until!r}")
    assert _row()["parked_at"] is None


def test_cli_park_an_unknown_or_finished_run_exits_1(db, orch, capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db(status="done")
    for ident, words in (("nope", "park: no run matches 'nope'"), ("CMX-5", "is 'done'")):
        with pytest.raises(SystemExit) as exc:
            main.cmd_park(_ns(run=ident, reason="r", until=None))
        assert exc.value.code == 1
        assert words in capsys.readouterr().out


def test_cli_unpark(db, orch, capsys):
    db(parked_at=_iso(NOW), parked_reason="r")
    main.cmd_unpark(_ns(run="CMX-5"))
    assert "▶️ CMX-5 unparked" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main.cmd_unpark(_ns(run="CMX-5"))
    assert exc.value.code == 1
    assert "unpark: CMX-5 is not parked" in capsys.readouterr().out


def test_the_parser_wires_park_and_unpark(monkeypatch):
    called = []
    monkeypatch.setattr(main, "cmd_park", lambda a: called.append(("park", a.run, a.reason,
                                                                   a.until)))
    monkeypatch.setattr(main, "cmd_unpark", lambda a: called.append(("unpark", a.run)))
    monkeypatch.setattr("sys.argv", ["chela", "park", "CMX-5", "--reason", "r", "--until", "2h"])
    main.main()
    monkeypatch.setattr("sys.argv", ["chela", "unpark", "CMX-5"])
    main.main()
    assert called == [("park", "CMX-5", "r", "2h"), ("unpark", "CMX-5")]


def test_dispatch_runs_shows_the_park_and_the_slot_wait(db, capsys, monkeypatch):
    db("CMX-5", parked_at=_iso(NOW - 7200), parked_reason="flaky fix first",
       parked_until="run:CMX-9", parked_by="orchestrator")
    db("CMX-6")
    db("CMX-7", status="awaiting_review")
    monkeypatch.setattr(main.time, "time", lambda: NOW)
    monkeypatch.setattr(suite_guard, "waits_in",
                        lambda w: {"waiting_since": NOW - 300, "waited": 0}
                        if w == "/wt/CMX-6" else None)
    monkeypatch.setattr(suite_guard, "holders", lambda: ["CMX-12"])
    main.cmd_dispatch_runs(_ns(status=None, awaiting=False))
    out = capsys.readouterr().out.splitlines()
    stamp = datetime.fromtimestamp(NOW - 7200, timezone.utc).strftime("%Y-%m-%d %H:%M")
    slot_stamp = datetime.fromtimestamp(NOW - 300, timezone.utc).strftime("%Y-%m-%d %H:%M")
    assert (f"      ⏸️ parked since {stamp}Z (2h00m ago): flaky fix first  until=run:CMX-9  "
            "by=orchestrator") in out
    assert (f"      ⏳ waiting since {slot_stamp}Z (5m ago): waiting for a suite slot "
            "(holder: CMX-12)") in out
    assert sum(line.startswith("      ") for line in out) == 2     # CMX-7 (in review): none


def test_dispatch_runs_status_view_shows_the_park(db, capsys, monkeypatch):
    db("CMX-5", parked_at=_iso(NOW - 60), parked_reason="r")
    monkeypatch.setattr(suite_guard, "waits_in", lambda w: None)
    main.cmd_dispatch_runs(_ns(status="running", awaiting=False))
    out = capsys.readouterr().out.splitlines()
    assert len(out) == 2 and out[1].startswith("      ⏸️ parked since ") and out[1].endswith(": r")


def test_park_line_with_an_unreadable_start():
    assert main._park_line({"kind": "manual", "reason": "r", "since": None}, NOW) == (
        "      ⏸️ parked since ?: r")


# --- the Work board -----------------------------------------------------------------

def test_the_work_board_carries_each_active_runs_park(monkeypatch, tmp_path):
    pytest.importorskip("flask")
    from chela.dashboard import app as dash
    from tests.test_runs_linear_payload import _LinearSource
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\nproject_key: CMX\ntracker:\n  kind: linear\n  team: CMX\n---\nprompt\n")
    wf_path = (repo / "WORKFLOW.md").resolve()
    common = {"workflow_path": str(wf_path), "started_at": "2026-10-22T10:00:00+00:00",
              "title": "t"}
    runs = [{**_parked("CMX-5"), **common, "worktree_path": "/wt/CMX-5"},
            {**_run("CMX-6"), **common, "worktree_path": "/wt/CMX-6"}]
    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: None)
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [wf_path])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [dict(r) for r in runs])
    monkeypatch.setattr(dash, "get_source", lambda wf: _LinearSource([]))
    monkeypatch.setattr(suite_guard, "waits_in", lambda w: None)
    wf = dash.app.test_client().get("/api/dispatcher").get_json()["workflows"][0]
    by_id = {r["task_id"]: r for r in wf["active_runs"]}
    assert by_id["CMX-5"]["park"]["kind"] == "manual"
    assert by_id["CMX-5"]["park"]["reason"] == "waiting for the flaky fix"
    assert by_id["CMX-6"]["park"] is None
