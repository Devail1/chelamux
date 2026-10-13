"""CMX-114: the agent brief carries a guard-category checklist the agent walks BEFORE hand-in.

CMX-87 (`chela release`) took 13 judge rounds because each round found the next narrow gap
in a category the tests had covered one case at a time. The fix moves the whole-category
sweep to the start, into step 3 of the real `WORKFLOW.md` agent prompt. These pin that the
RENDERED prompt — what a dispatched agent actually reads — carries the checklist heading and
all seven items, in order, inside step 3, so a later edit can't silently drop one.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from chela import dispatcher
from chela.sources import Task
from chela.workflow import load_workflow, render_prompt

_REPO_ROOT = Path(__file__).resolve().parent.parent

HEADING = "GUARD-CATEGORY CHECKLIST — walk your OWN diff before hand-in."

# (item number, a phrase that is that item's substance — literal, never read off the file)
ITEMS = [
    (1, "**Every `except` clause** — raise what it must catch but a NARROWER clause would miss"),
    (2, "**Every subprocess / external call** — pin the FULL argv, cwd, env and timeout on the "
        "SUCCESS path too."),
    (3, "**Literal documented values** — never the module's own constant as the expected value."),
    (4, "**Every branch on a kind/type/flag** — drive BOTH kinds through it"),
    (5, "**Every read that can fail** (remote ref, file, API) — an empty/unreadable result "
        "fails CLOSED"),
    (6, "**Every flag/config input** — explicit flag > workflow/config > default"),
    (7, "**Values from the authoritative record** (stored request, DB row), never a "
        "caller-supplied copy"),
]


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _rendered_prompt() -> str:
    wf = load_workflow(_REPO_ROOT / "WORKFLOW.md")
    task = Task(id="abc123", title="short title", file="TODO.md", line_number=3,
                raw="- [ ] short title")
    prompt = render_prompt(
        wf.prompt_template,
        dispatcher._prompt_vars(wf, task, "/wt", "cmx-1", "dev", 1),
    )
    return " ".join(prompt.split())


def _step_3(text: str) -> str:
    start = text.index("3. **⚖️ SELF-VERIFY YOUR GUARDS")
    end = text.index("4. **Commit in the worktree.**", start)
    return text[start:end]


def test_the_rendered_prompt_carries_the_checklist_heading_inside_step_3():
    step3 = _step_3(_rendered_prompt())
    assert HEADING in step3
    assert "list in the PR body the items that don't, and why" in step3
    assert "`docs/DEFEAT_SHAPES.md` entries 87/87b/87c" in step3


def test_the_rendered_prompt_carries_all_seven_items_in_order_after_the_heading():
    step3 = _step_3(_rendered_prompt())
    pos = step3.index(HEADING)
    for n, phrase in ITEMS:
        entry = f"{n}. {phrase}"
        assert step3.count(entry) == 1, f"checklist item {n} missing or duplicated: {entry!r}"
        nxt = step3.index(entry)
        assert nxt > pos, f"checklist item {n} is out of order"
        pos = nxt


def test_runs_md_hand_in_section_mentions_the_checklist():
    text = " ".join((_REPO_ROOT / "docs" / "RUNS.md").read_text().split())
    lifecycle = text[text.index("## The lifecycle"):text.index("## Risk and the judge")]
    assert "guard-category checklist" in lifecycle
    assert "`WORKFLOW.md` step 3" in lifecycle
