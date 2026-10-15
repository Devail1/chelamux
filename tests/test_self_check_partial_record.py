"""⏳ CMX-141 slice 2 — a budget-cut self-check is recorded on the run, in the PR body and in
the judge prompt."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, judge, main
from tests.test_judge_sweep import _run_row


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def test_task_finished_hands_the_partial_record_to_the_marker(tmp_path):
    check = {"ok": True, "blocking": 0, "cannot_verify": "", "exhausted": "over budget",
             "outcomes": [{"verdict": "KILLED", "guard": "g0", "file": "guard.py"}],
             "unrun": [{"guard": "g1", "file": "guard.py", "verdict": "UNRUN"}]}
    with patch.object(dispatcher, "check_hand_in_size", return_value={"ok": True}), \
            patch.object(dispatcher, "verify_self_check", return_value=check), \
            patch.object(dispatcher, "verify_sweep", return_value={"ok": True, "skipped": "x"}), \
            patch.object(dispatcher, "request_task_finished",
                         return_value={"ok": True, "task_id": "cmx-88"}) as req, \
            patch.object(sys, "argv", ["chela", "task-finished", "cmx-88",
                                        "--self-check-experiments", "e.json"]):
        main.main()
    req.assert_called_once_with("cmx-88", handoff=None, self_check_partial={
        "measured": 1, "total": 2, "exhausted": "over budget",
        "unrun": [{"file": "guard.py", "guard": "g1"}]})


PARTIAL = {"measured": 1, "total": 2, "exhausted": "over the 1-experiment budget (2 given)",
           "unrun": [{"file": "guard.py", "guard": "g1\x1b[2J"}]}
NOTE = ("⏳ **Self-check PARTIAL — 1 experiment(s) UNRUN** (CANNOT VERIFY, not passed; over "
        "the 1-experiment budget (2 given)): guard.py: g1\x1b[2J")


@pytest.mark.parametrize("record", [PARTIAL, None])
def test_the_daemon_records_it_on_the_run_the_pr_and_the_judge(tmp_path, record):
    from chela.workflow import parse_workflow

    root = tmp_path / "wt"
    root.mkdir()
    _run_row(tmp_path, root)
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET self_check_partial='{\"unrun\": [{\"file\": \"stale\"}]}'")
        conn.commit()
    with patch.object(dispatcher, "_read_pr_url", return_value="https://github.com/o/r/pull/7"), \
            patch.object(dispatcher, "_read_pr_status", return_value=(None, None)), \
            patch.object(dispatcher, "_kill_window"), \
            patch.object(dispatcher, "_append_pr_body", return_value=(True, "")) as body:
        assert dispatcher.mark_awaiting_review("cmx-88", self_check_partial=record)["ok"]
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs").fetchone()
    wf = parse_workflow(Path("W.md"), "---\nproject_key: TT\n---\n")
    prompt = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT,
                                      dispatcher._judge_vars(wf, row, root, "deadbeef"))
    if record is None:
        assert row["self_check_partial"] is None and "Self-check PARTIAL" not in prompt
        return body.assert_not_called()
    body.assert_called_once_with("https://github.com/o/r/pull/7", str(tmp_path), NOTE)
    assert json.loads(row["self_check_partial"]) == record
    assert "UNRUN** (CANNOT VERIFY, not passed;" in prompt and "\x1b" not in prompt


@pytest.mark.parametrize("record", [{}, {"unrun": []}, {"unrun": "x"}, "nope"])
def test_render_says_nothing_without_an_unrun_list(record):
    assert judge.render_self_check_partial(record) == ""


def test_the_marker_carries_the_partial_record(tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    _run_row(tmp_path, root)
    assert dispatcher.request_task_finished("cmx-88", self_check_partial=PARTIAL)["ok"]
    assert dispatcher._read_task_finished_size_gate(root, "self_check_partial") == PARTIAL
    assert dispatcher._read_task_finished_size_gate(root) is None
