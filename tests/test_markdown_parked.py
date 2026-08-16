"""`MarkdownSource.parked_tasks_from_text` surfaces the PARKED
(`<!-- blocked: ... -->`) bullets that `tasks_from_text` deliberately skips.

Before this, a parked bullet was invisible everywhere: `tasks_from_text`
already excludes it from Open (see tests/test_markdown_depends.py's
`test_depends_does_not_disturb_the_blocked_marker`), and it lives in TODO.md
rather than BACKLOG.md, so it never reached the dashboard's Backlog lane
either — a blocked ticket sat in the tracker with the board showing 0 for it
(Liav, 2026-08-12: "should we see parked in backlog?"). This module tests the
PARSE only; chela.dashboard.app wires it into the `/api/dispatcher` payload
and tests/kanban_flatten.test.mjs pins the render.
"""
from __future__ import annotations

from pathlib import Path

from chela.sources.markdown import MarkdownSource, _title_id
from chela.workflow import WorkflowDef

TRACKER = "TODO.md"


def _source(tmp_path: Path) -> MarkdownSource:
    wf = WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"tracker": {"kind": "markdown", "path": TRACKER}},
        prompt_template="",
    )
    return MarkdownSource(wf)


def _id(title: str) -> str:
    return _title_id(TRACKER, title)


def test_a_plain_open_bullet_is_not_parked(tmp_path):
    tasks = _source(tmp_path).parked_tasks_from_text("- [ ] a plain task\n")
    assert tasks == []


def test_a_blocked_bullet_is_returned_by_parked_tasks_from_text(tmp_path):
    text = "- [ ] a task <!-- blocked: waiting on fixtures -->\n"
    parked = _source(tmp_path).parked_tasks_from_text(text)
    assert len(parked) == 1
    assert parked[0].title == "a task"


def test_a_blocked_bullet_still_stays_out_of_tasks_from_text(tmp_path):
    # 🔴 GUARD: this feature must not change tasks_from_text's own behaviour — a
    # parked bullet becoming claimable again would be a much worse regression than
    # the invisibility bug this fixes.
    text = "- [ ] a task <!-- blocked: waiting on fixtures -->\n"
    src = _source(tmp_path)
    assert src.tasks_from_text(text) == []
    assert len(src.parked_tasks_from_text(text)) == 1


def test_the_blocked_reason_is_captured_verbatim(tmp_path):
    text = "- [ ] a task <!-- blocked: waiting on fixtures -->\n"
    parked = _source(tmp_path).parked_tasks_from_text(text)
    assert parked[0].body == "waiting on fixtures"


def test_a_bare_blocked_marker_with_no_reason_yields_no_body(tmp_path):
    # `<!-- blocked -->` alone (no colon) is valid per BLOCKED_RE — it just has no
    # reason text to show.
    text = "- [ ] a task <!-- blocked -->\n"
    parked = _source(tmp_path).parked_tasks_from_text(text)
    assert len(parked) == 1
    assert parked[0].body is None


def test_the_title_and_id_are_the_bare_title_not_the_raw_marker_attached_line(tmp_path):
    # 🔴 GUARD: a human names this task via its bare visible title in a `depends:`
    # marker elsewhere in the tracker (see chela.runtime_truth._parked_ids_from_text) —
    # never the raw bullet with its own comment attached. If the id hashed off the
    # raw title (comment included), a `depends: "a task"` reference elsewhere in the
    # SAME tracker would resolve to a different id than this parked task's own, and
    # look like a broken reference forever.
    text = "- [ ] a task <!-- blocked: waiting on fixtures -->\n"
    parked = _source(tmp_path).parked_tasks_from_text(text)
    assert parked[0].title == "a task"
    assert parked[0].id == _id("a task")


def test_line_number_and_raw_are_preserved(tmp_path):
    text = "\n\n- [ ] a task <!-- blocked: waiting on fixtures -->\n"
    parked = _source(tmp_path).parked_tasks_from_text(text)
    assert parked[0].line_number == 3
    assert parked[0].raw == "- [ ] a task <!-- blocked: waiting on fixtures -->"


def test_list_parked_tasks_reads_the_tracker_file(tmp_path):
    src = _source(tmp_path)
    (tmp_path / TRACKER).write_text(
        "- [ ] open task\n- [ ] parked task <!-- blocked: reason -->\n"
    )
    parked = src.list_parked_tasks()
    assert [t.title for t in parked] == ["parked task"]


def test_list_parked_tasks_returns_empty_when_the_tracker_is_missing(tmp_path):
    assert _source(tmp_path).list_parked_tasks() == []


def test_multiple_parked_bullets_are_all_returned_in_order(tmp_path):
    text = (
        "- [ ] first <!-- blocked: a -->\n"
        "- [ ] an open one\n"
        "- [ ] second <!-- blocked: b -->\n"
    )
    parked = _source(tmp_path).parked_tasks_from_text(text)
    assert [t.title for t in parked] == ["first", "second"]
