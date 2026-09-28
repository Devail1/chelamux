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


def test_a_quoted_title_containing_a_semicolon_still_resolves(tmp_path):
    # 🔴 GUARD: a naive `raw.split(";")` done BEFORE quote-stripping cuts a
    # quoted title with an embedded `;` into two garbage segments, neither of
    # which hashes to the real target — the dependency silently resolves to
    # ids nothing satisfies and the task is stuck forever, indistinguishable
    # from a typo. Quoting must make the whole title, `;` included, one segment.
    text = (
        "- [ ] fix the bug; handle the edge case\n"
        '- [ ] follow-up task <!-- depends: "fix the bug; handle the edge case" -->\n'
    )
    tasks = _source(tmp_path).tasks_from_text(text)
    follow_up = next(t for t in tasks if t.title.startswith("follow-up"))
    assert follow_up.depends == (_id("fix the bug; handle the edge case"),)


def test_a_semicolon_titled_dependency_mixed_with_a_plain_one_both_resolve(tmp_path):
    text = (
        "- [ ] a; b\n"
        "- [ ] plain prerequisite\n"
        '- [ ] follow-up task <!-- depends: "a; b"; "plain prerequisite" -->\n'
    )
    tasks = _source(tmp_path).tasks_from_text(text)
    follow_up = next(t for t in tasks if t.title.startswith("follow-up"))
    assert set(follow_up.depends) == {_id("a; b"), _id("plain prerequisite")}


def test_depends_does_not_disturb_the_blocked_marker(tmp_path):
    # `<!-- blocked -->` still removes the task from the open list entirely — a
    # wholly separate, pre-existing marker this feature must not interact with.
    text = "- [ ] a task <!-- blocked: reason -->\n"
    tasks = _source(tmp_path).tasks_from_text(text)
    assert tasks == []


# ── CMX-384: every bullet's id hashes its BARE title ─────────────────────────────


def test_an_unmarked_tasks_id_is_byte_for_byte_the_raw_title_hash(tmp_path):
    # ⭐ The case that must be ACCEPTED: no mass re-key. An unmarked bullet's id is
    # exactly what it was before CMX-384 — the hash of its (whole) title.
    tasks = _source(tmp_path).tasks_from_text("- [ ] a plain task\n")
    assert tasks[0].id == _id("a plain task")


def test_a_marker_carrying_tasks_id_is_its_bare_title_hash(tmp_path):
    raw = 'follow-up task <!-- depends: "prerequisite task" -->'
    tasks = _source(tmp_path).tasks_from_text(f"- [ ] {raw}\n- [ ] prerequisite task\n")
    follow_up = next(t for t in tasks if t.title.startswith("follow-up"))
    assert follow_up.id == _id("follow-up task")
    assert follow_up.id != _id(raw)


def test_a_three_hop_chain_resolves_each_edge_to_a_real_task(tmp_path):
    text = (
        "- [ ] task A\n"
        '- [ ] task B <!-- depends: "task A" -->\n'
        '- [ ] task C <!-- depends: "task B" -->\n'
    )
    by_title = {t.title.split(" <!--")[0]: t for t in _source(tmp_path).tasks_from_text(text)}
    assert by_title["task B"].depends == (by_title["task A"].id,)
    assert by_title["task C"].depends == (by_title["task B"].id,)


def test_a_struck_marker_carrying_task_is_closed_under_its_bare_id(tmp_path):
    # C's `depends: "task B"` is satisfied only if B's `[x]` line yields B's bare id.
    closed = _source(tmp_path).closed_ids_from_text('- [x] task B <!-- depends: "task A" -->\n')
    assert closed == {_id("task B")}


def test_strike_lines_finds_a_marker_carrying_task_by_its_bare_id():
    from chela.sources.markdown import strike_lines

    text = '- [ ] task B <!-- depends: "task A" -->\n'
    new, results = strike_lines(text, TRACKER, [_id("task B")])
    assert results == {_id("task B"): "struck"}
    assert new == '- [x] task B <!-- depends: "task A" -->\n'


def test_a_depends_marker_quoted_in_inline_code_is_not_a_marker(tmp_path):
    # The CMX-384 bullet itself quoted the syntax in backticks and was parsed as naming a
    # task called "…" — blocked forever. Prose about a marker is not a marker.
    text = '- [ ] document the `<!-- depends: "…" -->` syntax\n'
    assert _source(tmp_path).tasks_from_text(text)[0].depends == ()


def test_a_real_marker_after_a_quoted_one_still_counts(tmp_path):
    text = '- [ ] explain `<!-- depends: "x" -->` <!-- depends: "real one" -->\n'
    assert _source(tmp_path).tasks_from_text(text)[0].depends == (_id("real one"),)


def test_legacy_raw_ids_maps_only_marker_carrying_tasks(tmp_path):
    raw = 'task B <!-- depends: "task A" -->'
    src = _source(tmp_path)
    tasks = src.tasks_from_text(f"- [ ] task A\n- [ ] {raw}\n")
    assert src.legacy_raw_ids(tasks) == {_id(raw): _id("task B")}


def test_strike_lines_reports_an_already_struck_marker_carrying_task_under_its_bare_id():
    # 🔴 GUARD (judge round 1, mutation 1): the `[x]` branch of strike_lines must hash the
    # BARE title too. Corrupt it back to the raw line → the bare id is never matched, so
    # closing an already-struck marker-carrying task reports nothing for it → RED.
    from chela.sources.markdown import strike_lines

    text = '- [x] task B <!-- depends: "task A" -->\n'
    new, results = strike_lines(text, TRACKER, [_id("task B")])
    assert results == {_id("task B"): "already"}
    assert new == text
