"""CMX-450: a manual window name and a queued title survive a tmux restart, kept by session id.

Both live as tmux window options and die with the server. :mod:`chela.session_names` keeps
them by the session the window runs, and :func:`chela.session_names.restore_tick` puts them
back on whichever window runs that session after a restore. No live tmux: a fake tmux keeps
per-window options; session resolution is stubbed at its seams.
"""
from __future__ import annotations

import inspect
import json
import subprocess
import types

import pytest

from chela import agent_manager, config, discovery, rc_rename, session_names, sessions, sessionids
from chela import main, suggest_names, transcripts
from chela.dashboard import app as dash
from tests.test_suggest_names import EMPTY, _Fleet

SID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"


def _pane(wid, manual=False, pending=None):
    return sessions.Pane(wid=wid, manual_name=manual, title_pending=pending)


class _Tmux:
    """Records argv; rename-window / set-window-option succeed unless ``fail`` names one."""

    def __init__(self, fail=()):
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.fail = set(fail)

    def __call__(self, cmd, *a, **kw):
        self.calls.append(list(cmd))
        self.kwargs.append(kw)
        rc = 1 if tuple(cmd[:2]) in self.fail else 0
        return types.SimpleNamespace(returncode=rc, stdout="", stderr="boom" if rc else "")


@pytest.fixture
def tmux(monkeypatch):
    fake = _Tmux()
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    return fake


@pytest.fixture
def rc(monkeypatch):
    seen = []
    monkeypatch.setattr(rc_rename, "request", lambda wid, name: seen.append((wid, name)))
    return seen


def _store():
    return json.loads((config.CHELA_DIR / "session-names.json").read_text())


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

def test_record_name_and_title_by_session_and_forget():
    assert session_names.record_name(SID, "tapparty") is True
    assert session_names.record_title(SID, "One Letter: ladder words") is True
    row = _store()[SID]
    assert (row["name"], row["title"]) == ("tapparty", "One Letter: ladder words")
    assert session_names.record_title(SID, None) is True
    assert "title" not in _store()[SID] and _store()[SID]["name"] == "tapparty"
    assert session_names.record_name(SID, None) is True
    assert _store() == {}                     # neither left: the row goes


def test_no_session_id_records_nothing():
    assert session_names.record_name(None, "x") is False
    assert session_names.record_title("", "x") is False
    assert not (config.CHELA_DIR / "session-names.json").exists()
    assert session_names.entries() == {}


@pytest.mark.parametrize("sid", [None, ""])
def test_no_session_id_has_no_stored_name(sid):
    """CMX-505: a missing session id reads no name, even when the store holds a row keyed by
    the empty string (a hand-edited or foreign file: a JSON key can be ``""``). Without the
    guard ``name_for("")`` would hand that row's name to a restore that has no session."""
    path = config.CHELA_DIR / "session-names.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"": {"name": "stray", "ts": 1.0}, SID: {"name": "mine"}}))
    assert session_names.entries()[""]["name"] == "stray"     # the row really is readable
    assert session_names.name_for(SID) == "mine"
    assert session_names.name_for(sid) is None


def test_forgetting_what_was_never_stored_writes_nothing():
    assert session_names.record_title(SID, None) is True
    assert not (config.CHELA_DIR / "session-names.json").exists()


def test_an_unwritable_store_is_logged_not_raised(monkeypatch):
    def boom(data):
        raise PermissionError("read-only")
    monkeypatch.setattr(session_names, "_write", boom)
    assert session_names.record_name(SID, "x") is False


@pytest.mark.parametrize("raw", ["not json", "[1, 2]", '{"s": "not a dict"}'])
def test_a_corrupt_store_reads_as_empty(raw):
    path = config.CHELA_DIR / "session-names.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw)
    assert session_names.entries() == {}


def test_old_rows_are_pruned_on_write(monkeypatch):
    now = [1_000_000_000.0]
    monkeypatch.setattr(session_names.time, "time", lambda: now[0])
    session_names.record_name(OTHER, "old")
    now[0] += 90 * 86400 + 1                  # 90 days, literal: the documented window
    session_names.record_name(SID, "new")
    assert set(_store()) == {SID}
    now[0] += 89 * 86400
    session_names.record_name("33333333-2222-3333-4444-555555555555", "x")
    assert SID in _store()


def test_session_for_window_live_then_pin_then_none(monkeypatch):
    monkeypatch.setattr(sessions, "session_of_window", lambda wid: {"@1": SID}.get(wid))
    monkeypatch.setattr(sessionids, "session_id_for", lambda wid: {"@2": OTHER}.get(wid))
    assert session_names.session_for_window("@1") == SID
    assert session_names.session_for_window("@2") == OTHER
    assert session_names.session_for_window("@3") is None

    def boom(wid):
        raise RuntimeError("no tmux")
    monkeypatch.setattr(sessions, "session_of_window", boom)
    assert session_names.session_for_window("@1") is None


# ---------------------------------------------------------------------------
# Restoring: names
# ---------------------------------------------------------------------------

def _tick(monkeypatch, panes, windows, where):
    monkeypatch.setattr(sessions, "wid_for_session", lambda sid, pm: where.get(sid))
    return session_names.restore_tick(pane_map=panes, windows=windows)


def test_a_stored_name_goes_back_on_the_restored_window(monkeypatch, tmux, rc):
    session_names.record_name(SID, "tapparty")
    actions = _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "swim-proto"}, {SID: "@7"})
    assert actions == ["@7 name swim-proto -> tapparty"]
    assert tmux.calls[0] == ["tmux", "rename-window", "-t", "=chela:@7", "tapparty"]
    assert tmux.kwargs[0] == {"capture_output": True, "text": True, "timeout": 5}
    assert ["tmux", "set-window-option", "-t", "=chela:@7", "@chela_manual_name", "1"] in tmux.calls
    assert ["tmux", "set-window-option", "-t", "=chela:@7", "allow-rename", "off"] in tmux.calls
    assert rc == [("@7", "tapparty")]


def test_a_window_already_manually_named_is_left_alone(monkeypatch, tmux, rc):
    session_names.record_name(SID, "tapparty")
    assert _tick(monkeypatch, {"@7": _pane("@7", manual=True)}, {"@7": "renamed-since"},
                 {SID: "@7"}) == []
    assert tmux.calls == [] and rc == []


def test_a_name_another_window_holds_is_never_taken(monkeypatch, tmux, rc):
    session_names.record_name(SID, "tapparty")
    assert _tick(monkeypatch, {"@7": _pane("@7"), "@8": _pane("@8")},
                 {"@7": "swim-proto", "@8": "tapparty"}, {SID: "@7"}) == []
    assert tmux.calls == []


def test_a_window_already_wearing_its_name_is_only_marked(monkeypatch, tmux, rc):
    session_names.record_name(SID, "tapparty")
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "tapparty"},
                 {SID: "@7"}) == ["@7 name tapparty -> tapparty"]
    assert ["tmux", "set-window-option", "-t", "=chela:@7", "@chela_manual_name", "1"] in tmux.calls


def test_an_unresolved_session_renames_nothing(monkeypatch, tmux, rc):
    session_names.record_name(SID, "tapparty")
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "swim-proto"}, {}) == []
    # A wid the pane map has but tmux no longer lists: not live, nothing done.
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {}, {SID: "@7"}) == []
    assert tmux.calls == []


@pytest.mark.parametrize("exc", [FileNotFoundError("tmux"), subprocess.TimeoutExpired("tmux", 5)])
def test_a_rename_tmux_cannot_run_is_not_reported(monkeypatch, rc, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(subprocess, "run", boom)
    session_names.record_name(SID, "tapparty")
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "swim-proto"}, {SID: "@7"}) == []
    assert rc == []


def test_a_rename_tmux_refuses_is_not_marked_manual(monkeypatch, rc):
    fake = _Tmux(fail={("tmux", "rename-window")})
    monkeypatch.setattr(subprocess, "run", fake)
    session_names.record_name(SID, "tapparty")
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "swim-proto"}, {SID: "@7"}) == []
    assert [c[:2] for c in fake.calls] == [["tmux", "rename-window"]] and rc == []


def test_an_empty_store_reads_no_panes(monkeypatch):
    monkeypatch.setattr(sessions, "panes", lambda *a, **k: pytest.fail("read panes"))
    assert session_names.restore_tick() == []


# ---------------------------------------------------------------------------
# Restoring: queued titles
# ---------------------------------------------------------------------------

def test_a_stored_title_is_queued_again_on_the_restored_window(monkeypatch, tmux):
    monkeypatch.setattr(session_names, "_applied_title", lambda sid: None)
    session_names.record_title(SID, "One Letter: ladder words")
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "oneletter"}, {SID: "@7"}) == [
        "@7 title 'One Letter: ladder words' queued again"]
    assert tmux.calls == [["tmux", "set-window-option", "-t", "=chela:@7",
                           "@chela_title_pending", "One Letter: ladder words"]]


def test_a_title_already_queued_is_not_queued_twice(monkeypatch, tmux):
    session_names.record_title(SID, "T")
    assert _tick(monkeypatch, {"@7": _pane("@7", pending="T")}, {"@7": "w"}, {SID: "@7"}) == []
    assert tmux.calls == []


def test_a_title_the_transcript_shows_applied_is_dropped(monkeypatch, tmp_path, tmux):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps({"type": "custom-title", "customTitle": "T"}) + "\n")
    monkeypatch.setattr(sessions, "transcript_for_session", lambda sid: path if sid == SID else None)
    session_names.record_title(SID, "T")
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "w"}, {SID: "@7"}) == [
        "@7 title 'T' already applied"]
    assert tmux.calls == [] and session_names.entries() == {}


def test_a_different_applied_title_does_not_drop_the_queued_one(monkeypatch, tmp_path, tmux):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps({"type": "custom-title", "customTitle": "Older"}) + "\n")
    monkeypatch.setattr(sessions, "transcript_for_session", lambda sid: path)
    session_names.record_title(SID, "T")
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "w"}, {SID: "@7"}) == [
        "@7 title 'T' queued again"]
    monkeypatch.setattr(sessions, "transcript_for_session", lambda sid: None)
    assert session_names._applied_title(SID) is None


def test_a_title_retitled_since_it_was_queued_is_dropped(monkeypatch, tmp_path, tmux):
    """Queued 'A' over 'Old'; the user then ran `/rename B` themselves: B wins, A is dropped."""
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps({"type": "custom-title", "customTitle": "Old"}) + "\n")
    monkeypatch.setattr(sessions, "transcript_for_session", lambda sid: path)
    session_names.record_title(SID, "A")
    assert session_names.entries()[SID]["title_over"] == "Old"
    with path.open("a") as fh:
        fh.write(json.dumps({"type": "custom-title", "customTitle": "B"}) + "\n")
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "w"}, {SID: "@7"}) == [
        "@7 title 'A' dropped: retitled to 'B'"]
    assert tmux.calls == [] and session_names.entries() == {}


def test_a_title_queued_over_no_title_is_dropped_once_one_appears(monkeypatch, tmp_path, tmux):
    path = tmp_path / "s.jsonl"
    path.write_text("")
    monkeypatch.setattr(sessions, "transcript_for_session", lambda sid: path)
    session_names.record_title(SID, "A")
    assert session_names.entries()[SID]["title_over"] == ""
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "w"}, {SID: "@7"}) == [
        "@7 title 'A' queued again"]
    path.write_text(json.dumps({"type": "custom-title", "customTitle": "B"}) + "\n")
    assert _tick(monkeypatch, {"@7": _pane("@7")}, {"@7": "w"}, {SID: "@7"}) == [
        "@7 title 'A' dropped: retitled to 'B'"]


def test_a_non_numeric_ts_is_pruned_not_raised():
    path = config.CHELA_DIR / "session-names.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({OTHER: {"name": "x", "ts": "x"}}))
    assert session_names.record_name(SID, "tapparty") is True
    assert set(_store()) == {SID}


def test_a_queue_tmux_refuses_is_not_reported(monkeypatch):
    fake = _Tmux(fail={("tmux", "set-window-option")})
    monkeypatch.setattr(subprocess, "run", fake)
    monkeypatch.setattr(session_names, "_applied_title", lambda sid: None)
    session_names.record_title(SID, "T")
    assert _tick(monkeypatch, {"@7": _pane("@7", manual=True)}, {"@7": "w"}, {SID: "@7"}) == []
    assert session_names.entries()[SID]["title"] == "T"


# ---------------------------------------------------------------------------
# The writers: dashboard rename, Suggest names' queue
# ---------------------------------------------------------------------------

def test_the_dashboard_rename_records_the_name_by_session(monkeypatch, tmux):
    monkeypatch.setattr(dash.discovery, "get_windows_by_id", lambda: {"@2": "swim-proto"})
    monkeypatch.setattr(session_names, "session_for_window", lambda wid: {"@2": SID}.get(wid))
    r = dash.app.test_client().post("/api/agents/@2/rename", json={"name": "tapparty"})
    assert r.status_code == 200
    assert session_names.entries()[SID]["name"] == "tapparty"


def test_a_refused_dashboard_rename_records_nothing(monkeypatch):
    monkeypatch.setattr(dash.subprocess, "run", _Tmux(fail={("tmux", "rename-window")}))
    monkeypatch.setattr(dash.discovery, "get_windows_by_id", lambda: {"@2": "swim-proto"})
    monkeypatch.setattr(session_names, "session_for_window", lambda wid: SID)
    r = dash.app.test_client().post("/api/agents/@2/rename", json={"name": "tapparty"})
    assert r.status_code == 500 and session_names.entries() == {}


def test_a_queued_title_is_recorded_and_cleared_once_typed(monkeypatch):
    monkeypatch.setattr(session_names, "session_for_window", lambda wid: {"@1": SID}.get(wid))
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "busy"}, panes={"@1": EMPTY})
    assert suggest_names.apply_title("@1", "Ladder")["state"] == "pending"
    assert session_names.entries()[SID]["title"] == "Ladder"
    f.status["@1"] = "idle"
    assert suggest_names.flush_pending() == {"@1": "sent"}
    assert session_names.entries() == {}


def test_a_dropped_title_is_cleared_from_the_store(monkeypatch):
    monkeypatch.setattr(session_names, "session_for_window", lambda wid: SID)
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "busy"}, panes={"@1": EMPTY})
    suggest_names.apply_title("@1", "Ladder")
    f.pids.clear()                            # Claude exited: the queue is dropped
    assert suggest_names.flush_pending() == {"@1": "no-claude"}
    assert session_names.entries() == {}


def test_a_title_tmux_will_not_queue_is_not_recorded(monkeypatch):
    monkeypatch.setattr(session_names, "session_for_window", lambda wid: SID)
    f = _Fleet(monkeypatch, pids={"@1": 11}, status={"@1": "busy"}, panes={"@1": EMPTY})
    f.tmux.fail_set = True
    assert suggest_names.apply_title("@1", "Ladder")["state"] == "refused"
    assert session_names.entries() == {}


# ---------------------------------------------------------------------------
# Acceptance: rename + queue, a tmux restart, a restore
# ---------------------------------------------------------------------------

def test_acceptance_name_and_title_survive_a_tmux_restart(monkeypatch, rc):
    """Rename a session and queue a title, kill the tmux server (every window option gone,
    the session comes back at a NEW window id named after its folder), restore: the name is
    back on the new window and the title is typed once the session is idle."""
    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")
    where = {"@3": SID}
    monkeypatch.setattr(session_names, "session_for_window", lambda wid: where.get(wid))
    monkeypatch.setattr(session_names, "_applied_title", lambda sid: None)
    f = _Fleet(monkeypatch, pids={"@3": 11}, status={"@3": "busy"}, panes={"@3": EMPTY})
    monkeypatch.setattr(dash.subprocess, "run", f.tmux)
    monkeypatch.setattr(dash.discovery, "get_windows_by_id", lambda: {"@3": "swim-proto"})
    assert dash.app.test_client().post("/api/agents/@3/rename",
                                       json={"name": "tapparty"}).status_code == 200
    assert suggest_names.apply_title("@3", "Ladder words")["state"] == "pending"

    # The tmux server dies: options gone, the session resumes at @0 named after its folder.
    f.tmux.options.clear()
    where.clear()
    where["@0"] = SID
    f.pids.clear()
    f.pids["@0"] = 21
    f.status.clear()
    f.status["@0"] = "busy"
    f.panes["@0"] = EMPTY
    windows = {"@0": "swim-proto"}
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: windows)
    monkeypatch.setattr(sessions, "wid_for_session", lambda sid, pm: {SID: "@0"}.get(sid))
    monkeypatch.setattr(sessions, "panes", lambda *a, **k: {"@0": _pane("@0")})
    assert suggest_names.flush_pending() == {}             # without the store: nothing queued

    actions = session_names.restore_tick()
    assert actions == ["@0 name swim-proto -> tapparty", "@0 title 'Ladder words' queued again"]
    assert ["tmux", "rename-window", "-t", "=chela:@0", "tapparty"] in f.tmux.calls
    assert f.tmux.options["@0"]["@chela_manual_name"] == "1"
    assert suggest_names.flush_pending() == {"@0": "busy"}  # still waits for idle
    f.status["@0"] = "idle"
    assert suggest_names.flush_pending() == {"@0": "sent"}
    assert f.sent == [("@0", "/rename Ladder words", {"interrupt": False})]
    row = session_names.entries()[SID]
    assert row["name"] == "tapparty" and "title" not in row and "title_over" not in row


def test_restore_tick_reads_fresh_state_when_not_given(monkeypatch, tmux, rc):
    session_names.record_name(SID, "tapparty")
    monkeypatch.setattr(sessions, "panes", lambda *a, **k: {"@7": _pane("@7")})
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@7": "swim-proto"})
    monkeypatch.setattr(sessions, "wid_for_session", lambda sid, pm: "@7" if "@7" in pm else None)
    assert session_names.restore_tick() == ["@7 name swim-proto -> tapparty"]


def test_the_manual_flag_is_what_stops_a_second_rename(monkeypatch, tmux, rc):
    monkeypatch.setattr(agent_manager, "mark_manual_name", lambda target: None)
    session_names.record_name(SID, "tapparty")
    panes = {"@7": _pane("@7")}
    assert _tick(monkeypatch, panes, {"@7": "swim-proto"}, {SID: "@7"})
    panes["@7"] = _pane("@7", manual=True)
    assert _tick(monkeypatch, panes, {"@7": "something-else"}, {SID: "@7"}) == []


# ---------------------------------------------------------------------------
# The daemon's tick step (CMX-501: its `if restored:` survived the nightly sweep)
# ---------------------------------------------------------------------------

_SAID = "Restored session names/titles: "


def test_the_daemon_step_says_what_it_restored(monkeypatch, caplog):
    monkeypatch.setattr(session_names, "restore_tick",
                        lambda: ["@7 name swim-proto -> tapparty", "@9 title queued 'X'"])
    with caplog.at_level("INFO", logger=main.log.name):
        main.restore_session_names()
    said = [r for r in caplog.records if r.getMessage().startswith(_SAID)]
    assert [(r.levelname, r.getMessage()) for r in said] == [
        ("INFO", "Restored session names/titles: @7 name swim-proto -> tapparty; "
                 "@9 title queued 'X'")]


def test_the_daemon_step_is_silent_when_nothing_was_restored(monkeypatch, caplog):
    monkeypatch.setattr(session_names, "restore_tick", lambda: [])
    with caplog.at_level("DEBUG", logger=main.log.name):
        main.restore_session_names()
    assert [r for r in caplog.records if r.name == main.log.name] == []


def test_the_daemon_step_logs_a_failure_and_never_raises(monkeypatch, caplog):
    def boom():
        raise RuntimeError("tmux gone")

    monkeypatch.setattr(session_names, "restore_tick", boom)
    with caplog.at_level("INFO", logger=main.log.name):
        main.restore_session_names()                 # must not raise
    assert [(r.levelname, r.getMessage()) for r in caplog.records] == [
        ("ERROR", "Session name/title restore failed")]
    assert "tmux gone" in caplog.text


def test_the_daemon_tick_runs_the_restore_before_the_title_flush():
    """Wired into ``cmd_run``'s loop, before the suggested-title flush, so a re-queued title
    is tried on the same tick."""
    src = inspect.getsource(main.cmd_run)
    loop = src.index("    while not stop.stopping:")
    call = src.index("            restore_session_names()\n")
    assert loop < call < src.index("suggest_names.flush_pending()")


def test_transcripts_custom_title_is_the_applied_check(monkeypatch, tmp_path):
    path = tmp_path / "s.jsonl"
    path.write_text(json.dumps({"type": "custom-title", "customTitle": "X"}) + "\n")
    monkeypatch.setattr(sessions, "transcript_for_session", lambda sid: path)
    assert session_names._applied_title(SID) == transcripts.latest_custom_title(path) == "X"
