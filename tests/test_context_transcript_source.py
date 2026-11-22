"""CMX-475 — the context % comes from the session transcript by default.

The statusLine command ``chela install-statusline`` writes is now optional: sessions
started before the install, org-managed settings that override it and users with their own
status line all used to get no (or a stale) reading. And right after ``/compact`` the bar
kept the old, high pre-compaction number until something refreshed it.

Fixture transcripts cover: a reading before a compact, the compacted state, the first
post-compact turn, no turn at all (``/clear``), a 1M window against a 200k one, each
window-size source in order, and the unknown window. A negative control shows the stale
post-compact value the old reader served.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import config, context, launch_settings, orchestrator, runtime_truth, sessions, transcripts
from chela.dashboard import app as dash

SID = "1c0ffee0-0000-4000-8000-000000000475"
NAME, WID, PID = "cmx-475", "@47", 4750


def _turn(*, inp=2, read=0, create=0, out=0, model="claude-opus-5-5", ts="2026-11-22T10:00:00.000Z",
          **extra) -> dict:
    return {"type": "assistant", "timestamp": ts, "cwd": "/nonexistent/proj", **extra,
            "message": {"model": model, "usage": {
                "input_tokens": inp, "cache_read_input_tokens": read,
                "cache_creation_input_tokens": create, "output_tokens": out}}}


def _boundary(ts="2026-11-22T10:05:00.000Z", pre=818_278, post=17_737) -> dict:
    return {"type": "system", "subtype": "compact_boundary", "timestamp": ts,
            "content": "Conversation compacted",
            "compactMetadata": {"trigger": "manual", "preTokens": pre, "postTokens": post}}


def _summary(ts="2026-11-22T10:05:00.000Z") -> dict:
    return {"type": "user", "isCompactSummary": True, "timestamp": ts,
            "message": {"role": "user", "content": "This session is being continued…"}}


def _write(tmp_path: Path, *records: dict) -> Path:
    p = tmp_path / f"{SID}.jsonl"
    p.write_text("".join(json.dumps(r) + "\n" for r in records))
    return p


def _append(path: Path, *records: dict) -> None:
    with path.open("a") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.fixture
def quiet(monkeypatch, tmp_path):
    """No window-size source answers unless a test plants one."""
    monkeypatch.setattr(context, "MANAGED_SETTINGS", (tmp_path / "managed.json",))
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", tmp_path / "ctx")
    monkeypatch.setattr(context.config, "default_context_window_explicit", lambda: None)
    monkeypatch.setattr(launch_settings, "for_session", lambda sid, path=None: {})
    monkeypatch.setattr(sessions, "panes", lambda: {})
    monkeypatch.setattr(context, "window_session", lambda wid: SID if wid else None)
    return tmp_path


def _live(path: Path | None) -> dict | None:
    with patch.object(transcripts, "_resolve_agent_transcript", lambda name, wid=None: path):
        return context.live_snapshot(NAME, WID)


# --- the reading itself -------------------------------------------------------------

def test_the_reading_is_input_plus_cache_read_plus_cache_creation_not_output(tmp_path):
    """Verified against Claude Code's own `/context` on a real transcript: 2 + 634,260 +
    805 input-side tokens with 256 output printed "635.1k" — output is not counted."""
    p = _write(tmp_path, _turn(inp=2, read=634_260, create=805, out=256))
    u = transcripts.latest_context_usage(p)
    assert u["used_tokens"] == 635_067
    assert u["compacted"] is False and u["session_id"] == SID
    assert u["cwd"] == "/nonexistent/proj" and u["ts"] == "2026-11-22T10:00:00.000Z"


@pytest.mark.parametrize("usage,expected", [
    ({"input_tokens": True, "cache_read_input_tokens": 10}, 10),
    ({"input_tokens": -5, "cache_read_input_tokens": 10}, 10),
    ({"input_tokens": "9", "cache_read_input_tokens": 10}, 10),
    ({"input_tokens": None, "cache_creation_input_tokens": 7}, 7),
    ({}, 0),
], ids=["bool", "negative", "string", "null", "empty"])
def test_usage_tokens_counts_only_real_positive_ints(usage, expected):
    assert transcripts.usage_tokens(usage) == expected


def test_a_synthetic_or_sidechain_turn_is_not_the_reading(tmp_path):
    p = _write(tmp_path, _turn(inp=50_000),
               _turn(inp=0, model="<synthetic>"),
               _turn(inp=900_000, isSidechain=True))
    assert transcripts.latest_context_usage(p)["used_tokens"] == 50_000


def test_a_sidechain_compaction_is_not_this_sessions(tmp_path):
    p = _write(tmp_path, _turn(inp=50_000), {**_boundary(), "isSidechain": True})
    assert transcripts.latest_context_usage(p)["compacted"] is False


# --- /compact -------------------------------------------------------------------------

@pytest.mark.parametrize("marker", [_boundary, _summary], ids=["compact_boundary", "isCompactSummary"])
def test_a_compaction_newer_than_the_last_usage_is_the_compacted_state(tmp_path, marker):
    p = _write(tmp_path, _turn(read=816_217, create=1_760, inp=2), marker())
    u = transcripts.latest_context_usage(p)
    assert u["compacted"] is True and u["used_tokens"] is None
    assert u["stale_used_tokens"] == 817_979
    assert u["model"] == "claude-opus-5-5"


def test_compact_metadata_is_carried_and_bad_values_dropped(tmp_path):
    p = _write(tmp_path, _turn(inp=10), _boundary(pre=818_278, post=17_737))
    u = transcripts.latest_context_usage(p)
    assert (u["pre_tokens"], u["post_tokens"]) == (818_278, 17_737)
    bad = {**_boundary(), "compactMetadata": {"preTokens": "818278", "postTokens": True}}
    p = _write(tmp_path, _turn(inp=10), bad)
    u = transcripts.latest_context_usage(p)
    assert (u["pre_tokens"], u["post_tokens"]) == (None, None)
    p = _write(tmp_path, _turn(inp=10), {**_boundary(), "compactMetadata": "not a dict"})
    assert transcripts.latest_context_usage(p)["pre_tokens"] is None


def test_a_compaction_with_no_turn_before_it_has_no_stale_number(tmp_path):
    p = _write(tmp_path, _boundary())
    u = transcripts.latest_context_usage(p)
    assert u["compacted"] is True
    assert (u["stale_used_tokens"], u["model"]) == (None, None)


def test_compacted_state_then_the_first_post_compact_turn_shows_the_real_percent(tmp_path, quiet):
    p = _write(tmp_path, _turn(read=816_217, create=1_760, inp=2, model="claude-opus-5-5[1m]"),
               _boundary())
    snap = _live(p)
    assert snap["state"] == "compacted"
    assert snap["used_pct"] is None and snap["used_k"] is None
    assert snap["pre_k"] == 818.3                       # what the stale number was
    _append(p, _turn(read=25_876, create=46_934, inp=2, model="claude-opus-5-5[1m]",
                     ts="2026-11-22T10:41:21.753Z"))
    snap = _live(p)
    assert snap["state"] == "ok"
    assert (snap["used_k"], snap["total_k"], snap["used_pct"]) == (72.8, 1000.0, 7)


def test_negative_control_the_old_reader_served_the_stale_pre_compact_percent(tmp_path, quiet):
    """TODAY's (pre-CMX-475) behaviour, reproduced on the same fixture: the reader took the
    latest usage and nothing else, and a fresh statusLine cache always won — both say the
    stale 82% after the compact. The new reading says `compacted`."""
    p = _write(tmp_path, _turn(read=816_217, create=1_760, inp=2), _boundary())
    old = transcripts.latest_record(p, transcripts._is_usage_turn)
    assert round(transcripts.usage_tokens(old["message"]["usage"]) / 1_000_000 * 100) == 82
    statusline = {"name": NAME, "used_pct": 82, "used_k": 818.0, "total_k": 1000.0,
                  "source": "statusline", "state": "ok", "ts": "2026-11-22T10:06:00+00:00",
                  "cost_usd": 4.2}
    with patch.object(context, "_cache_snapshot", lambda name, sid=None: statusline):
        assert (context._cache_snapshot(NAME, SID) or None)["used_pct"] == 82   # the old rule
        snap = _live(p)
    assert snap["state"] == "compacted" and snap["used_pct"] is None
    assert snap["cost_usd"] == 4.2                      # the statusLine's cost is kept


# --- no turn yet (/clear) -------------------------------------------------------------

def test_no_assistant_turn_is_no_turns_never_zero(tmp_path, quiet):
    p = _write(tmp_path, {"type": "user", "message": {"role": "user", "content": "hi"}})
    assert transcripts.latest_context_usage(p) is None
    snap = _live(p)
    assert snap["state"] == "no_turns"
    assert snap["used_pct"] is None and snap["used_k"] is None


def test_no_turns_beats_a_statusline_number(tmp_path, quiet):
    """/clear starts a new transcript; a statusLine number is from before it."""
    p = _write(tmp_path)
    stale = {"used_pct": 40, "source": "statusline", "state": "ok", "ts": "2099-01-01T00:00:00+00:00"}
    with patch.object(context, "_cache_snapshot", lambda name, sid=None: stale):
        assert _live(p)["state"] == "no_turns"


def test_no_transcript_at_all_and_no_statusline_is_no_snapshot(quiet):
    assert _live(None) is None


def test_a_failing_transcript_read_leaves_the_statusline_standing(quiet):
    sl = {"used_pct": 12, "source": "statusline", "state": "ok", "ts": "2026-11-22T10:00:00+00:00"}

    def boom(name, wid=None):
        raise RuntimeError("tmux went away")

    with (patch.object(context, "_cache_snapshot", lambda name, sid=None: sl),
          patch.object(transcripts, "_resolve_agent_transcript", boom)):
        assert context.live_snapshot(NAME, WID) is sl


# --- the newer of the two sources ----------------------------------------------------

def _sl(ts: str | None, pct=30) -> dict:
    return {"name": NAME, "used_pct": pct, "source": "statusline", "state": "ok", "ts": ts,
            "cost_usd": 1.5, "rate_limit_pct": 12, "rate_limit_resets_at": 1,
            "weekly_rl_pct": 3, "weekly_rl_resets_at": 2, "session_name": "s", "branch": "b"}


def _tr(ts: str | None, pct=40, state="ok") -> dict:
    return {**context._blank(NAME), "used_pct": pct, "state": state, "ts": ts}


def test_pick_the_newer_transcript_keeps_the_statuslines_extras():
    got = context.pick(_sl("2026-11-22T10:00:00+00:00"), _tr("2026-11-22T10:00:01.000Z"))
    assert got["used_pct"] == 40 and got["source"] == "transcript"
    assert (got["cost_usd"], got["rate_limit_pct"], got["rate_limit_resets_at"],
            got["weekly_rl_pct"], got["weekly_rl_resets_at"], got["session_name"],
            got["branch"]) == (1.5, 12, 1, 3, 2, "s", "b")


def test_pick_the_newer_statusline_wins_and_a_tie_goes_to_it():
    assert context.pick(_sl("2026-11-22T10:00:02+00:00"), _tr("2026-11-22T10:00:01Z"))["used_pct"] == 30
    assert context.pick(_sl("2026-11-22T10:00:01+00:00"), _tr("2026-11-22T10:00:01Z"))["used_pct"] == 30


@pytest.mark.parametrize("s_ts,t_ts", [(None, "2026-11-22T10:00:01Z"), ("2026-11-22T10:00:01Z", None),
                                       ("garbage", "2026-11-22T10:00:01Z"), ("2026-11-22T10:00:01Z", 5)])
def test_pick_an_unreadable_timestamp_keeps_the_statusline(s_ts, t_ts):
    assert context.pick(_sl(s_ts), _tr(t_ts))["used_pct"] == 30


def test_pick_a_naive_timestamp_is_read_as_utc():
    assert context.pick(_sl("2026-11-22T10:00:00"), _tr("2026-11-22T10:00:01Z"))["used_pct"] == 40


def test_pick_either_source_alone():
    assert context.pick(None, _tr("x"))["used_pct"] == 40
    assert context.pick(_sl("x"), None)["used_pct"] == 30
    assert context.pick(None, None) is None


def test_a_statusline_snapshot_names_itself(tmp_path, monkeypatch):
    path = tmp_path / "x.json"
    path.write_text(json.dumps({"context_window": {"used_percentage": 5, "context_window_size": 200_000}}))
    monkeypatch.setattr(context.config, "cache_stale_seconds", lambda: 7200)
    snap = context._snapshot_from(path, NAME)
    assert (snap["source"], snap["state"], snap["window_source"]) == ("statusline", "ok", "statusline")


def test_no_statusline_installed_matches_the_transcripts_last_usage(tmp_path, quiet):
    """Acceptance: a session with no status line shows its transcript's last usage."""
    p = _write(tmp_path, _turn(inp=100_000), _turn(inp=2, read=148_000, create=2_000, out=900))
    snap = _live(p)
    assert snap["source"] == "transcript" and snap["used_k"] == 150.0


# --- the window: 1M against 200k, and which source decided -------------------------

def _resolve(used=150_000, model="claude-opus-5-5", cwd=None, wid=None):
    return context.resolve_window(used, session_id=SID, agent_name=NAME, model=model,
                                  cwd=cwd, window_id=wid)


@pytest.mark.parametrize("model,hit", [
    ("opus[1m]", True), ("claude-opus-5-5[1m]", True), ("OPUS[1M]", True), ("sonnet[1m] ", True),
    ("opus", False), ("claude-opus-5-5", False), ("[1m]opus", False), ("opus[1m]x", False),
    ("opus-1m", False), ("", False), (None, False),
])
def test_the_1m_marker(model, hit):
    assert context.has_1m_marker(model) is hit


def test_1m_versus_200k_from_the_same_usage(tmp_path, quiet):
    """The same 150K is 15% of a 1M window and 75% of a 200k one."""
    p = _write(tmp_path, _turn(inp=150_000, model="claude-opus-5-5[1m]"))
    assert _live(p)["used_pct"] == 15
    p = _write(tmp_path, _turn(inp=150_000))
    with patch.object(context, "_reported_window", lambda sid, name=None: (200_000, "Sonnet")):
        snap = _live(p)
    assert (snap["total_k"], snap["used_pct"], snap["window_source"]) == (200.0, 75, "statusline")


def test_source_transcript(quiet):
    assert _resolve(model="claude-opus-5-5[1m]") == {"window": 1_000_000, "source": "transcript", "model": None}


def test_source_launch_flag_on_the_live_process(quiet, monkeypatch):
    monkeypatch.setattr(sessions, "panes", lambda: {WID: SimpleNamespace(claude_pid=PID)})
    seen = []
    monkeypatch.setattr(sessions, "_cmdline_argv",
                        lambda pid: seen.append(pid) or ["node", "/x/claude", "--model", "opus[1m]"])
    assert _resolve(wid=WID)["source"] == "launch"
    assert seen == [PID]


def test_source_launch_flag_equals_form_and_last_wins():
    assert context._flag_model(["--model=opus[1m]"]) == "opus[1m]"
    assert context._flag_model(["--model", "sonnet", "--model", "opus[1m]"]) == "opus[1m]"
    assert context._flag_model(["--model"]) is None
    assert context._flag_model(["--model="]) is None
    assert context._flag_model(["--models", "opus[1m]"]) is None


def test_source_launch_env(quiet, monkeypatch):
    monkeypatch.setattr(sessions, "panes", lambda: {WID: SimpleNamespace(claude_pid=PID)})
    monkeypatch.setattr(sessions, "_cmdline_argv", lambda pid: ["claude"])
    monkeypatch.setattr(launch_settings, "read_environ",
                        lambda pid: {"ANTHROPIC_MODEL": "claude-opus-5-5[1m]"} if pid == PID else {})
    assert _resolve(wid=WID)["source"] == "launch"


def test_source_launch_recorded_cmx_372(quiet, monkeypatch):
    monkeypatch.setattr(launch_settings, "for_session",
                        lambda sid, path=None: {"flags": ["--model", "opus[1m]"]} if sid == SID else {})
    assert _resolve()["source"] == "launch"


def test_a_failing_pane_lookup_falls_back_to_the_record(quiet, monkeypatch):
    def boom():
        raise RuntimeError("no tmux")
    monkeypatch.setattr(sessions, "panes", boom)
    monkeypatch.setattr(launch_settings, "for_session",
                        lambda sid, path=None: {"flags": ["--model", "opus[1m]"]})
    assert _resolve(wid=WID)["source"] == "launch"


def test_a_recorded_flags_value_that_is_not_a_list_is_no_launch_model(quiet, monkeypatch):
    monkeypatch.setattr(launch_settings, "for_session", lambda sid, path=None: {"flags": "--model opus[1m]"})
    assert _resolve()["source"] == "unknown"


@pytest.mark.parametrize("where", ["managed", "local", "project", "user"])
def test_source_settings(quiet, monkeypatch, where):
    proj = quiet / "proj"
    (proj / ".claude").mkdir(parents=True)
    claude = quiet / "claude"
    claude.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    path = {"managed": quiet / "managed.json",
            "local": proj / ".claude" / "settings.local.json",
            "project": proj / ".claude" / "settings.json",
            "user": claude / "settings.json"}[where]
    path.write_text(json.dumps({"model": "opus[1m]"}))
    assert _resolve(cwd=str(proj))["source"] == "settings"


def test_settings_precedence_the_first_file_naming_a_model_decides(quiet, monkeypatch):
    proj = quiet / "proj"
    (proj / ".claude").mkdir(parents=True)
    claude = quiet / "claude"
    claude.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    (claude / "settings.json").write_text(json.dumps({"model": "opus[1m]"}))
    (proj / ".claude" / "settings.json").write_text(json.dumps({"model": "sonnet"}))
    assert context._settings_model(str(proj)) == "sonnet"
    assert _resolve(cwd=str(proj))["source"] == "unknown"
    (quiet / "managed.json").write_text(json.dumps({"model": "opus[1m]"}))
    assert context._settings_model(str(proj)) == "opus[1m]"


@pytest.mark.parametrize("content", ["{not json", "[1]", json.dumps({"model": 5}),
                                     json.dumps({"model": "  "}), None],
                         ids=["bad-json", "not-a-dict", "not-a-string", "blank", "a-directory"])
def test_an_unusable_settings_file_is_skipped(quiet, monkeypatch, content):
    claude = quiet / "claude"
    claude.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    (claude / "settings.json").write_text(json.dumps({"model": "opus[1m]"}))
    managed = quiet / "managed.json"
    if content is None:
        managed.mkdir()                                  # IsADirectoryError, an OSError
    else:
        managed.write_text(content)
    assert context._settings_model(None) == "opus[1m]"


def test_a_bare_launch_model_overrides_a_1m_setting(quiet, monkeypatch):
    """Claude Code: `--model` beats the settings `model`, so a bare launch model stops the
    model chain — the settings file's [1m] is not this session's window."""
    (quiet / "managed.json").write_text(json.dumps({"model": "opus[1m]"}))
    monkeypatch.setattr(launch_settings, "for_session", lambda sid, path=None: {"flags": ["--model", "sonnet"]})
    assert _resolve()["source"] == "unknown"


def test_source_statusline_at_any_age(quiet):
    with patch.object(context, "_reported_window", lambda sid, name=None: (1_000_000, "Opus 5.5")):
        assert _resolve() == {"window": 1_000_000, "source": "statusline", "model": "Opus 5.5"}


def test_source_usage_past_200k_proves_1m(quiet):
    assert _resolve(used=200_001)["source"] == "usage"
    assert _resolve(used=200_000)["source"] == "unknown"
    assert _resolve(used=1_000_001)["source"] == "unknown"


def test_usage_past_a_reported_window_overrides_it(quiet):
    with patch.object(context, "_reported_window", lambda sid, name=None: (100_000, "x")):
        assert _resolve(used=159_000)["source"] == "usage"
        assert _resolve(used=100_000)["source"] == "statusline"


def test_source_config_only_when_set(quiet, monkeypatch):
    monkeypatch.setattr(context.config, "default_context_window_explicit", lambda: 500_000)
    assert _resolve() == {"window": 500_000, "source": "config", "model": None}


def test_unknown_window_shows_tokens_and_no_percent(tmp_path, quiet):
    p = _write(tmp_path, _turn(inp=2, read=180_000))
    snap = _live(p)
    assert snap["window_source"] == "unknown"
    assert (snap["used_k"], snap["total_k"], snap["used_pct"], snap["free_pct"]) == (180.0, None, None, None)
    assert snap["estimated"] is False


@pytest.mark.parametrize("source,estimated", [("transcript", False), ("statusline", False),
                                              ("usage", True), ("config", True)])
def test_only_inferred_windows_are_estimated(quiet, source, estimated):
    with patch.object(context, "resolve_window",
                      lambda used, **kw: {"window": 1_000_000, "source": source, "model": None}):
        w = context.window_for(100_000, SID)
    assert (w["estimated"], w["window_source"], w["used_pct"]) == (estimated, source, 10)


# --- the knob: only an operator-set default counts ----------------------------------

@pytest.mark.parametrize("env,expected", [(None, None), ("500000", 500_000), ("0", None),
                                          ("-3", None), ("abc", None)])
def test_default_context_window_explicit(monkeypatch, env, expected):
    if env is None:
        monkeypatch.delenv("CHELA_DEFAULT_CONTEXT_WINDOW", raising=False)
    else:
        monkeypatch.setenv("CHELA_DEFAULT_CONTEXT_WINDOW", env)
    assert config.default_context_window_explicit() == expected


# --- the surfaces -----------------------------------------------------------------------

def _api_row(snap: dict) -> dict:
    with (patch("chela.discovery.get_all_windows", return_value={NAME: WID}),
          patch("chela.agent_manager.claude_pid", return_value=PID),
          patch.object(context, "live_snapshot", lambda name, wid=None: snap)):
        return dash.app.test_client().get("/api/agents/context").get_json()[0]


def test_api_row_carries_state_window_source_and_pre():
    row = _api_row({**context._blank(NAME), "state": "compacted", "pre_k": 818.3})
    assert (row["state"], row["pre"], row["used_pct"], row["used"]) == ("compacted", "818.3K", None, None)
    row = _api_row({**context._blank(NAME), "state": "ok", "used_k": 430.0, "window_source": "unknown"})
    assert (row["state"], row["window_source"], row["used"], row["total"]) == ("ok", "unknown", "430K", None)
    row = _api_row({"name": NAME, "used_pct": 5})
    assert row["state"] == "ok"


def test_peek_says_compacted_not_the_old_number():
    p = {"wid": "@1", "name": "n", "health": "green", "liveness": "alive",
         "session_status": "idle", "claude_running": True, "cwd": "/x",
         "window_type": "claude", "recap": None,
         "context": {"compacted": True, "used_tokens": None, "stale_used_tokens": 817_979}}
    out = orchestrator.format_peek(p)
    assert "  context: compacted — the real % shows after the next turn\n" in out
    assert "817" not in out


# --- chela doctor ---------------------------------------------------------------------

def _report(rows):
    return runtime_truth._context_source_report(None, runtime_truth.observed(rows))


def test_doctor_names_each_sessions_source():
    ok = {"source": "transcript", "state": "ok", "used_pct": 12, "window_source": "launch"}
    [f] = _report([(NAME, WID, ok)])
    assert f.level == runtime_truth.OK
    assert f.title == f"context % for {NAME} ({WID}): 12% — source: transcript, window from: launch"


def test_doctor_flags_no_usable_source_as_unknown_never_zero():
    [f] = _report([(NAME, WID, None)])
    assert f.level == runtime_truth.WARN and "UNKNOWN" in f.title and "0%" not in f.title


def test_doctor_unknown_window():
    snap = {"source": "transcript", "state": "ok", "used_pct": None, "used_k": 430.0}
    [f] = _report([(NAME, WID, snap)])
    assert f.level == runtime_truth.WARN
    assert f.title == f"context % for {NAME} ({WID}): 430.0K tokens, window size UNKNOWN — shown as tokens, no % (source: transcript)"


@pytest.mark.parametrize("state,text", [("no_turns", "— (no assistant turn yet"), ("compacted", "compacted —")])
def test_doctor_the_states_with_no_percent(state, text):
    [f] = _report([(NAME, WID, {"source": "transcript", "state": state, "used_pct": None})])
    assert f.level == runtime_truth.INFO and text in f.title


def test_doctor_no_live_session():
    [f] = _report([])
    assert f.level == runtime_truth.OK and "no live Claude session" in f.title


def test_doctor_read_lists_only_windows_running_claude(monkeypatch):
    monkeypatch.setattr("chela.discovery.get_windows_by_id", lambda: {"@1": "a", "@2": "shell"})
    monkeypatch.setattr("chela.agent_manager.claude_pid", lambda wid: 11 if wid == "@1" else None)
    monkeypatch.setattr(context, "live_snapshot", lambda name, wid=None: {"name": name, "wid": wid})
    assert runtime_truth._context_snapshots() == [("a", "@1", {"name": "a", "wid": "@1"})]


def test_doctor_without_tmux_cannot_verify(monkeypatch):
    monkeypatch.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: None)
    assert runtime_truth._context_source_read().unverifiable
    assert runtime_truth.fact("context.source").unverifiable_level == runtime_truth.WARN


def test_stale_statusline_age_does_not_matter_for_the_transcript(tmp_path, quiet):
    """The cache file going stale (age) never hides the transcript reading."""
    (quiet / "ctx").mkdir(exist_ok=True)
    old = quiet / "ctx" / "x.json"
    old.write_text("{}")
    t = time.time() - 99_999
    os.utime(old, (t, t))
    p = _write(tmp_path, _turn(inp=50_000, model="opus[1m]"))
    assert _live(p)["used_pct"] == 5
