"""The markdown tracker source captures a task's FULL multi-line brief, not
just its `- [ ]` bullet line.

Task-detail modal follow-up: `Task.raw` was always just the bullet — the
OBJECTIVE/BOUNDARIES/GUARDS/VERIFY paragraphs indented beneath it in TODO.md
were invisible to everything downstream (the modal, the dispatch critic's
brief-review). `MarkdownSource.tasks_from_text` now also captures `Task.body`:
the title plus the bullet's CONTINUATION BLOCK — every following line that is
blank or indented, stopping at the first non-blank column-0 line (the next
bullet, a `## ` header, or any other top-level text) — dedented by its common
leading indent.

This must not disturb what already worked: `id` is still the hash of the
TITLE LINE alone (chela.dispatcher._strike_merged_tasks and closed_ids_from_
text/strike_lines still match by that same single-line hash — see
tests/test_dispatcher_tracker_strike.py), and `line_number` is still the
bullet's own 1-based line, unaffected by how many continuation lines it eats.
"""
from __future__ import annotations

from pathlib import Path

from chela.sources.markdown import MarkdownSource, _title_id
from chela.workflow import WorkflowDef

TRACKER = "TODO.md"

FIXTURE = """# TODO

## Open

- [ ] **Title one.** Short description.

  **OBJECTIVE.**
  1. Do the first thing.
  2. Do the second thing.

  **BOUNDARIES.** Only `file.py`.

- [ ] bare task with no body

## Backlog

- [ ] next task
"""


def _source(tmp_path: Path) -> MarkdownSource:
    wf = WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"tracker": {"kind": "markdown", "path": TRACKER}},
        prompt_template="",
    )
    return MarkdownSource(wf)


def _id(title: str) -> str:
    return _title_id(TRACKER, title)


def test_body_captures_the_full_multiline_continuation(tmp_path):
    tasks = _source(tmp_path).tasks_from_text(FIXTURE)
    one = next(t for t in tasks if t.title.startswith("**Title one.**"))

    # (a) 🔴 GUARD: stopping at the first BLANK line (the one right after the
    # bullet) instead of the first non-blank COLUMN-0 line would capture zero
    # continuation — body would be missing OBJECTIVE and BOUNDARIES entirely.
    assert "**OBJECTIVE.**" in one.body
    assert "1. Do the first thing." in one.body
    assert "2. Do the second thing." in one.body
    assert "**BOUNDARIES.**" in one.body
    assert "Only `file.py`." in one.body


def test_body_stops_at_the_next_column_zero_line(tmp_path):
    tasks = _source(tmp_path).tasks_from_text(FIXTURE)
    one = next(t for t in tasks if t.title.startswith("**Title one.**"))

    # (b) 🔴 GUARD: a continuation-scan that does not stop at column 0 (e.g.
    # stopping only at a SECOND consecutive blank line, or never stopping
    # short of EOF) would swallow the `## Backlog` header and/or the next
    # task's own bullet into this task's body.
    assert "## Backlog" not in one.body
    assert "bare task with no body" not in one.body
    assert "next task" not in one.body


def test_body_is_dedented_by_its_common_leading_indent(tmp_path):
    tasks = _source(tmp_path).tasks_from_text(FIXTURE)
    one = next(t for t in tasks if t.title.startswith("**Title one.**"))

    # (c) 🔴 GUARD: skipping the dedent step leaves every continuation line's
    # original 2-space indent in place — the modal would render it as
    # (harmless but wrong) leading whitespace inside a markdown paragraph.
    assert "  **OBJECTIVE.**" not in one.body   # the indented form is gone
    assert "\n**OBJECTIVE.**" in one.body        # dedented form is present
    assert one.body == (
        "**Title one.** Short description.\n\n"
        "**OBJECTIVE.**\n"
        "1. Do the first thing.\n"
        "2. Do the second thing.\n\n"
        "**BOUNDARIES.** Only `file.py`."
    )


def test_a_bare_bullet_with_no_indented_body_has_body_none(tmp_path):
    tasks = _source(tmp_path).tasks_from_text(FIXTURE)
    bare = next(t for t in tasks if t.title == "bare task with no body")

    # (d) 🔴 GUARD: a continuation scan that returns "" instead of None for an
    # empty/all-blank capture would make a bare one-line task look like it HAS
    # a (empty) body — the modal's brief>body>raw fallback (taskmodalmodel.js's
    # briefSource) treats '' as absent, but chela.dispatcher._task_brief's
    # `task.body or task.raw` only takes that fallback for a FALSY body, so
    # this must be the real Python `None`, not an empty string.
    assert bare.body is None


def test_ids_and_line_numbers_are_unchanged_by_multiline_capture(tmp_path):
    """(e) 🔴 GUARD: ids/line_numbers must be EXACTLY what the old one-line-only
    parse produced — id is still the hash of the title line alone (so the
    strike/closed-ids match in chela.sources.markdown still matches by the same
    hash — test_dispatcher_tracker_strike.py), and line_number is still the
    bullet's OWN 1-based line, not shifted by however many continuation lines
    it consumed. A parser that advanced the outer loop's line counter past a
    task's continuation block would desync every LATER task's line_number —
    this is exactly what would go RED if `tasks_from_text`'s `enumerate` were
    changed to skip ahead instead of using a pure lookahead."""
    tasks = _source(tmp_path).tasks_from_text(FIXTURE)
    one = next(t for t in tasks if t.title.startswith("**Title one.**"))
    bare = next(t for t in tasks if t.title == "bare task with no body")
    nxt = next(t for t in tasks if t.title == "next task")

    assert one.id == _id("**Title one.** Short description.")
    assert one.line_number == 5

    assert bare.id == _id("bare task with no body")
    assert bare.line_number == 13

    assert nxt.id == _id("next task")
    assert nxt.line_number == 17


def test_body_is_none_for_a_task_with_no_following_lines_at_all(tmp_path):
    """Edge case: the very last line of the file is a bullet with nothing after
    it (not even a trailing blank line) — the lookahead must not index past the
    end of `lines`."""
    text = "- [ ] only task, end of file"
    tasks = _source(tmp_path).tasks_from_text(text)
    assert len(tasks) == 1
    assert tasks[0].body is None


def test_body_none_task_still_round_trips_through_list_open_tasks(tmp_path):
    """Sanity: MarkdownSource.list_open_tasks (the disk-backed entry point, not
    just the pure tasks_from_text) carries `body` through untouched."""
    (tmp_path / TRACKER).write_text(FIXTURE)
    wf = WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"tracker": {"kind": "markdown", "path": TRACKER}},
        prompt_template="",
    )
    tasks = MarkdownSource(wf).list_open_tasks()
    one = next(t for t in tasks if t.title.startswith("**Title one.**"))
    assert one.body is not None
    assert "**OBJECTIVE.**" in one.body
