"""Orchestrator toolkit (Slice 1): CHELA_WID self-identity + peek/read.

Exercised against synthetic tmux/transcript state via monkeypatch, so no live
tmux, dashboard server, or claude process is needed.
"""
import json

import pytest

from chela import agent_manager, discovery, orchestrator, transcripts


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
    monkeypatch.setattr(transcripts, "transcript_for_cwd", lambda cwd, base=None: f)
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


# --- peek assembly -------------------------------------------------------------

def test_peek_assembles_from_shared_layer(tmp_path, monkeypatch):
    f = tmp_path / "session.jsonl"
    _write_transcript(f)
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@7": "worker"})
    monkeypatch.setattr(discovery, "get_window_cwd_by_id", lambda wid: "/home/x/proj")
    monkeypatch.setattr(transcripts, "transcript_for_cwd", lambda cwd, base=None: f)
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
