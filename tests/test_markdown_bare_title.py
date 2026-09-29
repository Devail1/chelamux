"""CMX-392: `Task.title` is the BARE visible title; `Task.raw` keeps the whole line.

A bullet that carries a `<!-- depends: ... -->` marker used to hand its whole marker to
`Task.title`, so run rows, the Work view and every inbox notice showed the raw comment
instead of the task's name. The id already hashed the bare title (CMX-384) and must not
move; the strike matches the tracker line by that id, so it must still find the line.
"""
from __future__ import annotations

from pathlib import Path

from chela.sources.markdown import MarkdownSource, _title_id, raw_title
from chela.workflow import WorkflowDef

TRACKER = "TODO.md"
MARKED = '- [ ] task B <!-- depends: "task A" -->'
TEXT = f"- [ ] task A\n{MARKED}\n- [ ] unrelated task\n"


def _source(tmp_path: Path) -> MarkdownSource:
    wf = WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"tracker": {"kind": "markdown", "path": TRACKER}},
        prompt_template="",
    )
    return MarkdownSource(wf)


def _by_bare(tmp_path: Path) -> dict:
    return {t.title: t for t in _source(tmp_path).tasks_from_text(TEXT)}


def test_a_marker_carrying_bullets_title_carries_no_comment_text(tmp_path):
    # GUARD (a)
    t = _by_bare(tmp_path)["task B"]
    assert t.title == "task B"
    assert "<!--" not in t.title and "depends" not in t.title
    # ...while `raw` keeps the full line, marker and all.
    assert t.raw == MARKED
    assert raw_title(t) == 'task B <!-- depends: "task A" -->'


def test_a_marker_carrying_bullets_id_is_unchanged_bare_title_hash(tmp_path):
    # GUARD (b) — ids must not change; CMX-384 already hashed the bare title.
    tasks = _by_bare(tmp_path)
    assert tasks["task B"].id == _title_id(TRACKER, "task B")
    assert tasks["task B"].depends == (tasks["task A"].id,)


def test_striking_a_marker_carrying_task_flips_its_line_and_nothing_else(tmp_path):
    # GUARD (c) ⭐ — the dispatcher strikes by the id the parse handed out; it must find
    # the marker-carrying line and leave the unrelated bullet alone.
    src = _source(tmp_path)
    tracker = tmp_path / TRACKER
    tracker.write_text(TEXT)
    tid = _by_bare(tmp_path)["task B"].id
    assert src.close_tasks([tid]) == {tid: "struck"}
    assert tracker.read_text() == (
        "- [ ] task A\n"
        '- [x] task B <!-- depends: "task A" -->\n'
        "- [ ] unrelated task\n"
    )
    # ...and the struck line reads back as closed under the same id.
    assert tid in src.closed_ids_from_text(tracker.read_text())


def test_an_unmarked_bullets_title_and_id_are_byte_for_byte_unchanged(tmp_path):
    # GUARD (d)
    t = _by_bare(tmp_path)["unrelated task"]
    assert t.title == "unrelated task"
    assert t.raw == "- [ ] unrelated task"
    assert t.id == _title_id(TRACKER, "unrelated task")
    assert raw_title(t) == t.title


def test_legacy_raw_ids_still_maps_the_pre_cmx384_raw_line_id(tmp_path):
    # `legacy_raw_ids` hashed `t.title` when title WAS the raw text; it must now read
    # the raw text back from `t.raw`, or the CMX-384 re-key silently maps nothing.
    src = _source(tmp_path)
    tasks = src.tasks_from_text(TEXT)
    raw = 'task B <!-- depends: "task A" -->'
    assert src.legacy_raw_ids(tasks) == {_title_id(TRACKER, raw): _title_id(TRACKER, "task B")}
