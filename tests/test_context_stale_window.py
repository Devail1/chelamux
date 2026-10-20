"""CMX-292 — an idle 1M session must not read as 80% of 200K.

Seen 2026-10-20: a session idle for hours had a by-session statusLine cache saying
``context_window_size: 1000000`` / 16%, but the cache was older than
``cache_stale_seconds()``, so the reader fell back to the transcript — which records
token counts but not the window — and sized ~159K against the 200K default: 80%.

A session's window size does not change while it runs, so the fallback now takes the
window (and model) from that SAME session's cache at any age, and only the window:
usage still comes from the transcript. No cache for the session ever ⇒ the 200K
default, ``estimated: True``, as before.

Every consumer is driven: ``/api/agents/context`` (the sidebar row, the pane/tile
bottom bar and the phone view all render this one endpoint's rows), the Cost tab's
live read, and ``chela peek``.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import agent_manager, context, discovery, orchestrator, sessions
from chela.dashboard import app as dash

SID = "08dc5f45-0000-4000-8000-000000000001"     # the window's own session
OTHER = "5b7c11aa-0000-4000-8000-000000000002"   # a different session
NAME, WID, PID = "tradeplan-d9", "@9", 9009
USED = 159_000


def _payload(sid: str, *, size=1_000_000, pct=16, display="Opus 5.5") -> dict:
    return {
        "session_id": sid,
        "context_window": {"used_percentage": pct, "context_window_size": size,
                           "remaining_percentage": 100 - pct},
        "model": {"id": "claude-opus-5-5", "display_name": display},
        "cost": {"total_cost_usd": 3.0},
    }


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "context"
    (cache / "by-session").mkdir(parents=True)
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", cache)
    monkeypatch.setattr(context.config, "cache_stale_seconds", lambda: 7200)
    monkeypatch.setattr(context.config, "default_context_window", lambda: 200_000)
    transcript = tmp_path / f"{SID}.jsonl"
    transcript.write_text(json.dumps({"type": "assistant", "message": {
        "model": "claude-opus-5-5",
        "usage": {"input_tokens": 9_000, "cache_read_input_tokens": 150_000}}}) + "\n")

    def write(path: Path, data, *, age: float = 4 * 3600) -> Path:
        path.write_text(data if isinstance(data, str) else json.dumps(data))
        t = time.time() - age
        os.utime(path, (t, t))
        return path

    pane = SimpleNamespace(wid=WID, claude_pid=PID)
    with (
        patch("chela.discovery.get_all_windows", return_value={NAME: WID}),
        patch.object(discovery, "get_windows_by_id", return_value={WID: NAME}),
        patch.object(discovery, "get_window_cwd_by_id", return_value="/nonexistent/cmx-292"),
        patch("chela.agent_manager.claude_pid", side_effect={WID: PID}.get),
        patch.object(agent_manager, "session_status_map",
                     return_value={"by_pid": {PID: "idle"}, "cwd_by_pid": {}, "by_cwd": {}}),
        patch.object(agent_manager, "window_type", return_value="claude"),
        patch.object(sessions, "panes", return_value={WID: pane}),
        patch.object(sessions, "registry_session", side_effect={PID: SID}.get),
        patch.object(sessions, "transcript_for_window",
                     side_effect=lambda wid, base=None: transcript if wid == WID else None),
    ):
        yield SimpleNamespace(
            own=cache / "by-session" / f"{SID}.json",
            other=cache / "by-session" / f"{OTHER}.json",
            window_file=context.cache_path(NAME),
            write=write,
        )


def _surfaces() -> dict[str, tuple]:
    """``{surface: (total_k, used_pct, estimated)}`` as each consumer reports it."""
    client = dash.app.test_client()
    rows = {r["name"]: r for r in client.get("/api/agents/context").get_json()}
    row = rows[NAME]
    snap = context.live_snapshot(NAME, WID)
    peek = orchestrator.peek(WID)["context"]
    return {
        "snapshot": (snap["total_k"], snap["used_pct"], snap["estimated"]),
        # sidebar row + pane bottom bar + phone view: one endpoint, rendered as `total`
        "api": ({"1M": 1000.0, "200K": 200.0}[row["total"]], row["used_pct"], row["estimated"]),
        "peek": (peek["total_k"], peek["used_pct"], peek["estimated"]),
    }


def _assert_every_surface(expected):
    for surface, got in _surfaces().items():
        assert got == expected, f"{surface}: {got} != {expected}"


def test_a_stale_cache_for_the_same_session_sizes_the_transcript_usage_at_1m(env):
    env.write(env.own, _payload(SID))
    _assert_every_surface((1000.0, 16, False))
    snap = context.live_snapshot(NAME, WID)
    assert snap["source"] == "transcript" and snap["used_k"] == 159.0
    assert snap["free_k"] == 841.0 and snap["free_pct"] == 84
    assert snap["model"] == "Opus 5.5"
    assert snap["cost_usd"] is None  # the stale cost/usage numbers are never served


def test_the_stale_caches_usage_is_never_used_only_its_window(env):
    """The cache says 50%; the transcript says 159K. 159K of 1M is 16%."""
    env.write(env.own, _payload(SID, pct=50))
    _assert_every_surface((1000.0, 16, False))
    assert context.live_snapshot(NAME, WID)["used_k"] == 159.0


def test_no_cache_ever_keeps_the_estimated_200k_default(env):
    _assert_every_surface((200.0, 80, True))
    assert context.live_snapshot(NAME, WID)["model"] == "claude-opus-5-5"


def test_a_stale_cache_from_a_different_session_is_not_used(env):
    env.write(env.other, _payload(OTHER))
    env.write(env.window_file, _payload(OTHER))
    _assert_every_surface((200.0, 80, True))


def test_a_by_session_file_whose_payload_names_another_session_is_not_used(env):
    env.write(env.own, _payload(OTHER))
    _assert_every_surface((200.0, 80, True))


def test_a_stale_window_file_naming_the_same_session_supplies_the_window(env):
    """An old hook wrote no by-session file; the window file's payload is OUR session."""
    env.write(env.window_file, _payload(SID))
    _assert_every_surface((1000.0, 16, False))


def test_a_fresh_cache_still_wins_over_the_transcript(env):
    env.write(env.own, _payload(SID, pct=20), age=0)
    snap = context.live_snapshot(NAME, WID)
    assert snap["source"] == "statusline" and snap["used_pct"] == 20 and snap["total_k"] == 1000


@pytest.mark.parametrize("data", [
    "{not json",
    "[1, 2]",
    {"session_id": SID},
    {"session_id": SID, "context_window": "1000000"},
    {"session_id": SID, "context_window": {"context_window_size": 0}},
    {"session_id": SID, "context_window": {"context_window_size": True}},
    {"session_id": SID, "context_window": {"context_window_size": "1000000"}},
    {"session_id": SID, "context_window": {"context_window_size": None}},
], ids=["corrupt", "not-a-dict", "no-window", "window-not-a-dict", "zero", "bool",
        "string", "null"])
def test_an_unusable_cache_falls_back_to_the_estimate(env, data):
    env.write(env.own, data)
    _assert_every_surface((200.0, 80, True))


def test_an_unreadable_cache_falls_back_to_the_estimate(env):
    env.own.mkdir()  # IsADirectoryError — an OSError subclass
    _assert_every_surface((200.0, 80, True))


def test_a_cache_that_is_not_utf8_falls_back_to_the_estimate(env):
    env.own.write_bytes(b'{"session_id": "\xff\xfe"}')  # UnicodeDecodeError, a ValueError
    t = time.time() - 4 * 3600
    os.utime(env.own, (t, t))
    _assert_every_surface((200.0, 80, True))


def test_an_unusable_by_session_file_still_lets_the_window_file_answer(env):
    env.write(env.own, "{not json")
    env.write(env.window_file, _payload(SID))
    _assert_every_surface((1000.0, 16, False))


def test_a_model_less_cache_keeps_the_transcripts_model(env):
    data = _payload(SID)
    data["model"] = "Opus"
    env.write(env.own, data)
    snap = context.live_snapshot(NAME, WID)
    assert snap["model"] == "claude-opus-5-5" and snap["total_k"] == 1000


def test_usage_beyond_the_reported_window_is_bumped_and_estimated(env):
    """A 100K report under 159K of usage is evidently wrong: bumped to 1M, estimated."""
    env.write(env.own, _payload(SID, size=100_000))
    _assert_every_surface((1000.0, 16, True))


def test_window_for_without_a_session_uses_the_default():
    with patch.object(context.config, "default_context_window", return_value=200_000):
        w = context.window_for(USED, None)
    assert (w["total_k"], w["used_pct"], w["estimated"], w["model"]) == (200.0, 80, True, None)


def test_the_live_cost_row_carries_the_recovered_model(env):
    env.write(env.own, _payload(SID))
    rows = dash.app.test_client().get("/api/cost?window=live").get_json()
    assert rows == [{"name": NAME, "model": "Opus 5.5", "cost_usd": None}]


def test_format_peek_names_the_window_and_percentage(env):
    env.write(env.own, _payload(SID))
    out = orchestrator.format_peek(orchestrator.peek(WID))
    assert "  context: ~159K / 1000K tokens (16%) (claude-opus-5-5)\n" in out


def test_format_peek_marks_the_default_window_as_estimated(env):
    out = orchestrator.format_peek(orchestrator.peek(WID))
    assert "  context: ~159K / 200K tokens (80%, est) (claude-opus-5-5)\n" in out


def test_format_peek_without_a_window_keeps_the_bare_count():
    p = {"wid": "@1", "name": "n", "health": "green", "liveness": "alive",
         "session_status": "idle", "claude_running": True, "cwd": "/x",
         "window_type": "claude", "recap": None,
         "context": {"used_tokens": 159_000, "model": None}}
    assert "  context: ~159K tokens\n" in orchestrator.format_peek(p)


def test_no_session_never_matches_a_window_file_that_names_none(env):
    """With no session to match, a payload with no ``session_id`` must not pass as one."""
    data = _payload(SID)
    del data["session_id"]
    env.write(env.window_file, data)
    w = context.window_for(USED, None, NAME)
    assert (w["total_k"], w["used_pct"], w["estimated"]) == (200.0, 80, True)
