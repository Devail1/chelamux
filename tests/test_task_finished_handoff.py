"""📝 CMX-52 — `chela task-finished --handoff`: the agent's own note for its reviewer.

The note travels the same road as the completion itself (issue #502 B1): the agent's
`request_task_finished` puts it in the worktree marker, and the daemon's `tick()` —
via `mark_awaiting_review` — is the only thing that writes it to `runs.handoff`. NULL
(no handoff given) and "" (an explicit empty one) stay distinct all the way through. It is
never a gate and never written into `critic_notes`. The inbox `run_review` notice carries
a sanitised snippet of it; the raw note rides in the payload.
"""
from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

from chela import dispatcher, inbox

from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf


# ⚖️🧹 CMX-88: `task-finished` now also runs `chela judge sweep`. These tests drive the OTHER
# gates and have no real run to sweep, so the sweep is stubbed clean here; it has its own
# tests in tests/test_judge_sweep.py.
@pytest.fixture(autouse=True)
def _clean_sweep(monkeypatch):
    monkeypatch.setattr(dispatcher, "verify_sweep",
                        lambda task_id: {"ok": True, "survivors": [], "mutations": 0,
                                         "killed": 0, "cannot_verify": "", "recovered": ""})


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


MARKER = ".chela-task-finished-request.json"


# --- the column ------------------------------------------------------------------------


def test_the_runs_table_has_a_nullable_handoff_column_that_defaults_to_null(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(tmp_path))
    assert dispatcher.resolve_run("t1")["handoff"] is None


# --- agent side: request_task_finished -------------------------------------------------


def _running(tmp_path, task_id="t1"):
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id=task_id, status="running", worktree_path=str(wt))
    return wt


def test_request_task_finished_puts_the_handoff_in_the_marker_and_writes_no_row(tmp_path):
    wt = _running(tmp_path)
    result = dispatcher.request_task_finished("t1", handoff="changed X; look at Y")
    assert result["ok"] is True
    assert json.loads((wt / MARKER).read_text())["handoff"] == "changed X; look at Y"
    # the agent still writes no row — the daemon stores the note
    assert dispatcher.resolve_run("t1")["handoff"] is None


def test_request_task_finished_without_a_handoff_leaves_the_key_out(tmp_path):
    wt = _running(tmp_path)
    dispatcher.request_task_finished("t1")
    assert "handoff" not in json.loads((wt / MARKER).read_text())


def test_request_task_finished_keeps_an_explicit_empty_handoff(tmp_path):
    wt = _running(tmp_path)
    dispatcher.request_task_finished("t1", handoff="")
    assert json.loads((wt / MARKER).read_text())["handoff"] == ""


# --- daemon side: tick() applies the marker ----------------------------------------------


def _tick_with_marker(tmp_path, marker: dict | str):
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    (wt / MARKER).write_text(marker if isinstance(marker, str) else json.dumps(marker))
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             worktree_path=str(wt), pr_url=None, pr_state=None)
    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=_Source("abc123")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_read_pr_url", return_value=None), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)
    return summary, dispatcher.resolve_run("abc123")


def test_tick_persists_the_markers_handoff_on_the_run(tmp_path):
    summary, run = _tick_with_marker(
        tmp_path, {"task_id": "abc123", "handoff": "migrated runs.handoff"})
    assert summary["task_finished_applied"] == 1
    assert run["status"] == "awaiting_review"
    assert run["handoff"] == "migrated runs.handoff"
    # ⛔ never folded into the critic's column
    assert run["critic_notes"] is None


@pytest.mark.parametrize("marker, expected", [
    ({"task_id": "abc123"}, None),
    ({"task_id": "abc123", "handoff": ""}, ""),
])
def test_tick_keeps_null_and_empty_handoff_distinct(tmp_path, marker, expected):
    _, run = _tick_with_marker(tmp_path, marker)
    assert run["handoff"] == expected
    assert (run["handoff"] is None) == (expected is None)


@pytest.mark.parametrize("marker", [
    {"task_id": "abc123", "handoff": 42},
    {"task_id": "abc123", "handoff": ["a", "b"]},
    "not json at all",
])
def test_a_malformed_handoff_never_blocks_the_transition(tmp_path, marker):
    """A handoff is never a gate: a marker whose note is garbage (or a marker that is not
    even JSON) still completes the run — it just records no handoff."""
    summary, run = _tick_with_marker(tmp_path, marker)
    assert summary["task_finished_applied"] == 1
    assert run["status"] == "awaiting_review"
    assert run["handoff"] is None


def test_mark_awaiting_review_bounds_a_huge_handoff(tmp_path):
    assert dispatcher.HANDOFF_MAX_CHARS == 4000
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(tmp_path),
             window_name=None)
    result = dispatcher.mark_awaiting_review(
        "t1", handoff="x" * (dispatcher.HANDOFF_MAX_CHARS + 500))
    assert result["ok"] is True
    assert len(dispatcher.resolve_run("t1")["handoff"]) == dispatcher.HANDOFF_MAX_CHARS


# --- the CLI flag -------------------------------------------------------------------------


@pytest.mark.parametrize("argv_tail, expected", [
    (["--handoff", "look at the migration"], "look at the migration"),
    (["--handoff", ""], ""),
    ([], None),
])
def test_cli_forwards_handoff_to_request_task_finished(argv_tail, expected, capsys):
    from chela import main

    with patch.object(dispatcher, "request_task_finished",
                      return_value={"ok": True, "task_id": "t1", "requested": True}) as req, \
         patch.object(sys, "argv",
                      ["chela", "task-finished", "t1", "--no-new-guards", *argv_tail]):
        main.main()
    assert req.call_args.kwargs["handoff"] == expected


# --- the inbox notice --------------------------------------------------------------------


def _review(**over):
    return {"task_id": "T7", "title": "a task", "status": "awaiting_review",
            "branch_name": "cmx-52", "window_name": "cmx-52", "pr_state": "open",
            "pr_url": "https://github.com/x/y/pull/47", **over}


def test_the_run_review_notice_carries_a_sanitised_handoff_snippet():
    events, _ = inbox.run_events(
        [_review(handoff="added `handoff`; check $(rm -rf ~)\nthen the inbox")], {})
    (event,) = [e for e in events if e["kind"] == "run_review"]
    summary = event["summary"]
    assert "handoff:" in summary
    assert "added handoff" in summary and "then the inbox" in summary
    for bad in ("`", "$", "(", ")", "\n"):
        assert bad not in summary, f"{bad!r} survived into a summary typed at a prompt"
    # the payload keeps the raw record
    assert event["payload"]["handoff"] == "added `handoff`; check $(rm -rf ~)\nthen the inbox"


def test_the_run_review_snippet_is_bounded():
    events, _ = inbox.run_events([_review(handoff="word " * 200)], {})
    (event,) = [e for e in events if e["kind"] == "run_review"]
    assert len(event["summary"]) < 400
    assert "…" in event["summary"]


@pytest.mark.parametrize("handoff", [None, ""])
def test_no_handoff_means_no_handoff_text_and_the_payload_keeps_null_vs_empty(handoff):
    events, _ = inbox.run_events([_review(handoff=handoff)], {})
    (event,) = [e for e in events if e["kind"] == "run_review"]
    assert "handoff" not in event["summary"]
    assert event["payload"]["handoff"] == handoff


def test_a_rework_round_finished_without_a_handoff_does_not_inherit_the_last_one(tmp_path):
    """The same row is reused across rework rounds: each completion records ITS OWN note, so
    a round finished with no `--handoff` reads NULL, not the previous round's text."""
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(tmp_path),
             window_name=None)
        conn.execute("UPDATE runs SET handoff='round 1 note' WHERE task_id='t1'")
        conn.commit()
    assert dispatcher.mark_awaiting_review("t1")["ok"] is True
    assert dispatcher.resolve_run("t1")["handoff"] is None


def test_a_rework_round_finished_without_equivalents_does_not_inherit_the_last_ones(tmp_path):
    """⚖️🧹 CMX-88: like the handoff, a round's `--sweep-equivalent` claims belong to THAT
    round. A later round handing in with none must read NULL — inheriting round 1's claims
    would show the judge an equivalence the agent never made for the current code."""
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(tmp_path),
             window_name=None)
        conn.execute("UPDATE runs SET sweep_equivalents=? WHERE task_id='t1'",
                     (json.dumps([_SWEEP_NOTE]),))
        conn.commit()
    assert dispatcher.mark_awaiting_review("t1")["ok"] is True
    assert dispatcher.resolve_run("t1")["sweep_equivalents"] is None



_SWEEP_NOTE = {"key": "chela/x.py:12", "why": "log-only branch", "mutations": ["if-false: a → b"]}


@pytest.mark.parametrize("marker, expected", [
    ({"task_id": "abc123", "sweep_equivalents": [_SWEEP_NOTE]}, [_SWEEP_NOTE]),
    ({"task_id": "abc123"}, None),
])
def test_tick_persists_the_markers_sweep_equivalents_on_the_run(tmp_path, marker, expected):
    """⚖️🧹 CMX-88: the daemon records the agent's `--sweep-equivalent` notes from the
    marker; a marker without them leaves the column NULL."""
    _, run = _tick_with_marker(tmp_path, marker)
    stored = run["sweep_equivalents"]
    assert (json.loads(stored) if stored is not None else None) == expected


@pytest.mark.parametrize("marker, expected", [
    ({"task_id": "abc123", "size_gate": {"lines": 9, "limit": 3, "oversize_reason": "why"}},
     {"lines": 9, "limit": 3, "oversize_reason": "why"}),
    ({"task_id": "abc123", "size_gate": "not a record"}, None),
])
def test_tick_persists_the_markers_size_gate_on_the_run(tmp_path, marker, expected):
    _, run = _tick_with_marker(tmp_path, marker)
    stored = run["size_gate"]
    assert (json.loads(stored) if stored is not None else None) == expected
