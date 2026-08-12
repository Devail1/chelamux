"""Orchestrator toolkit (Slice 1): CHELA_WID self-identity + peek/read.

Exercised against synthetic tmux/transcript state via monkeypatch, so no live
tmux, dashboard server, or claude process is needed.
"""
import json
import os
import subprocess
import time
import types

import pytest

from chela import agent_manager, config, discovery, orchestrator, sessions, transcripts
from chela.sessions import Pane


# --- zero-config session scope (config.current_session precedence) -------------

def _fake_run(session_name):
    def run(cmd, **kw):
        # emulate `tmux display-message -p -t <pane> '#{session_name}'`
        return types.SimpleNamespace(returncode=0, stdout=session_name + "\n", stderr="")
    return run


def test_current_session_env_override_wins(monkeypatch):
    # explicit override beats a pane that would derive something else
    monkeypatch.setenv("CHELA_TMUX_SESSION", "explicit")
    monkeypatch.setenv("TMUX_PANE", "%9")
    monkeypatch.setattr(subprocess, "run", _fake_run("myteam"))
    assert config.current_session() == "explicit"


def test_current_session_derives_from_pane(monkeypatch):
    # no override → derive the caller's own pane session (e.g. myteam)
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%9")
    monkeypatch.setattr(subprocess, "run", _fake_run("myteam"))
    assert config.current_session() == "myteam"


def test_current_session_defaults_without_env_or_pane(monkeypatch):
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert config.current_session() == "chela"


def test_current_session_falls_back_when_tmux_fails(monkeypatch):
    monkeypatch.delenv("CHELA_TMUX_SESSION", raising=False)
    monkeypatch.setenv("TMUX_PANE", "%9")

    def failing(cmd, **kw):
        return types.SimpleNamespace(returncode=1, stdout="", stderr="no server")
    monkeypatch.setattr(subprocess, "run", failing)
    assert config.current_session() == "chela"


# --- CHELA_WID self-identity ---------------------------------------------------

def test_wid_env_prefix():
    assert agent_manager.wid_env_prefix("@28") == "export CHELA_WID=@28 && "
    # never emit a broken export for a falsy id
    assert agent_manager.wid_env_prefix("") == ""


def test_wid_env_prefix_quotes_odd_ids():
    # defensive: an id with shell metacharacters is shlex-quoted, so the ';rm'
    # can't break out of the export into a second command.
    out = agent_manager.wid_env_prefix("@2;rm -rf x")
    assert out == "export CHELA_WID='@2;rm -rf x' && "


def test_self_wid_prefers_env(monkeypatch):
    monkeypatch.setenv("CHELA_WID", "@42")
    assert orchestrator.self_wid() == "@42"


def test_self_wid_none_without_env_or_pane(monkeypatch):
    monkeypatch.delenv("CHELA_WID", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    assert orchestrator.self_wid() is None


# --- CMX-255: self_peer — the windowless fallback identity ----------------------

def test_self_peer_none_when_no_claude_ancestor_is_found(monkeypatch):
    monkeypatch.setattr(sessions, "own_claude_pid", lambda pid=None: None)
    assert orchestrator.self_peer() is None


def test_self_peer_reports_the_pid_and_resolved_session(monkeypatch):
    monkeypatch.setattr(sessions, "own_claude_pid", lambda pid=None: 4242)
    monkeypatch.setattr(sessions, "session_id_for_pid", lambda pid: "sid-abc" if pid == 4242 else None)
    assert orchestrator.self_peer() == {"pid": 4242, "session": "sid-abc"}


def test_self_peer_reports_no_session_when_it_cannot_be_resolved(monkeypatch):
    monkeypatch.setattr(sessions, "own_claude_pid", lambda pid=None: 4242)
    monkeypatch.setattr(sessions, "session_id_for_pid", lambda pid: None)
    assert orchestrator.self_peer() == {"pid": 4242, "session": None}


# --- liveness (shared with the dashboard) --------------------------------------

@pytest.mark.parametrize("running,status,expected", [
    (False, "waiting", ("waiting", "yellow")),
    (True, "busy", ("alive", "green")),
    (True, "idle", ("alive", "green")),
    (False, "idle", ("alive", "green")),
    (False, None, ("live", "grey")),
])
def test_liveness(running, status, expected):
    assert agent_manager.liveness(running, status) == expected


# --- transcript distillation ---------------------------------------------------

def _write_transcript(path):
    """A synthetic Claude Code JSONL covering the shapes iter_turns must handle."""
    records = [
        {"type": "user", "timestamp": "2026-07-11T10:00:00Z",
         "message": {"role": "user", "content": "review the diff and push"}},
        {"type": "assistant", "timestamp": "2026-07-11T10:00:05Z",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "thinking": "internal reasoning, must be hidden"},
             {"type": "text", "text": "On it — inspecting the changes."},
             {"type": "tool_use", "name": "Bash", "input": {}},
         ]}},
        # tool_result carrier (user record with list content) — skipped
        {"type": "user", "timestamp": "2026-07-11T10:00:06Z",
         "message": {"role": "user", "content": [{"type": "tool_result", "content": "ok"}]}},
        # meta record — skipped
        {"type": "user", "isMeta": True, "timestamp": "2026-07-11T10:00:07Z",
         "message": {"role": "user", "content": "<system meta>"}},
        # sidechain (sub-agent) — skipped by default
        {"type": "assistant", "isSidechain": True, "timestamp": "2026-07-11T10:00:08Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "subagent chatter"}]}},
        {"type": "assistant", "timestamp": "2026-07-11T10:00:10Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Diff looks correct; pushing now."}]}},
        # non-conversation record types — ignored
        {"type": "system", "subtype": "away_summary", "content": "recap text"},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def test_iter_turns_distills(tmp_path):
    f = tmp_path / "t.jsonl"
    _write_transcript(f)
    turns = list(transcripts.iter_turns(f))
    roles = [(t["role"], t["text"][:20]) for t in turns]
    # user prompt, assistant(text+Bash tool), assistant(text) — meta/sidechain/tool_result gone
    assert roles == [
        ("user", "review the diff and "),
        ("assistant", "On it — inspecting t"),
        ("assistant", "Diff looks correct; "),
    ]
    # the thinking block is never surfaced; the tool name is captured
    assert "internal reasoning" not in turns[1]["text"]
    assert turns[1]["tools"] == ["Bash"]


@pytest.fixture
def wired(tmp_path, monkeypatch):
    """Point orchestrator's window+transcript resolution at a synthetic window."""
    f = tmp_path / "session.jsonl"
    _write_transcript(f)
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@7": "worker"})
    monkeypatch.setattr(discovery, "get_window_cwd_by_id", lambda wid: "/home/x/proj")
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: f)
    return f


def test_read_tail(wired):
    r = orchestrator.read("@7", tail=2)
    assert r["ok"] and r["mode"] == "tail:2" and r["count"] == 2
    # last two turns, most recent last
    assert "Diff looks correct" in r["turns"][-1]


def test_read_query(wired):
    # substring match: "push" hits both the user prompt and "pushing now"
    assert orchestrator.read("@7", query="push")["count"] == 2
    # "pushing" is only in the assistant turn
    r = orchestrator.read("@7", query="pushing")
    assert r["ok"] and r["count"] == 1
    assert "pushing now" in r["turns"][0]
    # multi-term AND that can't be satisfied → no matches
    assert orchestrator.read("@7", query="pushing nonexistentterm")["count"] == 0


def test_read_all(wired):
    r = orchestrator.read("@7", all_turns=True)
    assert r["ok"] and r["mode"] == "all" and r["count"] == 3


def test_read_unknown_window(monkeypatch):
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {})
    r = orchestrator.read("@999", tail=5)
    assert r["ok"] is False and "not a live window" in r["error"]


def test_read_two_windows_sharing_a_cwd_do_not_alias(tmp_path, monkeypatch):
    """CMX-190: @7 and @8 both live in /home/x/proj. Resolution is PER-WINDOW
    (via `sessions.transcript_for_window`), so each gets its own transcript
    instead of both racing for whichever file has the newest record."""
    f7 = tmp_path / "s7.jsonl"
    f8 = tmp_path / "s8.jsonl"
    _write_transcript(f7)
    f8.write_text(json.dumps({
        "type": "assistant", "timestamp": "2026-07-11T11:00:00Z",
        "message": {"role": "assistant", "content": [{"type": "text", "text": "other agent's work"}]},
    }) + "\n")

    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@7": "one", "@8": "two"})
    monkeypatch.setattr(discovery, "get_window_cwd_by_id", lambda wid: "/home/x/proj")

    by_window = {"@7": f7, "@8": f8}
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: by_window[wid])

    r7 = orchestrator.read("@7", all_turns=True)
    r8 = orchestrator.read("@8", all_turns=True)
    assert r7["ok"] and r8["ok"]
    assert r7["turns"] != r8["turns"]
    assert not any("other agent's work" in t for t in r7["turns"])
    assert any("other agent's work" in t for t in r8["turns"])


def test_read_refused_cwd_guess_surfaces_explanation(monkeypatch):
    """When `sessions` refuses to guess (e.g. a shared cwd with no other
    evidence), `read` must surface WHY, not just a bare 'no transcript'."""
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@7": "worker"})
    monkeypatch.setattr(discovery, "get_window_cwd_by_id", lambda wid: "/home/x/proj")
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: None)
    monkeypatch.setattr(sessions, "explain", lambda wid, base=None: "cwd fallback REFUSED: @7, @8 share a cwd")

    r = orchestrator.read("@7", tail=5)
    assert r["ok"] is False
    assert "REFUSED" in r["error"] and "@8" in r["error"]


# --- peek assembly -------------------------------------------------------------

def test_peek_assembles_from_shared_layer(tmp_path, monkeypatch):
    f = tmp_path / "session.jsonl"
    _write_transcript(f)
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@7": "worker"})
    monkeypatch.setattr(discovery, "get_window_cwd_by_id", lambda wid: "/home/x/proj")
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: f)
    monkeypatch.setattr(agent_manager, "session_status_map",
                        lambda force=False: {"by_pid": {123: "busy"}, "cwd_by_pid": {}, "by_cwd": {}})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: 123)
    monkeypatch.setattr(agent_manager, "window_type", lambda wid, running=None: "claude")

    p = orchestrator.peek("@7")
    assert p["wid"] == "@7" and p["name"] == "worker"
    assert p["session_status"] == "busy"
    assert p["liveness"] == "alive" and p["health"] == "green"
    assert p["claude_running"] is True
    assert p["cwd"] == "/home/x/proj"
    # recap comes off the transcript's away_summary
    assert p["recap"] == "recap text"
    # format must not crash and should include identity + status
    out = orchestrator.format_peek(p)
    assert "@7" in out and "worker" in out and "recap" in out


def test_peek_none_for_dead_window(monkeypatch):
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {})
    assert orchestrator.peek("@999") is None


def test_peek_two_windows_sharing_a_cwd_do_not_alias(tmp_path, monkeypatch):
    """CMX-190: peek must resolve by window id, not by cwd — else @7 and @8
    (same cwd) would both surface whichever transcript has the newest record."""
    f7 = tmp_path / "s7.jsonl"
    f8 = tmp_path / "s8.jsonl"
    _write_transcript(f7)  # recap: "recap text"
    f8.write_text(json.dumps(
        {"type": "system", "subtype": "away_summary", "content": "other agent's recap"}
    ) + "\n")

    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@7": "one", "@8": "two"})
    monkeypatch.setattr(discovery, "get_window_cwd_by_id", lambda wid: "/home/x/proj")
    by_window = {"@7": f7, "@8": f8}
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: by_window[wid])
    monkeypatch.setattr(agent_manager, "session_status_map",
                        lambda force=False: {"by_pid": {}, "cwd_by_pid": {}, "by_cwd": {}})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: None)
    monkeypatch.setattr(agent_manager, "window_type", lambda wid, running=None: "claude")

    p7 = orchestrator.peek("@7")
    p8 = orchestrator.peek("@8")
    assert p7["recap"] == "recap text"
    assert p8["recap"] == "other agent's recap"


# --- CMX-190 mechanism guards -------------------------------------------------
#
# The two tests above stub `sessions.transcript_for_window`, so they prove the
# WIRING (read/peek pass the wid to a per-window resolver) but never exercise the
# aliasing itself. These two drive the real resolvers, with the fixture built so
# the SIBLING owns the newest mtime — i.e. the pre-fix code does not merely fail,
# it confidently returns the WRONG transcript.

def _sessions_sharing_a_cwd(tmp_path):
    """Two sessions under one project dir; @7's own transcript is the OLDER one."""
    proj = tmp_path / "projects" / "-home-x-proj"
    proj.mkdir(parents=True)
    s7, s8 = proj / "sess7.jsonl", proj / "sess8.jsonl"
    now = time.time()
    for f, txt, ts in ((s7, "SEVEN's work", now - 500), (s8, "EIGHT's work", now)):
        f.write_text(json.dumps({
            "type": "assistant", "timestamp": "2026-07-11T10:00:00Z",
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": txt}]},
        }) + "\n")
        os.utime(f, (ts, ts))
    return "/home/x/proj", tmp_path / "projects", s7, s8


def test_cwd_guess_returns_the_sibling_not_the_owner(tmp_path):
    """The mechanism CMX-190 removes: keyed on cwd, resolution is just
    newest-mtime-wins, so @7 is handed @8's transcript with full confidence.
    If this ever stops holding, the guard below is testing nothing."""
    cwd, base, s7, s8 = _sessions_sharing_a_cwd(tmp_path)
    assert transcripts.transcript_for_cwd(cwd, base=base) == s8 != s7


def test_shared_cwd_is_refused_not_guessed(tmp_path, monkeypatch):
    """@7 and @8 are both live in one cwd with no session evidence. The resolver
    must REFUSE — handing back a sibling's transcript is worse than silence,
    because a refusal reads as 'unknown' and a wrong transcript reads as fact."""
    cwd, base, s7, s8 = _sessions_sharing_a_cwd(tmp_path)
    pane_map = {"@7": Pane(wid="@7", launched_in=cwd, claude_pid=101),
                "@8": Pane(wid="@8", launched_in=cwd, claude_pid=102)}
    monkeypatch.setattr(sessions, "panes", lambda force=False: pane_map)

    res = sessions.resolve_window("@7", base=base)
    assert res.path is None, "must refuse, not hand back the sibling's transcript"
    assert "REFUSED" in (res.detail or "")
    # and the refusal must NAME the sibling, so an operator can see the ambiguity
    assert "@8" in res.detail
