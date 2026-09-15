"""issue #504 / SPEC 5.4: render_prompt must FAIL on an unknown {{var}} instead of
shipping it verbatim.

Before this, `render_prompt` was a bare substitution loop with no failure path: a
misspelled `{{taks_title}}` reached the dispatched agent as those literal characters,
silently, with no signal anywhere that the brief had a hole in it.

Two cases matter here, and the second is the one a suppress-only test misses (an
implementation that just refuses every template would still pass a test that only
covers the first case):
  1. an unprovided {{var}} fails the render, naming the offending variable;
  2. a template using ONLY provided variables still renders and dispatches normally.

Plus SPEC 5.5's blast radius: a bad template fails the ONE run attempt being
rendered, never the whole dispatch/watchdog/judge tick.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher
from chela.sources import Task
from chela.workflow import TemplateRenderError, WorkflowDef, render_prompt


# --- render_prompt itself -----------------------------------------------------------

def test_render_prompt_accepts_a_template_using_only_provided_vars():
    out = render_prompt(
        "Task {{task_id}}: {{task_title}} in {{workspace_path}}",
        {"task_id": "abc123", "task_title": "do a thing", "workspace_path": "/wt"},
    )
    assert out == "Task abc123: do a thing in /wt"


def test_render_prompt_fails_on_an_unprovided_var():
    with pytest.raises(TemplateRenderError) as exc:
        render_prompt("Task {{taks_title}}", {"task_title": "do a thing"})
    # the offending name must be IN the error, not just "something is wrong"
    assert "taks_title" in str(exc.value)


def test_render_prompt_names_every_unknown_var_not_just_the_first():
    with pytest.raises(TemplateRenderError) as exc:
        render_prompt("{{foo}} and {{bar}}", {})
    assert "foo" in str(exc.value)
    assert "bar" in str(exc.value)


def test_render_prompt_extra_provided_vars_are_fine():
    # A vars map is a superset of what the template references all the time (e.g. the
    # same hook_vars map renders both the prompt and an after_create shell command) —
    # only an UNRESOLVED reference in the template is an error, never an unused key.
    out = render_prompt("{{task_id}}", {"task_id": "abc123", "unused": "whatever"})
    assert out == "abc123"


def test_render_prompt_does_not_flag_prose_that_only_looks_like_the_syntax():
    # A WORKFLOW.md documenting the syntax to its own reader — spaces or punctuation
    # inside the braces — must not trip a naive "any {{...}}" check.
    out = render_prompt("wrap a var like {{ this }}, e.g. {{task_id}}", {"task_id": "x"})
    assert out == "wrap a var like {{ this }}, e.g. x"


# --- callers: a bad template fails only the ONE run attempt --------------------------

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _wf(tmp_path: Path, prompt_template: str) -> WorkflowDef:
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "TEST"},
        prompt_template=prompt_template,
    )


def _task(tmp_path: Path) -> Task:
    return Task(
        id="abc123", title="do a thing", file=str(tmp_path / "TODO.md"),
        line_number=7, raw="- [ ] do a thing",
    )


def test_spawn_raises_rather_than_shipping_the_broken_prompt(tmp_path):
    # `_spawn` itself does not swallow the error — its CALLER (`tick`'s dispatch loop)
    # is what turns this into a `failed` row for just this one task, exactly like any
    # other dispatch exception (git failure, tmux failure, …). Proving `_spawn` still
    # raises here is proving the broken prompt is never silently handed to `_launch_agent`.
    wf = _wf(tmp_path, "go {{taks_title}}")
    conn = _conn()
    with patch.object(dispatcher, "ensure_worktree", return_value=(tmp_path / "wt", True)), \
         pytest.raises(TemplateRenderError, match="taks_title"):
        dispatcher._spawn(wf, _task(tmp_path), attempt=1, conn=conn)
    # the claim (INSERT ... 'claimed') happened before the render, exactly as it does for
    # any other mid-spawn failure — the caller's generic `except Exception` (unmodified by
    # this change) is what flips this row to `failed` in production.
    row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["status"] == "claimed"


def _row(conn: sqlite3.Connection, tmp_path: Path, **over) -> sqlite3.Row:
    fields = {
        "task_id": "abc123", "workflow_path": str(tmp_path / "WORKFLOW.md"),
        "title": "do a thing", "status": "running", "window_name": "test-1",
        "branch_name": "test-1", "worktree_path": str(tmp_path / "wt"),
        "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1, "task_number": 1,
        "rework_count": 0,
    }
    fields.update(over)
    conn.execute(
        f"INSERT INTO runs ({', '.join(fields)}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (fields["task_id"],)).fetchone()


def test_renudge_prompt_raises_on_a_first_dispatch_template_with_an_unknown_var(tmp_path):
    # `_renudge_prompt` is called from the watchdog's re-nudge branch with no try/except
    # of its own around IT — the guard lives at that call site (`tick`), which now catches
    # exactly this and fails only the one stuck run instead of crashing the whole watchdog
    # pass. This pins that `_renudge_prompt` still raises, so that guard has something
    # real to catch.
    wf = _wf(tmp_path, "go {{taks_title}}")
    conn = _conn()
    row = _row(conn, tmp_path, rework_count=0)
    with pytest.raises(TemplateRenderError, match="taks_title"):
        dispatcher._renudge_prompt(wf, row, _task(tmp_path))


def test_renudge_prompt_raises_on_a_rework_template_with_an_unknown_var(tmp_path):
    wf = _wf(tmp_path, "go {{task_title}}")
    conn = _conn()
    row = _row(
        conn, tmp_path, rework_count=1, pr_url="https://github.com/o/r/pull/1",
        review_history='[{"round": 1, "at": "t", "body": "fix it"}]',
    )
    with patch.object(
        dispatcher, "REWORK_PROMPT", "round {{rework_round}}: {{not_a_real_var}}"
    ), pytest.raises(TemplateRenderError, match="not_a_real_var"):
        dispatcher._renudge_prompt(wf, row, None)
