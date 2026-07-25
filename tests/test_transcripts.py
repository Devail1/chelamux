"""Tests for transcript path resolution (`transcripts.transcript_for_cwd`)."""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chela import discovery, event_log, sessions, transcripts

CWD = "/home/x/proj"


def _write(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


def _proj_dir(base):
    d = base / transcripts.encode_cwd(CWD)
    d.mkdir(parents=True)
    return d


def test_prefers_newest_record_not_newest_mtime(tmp_path):
    """A pre-clear file with a bumped mtime but an older last record must lose
    to the fresh session whose last record is later — the /clear rebind bug."""
    proj = _proj_dir(tmp_path)

    old = proj / "old-session.jsonl"
    _write(old, [
        {"type": "user", "timestamp": "2026-07-11T10:00:00Z",
         "message": {"role": "user", "content": "work"}},
        # the /clear marker: appended last, so it bumps the file mtime, but its
        # content timestamp is still older than the fresh session's first record
        {"type": "system", "subtype": "clear", "timestamp": "2026-07-11T10:01:00Z"},
    ])

    new = proj / "new-session.jsonl"
    _write(new, [
        {"type": "user", "timestamp": "2026-07-11T10:05:00Z",
         "message": {"role": "user", "content": "fresh"}},
    ])

    # Bump the PRE-clear file's mtime to be the newest on disk (what /clear does).
    late = os.stat(new).st_mtime + 100
    os.utime(old, (late, late))
    assert os.stat(old).st_mtime > os.stat(new).st_mtime

    assert transcripts.transcript_for_cwd(CWD, base=tmp_path) == new


def test_falls_back_to_mtime_without_timestamps(tmp_path):
    """When no candidate carries a record timestamp, mtime still breaks the tie."""
    proj = _proj_dir(tmp_path)
    a = proj / "a.jsonl"
    b = proj / "b.jsonl"
    _write(a, [{"type": "system", "subtype": "x"}])
    _write(b, [{"type": "system", "subtype": "y"}])
    newer = os.stat(a).st_mtime + 50
    os.utime(b, (newer, newer))
    assert transcripts.transcript_for_cwd(CWD, base=tmp_path) == b


def test_timestamped_session_outranks_empty_new_one(tmp_path):
    """A brand-new session with no records yet does not steal the binding from
    the session that actually has content (mtime alone would pick the new one)."""
    proj = _proj_dir(tmp_path)
    live = proj / "live.jsonl"
    _write(live, [
        {"type": "user", "timestamp": "2026-07-11T10:00:00Z",
         "message": {"role": "user", "content": "hi"}},
    ])
    fresh = proj / "fresh.jsonl"
    fresh.write_text("")  # created but nothing written yet
    newer = os.stat(live).st_mtime + 100
    os.utime(fresh, (newer, newer))
    assert transcripts.transcript_for_cwd(CWD, base=tmp_path) == live


def test_none_when_no_transcripts(tmp_path):
    _proj_dir(tmp_path)
    assert transcripts.transcript_for_cwd(CWD, base=tmp_path) is None
    assert transcripts.transcript_for_cwd("", base=tmp_path) is None


# --- last_assistant_activity: the decisions inbox's completion evidence ---------
#
# The inbox reports a finished delegation it never sampled busy by asking "did this
# agent write an assistant turn AFTER I registered the watch?" (chela.inbox). The
# ASSISTANT filter is the load-bearing part: the orchestrator's dispatched prompt lands
# as a USER record, so counting any activity would read "your prompt arrived" as "the
# agent replied" — a phantom completion on every single dispatch.

def test_the_dispatched_prompt_alone_is_never_read_as_a_completion(tmp_path):
    # The evidence must be an ASSISTANT turn. The orchestrator's dispatched prompt lands
    # in the transcript as a USER record — counting any activity would read "your prompt
    # arrived" as "the agent replied", firing a phantom completion on every dispatch.
    now = datetime.now(timezone.utc)
    cwd = "/proj/thing"
    proj = tmp_path / transcripts.encode_cwd(cwd)
    proj.mkdir(parents=True)
    session = proj / "s.jsonl"

    def _rec(kind, when):
        return json.dumps({"type": kind, "timestamp": when.isoformat().replace("+00:00", "Z"),
                           "message": {"content": "x"}})

    # The agent replied an hour ago; the only NEW record is our just-dispatched prompt.
    session.write_text("\n".join([
        _rec("assistant", now - timedelta(hours=1)),
        _rec("user", now),                       # <- the dispatch we just sent
    ]) + "\n")

    last = transcripts.last_assistant_activity(cwd, base=tmp_path)
    assert last is not None
    # It reports the ASSISTANT turn (an hour old), NOT the fresh user prompt — so an
    # inbox watch registered just now sees no work-since-watch, and stays quiet.
    assert last < (now - timedelta(minutes=30)).timestamp()


def test_last_assistant_activity_sees_a_reply_written_after_the_watch(tmp_path):
    now = datetime.now(timezone.utc)
    cwd = "/proj/thing"
    proj = tmp_path / transcripts.encode_cwd(cwd)
    proj.mkdir(parents=True)
    (proj / "s.jsonl").write_text(json.dumps({
        "type": "assistant", "timestamp": now.isoformat().replace("+00:00", "Z"),
        "message": {"content": "done"},
    }) + "\n")

    last = transcripts.last_assistant_activity(cwd, base=tmp_path)
    assert last is not None and last > (now - timedelta(minutes=1)).timestamp()


# --- latest_ai_title: the session's auto-generated title (CMX-146) --------------
#
# Distinct from the recap (`away_summary`, an occasional "what happened while you
# were away" blurb): `ai-title` records are Claude Code's own short name for the
# conversation, revised as it evolves. The LATEST one is current.

def test_latest_ai_title_picks_the_newest_of_several(tmp_path):
    # The trailing record is deliberately NOT an ai-title (a later user turn, as a
    # real session would append) — this pins the `type == "ai-title"` filter itself,
    # not just recency: a predicate loosened to "any record" would match this last
    # line first, see no `aiTitle` field, and wrongly return None.
    f = tmp_path / "t.jsonl"
    _write(f, [
        {"type": "ai-title", "aiTitle": "Fix login bug", "sessionId": "s1"},
        {"type": "ai-title", "aiTitle": "Fix login bug and add tests", "sessionId": "s1"},
        {"type": "user", "message": {"role": "user", "content": "keep going"}},
    ])
    assert transcripts.latest_ai_title(f) == "Fix login bug and add tests"


def test_latest_ai_title_none_when_absent_or_blank(tmp_path):
    f = tmp_path / "t.jsonl"
    _write(f, [{"type": "user", "message": {"role": "user", "content": "hi"}}])
    assert transcripts.latest_ai_title(f) is None

    blank = tmp_path / "blank.jsonl"
    _write(blank, [{"type": "ai-title", "aiTitle": "   "}])
    assert transcripts.latest_ai_title(blank) is None


def test_summary_for_path_includes_ai_title_distinct_from_recap(tmp_path):
    f = tmp_path / "t.jsonl"
    _write(f, [
        {"type": "system", "subtype": "away_summary", "content": "recap text",
         "timestamp": "2026-07-11T10:00:00Z"},
        {"type": "ai-title", "aiTitle": "Investigate flaky CI"},
    ])
    summary = transcripts.summary_for_path(f)
    assert summary["recap"] == "recap text"
    assert summary["ai_title"] == "Investigate flaky CI"

    assert transcripts.summary_for_path(None) == {
        "recap": None, "recap_ts": None, "pr": None, "ai_title": None,
    }


# ---------------------------------------------------------------------------
# CMX-153: two same-cwd windows must resolve to THEIR OWN transcript, by
# window id — not whichever file wins the cwd "newest record" race.
# ---------------------------------------------------------------------------

SID_A = "7f3a91c2-4b8e-4d15-9c62-1e0d5a8b3f47"
SID_B = "cf19ca61-ffbb-4dbf-a8c7-66b74294fa69"


def _session_transcript(base, cwd, session_id, ai_title):
    proj = base / transcripts.encode_cwd(cwd)
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{session_id}.jsonl"
    path.write_text(json.dumps({"type": "ai-title", "aiTitle": ai_title}) + "\n")
    return path


def test_resolve_agent_transcript_prefers_window_id_over_the_cwd_guess(monkeypatch):
    """A caller that hands over a window id must get `sessions.transcript_for_window`'s
    answer, not the name/cwd guess — even when the cwd guess would return something
    else entirely. This is the actual wiring bug: the dashboard had a window id sitting
    right next to the transcript lookup and never used it (CMX-153)."""
    monkeypatch.setattr(sessions, "transcript_for_window",
                        lambda wid, base=None: f"/by-window/{wid}.jsonl")
    monkeypatch.setattr(transcripts, "transcript_for_cwd", lambda cwd, base=None: "/by-cwd/wrong.jsonl")
    monkeypatch.setattr(discovery, "get_window_cwd", lambda name: CWD)

    assert transcripts._resolve_agent_transcript("agent", window_id="@7") == "/by-window/@7.jsonl"


def test_resolve_agent_transcript_falls_back_to_cwd_with_no_window_id(monkeypatch):
    """A caller with no window id at all (none live, none looked up) keeps the old,
    best-effort cwd guess — it is still strictly better than nothing."""
    monkeypatch.setattr(transcripts, "transcript_for_cwd", lambda cwd, base=None: "/by-cwd/only.jsonl")
    monkeypatch.setattr(discovery, "get_window_cwd", lambda name: CWD)

    assert transcripts._resolve_agent_transcript("agent") == "/by-cwd/only.jsonl"


def test_agent_transcript_summary_disambiguates_two_windows_sharing_one_cwd(
        tmp_path, monkeypatch):
    """The actual bug, end to end: two windows launched in the SAME directory each get
    THEIR OWN ai_title back, because they are resolved by window (session id) rather
    than by the directory they happen to share."""
    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", root)
    monkeypatch.setattr(discovery, "get_window_cwd", lambda name: CWD)

    _session_transcript(root, CWD, SID_A, "Refactor the risk engine")
    _session_transcript(root, CWD, SID_B, "Write the onboarding docs")
    monkeypatch.setattr(sessions, "panes", lambda force=False: {
        "@1": sessions.Pane(wid="@1", path=CWD, command="claude", claude_pid=1,
                             launched_in=CWD, started=time.time() - 60),
        "@2": sessions.Pane(wid="@2", path=CWD, command="claude", claude_pid=2,
                             launched_in=CWD, started=time.time() - 60),
    })
    event_log.append("hook.pre_tool_use", "a", wid="@1", session_id=SID_A)
    event_log.append("hook.pre_tool_use", "b", wid="@2", session_id=SID_B)

    summary_a = transcripts.agent_transcript_summary("anthony_work", window_id="@1")
    summary_b = transcripts.agent_transcript_summary("anthony_work", window_id="@2")
    assert summary_a["ai_title"] == "Refactor the risk engine"
    assert summary_b["ai_title"] == "Write the onboarding docs"

    # what resolving by cwd/name alone could only ever do: hand BOTH of them the same
    # (arbitrary "newest") transcript — the bug this guards against.
    by_name_only = transcripts.agent_transcript_summary("anthony_work")
    assert by_name_only["ai_title"] in ("Refactor the risk engine", "Write the onboarding docs")


# --- claude_config_dir: honouring $CLAUDE_CONFIG_DIR, not hardcoding ~/.claude ------
# CMX-173: chela hardcoded ~/.claude/projects as the transcript root, so any adopter who
# relocates Claude Code's config dir via $CLAUDE_CONFIG_DIR got NO transcript resolution
# at all (recaps, PR links, ai-titles, the telegram relay all silently went dead).

def test_claude_config_dir_honours_the_env_var(monkeypatch, tmp_path):
    custom = tmp_path / "somewhere-else"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom))
    assert transcripts.claude_config_dir() == custom


def test_claude_config_dir_defaults_to_dot_claude_when_unset(monkeypatch):
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    assert transcripts.claude_config_dir() == Path.home() / ".claude"


def test_hooks_claude_config_dir_delegates_to_transcripts(monkeypatch, tmp_path):
    """hooks.py must not keep its own copy of this logic — one source of truth, or the
    two can silently drift back out of sync the way the original bug did."""
    from chela import hooks

    custom = tmp_path / "elsewhere"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(custom))
    assert hooks.claude_config_dir() == custom == transcripts.claude_config_dir()
