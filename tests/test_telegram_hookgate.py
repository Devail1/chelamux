"""The hook payload as the CONTENT authority for a gate (CMX-49).

What these lock in:

  * **"pending" is a FACT, not a guess** — a ``hook.pre_tool_use`` for an interactive
    tool with no ``hook.post_tool_use`` bearing the same ``tool_use_id``. That is why the
    content is read from ``PreToolUse`` and not from the ``PermissionRequest`` that fires
    at the same moment: ``PermissionRequest`` carries no ``tool_use_id`` (measured), so it
    can be paired with nothing and can never be known to be over;
  * the payload survives the trip **in full** — every question, every option's ``label``,
    ``description`` and ``preview``. Losing any of them is the bug;
  * a resolved gate, a non-interactive tool, another window's gate, and an event from a
    **previous boot** (those are the pre-#61 records with the WRONG ``wid``) all resolve
    to ``None``, so the caller falls back to the pane rather than posting a lie.
"""
from __future__ import annotations

import pytest

from chela import event_log
from chela.telegram.hookgate import parse_questions, pending_gate


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    """Never the real ``~/.chela/events.jsonl`` (the read cache is keyed by path)."""
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))


# The real thing: the payload of `events.jsonl` seq 184 (2026-07-14) — the 3-question,
# preview-bearing AskUserQuestion that reached the phone as a bare nav row.
TOOL_INPUT = {
    "questions": [
        {
            "question": "How aggressive should the sidebar consolidation be?",
            "header": "Sidebar",
            "multiSelect": False,
            "options": [
                {"label": "Spine: 5 views, Feed is home",
                 "description": "Feed becomes the default landing view.",
                 "preview": "BEFORE (6)          AFTER (5)\n  Wall                Feed"},
                {"label": "Conservative: add Feed",
                 "description": "Lower risk, less payoff.",
                 "preview": "BEFORE (6)          AFTER (6)"},
            ],
        },
        {
            "question": "Scope of the Feed: read-only, or can you ACT on a row?",
            "header": "Act path",
            "multiSelect": False,
            "options": [
                {"label": "Read-only", "description": "Ship the render first."},
                {"label": "Actionable", "description": "Answer a gate from the row."},
            ],
        },
    ],
}


def _pre(wid="@1", tuid="toolu_01", tool="AskUserQuestion", tool_input=None):
    return event_log.append(
        "hook.pre_tool_use", f"{tool}: asking",
        {"hook_event_name": "PreToolUse", "tool_name": tool, "tool_use_id": tuid,
         "tool_input": TOOL_INPUT if tool_input is None else tool_input},
        wid=wid, session_id="sess-1",
    )


def _post(wid="@1", tuid="toolu_01", tool="AskUserQuestion"):
    return event_log.append(
        "hook.post_tool_use", f"{tool} done",
        {"hook_event_name": "PostToolUse", "tool_name": tool, "tool_use_id": tuid,
         "tool_response": {}},
        wid=wid, session_id="sess-1",
    )


def test_an_unresolved_pre_tool_use_is_the_gate_in_full():
    _pre()
    gate = pending_gate("@1")
    assert gate is not None
    assert gate.tool == "AskUserQuestion"
    assert gate.tool_use_id == "toolu_01"
    # Every question, and every option's label + description + PREVIEW. The whole point:
    # the relay used to show none of this while the log already held all of it.
    assert len(gate.questions) == 2
    q1, q2 = gate.questions
    assert q1.header == "Sidebar" and q1.multi_select is False
    assert [o.label for o in q1.options] == [
        "Spine: 5 views, Feed is home", "Conservative: add Feed"]
    assert q1.options[0].description == "Feed becomes the default landing view."
    assert q1.options[0].preview.startswith("BEFORE (6)")
    assert q2.question.startswith("Scope of the Feed")
    # A preview is OPTIONAL and usually absent — it must come through as empty, not None.
    assert q2.options[0].preview == ""


def test_a_post_tool_use_with_the_same_tool_use_id_resolves_it():
    _pre()
    assert pending_gate("@1") is not None
    _post()
    assert pending_gate("@1") is None      # answered → the pane is authoritative again


def test_a_post_for_a_DIFFERENT_tool_use_id_does_not_resolve_the_gate():
    _pre(tuid="toolu_A")
    _post(tuid="toolu_B")                  # some other tool call finished
    gate = pending_gate("@1")
    assert gate is not None and gate.tool_use_id == "toolu_A"


def test_the_newest_unresolved_gate_wins():
    _pre(tuid="toolu_old")
    _post(tuid="toolu_old")
    _pre(tuid="toolu_new")
    gate = pending_gate("@1")
    assert gate is not None and gate.tool_use_id == "toolu_new"


def test_a_non_interactive_tool_is_never_a_gate():
    # A subagent's events carry its PARENT's session_id, so they land on the parent's
    # window: an ordinary Bash call must not be dressed up as the gate on that screen.
    _pre(tuid="toolu_bash", tool="Bash", tool_input={"command": "ls"})
    assert pending_gate("@1") is None


def test_another_windows_gate_is_not_this_windows_gate():
    _pre(wid="@2")
    assert pending_gate("@1") is None
    assert pending_gate("@2") is not None


def test_a_pre_tool_use_with_no_tool_use_id_is_ignored():
    # PermissionRequest carries no tool_use_id — it can be paired with nothing, so it can
    # never be known to be resolved, so it is never the content source.
    _pre(tuid=None)
    assert pending_gate("@1") is None


def test_events_from_a_previous_boot_are_ignored():
    # The hook events written before PR #61 were correlated on `cwd` and are filed against
    # the WRONG window. Scoping to the current boot_id excludes them — a gate resolved
    # against one of those would be posted into a different agent's topic.
    _pre(tuid="toolu_stale")
    event_log.new_boot()
    assert pending_gate("@1") is None
    _pre(tuid="toolu_live")
    gate = pending_gate("@1")
    assert gate is not None and gate.tool_use_id == "toolu_live"


def test_an_empty_log_is_simply_no_gate():
    assert pending_gate("@1") is None      # the pre-plugin fleet: fall back to the pane


def test_a_read_failure_never_raises_into_the_relay():
    def boom(**_kwargs):
        raise OSError("log is on fire")

    assert pending_gate("@1", read=boom) is None


def test_an_unrecognised_tool_input_yields_no_questions():
    assert parse_questions(None) == ()
    assert parse_questions({"questions": "not a list"}) == ()
    # An option with no label is not pickable and is not an option.
    parsed = parse_questions({"questions": [{"question": "?", "options": [{}, {"label": "A"}]}]})
    assert [o.label for o in parsed[0].options] == ["A"]
