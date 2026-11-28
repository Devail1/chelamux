"""CMX-511: restore must not resume a session that is already live in another window.

11-28: a fresh Claude in @0 (launched ``--session-id 0eab56ea…``) ``/resume``d the
orchestrator conversation, and restore resumed it a second time in @2 — two processes on
one transcript. Restore judged "live" from launch command lines (``--resume <sid>``); a
``/resume`` inside a running Claude never shows there. These drive :func:`restore.resume`
against a real :func:`sessions.wids_running_session` over a fake pane map, with the two
sources a live window speaks through faked at their leaves: the hook event log and Claude
Code's own ``sessions/<pid>.json`` registry.
"""
from __future__ import annotations

import json

import pytest

from chela import event_log, sessions
from chela import restore as restore_mod
from chela.restore import RESUMED, SKIPPED, ApplyResult, Verdict, resume
from chela.spawn import SpawnResult

OLD = "786-1784045825"
A = "0eab56ea-7cf1-491b-b77c-d4931b87f411"     # what @0 was launched with
S = "c084360a-cdc2-4a78-b038-2442d830ed92"     # what @0 /resumed into, and restore owes
T = "11112222-3333-4444-5555-666677778888"
PID = 4242
TICKS = "2256324"


def _pane(wid="@0", resumed=None, pid=PID, started=100.0):
    return sessions.Pane(wid=wid, path="/home/x", command="claude", claude_pid=pid,
                         launched_in="/home/x", resumed=resumed, started=started)


def _row(session_id=S):
    return Verdict(store="session-ids", wid="@0", stamped_epoch=OLD, verdict="MANUAL",
                   session_id=session_id, new_wid=None, cwd="/home/x", label="orchestrator")


def _kit(calls, **overrides):
    kit = {
        "spawn_window": lambda cwd, command=None, **kw: (
            calls.append(("spawn", cwd, command)),
            SpawnResult(ok=True, name="x", wid="@99", cwd=cwd))[1],
        "check_resumed": lambda wid, session_id: True,
        "resume_blocked": lambda session_id: None,
        "record_resume_failure": lambda session_id, reason: calls.append(("fail", reason)),
        "clear_resume_failure": lambda session_id: None,
        "kill_window": lambda wid: calls.append(("kill", wid)),
        "register_orchestrator": lambda wid: {"ok": True},
        "launch_settings_for": lambda sid: None,
        "name_for": lambda sid: None,
        "remote_control_on": lambda: False,
        "archive": lambda entry: calls.append(("archive", entry["session_id"]
                                               if isinstance(entry, dict) else entry)),
        "remove_session": lambda wid, sid, stamped: (calls.append(("remove", sid)), True)[1],
    }
    kit.update(overrides)
    return kit


@pytest.fixture()
def fleet(monkeypatch):
    """@0 launched ``--session-id A`` (so its command line claims nothing), its hook log and
    registry set per test. ``sessions.panes`` returns this map for every ``force``."""
    state = {"panes": {"@0": _pane()}, "ring": [], "forced": []}

    def panes(force=False):
        state["forced"].append(force)
        return state["panes"]

    monkeypatch.setattr(sessions, "panes", panes)
    monkeypatch.setattr(event_log, "ring", lambda: list(state["ring"]))
    monkeypatch.setattr(sessions, "_proc_start_ticks", lambda pid: TICKS)
    return state


def _registry(pid, sid, ticks=TICKS):
    d = sessions.claude_sessions_dir()
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{pid}.json").write_text(json.dumps({"pid": pid, "sessionId": sid,
                                               "procStart": ticks}))


# --- acceptance: a window that /resumed into S makes restore skip S ------------------

def test_a_window_that_resumed_into_S_per_its_REGISTRY_makes_restore_skip_S(fleet):
    _registry(PID, S)                     # Claude Code rewrote it on the /resume
    calls = []

    results = resume([_row()], [], **_kit(calls))

    assert results == [ApplyResult(_row(), SKIPPED, "already live at @0")]
    assert calls == [], "nothing may be spawned, archived or removed"
    assert fleet["forced"] and all(fleet["forced"]), "the check must read panes fresh"


def test_a_window_that_resumed_into_S_per_its_HOOK_LOG_makes_restore_skip_S(fleet):
    fleet["ring"] = [{"wid": "@0", "ts": 101.0, "session_id": A},
                     {"wid": "@0", "ts": 102.0, "session_id": S}]
    calls = []

    results = resume([_row()], [], **_kit(calls))

    assert results == [ApplyResult(_row(), SKIPPED, "already live at @0")]
    assert calls == []


def test_NEGATIVE_CONTROL_the_old_command_line_check_resumes_S_a_second_time(fleet):
    """The same fleet with liveness judged off launch command lines only — the pre-CMX-511
    rule. @0's ``--session-id A`` claims nothing, so S is launched again: the 11-28 bug."""
    _registry(PID, S)
    pm = fleet["panes"]
    calls = []

    results = resume([_row()], [], **_kit(
        calls, windows_running=lambda sid: [w for w, p in pm.items() if p.resumed == sid]))

    assert ("spawn", "/home/x", f"claude --resume {S}") in calls
    assert results[0].action == RESUMED


def test_S_is_still_resumed_when_no_window_runs_it(fleet):
    _registry(PID, A)                     # @0 is still on the session it launched with
    calls = []

    results = resume([_row()], [], **_kit(calls))

    assert calls[0] == ("spawn", "/home/x", f"claude --resume {S}")
    assert results == [ApplyResult(_row(), RESUMED, "resumed at @99")]


def test_a_window_launched_resume_S_that_moved_on_to_T_does_not_block_S(fleet):
    """The command line is the session it was LAUNCHED with; the registry says it now runs T.
    S is gone, so restore must still bring it back."""
    fleet["panes"] = {"@0": _pane(resumed=S)}
    _registry(PID, T)
    calls = []

    results = resume([_row()], [], **_kit(calls))

    assert results[0].action == RESUMED
    assert calls[0][0] == "spawn"


# --- the race: the other window switches while the launch is coming up --------------

def test_a_switch_DURING_the_launch_closes_the_duplicate_and_skips(fleet):
    seen = iter([[], ["@0", "@99"]])
    calls = []

    results = resume([_row()], [], **_kit(calls, windows_running=lambda sid: next(seen)))

    assert calls == [("spawn", "/home/x", f"claude --resume {S}"), ("kill", "@99")]
    assert results == [ApplyResult(_row(), SKIPPED, (
        "already live at @0; it switched to this session while restore launched it, so "
        "the duplicate at @99 was closed"))]


def test_the_launched_window_itself_is_not_a_duplicate(fleet):
    seen = iter([[], ["@99"]])
    calls = []

    results = resume([_row()], [], **_kit(calls, windows_running=lambda sid: next(seen)))

    assert ("kill", "@99") not in calls
    assert [c[0] for c in calls] == ["spawn", "archive", "remove"]
    assert results == [ApplyResult(_row(), RESUMED, "resumed at @99")]


def test_the_default_post_check_reads_live_windows_through_a_fresh_pane_read(fleet):
    """End to end through the default seam: @0 switches to S after the pre-check, and the
    spawned @99 runs S by its command line. The duplicate is closed, @0 is kept."""
    calls = []

    def spawn(cwd, command=None, **kw):
        calls.append(("spawn", cwd, command))
        fleet["panes"] = {"@0": _pane(), "@99": _pane("@99", resumed=S, pid=PID + 1)}
        _registry(PID, S)
        return SpawnResult(ok=True, name="x", wid="@99", cwd=cwd)

    results = resume([_row()], [], **_kit(calls, spawn_window=spawn))

    assert calls[-1] == ("kill", "@99")
    assert results[0].action == SKIPPED and results[0].detail.startswith("already live at @0;")


# --- sessions.wids_running_session ---------------------------------------------------

def test_wids_running_session_falls_back_to_the_command_line_only_when_nothing_else_speaks(
        fleet):
    fleet["panes"] = {"@0": _pane(resumed=S), "@1": _pane("@1", pid=PID + 1)}

    assert sessions.wids_running_session(S, fleet["panes"]) == ["@0"]


def test_wids_running_session_lists_every_window_running_it(fleet):
    _registry(PID, S)
    _registry(PID + 1, S)
    fleet["panes"] = {"@0": _pane(), "@3": _pane("@3", pid=PID + 1),
                      "@5": _pane("@5", pid=PID + 2)}

    assert sessions.wids_running_session(S, fleet["panes"]) == ["@0", "@3"]


@pytest.mark.parametrize("sid", [None, "", "not a session id; rm -rf"])
def test_wids_running_session_refuses_a_malformed_id(fleet, sid):
    fleet["panes"] = {"@0": _pane(resumed=sid or None)}

    assert sessions.wids_running_session(sid, fleet["panes"]) == []


def test_default_windows_running_forces_a_fresh_pane_read(fleet):
    _registry(PID, S)

    assert restore_mod._default_windows_running(S) == ["@0"]
    assert fleet["forced"] == [True]
