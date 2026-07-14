"""Tests for seed-delivery confirmation in the dispatcher.

Regression cover: the dispatcher waited for the ready glyph and then pasted the
seed prompt — but a late boot-splash redraw (e.g. the "MCP servers need
authentication" line landing *after* `❯`) swallows the paste, leaving the agent
idle with no task until the reconcile watchdog notices minutes later.

The fix: after sending, poll the agent's native session status. Accepting a
prompt flips it idle → busy, so a still-idle agent means the paste was dropped
and the seed is re-sent (capped), instead of hoping the delay was enough.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from chela import dispatcher
from chela.sources import Task
from chela.workflow import WorkflowDef


def _conn() -> sqlite3.Connection:
    # The PRODUCTION schema, not a hand-copy of it (see dispatcher.ensure_schema).
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _wf(tmp_path: Path) -> WorkflowDef:
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "TEST"},
        prompt_template="go {{workspace_path}}",
    )


def _task(tmp_path: Path) -> Task:
    return Task(
        id="abc123", title="do a thing", file=str(tmp_path / "TODO.md"),
        line_number=7, raw="- [ ] do a thing",
    )


# --- _seed_landed: the idle → busy transition is the delivery receipt --------


def test_seed_landed_true_when_agent_goes_busy():
    """A freshly-booted agent reads idle until the seed makes it busy; the poll
    watches for that transition (not for "is it idle right now")."""
    statuses = iter(["idle", "idle", "busy"])
    with patch.object(dispatcher, "_agent_status", side_effect=lambda _w: next(statuses)), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._seed_landed("@1", timeout=10, poll=0.01) is True


def test_seed_landed_false_when_agent_stays_idle():
    """Still idle when the window closes → the paste was swallowed."""
    with patch.object(dispatcher, "_agent_status", return_value="idle"), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._seed_landed("@1", timeout=0.02, poll=0.01) is False


def test_seed_landed_none_when_status_unreadable():
    """No claude pid / no session listing → unverifiable, NOT "idle"."""
    with patch.object(dispatcher, "_agent_status", return_value=None):
        assert dispatcher._seed_landed("@1", timeout=1, poll=0.01) is None


def test_agent_status_reads_the_native_session_map():
    from chela import agent_manager

    with patch.object(agent_manager, "claude_pid", return_value=4242), \
         patch.object(agent_manager, "session_status_map", return_value={"by_pid": {4242: "busy"}}) as m:
        assert dispatcher._agent_status("@7") == "busy"
    m.assert_called_once_with(force=True)  # cached map would miss the transition

    with patch.object(agent_manager, "claude_pid", return_value=None):
        assert dispatcher._agent_status("@7") is None


# --- _send_seed: confirm, re-send on a dropped paste, cap the retries --------


def test_send_seed_stops_after_confirmation():
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "_seed_landed", return_value=True), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is True
    assert send.call_count == 1


def test_send_seed_resends_until_the_agent_goes_busy():
    landed = iter([False, True])
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "_seed_landed", side_effect=lambda _w: next(landed)), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is True
    assert send.call_count == 2
    # The re-send is the same full seed, through the same paste-buffer path.
    assert [c.args for c in send.call_args_list] == [("@1", "prompt")] * 2


def test_send_seed_caps_retries_on_a_dead_agent():
    """A genuinely-broken agent must not spin forever: cap the sends, log, and
    leave it to the reconcile watchdog."""
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "_seed_landed", return_value=False), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is True
    assert send.call_count == dispatcher.SEED_MAX_SENDS


def test_send_seed_fails_open_when_status_is_unreadable():
    """Unverifiable status must not trigger a blind duplicate paste."""
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "_seed_landed", return_value=None), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is True
    assert send.call_count == 1


def test_send_seed_reports_a_failed_send():
    with patch.object(dispatcher, "send_tmux", return_value=False) as send, \
         patch.object(dispatcher, "_seed_landed") as landed:
        assert dispatcher._send_seed("@1", "prompt", "abc") is False
    assert send.call_count == 1
    landed.assert_not_called()


# --- _spawn wires it in ------------------------------------------------------


def test_spawn_resends_a_dropped_seed(tmp_path):
    """End-to-end through _spawn: the agent never goes busy, so the seed is
    re-sent up to the cap and the run still ends up `running` (the watchdog owns
    it from here)."""
    wf, task = _wf(tmp_path), _task(tmp_path)
    conn = _conn()
    with patch.object(dispatcher, "ensure_worktree", return_value=(tmp_path / "wt", False)), \
         patch.object(dispatcher.subprocess, "run"), \
         patch.object(dispatcher, "_kill_windows_named"), \
         patch.object(dispatcher, "_new_window", return_value="@9"), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True), \
         patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "_seed_landed", return_value=False), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._spawn(wf, task, attempt=1, conn=conn) is True

    assert send.call_count == dispatcher.SEED_MAX_SENDS
    assert all(c.args[0] == "@9" for c in send.call_args_list)
    row = conn.execute("SELECT status FROM runs WHERE task_id=?", (task.id,)).fetchone()
    assert row["status"] == "running"
