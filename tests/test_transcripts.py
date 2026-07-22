"""Tests for transcript path resolution (`transcripts.transcript_for_cwd`)."""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from chela import transcripts

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
