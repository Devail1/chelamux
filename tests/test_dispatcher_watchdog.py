"""The reconcile watchdog must not false-fail a working agent, and must unblock a
dispatched agent stranded on an input dialog (2026-07-23 dispatcher hardening).

Two bugs made the 07-23 dogfood batch a babysitting exercise:

  (1) FALSE FAILURE. The Claude Code TUI paints its working spinner + a live
      "(<elapsed> · ↓ <n> tokens)" status line ABOVE the input box, and the box
      below stays an empty `❯` while it generates — so `_pane_idle_empty_prompt`
      returns True for an agent that is plainly still working. With only a
      "not busy" cross-check (which an unreadable status passes), the watchdog
      marked cmx-158/159/160 `failed` mid-work, after they had opened their PRs.

  (2) HUNG ON A DIALOG. A dispatched agent that calls `AskUserQuestion` (or hits a
      gated permission prompt) goes native-status "waiting" and shows a picker,
      not a bare `❯` — so the idle check never fired and it hung to MAX_ATTEMPTS
      with no human to answer it.

These pin the fixes at `tick()`'s watchdog: an activity veto + an affirmative-idle
requirement for (1), and an Escape-then-fail recovery for (2).
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

import pytest

from chela import dispatcher
from chela.workflow import WorkflowDef

WORKFLOW = """---
project_key: CMX
tracker:
  kind: markdown
  path: TODO.md
workspace:
  root: {root}
  base_branch: dev
concurrency:
  max: 1
---
seed
"""

WID = "cmx-1"

# The real false-failure pane: the working spinner + token counter sit ABOVE an
# EMPTY `❯` input box, so it trips `_pane_idle_empty_prompt` even though the agent
# is generating. This is exactly what a mid-work agent's pane looks like.
_WORKING_WITH_EMPTY_PROMPT = (
    "⏺ Editing dispatcher.py…\n"
    "✽ Puzzling… (6m 9s · ↓ 17.2k tokens)\n"
    "╭───────────────────────────────────╮\n"
    "│ ❯                                 │\n"
    "╰───────────────────────────────────╯\n"
)
# A genuinely stalled agent: a bare empty prompt, no spinner, no counter.
_BARE_IDLE = (
    "╭───────────────────────────────────╮\n"
    "│ ❯                                 │\n"
    "╰───────────────────────────────────╯\n"
)
# An AskUserQuestion picker — NOT a bare `❯`, so the idle check can't see it.
_QUESTION = (
    "╭─ Which approach? ─────────────────╮\n"
    "│ ❯ 1. Option A                     │\n"
    "│   2. Option B                     │\n"
    "╰───────────────────────────────────╯\n"
)

_OLD = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


@pytest.fixture
def ticking(tmp_path, monkeypatch):
    """A repo whose WORKFLOW.md drives a real tick(), tmux/gh/spawn stubbed."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(origin)], check=True, capture_output=True)
    repo = tmp_path / "work"
    subprocess.run(["git", "clone", str(origin), str(repo)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        subprocess.run(["git", "-C", str(repo), "config", k, v], check=True, capture_output=True)
    (repo / "TODO.md").write_text("- [ ] alpha\n")
    (repo / "WORKFLOW.md").write_text(WORKFLOW.format(root=tmp_path / ".chela" / "worktrees"))
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "seed"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "push", "-u", "origin", "dev"], check=True, capture_output=True)

    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "_tmux_windows", lambda: {WID})
    monkeypatch.setattr(dispatcher, "_kill_window", lambda name: None)
    monkeypatch.setattr(dispatcher, "_fire_after_done", lambda wf: None)
    monkeypatch.setattr(dispatcher, "_spawn", lambda *a, **kw: False)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **kw: True)
    return repo


def _wf(repo: Path) -> WorkflowDef:
    from chela.workflow import load_workflow
    return load_workflow(repo / "WORKFLOW.md")


def _seed_running(repo: Path, *, nudged: str | None) -> str:
    """A `running` first-dispatch row for the tracker's one open task, aged past
    the watchdog window; `nudged` set (and old) puts it at the terminal-fail step."""
    from chela.sources.markdown import MarkdownSource
    task_id = next(t.id for t in MarkdownSource(_wf(repo)).list_open_tasks())
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "branch_name, started_at, idle_nudged_at, attempt, pr_state) "
            "VALUES (?,?,?,'running',?,?,?,?,1,'open')",
            (task_id, str(repo / "WORKFLOW.md"), "alpha", WID, "cmx-1", _OLD, nudged),
        )
        conn.commit()
    return task_id


def _status_of(task_id: str) -> tuple[str, str | None]:
    with dispatcher._db() as conn:
        r = conn.execute(
            "SELECT status, last_error FROM runs WHERE task_id=?", (task_id,)
        ).fetchone()
    return r["status"], r["last_error"]


# ---- (1) false-failure guards ----------------------------------------------

def test_working_agent_is_never_failed_even_at_the_fail_step(ticking, monkeypatch):
    """The load-bearing guard: a pane showing the working spinner (above an empty
    prompt) must NOT be failed, even with an affirmative 'idle' status and both
    graces elapsed. Remove the activity veto → this run is failed → RED."""
    repo = ticking
    task_id = _seed_running(repo, nudged=_OLD)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w: _WORKING_WITH_EMPTY_PROMPT)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w: "idle")

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 0
    assert _status_of(task_id)[0] == "running"  # left alone — it's working


def test_unreadable_status_does_not_drive_a_terminal_fail(ticking, monkeypatch):
    """A bare-idle pane with an UNREADABLE status (None) must re-nudge, not fail —
    None is not evidence of idleness. Fail on `status != 'busy'` instead → RED."""
    repo = ticking
    task_id = _seed_running(repo, nudged=_OLD)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w: _BARE_IDLE)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w: None)

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 0
    assert summary["watchdog_renudged"] == 1  # re-nudged instead of killed
    assert _status_of(task_id)[0] == "running"


def test_genuinely_idle_agent_still_fails(ticking, monkeypatch):
    """The real stall path is intact: a bare-idle pane, affirmatively 'idle', past
    both graces → failed into the re-dispatch path."""
    repo = ticking
    task_id = _seed_running(repo, nudged=_OLD)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w: _BARE_IDLE)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w: "idle")

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 1
    status, err = _status_of(task_id)
    assert status == "failed"
    assert "idle at empty prompt" in err


# ---- (2) input-dialog (AskUserQuestion) recovery ---------------------------

def test_waiting_agent_is_escaped_not_failed_on_first_encounter(ticking, monkeypatch):
    """A dispatched agent blocked on a dialog (status 'waiting') is Escaped so it
    falls back to its own default — not failed, not ignored. Drop the waiting
    branch and it falls through to the idle check, which can't see a dialog pane →
    no Escape → RED."""
    repo = ticking
    task_id = _seed_running(repo, nudged=None)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w: _QUESTION)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w: "waiting")
    dismiss = Mock()
    monkeypatch.setattr(dispatcher, "_dismiss_input_block", dismiss)

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    dismiss.assert_called_once_with(WID)
    assert summary["watchdog_unblocked"] == 1
    assert summary["reconciled_failed"] == 0
    assert _status_of(task_id)[0] == "running"


def test_waiting_agent_that_does_not_recover_is_failed(ticking, monkeypatch):
    """Still 'waiting' a full grace after the Escape → it isn't recovering, so it
    fails into re-dispatch rather than hanging forever."""
    repo = ticking
    task_id = _seed_running(repo, nudged=_OLD)
    monkeypatch.setattr(dispatcher, "_capture_pane", lambda w: _QUESTION)
    monkeypatch.setattr(dispatcher, "_agent_status", lambda w: "waiting")
    monkeypatch.setattr(dispatcher, "_dismiss_input_block", Mock())

    summary = dispatcher.tick(repo / "WORKFLOW.md")

    assert summary["reconciled_failed"] == 1
    status, err = _status_of(task_id)
    assert status == "failed"
    assert "blocked on an input dialog" in err


# ---- unit guards for the two helpers ---------------------------------------

def test_pane_shows_activity_detects_spinner_and_counter():
    assert dispatcher._pane_shows_activity(_WORKING_WITH_EMPTY_PROMPT)
    assert dispatcher._pane_shows_activity("✻ Sautéed for 1m 22s")
    assert dispatcher._pane_shows_activity("  ✶ Working… (esc to interrupt)")
    # A bare idle prompt or a static dialog shows no work signal.
    assert not dispatcher._pane_shows_activity(_BARE_IDLE)
    assert not dispatcher._pane_shows_activity(_QUESTION)


def test_pane_shows_login_expired_detects_the_banner():
    banner = "✽ Sonnet 5\n\nLogin expired · Please run /login\n\n❯ \n"
    assert dispatcher._pane_shows_login_expired(banner)
    # Ordinary idle/working panes carry no such text.
    assert not dispatcher._pane_shows_login_expired(_BARE_IDLE)
    assert not dispatcher._pane_shows_login_expired(_WORKING_WITH_EMPTY_PROMPT)
    assert not dispatcher._pane_shows_login_expired(_QUESTION)


def test_dismiss_input_block_sends_escape(monkeypatch):
    run = Mock()
    monkeypatch.setattr(dispatcher.subprocess, "run", run)
    dispatcher._dismiss_input_block(WID)
    args = run.call_args.args[0]
    assert args[:3] == ["tmux", "send-keys", "-t"]
    assert args[-1] == "Escape"


def test_dismiss_input_block_never_raises(monkeypatch):
    monkeypatch.setattr(
        dispatcher.subprocess, "run", Mock(side_effect=OSError("no tmux"))
    )
    dispatcher._dismiss_input_block(WID)  # must not raise
