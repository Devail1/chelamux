"""Tests for the after_create hook firing in dispatcher._spawn.

after_create seeds one-shot per-worktree setup (canonically a least-privilege
`.claude/settings.local.json`) so an agent can launch without disabling the
permission system. Invariants under test:
  - fires exactly once, in the worktree cwd, when the worktree is freshly created
  - does NOT fire when ensure_worktree reused an existing worktree
  - a non-zero exit is a hard dispatch abort (no agent spawned)
  - unset → behaves exactly like today
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher
from chela.sources import Task
from chela.workflow import WorkflowDef

# after_create command exercising {{...}} substitution so the test proves the
# hook can target the freshly-created worktree path.
_HOOK = "echo {{workspace_path}}"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE runs (
            task_id TEXT PRIMARY KEY, workflow_path TEXT, title TEXT, status TEXT,
            window_name TEXT, worktree_path TEXT, branch_name TEXT,
            started_at TEXT, ended_at TEXT, attempt INTEGER, last_error TEXT,
            pr_url TEXT, pr_state TEXT, pr_mergeable TEXT, task_number INTEGER,
            idle_nudged_at TEXT
        )"""
    )
    return conn


def _wf(tmp_path: Path, after_create: str | None) -> WorkflowDef:
    cfg: dict = {"project_key": "TEST"}
    if after_create is not None:
        cfg["hooks"] = {"after_create": after_create}
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md", config=cfg, prompt_template="wt={{workspace_path}}"
    )


def _task(tmp_path: Path) -> Task:
    return Task(
        id="abc123", title="do a thing", file=str(tmp_path / "TODO.md"),
        line_number=7, raw="- [ ] do a thing",
    )


def _shell_calls(run_mock) -> list:
    """subprocess.run calls made with shell=True — i.e. the hooks, not tmux."""
    return [c for c in run_mock.call_args_list if c.kwargs.get("shell") is True]


def _tmux_new_window_called(run_mock) -> bool:
    return any(
        isinstance(c.args[0], list) and "new-window" in c.args[0]
        for c in run_mock.call_args_list
    )


def test_after_create_fires_once_in_worktree_on_fresh_creation(tmp_path):
    wf = _wf(tmp_path, after_create=_HOOK)
    worktree = tmp_path / "wt"
    with patch.object(dispatcher, "ensure_worktree", return_value=(worktree, True)), \
         patch.object(dispatcher.subprocess, "run") as run, \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        dispatcher._spawn(wf, _task(tmp_path), attempt=1, conn=_conn())

    hook_calls = _shell_calls(run)
    assert len(hook_calls) == 1
    call = hook_calls[0]
    # Rendered with the worktree path, run in the worktree cwd, abort-on-failure.
    assert call.args[0] == f"echo {worktree}"
    assert call.kwargs["cwd"] == worktree
    assert call.kwargs["check"] is True


def test_after_create_skipped_on_worktree_reuse(tmp_path):
    wf = _wf(tmp_path, after_create=_HOOK)
    worktree = tmp_path / "wt"
    with patch.object(dispatcher, "ensure_worktree", return_value=(worktree, False)), \
         patch.object(dispatcher.subprocess, "run") as run, \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        dispatcher._spawn(wf, _task(tmp_path), attempt=2, conn=_conn())

    # Reused worktree → the hook must not run, but dispatch still proceeds.
    assert _shell_calls(run) == []
    assert _tmux_new_window_called(run)


def test_after_create_nonzero_exit_aborts_dispatch(tmp_path):
    wf = _wf(tmp_path, after_create=_HOOK)
    worktree = tmp_path / "wt"

    def _run(cmd, *a, **kw):
        if kw.get("shell"):
            raise subprocess.CalledProcessError(returncode=1, cmd=cmd)
        return None

    with patch.object(dispatcher, "ensure_worktree", return_value=(worktree, True)), \
         patch.object(dispatcher.subprocess, "run", side_effect=_run) as run, \
         patch.object(dispatcher, "send_tmux") as send, \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        with pytest.raises(subprocess.CalledProcessError):
            dispatcher._spawn(wf, _task(tmp_path), attempt=1, conn=_conn())

    # Aborted before the agent was ever spawned.
    assert not _tmux_new_window_called(run)
    send.assert_not_called()


def test_after_create_unset_is_noop(tmp_path):
    wf = _wf(tmp_path, after_create=None)
    worktree = tmp_path / "wt"
    with patch.object(dispatcher, "ensure_worktree", return_value=(worktree, True)), \
         patch.object(dispatcher.subprocess, "run") as run, \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        dispatcher._spawn(wf, _task(tmp_path), attempt=1, conn=_conn())

    # No hook configured → no shell command, dispatch proceeds normally.
    assert _shell_calls(run) == []
    assert _tmux_new_window_called(run)
