"""🧑‍⚖️ THE CRITIC — advisory brief-review (CMX-88, persona-pattern step 3).

The critic's ONE hard promise is negative: it never blocks, delays, or changes a dispatch.
So the load-bearing test here is :func:`test_a_crashing_critic_never_breaks_dispatch` — it
corrupts the critic into raising and proves the dispatch still completes. If that guard ever
stays green with the swallow removed, the advisory-only property is decoration; corrupt it
(drop the ``try/except`` in ``dispatcher._run_critic``) and it must go red.

The rest pin the mechanical facts (the four-field detector) and the advisory rendering, each
written so a broken detector or a lying advisory turns it red — not so it passes whatever the
code does.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from chela import config, critic, dispatcher
from chela.sources import Task
from chela.workflow import WorkflowDef

# A brief that carries all four mandatory fields — the shape a well-formed dispatch brief
# (like this repo's own WORKFLOW.md prompt) has: an objective, its boundaries, its guardrails,
# and how to verify. Every one of the four detectors must fire on this, or it is not a
# detector.
COMPLETE_BRIEF = """\
## Your task
Implement the widget so the toolbar renders.

## Boundaries
Stay in scope: touch only chela/widget.py.

## Guardrails
⛔ Do NOT touch the tracker. You must not push to the base branch.

## Done criteria — how to verify
Run ruff and pytest; self-verify each guard before you hand off.
"""

# A threadbare brief: it says roughly what to do and nothing else. It must be flagged as
# missing the fields it genuinely lacks — a detector that calls this complete is asserting
# nothing.
THIN_BRIEF = "wt=/tmp/x"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _wf(tmp_path: Path, critic_cfg: object | None = None) -> WorkflowDef:
    cfg: dict = {"project_key": "TEST"}
    if critic_cfg is not None:
        cfg["critic"] = critic_cfg
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md", config=cfg, prompt_template="wt={{workspace_path}}"
    )


def _task(tmp_path: Path) -> Task:
    return Task(
        id="abc123", title="do a thing", file=str(tmp_path / "TODO.md"),
        line_number=7, raw="- [ ] do a thing",
    )


# --- the mechanical facts: the four-field detector ---------------------------


def test_a_complete_brief_carries_all_four_fields():
    review = critic.review_brief(COMPLETE_BRIEF)
    assert review.missing == []
    assert review.complete is True
    # Each field individually — so a detector that hard-codes "complete" cannot pass.
    for field_name in critic.FIELDS:
        assert review.present[field_name] is True, f"{field_name} should be detected"


def test_each_field_is_detected_independently():
    # Drop exactly one field's signals from a complete brief and ONLY that field goes missing.
    # This is what proves the four detectors are distinct rather than one check wearing four
    # names — corrupt any signal set and the matching case here flips.
    only = {
        "objective": "Implement the goal.",
        "boundaries": "Stay in scope; the boundaries are fixed.",
        "guardrails": "⛔ Do NOT touch the tracker.",
        "verify": "Run pytest and ruff to verify.",
    }
    for field_name, text in only.items():
        review = critic.review_brief(text)
        assert review.present[field_name] is True, f"{field_name!r} not detected in {text!r}"
        others = [f for f in critic.FIELDS if f != field_name]
        assert all(not review.present[o] for o in others), (
            f"{text!r} should only match {field_name!r}, got {review.present}"
        )


def test_a_threadbare_brief_is_flagged_missing_fields():
    review = critic.review_brief(THIN_BRIEF)
    assert review.complete is False
    # It names no objective, no boundaries, no guardrails, and no way to verify.
    assert set(review.missing) == set(critic.FIELDS)


def test_missing_is_reported_in_field_order():
    # A brief with only an objective is missing the other three, in FIELDS order.
    review = critic.review_brief("Implement the objective.")
    assert review.missing == ["boundaries", "guardrails", "verify"]


def test_a_non_string_brief_is_all_missing_not_a_crash():
    # The critic must never be the thing that turns a dispatch into an error.
    review = critic.review_brief(None)  # type: ignore[arg-type]
    assert review.missing == list(critic.FIELDS)


# --- the advisory rendering --------------------------------------------------


def test_a_complete_brief_gets_no_advisory():
    assert critic.advisory_body(critic.review_brief(COMPLETE_BRIEF)) == ""


def test_an_incomplete_brief_names_its_missing_fields_in_the_advisory():
    body = critic.advisory_body(critic.review_brief("Implement the objective."))
    assert body != ""
    for missing in ("boundaries", "guardrails", "verify"):
        assert missing in body
    # It must SAY it changed nothing — an advisory that reads like a gate is a lie.
    assert "not a gate" in body.lower()
    assert "boundaries" in body  # a named missing field, not just boilerplate


# --- the kill switches -------------------------------------------------------


def test_critic_is_on_by_default(tmp_path):
    assert critic.critic_enabled(_wf(tmp_path)) is True


def test_workflow_kill_switch_turns_it_off(tmp_path):
    assert critic.critic_enabled(_wf(tmp_path, {"enabled": False})) is False


def test_fleet_kill_switch_turns_it_off(tmp_path):
    with patch.object(config, "CRITIC_ENABLED", False):
        assert critic.critic_enabled(_wf(tmp_path)) is False


# --- the wiring: advisory-only, at dispatch ----------------------------------


def _spawn(wf: WorkflowDef, tmp_path: Path, conn: sqlite3.Connection):
    worktree = tmp_path / "wt"
    with patch.object(dispatcher, "ensure_worktree", return_value=(worktree, True)), \
         patch.object(dispatcher.subprocess, "run"), \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        return dispatcher._spawn(wf, _task(tmp_path), attempt=1, conn=conn)


def _row(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()


def test_dispatch_records_the_critic_advisory(tmp_path):
    # The thin prompt_template renders to a brief missing fields, so the critic writes a
    # non-empty note and stamps when it ran. Remove the _run_critic call from _spawn and both
    # go NULL → this fails.
    conn = _conn()
    assert _spawn(_wf(tmp_path), tmp_path, conn) is True
    row = _row(conn)
    assert row["status"] == "running"
    assert row["critic_reviewed_at"], "the critic should stamp when it ran"
    assert row["critic_notes"], "a thin brief should get a non-empty advisory"
    assert "boundaries" in row["critic_notes"]


def test_a_disabled_critic_leaves_no_note(tmp_path):
    # NULL critic_notes is the distinct fact "the critic never ran" — not "" (ran, clean).
    conn = _conn()
    with patch.object(config, "CRITIC_ENABLED", False):
        assert _spawn(_wf(tmp_path), tmp_path, conn) is True
    row = _row(conn)
    assert row["status"] == "running"
    assert row["critic_notes"] is None
    assert row["critic_reviewed_at"] is None


def test_a_crashing_critic_never_breaks_dispatch(tmp_path):
    # ⛔ THE LOAD-BEARING GUARD. Corrupt the critic into raising and the dispatch MUST still
    # complete: row 'running', the window spawned, no exception out of _spawn. Drop the
    # try/except in dispatcher._run_critic and this goes red — which is exactly the promise
    # "advisory-only" has to be able to fail on.
    conn = _conn()
    with patch.object(critic, "review_brief", side_effect=RuntimeError("boom")):
        assert _spawn(_wf(tmp_path), tmp_path, conn) is True
    row = _row(conn)
    assert row["status"] == "running", "the dispatch must succeed despite the crash"
    # The critic failed before it could write, so no note landed — and that cost nothing.
    assert row["critic_notes"] is None


def test_the_critic_runs_after_the_agent_is_launched(tmp_path):
    # Advisory-only means the dispatch is already DONE when the critic runs. If review_brief
    # is reached, the row must already be 'running' — the critic can only ever look at a
    # dispatch that has happened, never gate one that has not.
    conn = _conn()
    seen: dict[str, str | None] = {}
    real_review = critic.review_brief

    def _spy(text):
        seen["status"] = _row(conn)["status"]
        return real_review(text)

    with patch.object(critic, "review_brief", side_effect=_spy):
        _spawn(_wf(tmp_path), tmp_path, conn)
    assert seen["status"] == "running"
