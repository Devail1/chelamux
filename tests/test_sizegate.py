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
from chela.sources.linear import LinearError
from tests.test_linear_new_task import FakeTeam, _node, _source

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
    assert dispatcher.check_hand_in_size("cmx-1")["error"] == "no run with a worktree on record for cmx-1"
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


# --- slice 2: --split-plan --------------------------------------------------------------

SLICE = {"title": "t", "files": ["a.py"], "behaviour": "b", "tests": ["test_a"]}
PLAN = {"slices": [SLICE, {**SLICE, "title": "two"}, {**SLICE, "title": "three"}]}


def _plan(tmp_path, body=PLAN) -> str:
    (p := tmp_path / "plan.json").write_text(body if isinstance(body, str) else json.dumps(body))
    return str(p)


@pytest.mark.parametrize("body, error", [
    ("{", "could not be read"), ([], "needs `slices`"), ({"slices": [SLICE]}, "needs `slices`"),
    ({"slices": {"a": 1}}, "needs `slices`"), ({"slices": [SLICE, "x"]}, "slice 2 is missing "
     "title, files, behaviour, tests"),
    *[({"slices": [SLICE, {**SLICE, k: ""}]}, f"slice 2 is missing {k}") for k in SLICE],
])
def test_a_malformed_plan_is_refused_with_the_reason(tmp_path, body, error):
    slices, err = sizegate.load_plan(_plan(tmp_path, body))
    assert slices == [] and error in err
    assert "could not be read" in sizegate.load_plan(tmp_path / "missing.json")[1]


@pytest.mark.parametrize("limit, branch, error", [
    (14, True, "after the split this branch still changes 15 lines (limit 14) — move more to `cmx-88-rest`"),
    (15, False, "the rest of the split must be parked on branch `cmx-88-rest`, and there is no such branch"),
    (15, True, None),
])
def test_a_plan_needs_the_branch_under_the_limit_and_the_rest_parked(
        root, tmp_path, monkeypatch, limit, branch, error):
    monkeypatch.setattr(sizegate, "MAX_PR_LINES", limit)
    if branch:
        _git(root, "branch", "cmx-88-rest")
    out = dispatcher.check_hand_in_size("cmx-88", "ignored", _plan(tmp_path))
    if error:
        assert out == {"ok": False, "error": error}
    else:
        assert out == {"ok": True, "slices": PLAN["slices"],
                       "record": {"lines": 15, "limit": 15, "rest_branch": "cmx-88-rest"}}
    assert "could not be read" in dispatcher.check_hand_in_size("cmx-88", None, _plan(tmp_path, "{"))["error"]


def test_a_valid_plan_files_ordered_sub_issues_under_the_parent():
    parent = _node(1, "parent")
    parent["labels"] = {"nodes": [{"id": "lbl-risk", "name": "risk:high"}, {"name": "x"}]}
    ids = sizegate.file_sub_issues(_source(fake := FakeTeam(parent)), "CMX-1", PLAN["slices"], "cmx-1-rest")
    assert ids == ["CMX-100", "CMX-101"]
    creates = fake.inputs("create")
    assert [(c["title"], c["parentId"], c["labelIds"], c["stateId"]) for c in creates] == [
        ("two", "uuid-1", ["lbl-risk"], "st-Todo"), ("three", "uuid-1", ["lbl-risk"], "st-Todo")]
    assert creates[0]["description"] == ("b\n\n**Files:** a.py\n**Tests:** test_a\n\nSlice 2 of 3, split from "
                                         "CMX-1 by the hand-in size gate. Builds on branch `cmx-1-rest` — start from it.")
    assert fake.inputs("relation") == [
        {"issueId": "uuid-1", "relatedIssueId": "uuid-100", "type": "blocks"},
        {"issueId": "uuid-100", "relatedIssueId": "uuid-101", "type": "blocks"}]


def test_a_parent_alone_is_resolved_and_an_unknown_one_files_nothing():
    _source(fake := FakeTeam(_node(1, "parent"))).create_issue("x", parent="CMX-1")
    assert fake.inputs("create")[0]["parentId"] == "uuid-1"
    with pytest.raises(LinearError, match="no such issue: CMX-9"):
        _source(fake).create_issue("x", parent="CMX-9")
    assert fake.names().count("create") == 1


class _Tracker:
    def __init__(self, fail_at=None, warn_at=None):
        self.calls, self.fail_at, self.warn_at = [], fail_at, warn_at

    def create_issue(self, title, body, *, blocked_by, parent):
        self.calls.append((title, blocked_by, parent))
        n = len(self.calls)
        if n == self.fail_at:
            raise RuntimeError("boom")
        return {"identifier": f"CMX-{n + 99}", "warnings": ["w"] if n == self.warn_at else []}


@pytest.mark.parametrize("tracker, error", [
    (_Tracker(fail_at=2), "filing slice 3 ('three') failed: boom — already filed: CMX-100"),
    (_Tracker(warn_at=1), "slice 2 filed as CMX-100 but w — already filed: CMX-100"),
])
def test_a_failed_tracker_write_names_the_partial_set(tracker, error):
    with pytest.raises(RuntimeError) as ei:
        sizegate.file_sub_issues(tracker, "CMX-1", PLAN["slices"], "b")
    assert str(ei.value) == error


def test_file_split_refuses_on_a_failed_write_and_reuses_a_filed_split(root):
    record = {"lines": 1, "limit": 2, "rest_branch": "cmx-88-rest"}
    with patch.object(dispatcher, "get_source", return_value=_Tracker(fail_at=1)):
        out = dispatcher.file_split("cmx-88", record, PLAN["slices"])
    assert out == {"ok": False, "error": "the tracker write failed — filing slice 2 ('two') failed: boom — already filed: none"}
    with patch.object(dispatcher, "get_source", return_value=(t := _Tracker())):
        assert dispatcher.file_split("cmx-88", record, PLAN["slices"])["record"]["sub_issues"] == ["CMX-100", "CMX-101"]
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET size_gate='{\"sub_issues\": [\"CMX-7\"]}'")
        conn.commit()
    with patch.object(dispatcher, "get_source", return_value=t):
        assert dispatcher.file_split("cmx-88", record, PLAN["slices"]) == {
            "ok": True, "record": {**record, "sub_issues": ["CMX-7"]}}
    assert len(t.calls) == 2


@pytest.mark.parametrize("filed", [{"ok": True, "record": {"rest_branch": "b", "sub_issues": ["CMX-100"]}},
                                   {"ok": False, "error": "the tracker write failed — x"}])
def test_cli_files_the_split_then_continues_or_refuses(root, tmp_path, capsys, filed):
    _git(root, "branch", "cmx-88-rest")
    with patch.object(dispatcher, "verify_sweep", return_value={"ok": True, "skipped": "x"}), \
            patch.object(dispatcher, "file_split", return_value=filed) as fs, \
            patch.object(dispatcher, "request_task_finished",
                         return_value={"ok": True, "task_id": "cmx-88"}) as req:
        code = _task_finished("--split-plan", _plan(tmp_path))
    fs.assert_called_once_with("cmx-88", {"lines": 15, "limit": 300, "rest_branch": "cmx-88-rest"}, PLAN["slices"])
    if filed["ok"]:
        assert code is None and "📏 split filed: CMX-100" in capsys.readouterr().out
        req.assert_called_once_with("cmx-88", handoff=None, size_gate=filed["record"])
    else:
        assert code == 1 and "📏 the tracker write failed — x" in capsys.readouterr().out
        req.assert_not_called()


@pytest.mark.parametrize("record", [{"lines": 1, "limit": 2, "rest_branch": "cmx-88-rest", "sub_issues": ["CMX-100", "CMX-101"]},
                                    {"lines": 15, "limit": 14, "oversize_reason": "why"}])
def test_the_daemon_posts_an_inbox_notice_for_a_split(root, record):
    with patch.object(dispatcher, "_read_pr_url", return_value=None), \
            patch.object(dispatcher, "_kill_window"), \
            patch("chela.inbox.enqueue") as enqueue:
        assert dispatcher.mark_awaiting_review("cmx-88", size_gate=record)["ok"]
    if "oversize_reason" in record:
        return enqueue.assert_not_called()
    enqueue.assert_called_once_with(
        "split_filed", "cmx-88 was split: 📏 **Split hand-in:** slice 1 of 3; the rest is parked "
        "on `cmx-88-rest` and filed as CMX-100, CMX-101.", {"task_id": "cmx-88", **record})
