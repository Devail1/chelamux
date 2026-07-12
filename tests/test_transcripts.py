"""Tests for transcript path resolution (`transcripts.transcript_for_cwd`)."""
from __future__ import annotations

import json
import os

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
