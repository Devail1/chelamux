"""Tests for seed-delivery confirmation in the dispatcher.

Regression cover: the dispatcher waited for the ready glyph and then pasted the
seed prompt — but a late boot-splash redraw (e.g. an MCP-auth notice, or
`gh auth login for PR status`, landing *after* `❯`) swallows the separately-sent
Enter, stranding the pasted prompt on the input line unsubmitted until the
reconcile watchdog notices minutes later.

The fix: after sending, poll the agent's native session status. Accepting a
prompt flips it idle → busy, so a still-idle agent means the Enter was dropped
and it is re-sent on its own (capped) — never a full re-paste, which would type
the still-sitting prompt text a second time on top of itself.
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


# --- _send_seed: confirm, re-send Enter (not the paste), cap the retries -----


def test_send_seed_stops_after_confirmation():
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "resend_enter") as enter, \
         patch.object(dispatcher, "_seed_landed", return_value=True), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is True
    assert send.call_count == 1
    enter.assert_not_called()


def test_send_seed_resends_enter_only_until_the_agent_goes_busy():
    """A dropped Enter (paste landed, redraw ate the submit) must be retried by
    re-sending Enter alone — a second full send_tmux would type the still-sitting
    prompt text a second time on top of itself."""
    landed = iter([False, True])
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "resend_enter", return_value=True) as enter, \
         patch.object(dispatcher, "_seed_landed", side_effect=lambda _w: next(landed)), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is True
    assert send.call_count == 1  # the prompt itself is pasted exactly once
    assert send.call_args.args == ("@1", "prompt")
    enter.assert_called_once_with("@1")


def test_send_seed_caps_retries_on_a_dead_agent():
    """A genuinely-broken agent must not spin forever: cap the sends, log, and
    leave it to the reconcile watchdog."""
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "resend_enter", return_value=True) as enter, \
         patch.object(dispatcher, "_seed_landed", return_value=False), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is True
    assert send.call_count == 1
    assert enter.call_count == dispatcher.SEED_MAX_SENDS - 1


def test_send_seed_does_not_fail_open_on_unreadable_status():
    """`_seed_landed` → None (status UNREADABLE — exactly what a window mid-redraw
    returns, the moment a startup notice ate the Enter) must NOT be treated as
    landed. The old code failed open here ("assuming the seed landed") and stranded
    the seed unsubmitted — the residual that bit CMX-133. It must re-send Enter and
    only succeed once the agent actually goes busy. Restore the fail-open → RED."""
    landed = iter([None, True])
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "resend_enter", return_value=True) as enter, \
         patch.object(dispatcher, "_seed_landed", side_effect=lambda _w: next(landed)), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is True
    assert send.call_count == 1              # pasted exactly once
    enter.assert_called_once_with("@1")      # the None triggered an Enter resend, not a fail-open return


def test_send_seed_resends_enter_not_paste_on_persistently_unreadable_status():
    """Persistently unverifiable status (None every poll — a window stuck mid-redraw)
    must NOT fail open (the old bug that stranded CMX-133) and must NOT blindly
    re-PASTE (which would duplicate the prompt). It re-sends ENTER only, capped, then
    leaves a still-dead window to the reconcile watchdog. Restore the fail-open → RED
    (enter would then never be called)."""
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "resend_enter", return_value=True) as enter, \
         patch.object(dispatcher, "_seed_landed", return_value=None), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is True
    assert send.call_count == 1                               # never re-pastes
    assert enter.call_count == dispatcher.SEED_MAX_SENDS - 1  # re-sends Enter, capped


def test_send_seed_reports_a_failed_send():
    with patch.object(dispatcher, "send_tmux", return_value=False) as send, \
         patch.object(dispatcher, "_seed_landed") as landed:
        assert dispatcher._send_seed("@1", "prompt", "abc") is False
    assert send.call_count == 1
    landed.assert_not_called()


def test_send_seed_stops_when_a_dropped_enter_resend_fails():
    """resend_enter failing (tmux error) must fail the send, not spin forever
    treating the failure as another unconfirmed-landing retry."""
    with patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "resend_enter", return_value=False) as enter, \
         patch.object(dispatcher, "_seed_landed", return_value=False), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._send_seed("@1", "prompt", "abc") is False
    assert send.call_count == 1
    enter.assert_called_once_with("@1")


# --- _spawn wires it in ------------------------------------------------------


def test_spawn_resends_a_dropped_seed(tmp_path):
    """End-to-end through _spawn: the agent never goes busy, so the seed's Enter
    is re-sent up to the cap and the run still ends up `running` (the watchdog
    owns it from here)."""
    wf, task = _wf(tmp_path), _task(tmp_path)
    conn = _conn()
    with patch.object(dispatcher, "ensure_worktree", return_value=(tmp_path / "wt", False)), \
         patch.object(dispatcher.subprocess, "run"), \
         patch.object(dispatcher, "_kill_windows_named"), \
         patch.object(dispatcher, "_new_window", return_value="@9"), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True), \
         patch.object(dispatcher, "send_tmux", return_value=True) as send, \
         patch.object(dispatcher, "resend_enter", return_value=True) as enter, \
         patch.object(dispatcher, "_seed_landed", return_value=False), \
         patch.object(dispatcher.time, "sleep"):
        assert dispatcher._spawn(wf, task, attempt=1, conn=conn) is True

    # The prompt is pasted exactly once; every retry after that re-sends Enter only.
    assert send.call_count == 1
    assert send.call_args.args[0] == "@9"
    assert enter.call_count == dispatcher.SEED_MAX_SENDS - 1
    assert all(c.args[0] == "@9" for c in enter.call_args_list)
    row = conn.execute("SELECT status FROM runs WHERE task_id=?", (task.id,)).fetchone()
    assert row["status"] == "running"
