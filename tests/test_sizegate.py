"""📏 CMX-126 — ``chela task-finished`` refuses an oversized diff unless ``--oversize-reason``
is given, which is recorded on the run, in the PR body and in the judge prompt."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, sizegate
from chela.workflow import parse_workflow
from tests.test_judge_sweep import _git, _run_row, _task_finished

REAL_SIZE_GATE = True


MIXED = {"chela/a.py": "x\n" * 10, "tests/test_a.py": "x\n" * 5, "docs/guide.txt": "x\n" * 50,
         "README.md": "x\n" * 40, "changelog.d/C.txt": "x\n" * 30, "media/s.png": "x\n" * 9,
         "blob.bin": "\x00\x01\x02" * 50}


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A run whose branch adds MIXED (15 counted lines) on ``origin/dev``."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "dev")
    _git(root, "commit", "-q", "--allow-empty", "-m", "base")
    _git(root, "update-ref", "refs/remotes/origin/dev", "HEAD")
    for rel, body in MIXED.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(body)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "feature")
    _run_row(tmp_path, root)
    return root


def _wf(hand_in: str = ""):
    return parse_workflow(Path("W.md"), f"---\nproject_key: TT\n{hand_in}---\n")


def test_docs_media_changelog_and_binaries_do_not_count(root):
    assert sizegate.measure(root, "dev", sizegate.SIZE_EXCLUDE) == 15
    assert sizegate.measure(root, "dev", ()) == 15 + 50 + 40 + 30 + 9
    assert sizegate.measure(root, "dev", ("tests/*",)) == 10 + 50 + 40 + 30 + 9


def test_measure_pins_its_git_call(tmp_path):
    ok = subprocess.CompletedProcess([], 0, "3\t2\tchela/x.py\n", "")
    with patch.object(sizegate.subprocess, "run", return_value=ok) as run:
        assert sizegate.measure(tmp_path, "dev", ()) == 5
    run.assert_called_once_with(["git", "diff", "--numstat", "origin/dev...HEAD"],
                                cwd=str(tmp_path), capture_output=True, text=True, timeout=60)


def test_an_unmeasurable_branch_fails_closed(root, tmp_path):
    with pytest.raises(RuntimeError, match="origin/nope"):
        sizegate.measure(root, "nope", ())
    with pytest.raises(RuntimeError, match="could not measure"):
        sizegate.measure(tmp_path / "missing", "dev", ())     # FileNotFoundError ⊂ OSError
    with patch.object(sizegate.subprocess, "run", side_effect=subprocess.TimeoutExpired("g", 1)), \
            pytest.raises(RuntimeError, match="could not measure"):
        sizegate.measure(root, "dev", ())


@pytest.mark.parametrize("hand_in, expected", [
    ("", (300, sizegate.SIZE_EXCLUDE)),
    ("hand_in:\n  max_pr_lines: 120\n", (120, sizegate.SIZE_EXCLUDE)),
    ("hand_in:\n  size_exclude: ['*.lock']\n", (300, ("*.lock",))),
    *[(f"hand_in:\n  {bad}\n", ValueError) for bad in (
        "max_pr_lines: 0", "max_pr_lines: true", "max_pr_lines: '300'", "size_exclude: '*.md'")],
])
def test_workflow_beats_default_and_a_bad_value_raises(hand_in, expected):
    wf = _wf(hand_in)
    if expected is ValueError:
        with pytest.raises(ValueError):
            sizegate.settings(wf)
    else:
        assert sizegate.settings(wf) == expected


@pytest.mark.parametrize("limit, reason, expected", [
    (14, None, None), (14, "  ", None), (15, "ignored", {"ok": True, "record": None}),
    (14, "  one migration  ",
     {"ok": True, "record": {"lines": 15, "limit": 14, "oversize_reason": "one migration"}}),
])
def test_the_gate_refuses_over_the_limit_unless_overridden(root, monkeypatch, limit, reason,
                                                           expected):
    monkeypatch.setattr(sizegate, "MAX_PR_LINES", limit)
    out = dispatcher.check_hand_in_size("cmx-88", reason)
    if expected is None:
        assert out["ok"] is False and "changes 15 lines (limit 14" in out["error"]
    else:
        assert out == expected


def test_an_unknown_size_refuses(root, monkeypatch):
    assert dispatcher.check_hand_in_size("cmx-1")["ok"] is False           # no such run
    monkeypatch.setattr(sizegate, "MAX_PR_LINES", "nope")
    out = dispatcher.check_hand_in_size("cmx-88", "a reason")
    assert out["ok"] is False and "could not measure" in out["error"]


def test_cli_refuses_before_the_sweep(root, monkeypatch, capsys):
    monkeypatch.setattr(sizegate, "MAX_PR_LINES", 14)
    with patch.object(dispatcher, "verify_sweep") as swept, \
            patch.object(dispatcher, "request_task_finished") as req:
        assert _task_finished() == 1
    assert "📏 this branch changes 15 lines" in capsys.readouterr().out
    assert not swept.called and not req.called


@pytest.mark.parametrize("extra, size_gate", [
    (("--oversize-reason", "schema"), {"lines": 15, "limit": 14, "oversize_reason": "schema"}),
    ((), None),
])
def test_cli_hands_the_record_to_the_marker(root, monkeypatch, extra, size_gate):
    monkeypatch.setattr(sizegate, "MAX_PR_LINES", 14 if size_gate else 300)
    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
            patch.object(dispatcher, "verify_sweep", return_value={"ok": True, "skipped": "x"}):
        assert _task_finished(*extra) is None
    marker = json.loads(dispatcher._task_finished_request_path(root).read_text())
    assert marker.get("size_gate") == size_gate
    assert dispatcher._read_task_finished_size_gate(root) == size_gate


@pytest.mark.parametrize("record", [
    {"lines": 15, "limit": 14, "oversize_reason": "one\x1b[2J schema"}, None])
def test_the_daemon_records_it_on_the_run_the_pr_and_the_judge(root, tmp_path, record):
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET size_gate='{\"oversize_reason\": \"stale\"}'")
        conn.commit()
    with patch.object(dispatcher, "_read_pr_url", return_value="https://github.com/o/r/pull/7"), \
            patch.object(dispatcher, "_read_pr_status", return_value=(None, None)), \
            patch.object(dispatcher, "_kill_window"), \
            patch.object(dispatcher, "_append_pr_body", return_value=(True, "")) as body:
        assert dispatcher.mark_awaiting_review("cmx-88", size_gate=record)["ok"]
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs").fetchone()
    prompt = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT,
                                      dispatcher._judge_vars(_wf(), row, root, "deadbeef"))
    if record is None:
        assert row["size_gate"] is None and "Oversize" not in prompt
        return body.assert_not_called()
    body.assert_called_once_with("https://github.com/o/r/pull/7", str(tmp_path), "📏 **Oversize "
                                 "override:** 15 changed lines (limit 14). Agent's reason: one"
                                 "\x1b[2J schema")
    assert json.loads(row["size_gate"]) == record
    assert "Agent's reason: one schema" in prompt and "\x1b" not in prompt
