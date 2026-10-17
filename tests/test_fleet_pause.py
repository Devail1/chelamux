"""CMX-214: ``chela dispatch --pause --all`` / ``--resume``. Fake panes and processes only."""
from __future__ import annotations

import argparse

import pytest

from chela import config, dispatcher, fleet, hold, inbox, main, messenger


@pytest.fixture
def fake(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    world = {"runs": [], "procs": [], "keys": [], "msgs": [], "esc_ok": True, "msg_ok": True}
    monkeypatch.setattr(dispatcher, "list_runs", lambda: list(world["runs"]))
    monkeypatch.setattr(inbox, "_selfcheck_scan", lambda: list(world["procs"]))
    monkeypatch.setattr(messenger, "send_escape",
                        lambda wid: world["keys"].append(wid) or world["esc_ok"])
    monkeypatch.setattr(messenger, "send_message", lambda frm, wid, text: world["msgs"].append(
        (frm, wid, text)) or world["msg_ok"])
    return world


def _run(task, status="running", wid=None, worktree=None):
    return {"task_id": task, "status": status, "window_id": wid or f"@{task[1:]}",
            "window_epoch": None, "worktree_path": worktree}


def _cli(**over):
    args = dict(pause=False, resume=False, hold_status=False, all=False, reason="", ttl="1h")
    return main.cmd_dispatch_hold(argparse.Namespace(**{**args, **over}))


def test_pause_all_holds_and_interrupts_each_dispatched_window_only(fake, capsys):
    fake["runs"] = [_run("T1"), _run("T2", status="claimed"),
                    _run("T3", status="awaiting_review")]
    _cli(pause=True, all=True, reason="cs2")
    assert hold.active() is not None
    assert fake["keys"] == ["@1", "@2"]                     # Escape, never C-c
    assert fake["msgs"] == [
        ("chela", "@1", "Paused by the operator: start nothing (no tests, builds, installs, "
                        "hand-ins) until 'resume'. Note where you are."),
        ("chela", "@2", fleet.PAUSE_MESSAGE)]
    assert "T3" not in capsys.readouterr().out.split("Fleet")[1]
    _cli(hold_status=True)
    out = capsys.readouterr().out
    assert "Fleet PAUSED 0s ago by" in out and "agent T1 @ @1: interrupted" in out


def test_plain_pause_interrupts_nobody(fake):
    fake["runs"] = [_run("T1")]
    _cli(pause=True)
    assert hold.active() is not None
    assert fake["keys"] == [] and fake["msgs"] == [] and fleet.read() is None


def test_all_without_pause_is_refused(fake):
    with pytest.raises(SystemExit) as e:
        _cli(all=True)
    assert e.value.code == 2


@pytest.mark.parametrize("esc_ok,msg_ok", [(False, True), (True, False)])
def test_an_interrupt_that_did_not_land_is_failed_and_still_owed_resume(fake, esc_ok, msg_ok,
                                                                       caplog):
    fake["runs"], fake["esc_ok"], fake["msg_ok"] = [_run("T1")], esc_ok, msg_ok
    state = fleet.pause_all()
    assert state["agents"]["T1"]["state"] == "failed"
    assert "fleet pause: could not pause @1" in caplog.text
    fake["msg_ok"] = True
    assert fleet.resume_all()["resumed"] == {"T1": True}


@pytest.mark.parametrize("what", ["task-finished self-check", "judge sweep",
                                  "judge self-check"])
def test_an_agent_in_a_mutating_command_gets_the_message_but_no_escape(fake, tmp_path, what):
    wt = tmp_path / "wt"
    (wt / "sub").mkdir(parents=True)
    fake["runs"] = [_run("T1", worktree=str(wt))]
    fake["procs"] = [(what, wt / "sub", 100.0, 4242)]
    state = fleet.pause_all(by="@0")
    assert state["agents"]["T1"] == {"wid": "@1", "state": "finishing", "finishing": what}
    assert fake["keys"] == []                               # never cut mid-mutation
    assert fake["msgs"] == [("chela", "@1", fleet.PAUSE_MESSAGE)]   # the message, no Escape
    assert fleet.status_lines()[1] == f"  agent T1 @ @1: finishing {what}"
    assert fleet.resume_all()["resumed"] == {"T1": True}    # it stopped on our word too


@pytest.mark.parametrize("what,where", [("pytest", "a"),                   # mutates nothing
                                         ("task-finished self-check", "b")])   # elsewhere
def test_an_agent_not_mutating_its_own_worktree_is_interrupted_at_once(fake, tmp_path,
                                                                       what, where):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    fake["runs"] = [_run("T1", worktree=str(tmp_path / "a"))]
    fake["procs"] = [(what, tmp_path / where, 1.0, 1)]
    assert fleet.pause_all()["agents"]["T1"]["state"] == "interrupted"
    assert fake["keys"] == ["@1"]


def test_a_run_without_a_window_or_a_paused_one_is_not_messaged_again(fake):
    fake["runs"] = [_run("T1"), {**_run("T2"), "window_id": ""}]
    fleet.pause_all()
    fleet.pause_all()
    assert fake["keys"] == ["@1"] and len(fake["msgs"]) == 1
    assert set(fleet.read()["agents"]) == {"T1"}


def test_resume_sends_resume_to_each_frozen_agent_and_releases(fake, capsys):
    fake["runs"] = [_run("T1"), _run("T2")]
    _cli(pause=True, all=True)
    fake["msgs"].clear()
    fake["runs"] = [_run("T1"), _run("T2", status="failed")]
    _cli(resume=True)
    assert fake["msgs"] == [("chela", "@1", "resume: continue your plan")]
    assert hold.active() is None and fleet.read() is None
    assert "sent 'resume' to 1 of 1" in capsys.readouterr().out
    _cli(resume=True)                                       # idempotent: nothing twice
    assert len(fake["msgs"]) == 1
    _cli(pause=True, all=True)
    fake["msg_ok"] = False
    _cli(resume=True)
    assert "T1: 'resume' was NOT delivered" in capsys.readouterr().out


@pytest.mark.parametrize("body", ["{not json", '{"agents": []}', "[]"])
def test_an_unreadable_state_file_names_nobody(fake, body, caplog):
    fleet.path().write_text(body)
    assert fleet.read() is None
    assert fleet.resume_all() is None and fake["msgs"] == []
    assert "fleet pause state" in caplog.text
