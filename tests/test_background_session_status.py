"""CMX-28 — a window whose session MOVED to a Claude Code background session.

Measured live (2026-10-08): the pane's own ``claude`` (pid 1694219) has NO entry in
`claude agents --json`; the feed lists only the background process that descends from it
(pid 2528563, ``kind: background``, a new ``sessionId`` and ``name``). Looked up by the
pane's pid alone, the Wall read "unknown" and the sidebar "Done/Finished" — off the
original session's transcript, which ended with the move — while the agent was visibly
busy.

These lock in that the window follows the descendant entry (status, name, sessionId,
transcript), that nothing is invented when there is no such entry, and that the newest
background descendant wins when there are several. Fixtures and stubs only — no live
process, no live ``claude``.
"""
from __future__ import annotations

import json
import time
import types
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from chela import agent_manager, collab, inbox, notify, orchestrator, sessions, transcripts
from chela.dashboard import app as dash

PANE_CLAUDE = 1694219          # the foreground claude in the pane — no feed entry
BG = 2528563                   # the background session it moved to
OLD_SID = "7f3a91c2-4b8e-4d15-9c62-1e0d5a8b3f47"
NEW_SID = "da024f6d-e6c6-4220-bdd2-0f0f14c1dc7b"
BG_CWD = "/w/tradeplan-bg"     # only the descendant's entry carries it


def _smap(*, by_pid, ancestors=None, kinds=None, started=None, names=None, sids=None,
          cwds=None):
    return {
        "by_pid": by_pid, "cwd_by_pid": cwds or {}, "by_cwd": {},
        "ancestors_by_pid": ancestors or {}, "kind_by_pid": kinds or {},
        "started_by_pid": started or {}, "name_by_pid": names or {},
        "session_by_pid": sids or {},
    }


def _moved_smap(status="busy"):
    """The live shape: only the background descendant is in the feed."""
    return _smap(
        by_pid={BG: status},
        ancestors={BG: [2528536, 2528512, PANE_CLAUDE, 1693669]},
        kinds={BG: "background"},
        names={BG: "prove-byte-identical-prompt-move"},
        sids={BG: NEW_SID},
        cwds={BG: BG_CWD},
    )


# --- session_entry: the resolution itself ------------------------------------

def test_a_busy_background_descendant_gives_the_window_its_status():
    e = agent_manager.session_entry(PANE_CLAUDE, _moved_smap())
    assert e is not None
    assert e["status"] == "busy"
    assert e["pid"] == BG
    assert e["moved"] is True
    assert e["name"] == "prove-byte-identical-prompt-move"
    assert e["session_id"] == NEW_SID
    assert e["kind"] == "background"
    assert e["cwd"] == BG_CWD


def test_no_descendant_entry_still_reads_unknown_never_invented():
    """A feed entry that does NOT descend from the pane's claude (another window's agent)
    must not be borrowed — no entry for this pane means no status."""
    smap = _smap(by_pid={4242: "busy"}, ancestors={4242: [4000, 3000]},
                 kinds={4242: "background"})
    assert agent_manager.session_entry(PANE_CLAUDE, smap) is None


def test_two_background_descendants_the_newest_wins():
    smap = _smap(
        by_pid={5001: "idle", 5002: "busy"},
        ancestors={5001: [PANE_CLAUDE], 5002: [PANE_CLAUDE]},
        kinds={5001: "background", 5002: "background"},
        # 5001 started later; 5002 has the higher pid, so a pid-only pick would be wrong.
        started={5001: 2000.0, 5002: 1000.0},
        names={5001: "newer", 5002: "older"},
    )
    e = agent_manager.session_entry(PANE_CLAUDE, smap)
    assert e["pid"] == 5001 and e["name"] == "newer" and e["status"] == "idle"


def test_a_background_descendant_outranks_a_newer_non_background_one():
    smap = _smap(
        by_pid={5001: "idle", 5002: "busy"},
        ancestors={5001: [PANE_CLAUDE], 5002: [PANE_CLAUDE]},
        kinds={5001: "interactive", 5002: "background"},
        started={5001: 9000.0, 5002: 1000.0},
    )
    assert agent_manager.session_entry(PANE_CLAUDE, smap)["pid"] == 5002


def test_the_panes_own_entry_still_wins_when_it_has_one():
    smap = _smap(by_pid={PANE_CLAUDE: "idle", BG: "busy"},
                 ancestors={BG: [PANE_CLAUDE]}, kinds={BG: "background"})
    e = agent_manager.session_entry(PANE_CLAUDE, smap)
    assert e["pid"] == PANE_CLAUDE and e["status"] == "idle" and e["moved"] is False


def test_no_pane_claude_means_no_entry():
    assert agent_manager.session_entry(None, _moved_smap()) is None


def test_a_descendant_with_no_feed_entry_is_not_followed():
    """The parent chain alone is not an entry: a pid listed in ancestors_by_pid but absent
    from by_pid (it left the feed) must not be followed."""
    smap = _smap(by_pid={9001: "busy"}, ancestors={BG: [PANE_CLAUDE], 9001: [4000]},
                 kinds={BG: "background"})
    assert agent_manager.session_entry(PANE_CLAUDE, smap) is None


def test_ancestors_is_bounded_by_its_limit(monkeypatch):
    monkeypatch.setattr(sessions, "_ppid", lambda pid: pid - 1)
    assert sessions.ancestors(100, limit=3) == [99, 98, 97]


def test_the_refresh_records_kind_name_and_the_parent_chain(monkeypatch):
    payload = json.dumps([{"pid": BG, "status": "busy", "name": "bg-name",
                           "kind": "background", "sessionId": NEW_SID, "cwd": "/w"}])
    monkeypatch.setattr(agent_manager.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(returncode=0, stdout=payload,
                                                                stderr=""))
    monkeypatch.setattr(sessions, "proc_started", lambda pid: None)
    monkeypatch.setattr(sessions, "ancestors", lambda pid: [2528536, PANE_CLAUDE])
    saved = dict(agent_manager._status_cache)
    try:
        agent_manager.probe_native_status_feed()
        e = agent_manager.session_entry(PANE_CLAUDE, agent_manager.cached_status_map())
        assert e is not None and e["pid"] == BG and e["status"] == "busy"
        assert e["name"] == "bg-name" and e["kind"] == "background"
    finally:
        agent_manager._status_cache.clear()
        agent_manager._status_cache.update(saved)


def test_ancestors_walks_the_parent_chain_and_stops_at_init(monkeypatch):
    parents = {BG: 2528536, 2528536: PANE_CLAUDE, PANE_CLAUDE: 1}
    monkeypatch.setattr(sessions, "_ppid", lambda pid: parents.get(pid))
    assert sessions.ancestors(BG) == [2528536, PANE_CLAUDE]


def test_status_by_wid_follows_the_move(monkeypatch):
    monkeypatch.setattr(agent_manager, "session_status_map", lambda force=False: _moved_smap())
    monkeypatch.setattr(agent_manager, "get_windows_by_id", lambda: {"@80": "tradeplan"})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: PANE_CLAUDE)
    assert agent_manager.status_by_wid() == {"@80": "busy"}


def test_status_by_wid_omits_a_window_with_no_entry(monkeypatch):
    monkeypatch.setattr(agent_manager, "session_status_map",
                        lambda force=False: _smap(by_pid={4242: "busy"}, ancestors={4242: [4000]}))
    monkeypatch.setattr(agent_manager, "get_windows_by_id", lambda: {"@80": "tradeplan"})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: PANE_CLAUDE)
    assert agent_manager.status_by_wid() == {}


# --- the other consumers of the status feed ---------------------------------

def _wire_window(monkeypatch, module, smap):
    monkeypatch.setattr(module.discovery, "get_all_windows", lambda: {"tradeplan": "@80"})
    monkeypatch.setattr(agent_manager, "session_status_map", lambda force=False: smap)
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: PANE_CLAUDE)


def test_collab_agent_rooms_status_follows_the_move(monkeypatch):
    _wire_window(monkeypatch, collab, _moved_smap())
    assert collab._agent_rooms() == {"@80": {"name": "tradeplan", "status": "busy"}}


def test_collab_agent_rooms_has_no_status_without_a_descendant(monkeypatch):
    _wire_window(monkeypatch, collab, _smap(by_pid={4242: "busy"}, ancestors={4242: [4000]}))
    assert collab._agent_rooms() == {"@80": {"name": "tradeplan", "status": None}}


def test_notify_sees_a_moved_session_that_is_waiting(monkeypatch):
    _wire_window(monkeypatch, notify, _moved_smap("waiting"))
    assert notify.waiting_windows() == {"tradeplan"}


def test_notify_ignores_a_moved_session_that_is_busy(monkeypatch):
    _wire_window(monkeypatch, notify, _moved_smap("busy"))
    assert notify.waiting_windows() == set()


# --- the Wall + sidebar (/api/agents) ----------------------------------------

@contextmanager
def _fleet(smap, *, is_done=True):
    with (
        patch("chela.discovery.get_all_windows", return_value={"tradeplan": "@80"}),
        patch("chela.dispatcher.list_runs", return_value=[]),
        patch("chela.agent_manager.session_status_map", return_value=smap),
        patch("chela.agent_manager.claude_pid", side_effect=lambda wid: PANE_CLAUDE),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": None, "recap_ts": None, "pr": None, "ai_title": None}),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.inbox.is_done", return_value=is_done),
    ):
        yield


def _row(smap, **kw):
    with _fleet(smap, **kw):
        rows = dash.app.test_client().get("/api/agents").get_json()
    return {a["window_id"]: a for a in rows}["@80"]


def test_the_wall_reads_busy_not_unknown_for_a_moved_session():
    row = _row(_moved_smap())
    assert row["session_status"] == "busy"
    assert row["thinking"] is True
    assert row["session_name"] == "prove-byte-identical-prompt-move"
    assert row["session_moved"] is True
    # The pane's own pid has no cwd in the feed — only the followed entry does.
    assert row["cwd"] == BG_CWD


def test_the_sidebar_does_not_mark_a_busy_moved_session_finished():
    """The old session's transcript ended with the move (assistant last), so ``is_done``
    has its evidence — but the live session is busy, and busy is never `done`."""
    assert _row(_moved_smap())["done"] is False


def test_the_wall_still_reads_unknown_with_no_descendant_entry():
    row = _row(_smap(by_pid={4242: "busy"}, ancestors={4242: [4000]}))
    assert row["claude_running"] is True
    assert row["session_status"] is None
    assert row["session_name"] is None
    assert row["session_moved"] is False
    assert row["cwd"] is None


# --- peek: the name peers need ----------------------------------------------

def test_peek_exposes_the_background_sessions_name(monkeypatch):
    monkeypatch.setattr(orchestrator.discovery, "get_windows_by_id", lambda: {"@80": "tradeplan"})
    monkeypatch.setattr(orchestrator.discovery, "get_window_cwd_by_id", lambda wid: "/w")
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: None)
    monkeypatch.setattr(agent_manager, "session_status_map", lambda force=False: _moved_smap())
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: PANE_CLAUDE)
    monkeypatch.setattr(agent_manager, "window_type", lambda wid, running=None: "claude")

    p = orchestrator.peek("@80")
    assert p["session_status"] == "busy"
    assert p["session_name"] == "prove-byte-identical-prompt-move"
    assert p["session_id"] == NEW_SID
    assert p["session_kind"] == "background"
    assert p["session_moved"] is True
    session_line = [ln for ln in orchestrator.format_peek(p).splitlines()
                    if ln.strip().startswith("session:")]
    assert session_line == ["  session: prove-byte-identical-prompt-move — moved to a "
                            "background session; address it by this name"]


def test_peek_of_an_unmoved_session_names_it_without_the_moved_note(monkeypatch):
    monkeypatch.setattr(orchestrator.discovery, "get_windows_by_id", lambda: {"@80": "tradeplan"})
    monkeypatch.setattr(orchestrator.discovery, "get_window_cwd_by_id", lambda wid: "/w")
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: None)
    monkeypatch.setattr(agent_manager, "session_status_map", lambda force=False: _smap(
        by_pid={PANE_CLAUDE: "idle"}, names={PANE_CLAUDE: "tradeplan-main"},
        sids={PANE_CLAUDE: OLD_SID}))
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: PANE_CLAUDE)
    monkeypatch.setattr(agent_manager, "window_type", lambda wid, running=None: "claude")

    p = orchestrator.peek("@80")
    assert p["session_id"] == OLD_SID and p["session_moved"] is False
    session_line = [ln for ln in orchestrator.format_peek(p).splitlines()
                    if ln.strip().startswith("session:")]
    assert session_line == ["  session: tradeplan-main"]


# --- the transcript follows the new session ---------------------------------

def _write(projects, cwd, sid, records):
    proj = projects / transcripts.encode_cwd(cwd)
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{sid}.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


@pytest.fixture
def moved_window(tmp_path, monkeypatch):
    """@80: pinned to the OLD session (which ended on an assistant turn — "finished"),
    while the feed says its claude moved to NEW_SID, which is mid-turn (user last)."""
    cwd = "/w/tradeplan"
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", tmp_path)
    old = _write(tmp_path, cwd, OLD_SID, [
        {"type": "user", "timestamp": "2026-10-08T10:00:00Z", "message": {"content": "go"}},
        {"type": "assistant", "timestamp": "2026-10-08T10:05:00Z",
         "message": {"content": [{"type": "text", "text": "moving to background"}]}},
    ])
    new = _write(tmp_path, cwd, NEW_SID, [
        {"type": "user", "timestamp": "2026-10-08T13:50:00Z",
         "message": {"content": "run the full pytest"}},
    ])
    pane = sessions.Pane(wid="@80", path=cwd, command="claude", claude_pid=PANE_CLAUDE,
                         launched_in=cwd, started=time.time() - 3 * 3600)
    monkeypatch.setattr(sessions, "panes", lambda force=False: {"@80": pane})
    monkeypatch.setattr(sessions.sessionids, "session_id_for",
                        lambda wid: OLD_SID if wid == "@80" else None)
    monkeypatch.setattr(sessions, "_session_from_log", lambda wid, since: OLD_SID)
    monkeypatch.setattr(sessions, "_promote", lambda wid, sid: None)
    monkeypatch.setattr(agent_manager, "cached_status_map", lambda: _moved_smap())
    return types.SimpleNamespace(old=old, new=new)


def test_the_transcript_follows_the_background_session(moved_window):
    res = sessions.resolve_window("@80")
    assert res.session_id == NEW_SID
    assert res.path == moved_window.new
    assert res.source == "background"


def test_the_sidebars_done_reads_the_live_session_not_the_ended_one(moved_window):
    assert inbox.is_done("@80") is False


def test_without_the_move_the_window_keeps_its_own_session(moved_window, monkeypatch):
    """Control: with no descendant entry, resolution is exactly what it was (here, the
    event log's session) — the background tier must be inert, not a new default."""
    monkeypatch.setattr(agent_manager, "cached_status_map",
                        lambda: _smap(by_pid={PANE_CLAUDE: "idle"},
                                      sids={PANE_CLAUDE: NEW_SID}))
    res = sessions.resolve_window("@80")
    assert res.session_id == OLD_SID and res.source == "event_log"


def test_the_cli_status_lists_the_moved_session_name(monkeypatch, capsys):
    from chela import main
    monkeypatch.setattr(main.discovery, "get_all_windows", lambda: {"tradeplan": "@80"})
    monkeypatch.setattr(main.discovery, "get_window_cwd", lambda name: "/w")
    monkeypatch.setattr(agent_manager, "session_status_map", lambda force=False: _moved_smap())
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: PANE_CLAUDE)
    main.cmd_status(types.SimpleNamespace(sessions=True))
    out = capsys.readouterr().out
    assert "session: prove-byte-identical-prompt-move (moved to background)" in out



def test_the_cli_status_names_an_unmoved_session_without_the_moved_note(monkeypatch, capsys):
    from chela import main
    monkeypatch.setattr(main.discovery, "get_all_windows", lambda: {"tradeplan": "@80"})
    monkeypatch.setattr(main.discovery, "get_window_cwd", lambda name: "/w")
    monkeypatch.setattr(agent_manager, "session_status_map", lambda force=False: _smap(
        by_pid={PANE_CLAUDE: "idle"}, names={PANE_CLAUDE: "tradeplan-main"}))
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: PANE_CLAUDE)
    main.cmd_status(types.SimpleNamespace(sessions=True))
    out = capsys.readouterr().out
    assert out.rstrip().endswith("session: tradeplan-main")
    assert "moved" not in out


def test_the_cli_status_without_sessions_never_queries_the_feed(monkeypatch, capsys):
    from chela import main
    monkeypatch.setattr(main.discovery, "get_all_windows", lambda: {"tradeplan": "@80"})
    monkeypatch.setattr(main.discovery, "get_window_cwd", lambda name: "/w")

    def _boom(force=False):
        raise AssertionError("plain `chela status` must not call `claude agents --json`")
    monkeypatch.setattr(agent_manager, "session_status_map", _boom)
    main.cmd_status(types.SimpleNamespace(sessions=False))
    assert "session:" not in capsys.readouterr().out


def test_a_moved_session_with_no_transcript_yet_falls_back(moved_window):
    """The background tier only answers with a real transcript: no NEW_SID.jsonl means the
    ordinary tiers decide."""
    moved_window.new.unlink()
    res = sessions.resolve_window("@80")
    assert res.session_id == OLD_SID and res.source == "event_log"
