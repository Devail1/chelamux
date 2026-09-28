"""CMX-378: a dispatched agent never saw its TODO item's continuation block.

`_prompt_vars` (spawn), `dry_run`'s own vars map, and `_rework_vars` (rework) rendered only
`{{task_title}}` — the bullet line — into WORKFLOW.md / REWORK_PROMPT. `Task.body` (the
markdown source's title + its dedented OBJECTIVE/BOUNDARIES/GUARDS/VERIFY continuation) sat
on `runs.brief` for the task-detail modal, but no `{{task_body}}` var ever carried it into a
prompt an agent actually reads.

These pin the fix: a `task_body` var (`task.body or ""`, never the literal `None`) now
reaches the real WORKFLOW.md (both copies), `dispatcher.dry_run`'s preview, and the rework
prompt — and a bare one-line task (no continuation block, `task.body is None`) still renders
cleanly with an empty body, never a crash and never the string "None".
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from chela import dispatcher
from chela.sources import Task
from chela.workflow import WorkflowDef, load_workflow, render_prompt

_REPO_ROOT = Path(__file__).resolve().parent.parent

_DISTINCTIVE_LINE = "OBJECTIVE. Reticulate the xyzzy987 splines before touching anything else."


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    """A runs DB per test — ``dispatcher.DB_PATH`` is latched at import, so without this
    every test in the session would share one scheduler.db (see conftest.py)."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _multiline_task(**over) -> Task:
    fields = dict(
        id="abc123", title="short title", file="TODO.md", line_number=3,
        raw="- [ ] short title", body=f"short title\n\n{_DISTINCTIVE_LINE}",
    )
    fields.update(over)
    return Task(**fields)


def _bare_task(**over) -> Task:
    fields = dict(
        id="abc123", title="short title", file="TODO.md", line_number=3,
        raw="- [ ] short title",
    )
    fields.update(over)
    return Task(**fields)


# --- spawn: the real WORKFLOW.md this repo dispatches with ---------------------------

def test_the_real_workflow_md_renders_the_multiline_task_body():
    wf = load_workflow(_REPO_ROOT / "WORKFLOW.md")
    prompt = render_prompt(
        wf.prompt_template,
        dispatcher._prompt_vars(wf, _multiline_task(), "/wt", "cmx-1", "dev", 1),
    )
    assert _DISTINCTIVE_LINE in prompt


def test_the_real_workflow_md_renders_cleanly_for_a_bare_one_line_task():
    """The ACCEPTED case: no continuation block at all (`task.body is None`) — the prompt
    must still render (no crash) and must never leak the literal word "None" where the
    body would otherwise go."""
    wf = load_workflow(_REPO_ROOT / "WORKFLOW.md")
    prompt = render_prompt(
        wf.prompt_template,
        dispatcher._prompt_vars(wf, _bare_task(), "/wt", "cmx-1", "dev", 1),
    )
    assert "> short title" in prompt
    assert "None" not in prompt


def test_examples_workflow_md_also_renders_the_task_body():
    """The adopter-facing copy under `examples/` carries the identical fix."""
    wf = load_workflow(_REPO_ROOT / "examples" / "WORKFLOW.md")
    prompt = render_prompt(
        wf.prompt_template,
        dispatcher._prompt_vars(wf, _multiline_task(), "/wt", "cmx-1", "dev", 1),
    )
    assert _DISTINCTIVE_LINE in prompt


# --- dry_run: the CLI preview a human actually runs (this task's own VERIFY step) -----

def test_dry_run_shows_the_multiline_body_for_a_real_multiline_TODO_item(tmp_path):
    """`chela dispatch ./WORKFLOW.md --dry-run` on a multi-line item must show the body in
    the printed prompt — the exact manual check this task names."""
    (tmp_path / "WORKFLOW.md").write_text((_REPO_ROOT / "WORKFLOW.md").read_text())
    (tmp_path / "TODO.md").write_text(f"- [ ] short title\n\n  {_DISTINCTIVE_LINE}\n")

    plans = dispatcher.dry_run(tmp_path / "WORKFLOW.md")

    assert len(plans) == 1
    assert _DISTINCTIVE_LINE in plans[0]["prompt"]


def test_dry_run_renders_cleanly_for_a_bare_one_line_TODO_item(tmp_path):
    (tmp_path / "WORKFLOW.md").write_text((_REPO_ROOT / "WORKFLOW.md").read_text())
    (tmp_path / "TODO.md").write_text("- [ ] short title\n")

    plans = dispatcher.dry_run(tmp_path / "WORKFLOW.md")

    assert len(plans) == 1
    assert "> short title" in plans[0]["prompt"]
    assert "None" not in plans[0]["prompt"]


# --- rework: an agent sent back for changes still sees what it was originally asked ---

def _rework_wf() -> WorkflowDef:
    return WorkflowDef(
        path=Path("/repo/WORKFLOW.md"), config={"project_key": "TEST"},
        prompt_template=dispatcher.REWORK_PROMPT,
    )


def _rework_row(conn: sqlite3.Connection, task_id="abc123", **over) -> sqlite3.Row:
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "short title",
        "status": "changes_requested", "window_name": "test-1", "worktree_path": "/wt/abc123",
        "branch_name": "test-1", "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/1", "pr_state": "open",
        "rework_count": 0, "review_history": None,
    }
    fields.update(over)
    conn.execute(
        f"INSERT INTO runs ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def test_rework_prompt_renders_the_original_task_body():
    conn = _conn()
    row = _rework_row(conn)
    vars_ = dispatcher._rework_vars(
        _rework_wf(), row, "/wt/abc123", "fix the thing", 1, None, _multiline_task(),
    )
    prompt = render_prompt(dispatcher.REWORK_PROMPT, vars_)
    assert _DISTINCTIVE_LINE in prompt


def test_rework_prompt_renders_cleanly_with_no_task_looked_up():
    """`task=None` — the watchdog re-nudge's legacy shape (no `tasks_by_id` hit). Same
    ACCEPTED empty-body contract as the fresh-dispatch path: no crash, never "None"."""
    conn = _conn()
    row = _rework_row(conn)
    vars_ = dispatcher._rework_vars(
        _rework_wf(), row, "/wt/abc123", "fix the thing", 1, None, None,
    )
    prompt = render_prompt(dispatcher.REWORK_PROMPT, vars_)
    assert "None" not in prompt
