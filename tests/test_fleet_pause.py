"""CMX-214: ``chela dispatch --pause --all`` / ``--resume``. Fake panes and processes only.

Every fake appends to ONE ordered log (``fake["calls"]``), so a test asserts which calls
happened AND their order. ``inbox.run_wid`` is faked too, and by default it names a
DIFFERENT window than the row's raw ``window_id`` (``@row1`` vs ``@1``): code that reads the
row directly instead of the epoch-checked lookup messages ``@row1`` and goes red.
"""
from __future__ import annotations

import argparse
import threading
import time

import pytest

from chela import config, dispatcher, fleet, hold, inbox, main, messenger

PAUSE = fleet.PAUSE_MESSAGE
RESUME = fleet.RESUME_MESSAGE


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    world = {"runs": [], "procs": [], "calls": [], "wids": {}, "esc_ok": True,
             "msg_ok": True, "held": []}

    def run_wid(run, *a, **k):
        world["calls"].append(("run_wid", run["task_id"]))
        task = run["task_id"]
        return world["wids"].get(task, f"@{task[1:]}")

    def send_escape(wid):
        world["calls"].append(("esc", wid))
        world["held"].append(hold.active() is not None)
        return world["esc_ok"]

    def send_message(frm, wid, text):
        world["calls"].append(("msg", frm, wid, text))
        world["held"].append(hold.active() is not None)
        return world["msg_ok"]

    monkeypatch.setattr(dispatcher, "list_runs", lambda: list(world["runs"]))
    monkeypatch.setattr(inbox, "_selfcheck_scan", lambda: list(world["procs"]))
    monkeypatch.setattr(inbox, "run_wid", run_wid)
    monkeypatch.setattr(messenger, "send_escape", send_escape)
    monkeypatch.setattr(messenger, "send_message", send_message)
    return world


def sends(world):
    """The pane-touching calls, in order: ``("esc", wid)`` / ``("msg", wid, text)``."""
    return [(c[0], c[2], c[3]) if c[0] == "msg" else c
            for c in world["calls"] if c[0] in ("esc", "msg")]


def _run(task, status="running", worktree=None):
    # The row's raw id is deliberately NOT what run_wid answers (see the module docstring).
    return {"task_id": task, "status": status, "window_id": f"@row{task[1:]}",
            "window_epoch": "e-old", "worktree_path": worktree}


def _cli(**over):
    args = dict(pause=False, resume=False, hold_status=False, all=False, reason="", ttl="1h")
    return main.cmd_dispatch_hold(argparse.Namespace(**{**args, **over}))


# 1. fleet.pause_all: Escape THEN the message; a mutating command gets the message only.

def test_each_dispatched_agent_gets_escape_then_the_message_in_that_order(fake):
    fake["runs"] = [_run("T1"), _run("T2", status="claimed"),
                    _run("T3", status="awaiting_review")]
    state = fleet.pause_all(by="@0")
    assert sends(fake) == [("esc", "@1"), ("msg", "@1", PAUSE),
                           ("esc", "@2"), ("msg", "@2", PAUSE)]
    assert all(c[1] == "chela" for c in fake["calls"] if c[0] == "msg")
    assert PAUSE == ("Paused by the operator: start nothing (no tests, builds, installs, "
                     "hand-ins) until 'resume'. Note where you are.")
    assert state["agents"] == {"T1": {"wid": "@1", "state": "interrupted"},
                               "T2": {"wid": "@2", "state": "interrupted"}}
    assert fleet.read() == state


@pytest.mark.parametrize("what", ["task-finished self-check", "judge sweep",
                                  "judge self-check"])
def test_an_agent_in_a_mutating_command_gets_the_message_but_no_escape(fake, tmp_path, what):
    wt = tmp_path / "wt"
    (wt / "sub").mkdir(parents=True)
    fake["runs"] = [_run("T1", worktree=str(wt)), _run("T2")]
    fake["procs"] = [(what, wt / "sub", 100.0, 4242)]
    state = fleet.pause_all(by="@0")
    assert sends(fake) == [("msg", "@1", PAUSE),                 # never cut mid-mutation
                           ("esc", "@2"), ("msg", "@2", PAUSE)]
    assert state["agents"]["T1"] == {"wid": "@1", "state": "finishing", "finishing": what}
    assert fleet.status_lines()[1] == f"  agent T1 @ @1: finishing {what}"
    fake["calls"].clear()
    assert fleet.resume_all()["resumed"] == {"T1": True, "T2": True}   # owed one too
    assert sends(fake) == [("msg", "@1", RESUME), ("msg", "@2", RESUME)]


@pytest.mark.parametrize("what,where", [("pytest", "a"),                   # mutates nothing
                                         ("task-finished self-check", "b")])   # elsewhere
def test_an_agent_not_mutating_its_own_worktree_is_interrupted_at_once(fake, tmp_path,
                                                                       what, where):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    fake["runs"] = [_run("T1", worktree=str(tmp_path / "a"))]
    fake["procs"] = [(what, tmp_path / where, 1.0, 1)]
    assert fleet.pause_all()["agents"]["T1"] == {"wid": "@1", "state": "interrupted"}
    assert sends(fake) == [("esc", "@1"), ("msg", "@1", PAUSE)]


def test_an_escape_that_did_not_land_sends_no_message_and_is_reported(fake, caplog, capsys):
    fake["runs"], fake["esc_ok"] = [_run("T1")], False
    _cli(pause=True, all=True)
    assert sends(fake) == [("esc", "@1")]              # no message on top of a live turn
    assert fleet.read()["agents"]["T1"] == {"wid": "@1", "state": "failed"}
    assert "fleet pause: could not pause @1" in caplog.text
    assert "  agent T1 @ @1: failed" in capsys.readouterr().out.splitlines()
    fake["calls"].clear()
    assert fleet.resume_all()["resumed"] == {"T1": True}    # still owed a "resume"
    assert sends(fake) == [("msg", "@1", RESUME)]


def test_a_message_that_did_not_land_is_reported_failed(fake, caplog, capsys):
    fake["runs"], fake["msg_ok"] = [_run("T1")], False
    _cli(pause=True, all=True)
    assert sends(fake) == [("esc", "@1"), ("msg", "@1", PAUSE)]
    assert fleet.read()["agents"]["T1"]["state"] == "failed"
    assert "fleet pause: could not pause @1" in caplog.text
    assert "  agent T1 @ @1: failed" in capsys.readouterr().out.splitlines()


def test_a_successful_pause_logs_no_failure(fake, caplog):
    fake["runs"] = [_run("T1")]
    fleet.pause_all()
    assert "could not pause" not in caplog.text


# 2. main --pause --all: the hold is TAKEN first, and pause_all gets by=held.by.

def test_pause_all_takes_the_hold_before_the_first_send_and_records_its_taker(
        fake, monkeypatch, capsys):
    real_take, real_pause_all = hold.take, fleet.pause_all

    def take(**kw):
        fake["calls"].append(("hold-take",))
        return real_take(**{**kw, "by": "@orch"})

    def pause_all(**kw):
        fake["calls"].append(("pause_all", kw))
        return real_pause_all(**kw)
    monkeypatch.setattr(hold, "take", take)
    monkeypatch.setattr(fleet, "pause_all", pause_all)
    fake["runs"] = [_run("T1"), _run("T2")]
    _cli(pause=True, all=True, reason="cs2")
    kinds = [c[0] for c in fake["calls"]]
    assert kinds.index("hold-take") < kinds.index("pause_all") < kinds.index("esc")
    assert ("pause_all", {"by": "@orch"}) in fake["calls"]
    assert fake["held"] == [True] * 4                  # every Esc/message under the hold
    assert fleet.read()["by"] == "@orch"
    out = capsys.readouterr().out.splitlines()
    assert "Fleet PAUSED 0s ago by @orch (--all): agents paused" in out
    assert "  agent T1 @ @1: interrupted" in out


def test_plain_pause_interrupts_nobody(fake):
    fake["runs"] = [_run("T1")]
    _cli(pause=True)
    assert hold.active() is not None
    assert sends(fake) == [] and fleet.read() is None


def test_all_without_pause_is_refused(fake):
    with pytest.raises(SystemExit) as e:
        _cli(all=True)
    assert e.value.code == 2
    assert fake["calls"] == []


# 3. Windows come from inbox.run_wid (epoch-checked), at pause AND at resume time.

def test_pause_targets_only_the_window_run_wid_names(fake):
    fake["runs"] = [_run("T1"), _run("T2")]
    fake["wids"] = {"T1": "@7", "T2": None}      # T2's recorded id is from a dead epoch
    state = fleet.pause_all()
    assert ("run_wid", "T1") in fake["calls"] and ("run_wid", "T2") in fake["calls"]
    assert sends(fake) == [("esc", "@7"), ("msg", "@7", PAUSE)]   # never @row1 / @row2
    assert state["agents"] == {"T1": {"wid": "@7", "state": "interrupted"}}


def test_resume_re_reads_the_window_through_run_wid_at_resume_time(fake):
    fake["runs"] = [_run("T1"), _run("T2"), _run("T3")]
    fleet.pause_all()
    assert fleet.read()["agents"]["T1"]["wid"] == "@1"
    fake["calls"].clear()
    # Since the pause: T1 was respawned into @9, T2's id now resolves to nothing (a
    # recycled/other-epoch id), T3's run ended.
    fake["wids"] = {"T1": "@9", "T2": None}
    fake["runs"] = [_run("T1"), _run("T2"), _run("T3", status="failed")]
    assert fleet.resume_all()["resumed"] == {"T1": True, "T2": False}
    assert ("run_wid", "T1") in fake["calls"] and ("run_wid", "T2") in fake["calls"]
    assert sends(fake) == [("msg", "@9", RESUME)]       # not the stored @1, not @row*
    assert not fleet.path().exists()
    assert not fleet.path().with_name(fleet.STATE_FILE_NAME + ".resuming").exists()


def test_a_run_without_a_window_or_a_paused_one_is_not_messaged_again(fake):
    fake["runs"], fake["wids"] = [_run("T1"), _run("T2")], {"T2": None}
    fleet.pause_all()
    fleet.pause_all()
    assert sends(fake) == [("esc", "@1"), ("msg", "@1", PAUSE)]
    assert set(fleet.read()["agents"]) == {"T1"}


# 4. --hold-status prints who paused, from the stored state; re-pausing keeps since/by.

def test_hold_status_prints_the_stored_taker_and_age(fake, monkeypatch, capsys):
    fleet.path().write_text('{"since": 1000.0, "by": "@42", "agents": {"T1": '
                            '{"wid": "@1", "state": "finishing", "finishing": "judge sweep"}}}')
    monkeypatch.setattr(fleet.time, "time", lambda: 1000.0 + 125)
    _cli(hold_status=True)
    out = capsys.readouterr().out.splitlines()
    assert out[-2:] == ["Fleet PAUSED 2m ago by @42 (--all): agents paused",
                        "  agent T1 @ @1: finishing judge sweep"]
    fleet.path().write_text('{"since": 1000.0, "by": "", "agents": {}}')
    assert fleet.status_lines() == ["Fleet PAUSED 2m ago by ? (--all): agents paused"]


def test_re_pausing_keeps_since_and_by_skips_finishing_but_retries_failed(fake, tmp_path,
                                                                         monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    fake["runs"] = [_run("T1", worktree=str(wt)), _run("T2")]
    fake["procs"] = [("task-finished self-check", wt, 1.0, 1)]
    fake["esc_ok"] = False                                  # T2's Escape does not land
    first = fleet.pause_all(by="@0")
    assert {t: a["state"] for t, a in first["agents"].items()} == {
        "T1": "finishing", "T2": "failed"}
    fake["procs"], fake["esc_ok"] = [], True                # T1's self-check has ended
    fake["calls"].clear()
    monkeypatch.setattr(fleet.time, "time", lambda: first["since"] + 600)
    again = fleet.pause_all(by="@5")
    assert sends(fake) == [("esc", "@2"), ("msg", "@2", PAUSE)]   # T1 not messaged again
    assert again["agents"]["T1"] == first["agents"]["T1"]   # still owed only one "resume"
    assert again["agents"]["T2"]["state"] == "interrupted"
    assert (again["since"], again["by"]) == (first["since"], "@0")
    assert fleet.read() == again
    assert fleet.status_lines()[0] == "Fleet PAUSED 10m ago by @0 (--all): agents paused"


# --resume, end to end.

def test_resume_sends_resume_to_each_paused_agent_and_releases(fake, capsys):
    fake["runs"] = [_run("T1"), _run("T2")]
    _cli(pause=True, all=True)
    fake["calls"].clear()
    fake["runs"] = [_run("T1"), _run("T2", status="failed")]
    _cli(resume=True)
    assert sends(fake) == [("msg", "@1", "resume: continue your plan")]
    assert hold.active() is None and fleet.read() is None
    assert "sent 'resume' to 1 of 1" in capsys.readouterr().out
    _cli(resume=True)                                       # idempotent: nothing twice
    assert len(sends(fake)) == 1
    _cli(pause=True, all=True)
    fake["msg_ok"] = False
    _cli(resume=True)
    assert "T1: 'resume' was NOT delivered" in capsys.readouterr().out


def test_resume_messages_while_the_hold_stands_and_counts_only_deliveries(fake, capsys,
                                                                         monkeypatch):
    fake["runs"] = [_run("T1"), _run("T2")]
    _cli(pause=True, all=True)
    held_at_send = []

    def send(frm, wid, text):
        held_at_send.append(hold.active() is not None)
        return wid == "@1"                                  # T2's 'resume' does not land
    monkeypatch.setattr(messenger, "send_message", send)
    _cli(resume=True)
    assert held_at_send == [True, True]     # no tick could claim while agents were resumed
    assert hold.active() is None
    out = capsys.readouterr().out
    assert "sent 'resume' to 1 of 2 interrupted agent(s)" in out
    assert "T2: 'resume' was NOT delivered" in out and "T1: 'resume' was NOT" not in out


def test_two_concurrent_resumes_send_resume_exactly_once(fake, monkeypatch):
    fake["runs"] = [_run("T1")]
    fleet.pause_all()
    fake["calls"].clear()
    # Each caller that gets as far as READING the state waits here for the other, so a
    # claim that is not atomic (read, then unlink) lets both read it and both send.
    inside = threading.Barrier(2, timeout=1.0)
    real_read = fleet.read

    def read(p=None):
        try:
            inside.wait()
        except threading.BrokenBarrierError:
            pass                                            # the other caller lost the claim
        return real_read(p)
    monkeypatch.setattr(fleet, "read", read)
    start, results = threading.Barrier(2), []

    def caller():
        start.wait()
        results.append(fleet.resume_all())
    threads = [threading.Thread(target=caller) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert sends(fake) == [("msg", "@1", RESUME)]
    assert sorted(r is None for r in results) == [False, True]


@pytest.mark.parametrize("body", ["{not json", '{"agents": []}', "[]"])
def test_an_unreadable_state_file_names_nobody(fake, body, caplog):
    fleet.path().write_text(body)
    assert fleet.read() is None
    assert fleet.resume_all() is None and sends(fake) == []
    assert "fleet pause state" in caplog.text
    kept = fleet.path().with_name(fleet.STATE_FILE_NAME + ".resuming")
    assert not fleet.path().exists() and kept.read_text() == body   # kept, for the hand-off
    assert str(kept) in caplog.text


def test_ttl_expiry_resumes_the_fleet(fake):
    assert fleet.tick(None) == []                           # no fleet pause: no-op
    fake["runs"] = [_run("T1")]
    hold.take(ttl_seconds=1)
    fleet.pause_all()
    fake["calls"].clear()
    assert fleet.tick(hold.active(now=time.time() + 5)) == [
        "fleet pause ENDED with the hold — sent 'resume' to 1/1 agent(s)"]
    assert sends(fake) == [("msg", "@1", "resume: continue your plan")]
    assert fleet.read() is None and fleet.tick(None) == []


def test_a_live_hold_keeps_the_fleet_paused(fake):
    fake["runs"] = [_run("T1")]
    held = hold.take(ttl_seconds=600)
    fleet.pause_all()
    fake["calls"].clear()
    assert fleet.tick(held) == [] and sends(fake) == [] and fleet.read() is not None


# --- the stall detector respects a pause -----------------------------------------------

def test_a_held_queue_mutes_stuck_until_resume_then_the_clock_restarts(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    store, now = inbox._empty(), 1_000_000.0
    run = {"task_id": "T1", "status": "running", "window_name": "cmx-1"}
    probe = {"T1": {"activity": now - 1800, "markers": {}}}   # idle 30 min
    assert inbox.stuck_events([run], store, probe, now, held=True) == []
    assert inbox.stuck_events([run], store, probe, now + 600, held=True) == []
    resumed = now + 600
    assert inbox.stuck_events([run], store, probe, resumed + 1100) == []
    kinds = [e["kind"] for e in inbox.stuck_events([run], store, probe, resumed + 1200)]
    assert kinds == ["run_stuck"]


def test_without_a_hold_the_same_idle_agent_is_stuck(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    run = {"task_id": "T1", "status": "running", "window_name": "cmx-1"}
    probe = {"T1": {"activity": 1_000_000.0 - 1800, "markers": {}}}
    events = inbox.stuck_events([run], inbox._empty(), probe, 1_000_000.0, held=False)
    assert [e["kind"] for e in events] == ["run_stuck"]


def test_a_resume_another_caller_already_claimed_is_not_reported(fake, monkeypatch):
    fake["runs"] = [_run("T1")]
    fleet.pause_all()
    monkeypatch.setattr(fleet, "resume_all", lambda: None)   # `--resume` won the rename
    assert fleet.tick(None) == []


def test_a_resume_that_did_not_land_is_named_not_counted_as_sent(fake):
    fake["runs"] = [_run("T1"), _run("T2")]
    fleet.pause_all()
    fake["msg_ok"] = False
    assert fleet.tick(None) == [
        "fleet pause ENDED with the hold — sent 'resume' to 0/2 agent(s)",
        "fleet resume did NOT reach T1, T2: send it 'resume' by hand"]
