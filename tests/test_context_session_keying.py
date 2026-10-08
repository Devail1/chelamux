"""CMX-29 — a window's context bar must carry its OWN session's numbers.

The statusLine hook (``scripts/cache-statusline.sh``) resolves the window name from
``$TMUX_PANE`` — and every process launched from a pane inherits that variable. On
2026-10-08 a Claude Code BACKGROUND session (71%, $45) and the window's real
interactive session (9%, $0.82) took turns overwriting ``liavedunix.json``, and the
bar served whichever wrote last. The fix caches by the payload's ``session_id`` too,
and the dashboard reads the session the window's own claude process is running.

These tests drive the REAL hook script (with a fake ``tmux`` on PATH) so the write
side and the read side are exercised together, in both write orders.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import context, sessions, transcripts
from chela.dashboard import app as dash

HOOK = Path(__file__).resolve().parent.parent / "scripts" / "cache-statusline.sh"

FG = "3e397fae-0000-4000-8000-000000000001"     # @6's own interactive session
BG = "da024f6d-0000-4000-8000-000000000002"     # background agent that inherited @6's pane
OTHER = "5b7c11aa-0000-4000-8000-000000000003"  # @90, same cwd as @6

WINDOWS = {"liavedunix": "@6", "liavedunix-2": "@90"}
PANE_OF = {"%6": "liavedunix", "%90": "liavedunix-2"}
CLAUDE_PID = {"@6": 6006, "@90": 9009}
REGISTRY = {6006: FG, 9009: OTHER}  # what each pane's OWN claude process claims


def _payload(sid: str, pct: float, cost: float) -> str:
    return json.dumps({
        "session_id": sid,
        "cwd": "/nonexistent/cmx-29",
        "context_window": {"used_percentage": pct, "context_window_size": 1_000_000,
                           "remaining_percentage": 100 - pct},
        "model": {"display_name": "Opus"},
        "cost": {"total_cost_usd": cost},
        "session_name": sid[:8],
    })


@pytest.fixture
def env(tmp_path, monkeypatch):
    chela_dir = tmp_path / "chela"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # Fake tmux: `display-message -t <pane> -p '#{window_name}'` → the pane's window name.
    cases = "\n".join(f'    {pane}) echo {name} ;;' for pane, name in PANE_OF.items())
    tmux = bindir / "tmux"
    tmux.write_text(f'#!/usr/bin/env bash\ncase "$3" in\n{cases}\nesac\n')
    tmux.chmod(0o755)
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", chela_dir / "context")

    def run_hook(pane: str, sid: str, pct: float, cost: float) -> None:
        e = dict(os.environ, CHELA_DIR=str(chela_dir), TMUX_PANE=pane,
                 PATH=f"{bindir}{os.pathsep}{os.environ['PATH']}")
        subprocess.run(["bash", str(HOOK)], input=_payload(sid, pct, cost), env=e,
                       text=True, check=True, timeout=20)

    return SimpleNamespace(run_hook=run_hook, cache=chela_dir / "context")


@pytest.fixture
def fleet():
    panes = {wid: SimpleNamespace(wid=wid, claude_pid=pid) for wid, pid in CLAUDE_PID.items()}
    with (
        patch("chela.discovery.get_all_windows", return_value=dict(WINDOWS)),
        patch("chela.agent_manager.claude_pid", side_effect=CLAUDE_PID.get),
        patch.object(sessions, "panes", return_value=panes),
        patch.object(sessions, "registry_session", side_effect=REGISTRY.get),
    ):
        yield


def _rows() -> dict[str, dict]:
    rows = dash.app.test_client().get("/api/agents/context").get_json()
    return {r["name"]: r for r in rows}


# Both write orders: the background session landing LAST is the live symptom (it
# writes every few seconds, so it usually wins); landing FIRST must be just as right.
ORDERS = {
    "background-writes-last": [("%6", FG, 9, 0.82), ("%90", OTHER, 33, 5.0), ("%6", BG, 72, 48.0)],
    "background-writes-first": [("%6", BG, 72, 48.0), ("%90", OTHER, 33, 5.0), ("%6", FG, 9, 0.82)],
}


@pytest.mark.parametrize("order", list(ORDERS), ids=list(ORDERS))
def test_each_window_row_carries_its_own_sessions_numbers(env, fleet, order):
    for write in ORDERS[order]:
        env.run_hook(*write)
    rows = _rows()
    assert rows["liavedunix"]["used_pct"] == 9
    assert rows["liavedunix"]["cost_usd"] == 0.82
    assert rows["liavedunix"]["session_name"] == FG[:8]
    assert rows["liavedunix-2"]["used_pct"] == 33
    assert rows["liavedunix-2"]["cost_usd"] == 5.0


@pytest.mark.parametrize("order", list(ORDERS), ids=list(ORDERS))
def test_live_cost_rows_carry_each_windows_own_session_cost(env, fleet, order):
    """The SECOND reader of the cache: ``/api/cost?window=live`` (the Cost tab) must
    hand ``live_snapshot`` the window id too — a name-only read serves the window
    file, i.e. whichever session wrote last, and @6's row shows the background
    agent's $48 in one of the two orders."""
    for write in ORDERS[order]:
        env.run_hook(*write)
    rows = {r["name"]: r for r in dash.app.test_client().get("/api/cost?window=live").get_json()}
    assert rows["liavedunix"]["cost_usd"] == 0.82
    assert rows["liavedunix-2"]["cost_usd"] == 5.0


def test_the_hook_caches_every_session_under_its_own_id(env):
    """The write side alone: the window file is last-writer-wins (that is the bug's
    shape), but each session's by-session file holds only its own payload."""
    for write in ORDERS["background-writes-last"]:
        env.run_hook(*write)
    by = env.cache / "by-session"
    assert json.loads((by / f"{FG}.json").read_text())["cost"]["total_cost_usd"] == 0.82
    assert json.loads((by / f"{BG}.json").read_text())["cost"]["total_cost_usd"] == 48.0
    assert json.loads((env.cache / "liavedunix.json").read_text())["session_id"] == BG


def test_a_session_id_that_is_not_an_id_never_becomes_a_path(env):
    env.run_hook("%6", "../../escape", 10, 1.0)
    assert not (env.cache / "by-session").exists()
    assert not (env.cache.parent / "escape.json").exists()


def test_a_foreign_window_file_is_refused_when_the_own_session_has_no_file(env, fleet):
    """An old hook (no by-session write) left only the window file — written by the
    background session. Serving it would be the bug; no statusline row is right."""
    env.cache.mkdir(parents=True)
    (env.cache / "liavedunix.json").write_text(_payload(BG, 72, 48.0))
    assert context._cache_snapshot("liavedunix", FG) is None
    (env.cache / "liavedunix.json").write_text(_payload(FG, 9, 0.82))
    assert context._cache_snapshot("liavedunix", FG)["cost_usd"] == 0.82


# --- the transcript fallback (no statusLine installed) -----------------------------

def _transcript(tmp_path: Path, name: str, used: int) -> Path:
    p = tmp_path / f"{name}.jsonl"
    p.write_text(json.dumps({"type": "assistant", "message": {
        "model": "claude-opus-5", "usage": {"input_tokens": used}}}) + "\n")
    return p


def test_transcript_fallback_resolves_each_windows_own_transcript(tmp_path, monkeypatch, fleet):
    """Two windows in one cwd, no statusLine cache: each must be read from ITS session's
    transcript, never both from "the newest JSONL in the cwd"."""
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", tmp_path / "empty")
    own = {"@6": _transcript(tmp_path, "fg", 90_000), "@90": _transcript(tmp_path, "other", 330_000)}
    newest_in_cwd = _transcript(tmp_path, "bg", 720_000)
    with (
        patch.object(sessions, "transcript_for_window", side_effect=lambda wid, base=None: own.get(wid)),
        patch.object(transcripts, "transcript_for_cwd", return_value=newest_in_cwd),
        patch("chela.discovery.get_window_cwd", return_value="/same/cwd"),
    ):
        rows = _rows()
    assert rows["liavedunix"]["used"] == "90K"
    assert rows["liavedunix-2"]["used"] == "330K"


def test_zero_setup_fallback_without_a_window_id_still_reads_the_cwd(tmp_path, monkeypatch):
    """No regression for a caller with no window id: name → cwd → newest transcript."""
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", tmp_path / "empty")
    path = _transcript(tmp_path, "solo", 50_000)
    with (
        patch.object(transcripts, "transcript_for_cwd", return_value=path),
        patch("chela.discovery.get_window_cwd", return_value="/solo"),
    ):
        snap = context.live_snapshot("solo")
    assert snap["used_k"] == 50.0 and snap["source"] == "transcript"


def test_zero_setup_fallback_with_an_unidentified_window_still_resolves(tmp_path, monkeypatch):
    """A window whose session nothing can name (no registry, no hook record) still gets
    the transcript estimate its own resolution finds (resolve_window's cwd tier)."""
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", tmp_path / "empty")
    path = _transcript(tmp_path, "solo", 50_000)
    with (
        patch.object(sessions, "panes", return_value={}),
        patch.object(sessions, "session_of_window", return_value=None),
        patch.object(sessions, "transcript_for_window", side_effect=lambda wid, base=None: path),
    ):
        snap = context.live_snapshot("solo", "@3")
    assert snap["used_k"] == 50.0


def test_prune_snapshots_drops_only_stale_session_cache_files(tmp_path, monkeypatch):
    """Through the PUBLIC entry point the daemon calls (``prune_snapshots``), not the
    private helper: one by-session file accrues per session ever run, so retention
    must reach them — a prune that only trims the DB lets them grow forever."""
    by = tmp_path / "context" / "by-session"
    by.mkdir(parents=True)
    monkeypatch.setattr(context, "CONTEXT_CACHE_DIR", tmp_path / "context")
    monkeypatch.setattr(context, "DB_PATH", tmp_path / "scheduler.db")
    old, new = by / f"{BG}.json", by / f"{FG}.json"
    old.write_text("{}")
    new.write_text("{}")
    os.utime(old, (1, 1))
    context.prune_snapshots(30)
    assert not old.exists(), "prune_snapshots left a 30-day-stale by-session cache file"
    assert new.exists()
