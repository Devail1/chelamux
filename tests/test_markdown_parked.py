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


def test_a_done_bullet_carrying_a_blocked_marker_is_not_parked(tmp_path):
    # 🔴 GUARD (round 5, PR #372): the OPEN axis and the BLOCKED axis are both
    # required — a bullet must be open-AND-blocked to be parked. The sibling test
    # above pins the open-but-unblocked negative control; this pins the other axis,
    # blocked-but-DONE. Without this, widening the match from OPEN_RE alone to
    # `OPEN_RE or DONE_RE` would surface an already-completed `- [x] ... <!-- blocked:
    # ... -->` bullet as a parked card in the Backlog lane forever — a checked-off
    # ticket has no future write that could ever move it off the board again — while
    # every other fixture in this file (which only ever combines `[ ]` with a blocked
    # marker) stayed green.
    text = "- [x] a done task <!-- blocked: waiting on fixtures -->\n"
    assert _source(tmp_path).parked_tasks_from_text(text) == []


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


def test_the_blocked_reason_stops_at_its_own_marker_when_a_second_marker_follows(tmp_path):
    # 🔴 GUARD (round 7, PR #372): every OTHER fixture in this file carries exactly one
    # marker per bullet, so a non-greedy `(.*?)` and a greedy `(.*)` capture the SAME
    # reason on all of them — the file's whole fixture family is a fixed point of that
    # mutation (docs/defeat_shapes/29 names the general shape). `depends:` is the
    # documented pairing (`_TRAILING_COMMENT_RE`'s own comment says "marker(s)", plural)
    # and TODO.md bullets routinely carry both, so this pins the one line where greedy and
    # non-greedy diverge: greedy would swallow the SECOND marker into the reason too.
    text = '- [ ] a task <!-- blocked: waiting on fixtures --> <!-- depends: "other thing" -->\n'
    parked = _source(tmp_path).parked_tasks_from_text(text)
    assert len(parked) == 1
    assert parked[0].body == "waiting on fixtures"


def test_the_blocked_reason_is_captured_from_an_uppercase_marker(tmp_path):
    # 🔴 GUARD (round 7, PR #372): BLOCKED_RE (decides parked-or-not) is IGNORECASE, and
    # BLOCKED_REASON_RE deliberately mirrors it — but every OTHER fixture in this file
    # writes its marker lowercase, so dropping IGNORECASE from BLOCKED_REASON_RE alone is
    # invisible to them: the bullet stays parked (BLOCKED_RE untouched) but silently loses
    # its reason (body degrades to None). This pins the case where the two regexes'
    # flags would diverge.
    text = "- [ ] a task <!-- BLOCKED: waiting on fixtures -->\n"
    parked = _source(tmp_path).parked_tasks_from_text(text)
    assert len(parked) == 1
    assert parked[0].body == "waiting on fixtures"


def test_multiple_parked_bullets_are_all_returned_in_order(tmp_path):
    text = (
        "- [ ] first <!-- blocked: a -->\n"
        "- [ ] an open one\n"
        "- [ ] second <!-- blocked: b -->\n"
    )
    parked = _source(tmp_path).parked_tasks_from_text(text)
    assert [t.title for t in parked] == ["first", "second"]
