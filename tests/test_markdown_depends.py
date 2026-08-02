"""`MarkdownSource` parses a bullet's `<!-- depends: ... -->` marker into
`Task.depends` — the ids of the OTHER tasks it names (see chela.dispatcher._ready,
the sole place these are enforced, in tests/test_dispatcher_depends.py).

This module only tests the PARSE: a task carries the right ids, in the right
count, resolved the same way `_task_id` resolves any task's own identity. It
deliberately does NOT drop the task from `tasks_from_text` — a task with an
unmet dependency must still be visible as an open task; only the dispatcher's
claim path treats it as not-yet-claimable.
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


def test_a_bullet_with_no_depends_marker_has_no_dependencies(tmp_path):
    tasks = _source(tmp_path).tasks_from_text("- [ ] a plain task\n")
    assert tasks[0].depends == ()


def test_a_single_quoted_depends_resolves_to_the_named_tasks_id(tmp_path):
    text = (
        "- [ ] prerequisite task\n"
        '- [ ] follow-up task <!-- depends: "prerequisite task" -->\n'
    )
    tasks = _source(tmp_path).tasks_from_text(text)
    follow_up = next(t for t in tasks if t.title.startswith("follow-up"))
    assert follow_up.depends == (_id("prerequisite task"),)


def test_depends_still_returns_the_task_it_does_not_drop_it(tmp_path):
    # Regression guard: an EARLIER, wrong design dropped a task with an unmet
    # dependency straight out of `tasks_from_text` — which would make it vanish
    # from every UI/reconcile consumer of `list_open_tasks`, not just the claim
    # queue. It must still come back as a normal open task, merely annotated.
    text = '- [ ] follow-up task <!-- depends: "something not written yet" -->\n'
    tasks = _source(tmp_path).tasks_from_text(text)
    assert len(tasks) == 1
    assert tasks[0].title.startswith("follow-up task")


def test_multiple_semicolon_separated_titles_each_resolve(tmp_path):
    text = (
        '- [ ] follow-up task <!-- depends: "task a"; "task b" -->\n'
    )
    tasks = _source(tmp_path).tasks_from_text(text)
    assert set(tasks[0].depends) == {_id("task a"), _id("task b")}


def test_unquoted_titles_are_also_accepted(tmp_path):
    text = "- [ ] follow-up task <!-- depends: prerequisite task -->\n"
    tasks = _source(tmp_path).tasks_from_text(text)
    assert tasks[0].depends == (_id("prerequisite task"),)


def test_a_blank_depends_marker_yields_no_dependencies_rather_than_an_empty_string_one(tmp_path):
    # 🔴 GUARD: a parse that does not drop blank segments would produce a
    # dependency on `_id("")` — an id nothing can ever satisfy, silently
    # wedging the task forever.
    text = "- [ ] follow-up task <!-- depends:  -->\n"
    tasks = _source(tmp_path).tasks_from_text(text)
    assert tasks[0].depends == ()


def test_depends_does_not_disturb_the_blocked_marker(tmp_path):
    # `<!-- blocked -->` still removes the task from the open list entirely — a
    # wholly separate, pre-existing marker this feature must not interact with.
    text = "- [ ] a task <!-- blocked: reason -->\n"
    tasks = _source(tmp_path).tasks_from_text(text)
    assert tasks == []
