"""🔀 CMX-189 — ``chela task-finished`` refuses a branch that conflicts with, or is behind,
``origin/<base>``, and tells the agent how to merge it in. Fixture: a tmp repo with a local
bare remote (never the live repo)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import call, patch

import pytest

from chela import basesync, dispatcher
from chela.workflow import parse_workflow
from tests.test_judge_sweep import _git, _run_row, _task_finished

REAL_BASE_SYNC = True


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """``wt`` is a run's worktree on ``feature`` (edits a.txt) forked from ``dev`` on a bare
    ``origin``; ``seed`` is another clone that can move ``origin/dev`` on."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    remote, seed, wt = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "wt"
    _git(tmp_path, "init", "-q", "--bare", "-b", "dev", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    _git(seed, "checkout", "-qb", "dev")
    (seed / "a.txt").write_text("a\n")
    (seed / "b.txt").write_text("b\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-qm", "base")
    _git(seed, "push", "-q", "origin", "dev")
    _git(tmp_path, "clone", "-q", "-b", "dev", str(remote), str(wt))
    _git(wt, "checkout", "-qb", "feature")
    (wt / "a.txt").write_text("feature\n")
    _git(wt, "commit", "-qam", "feature")
    _run_row(tmp_path, wt)
    return seed, wt


def _advance(seed: Path, name: str, body: str) -> None:
    (seed / name).write_text(body)
    _git(seed, "commit", "-qam", f"dev {name}")
    _git(seed, "push", "-q", "origin", "dev")


def test_a_conflicting_branch_is_refused_listing_the_file(repo):
    seed, _ = repo
    _advance(seed, "a.txt", "dev\n")
    for allow in (False, True):         # --allow-behind never lets a conflict through
        out = dispatcher.check_base_sync("cmx-88", allow)
        assert out["ok"] is False
        assert "CONFLICTS with origin/dev in 1 file(s):\n     a.txt\n" in out["error"]
        assert "--allow-behind" not in out["error"]


def test_behind_but_clean_is_refused_without_the_flag_and_recorded_with_it(repo):
    seed, _ = repo
    _advance(seed, "b.txt", "dev\n")
    out = dispatcher.check_base_sync("cmx-88")
    assert out["ok"] is False
    assert out["error"].startswith("this branch is 1 commit(s) behind origin/dev")
    assert "pass --allow-behind (recorded)" in out["error"]
    assert dispatcher.check_base_sync("cmx-88", True) == {
        "ok": True, "record": {"base": "dev", "behind": 1, "allow_behind": True}}


def test_an_up_to_date_branch_is_accepted(repo):
    assert dispatcher.check_base_sync("cmx-88") == {"ok": True, "record": None}


def test_a_missing_remote_fails_closed(repo, tmp_path):
    _, wt = repo
    _git(wt, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    out = dispatcher.check_base_sync("cmx-88", True)
    assert out["ok"] is False
    assert out["error"].startswith("could not check this branch against its base — "
                                   "`git fetch origin dev` failed: ")
    assert "Unknown is not in sync" in out["error"]
    assert dispatcher.check_base_sync("cmx-1")["error"] == \
        "no run with a worktree on record for cmx-1"


def test_an_unreadable_workflow_fails_closed(repo, tmp_path):
    (tmp_path / "WORKFLOW.md").write_text("---\n: [\n---\n")
    out = dispatcher.check_base_sync("cmx-88", True)
    assert out["ok"] is False and out["error"].startswith("could not check this branch")


def test_cli_refuses_a_conflict_before_the_self_check_and_sweep(repo, capsys):
    seed, _ = repo
    _advance(seed, "a.txt", "dev\n")
    with patch.object(dispatcher, "check_hand_in_size") as size, \
            patch.object(dispatcher, "verify_sweep") as swept, \
            patch.object(dispatcher, "request_task_finished") as req:
        assert _task_finished("--allow-behind") == 1
    out = capsys.readouterr().out
    assert "task-finished: 🔀 this branch CONFLICTS with origin/dev" in out
    assert "     a.txt\n" in out and "git merge origin/dev" in out
    assert not size.called and not swept.called and not req.called


@pytest.mark.parametrize("extra, record", [
    ((), None), (("--allow-behind",), {"base": "dev", "behind": 1, "allow_behind": True})])
def test_cli_hands_the_record_to_the_marker(repo, extra, record, capsys):
    seed, wt = repo
    if record:
        _advance(seed, "b.txt", "dev\n")
    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
            patch.object(dispatcher, "verify_sweep", return_value={"ok": True, "skipped": "x"}):
        assert _task_finished(*extra) is None
    marker = json.loads(dispatcher._task_finished_request_path(wt).read_text())
    assert marker.get("base_sync") == record
    assert dispatcher._read_task_finished_size_gate(wt, "base_sync") == record


@pytest.mark.parametrize("record", [{"base": "dev", "behind": 3, "allow_behind": True}, None])
def test_the_daemon_records_it_on_the_run_and_the_judge_sees_it(repo, record):
    _, wt = repo
    with patch.object(dispatcher, "_read_pr_url", return_value=None), \
            patch.object(dispatcher, "_read_pr_status", return_value=(None, None)), \
            patch.object(dispatcher, "_kill_window"):
        dispatcher.request_task_finished("cmx-88", base_sync=record)
        assert dispatcher.mark_awaiting_review(
            "cmx-88", base_sync=dispatcher._read_task_finished_size_gate(wt, "base_sync"))["ok"]
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs").fetchone()
    wf = parse_workflow(Path("W.md"), "---\nproject_key: TT\n---\n")
    prompt = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT,
                                      dispatcher._judge_vars(wf, row, wt, "deadbeef"))
    line = "🔀 Handed in 3 commit(s) behind origin/dev with --allow-behind (merges cleanly)."
    if record is None:
        assert row["base_sync"] is None and "Handed in" not in prompt
    else:
        assert json.loads(row["base_sync"]) == record and line in prompt


def test_check_pins_every_git_call(tmp_path):
    ok = subprocess.CompletedProcess
    replies = [ok([], 0, "", ""), ok([], 0, "2\n", ""), ok([], 0, "abc123\n", "")]
    with patch.object(basesync.subprocess, "run", side_effect=replies) as run:
        assert basesync.check(tmp_path, "dev") == {"behind": 2, "conflicts": []}
    kw = {"cwd": str(tmp_path), "capture_output": True, "text": True}
    assert run.call_args_list == [
        call(["git", "fetch", "--quiet", "origin", "+refs/heads/dev:refs/remotes/origin/dev"],
             **kw, timeout=120),
        call(["git", "rev-list", "--count", "HEAD..origin/dev"], **kw, timeout=60),
        call(["git", "merge-tree", "--write-tree", "--name-only", "HEAD", "origin/dev"],
             **kw, timeout=60),
    ]


def test_conflicts_are_read_up_to_the_blank_line_once_each(tmp_path):
    ok = subprocess.CompletedProcess
    out = "abc\nx.py\ny.py\nx.py\n\nAuto-merging z.py\nCONFLICT (content): x.py\n"
    with patch.object(basesync.subprocess, "run",
                      side_effect=[ok([], 0, "", ""), ok([], 0, "4", ""), ok([], 1, out, "")]):
        assert basesync.check(tmp_path, "dev") == {"behind": 4, "conflicts": ["x.py", "y.py"]}


@pytest.mark.parametrize("replies, error", [
    ([(128, "", "boom")], "`git fetch origin dev` failed: boom"),
    ([(0, "", ""), (128, "", "bad ref")], "could not count commits behind origin/dev: bad ref"),
    ([(0, "", ""), (0, "", "")], "could not count commits behind origin/dev"),
    ([(0, "", ""), (0, "1", ""), (128, "", "old git")],
     "`git merge-tree` against origin/dev failed: old git"),
    ([(0, "", ""), (0, "1", ""), (1, "abc\n\nmsg\n", "")], "but named no file"),
])
def test_every_unreadable_answer_fails_closed(tmp_path, replies, error):
    side = [subprocess.CompletedProcess([], rc, o, e) for rc, o, e in replies]
    with patch.object(basesync.subprocess, "run", side_effect=side), \
            pytest.raises(RuntimeError, match=error.replace("`", ".").replace("(", ".")):
        basesync.check(tmp_path, "dev")


@pytest.mark.parametrize("exc", [FileNotFoundError("no git"), subprocess.TimeoutExpired("g", 1)])
def test_git_that_cannot_run_fails_closed(tmp_path, exc):
    with patch.object(basesync.subprocess, "run", side_effect=exc), \
            pytest.raises(RuntimeError, match="could not run"):
        basesync.check(tmp_path, "dev")


def test_the_refusal_says_merge_never_rebase():
    text = basesync.refusal("dev", 2, [])
    assert "     git fetch origin dev\n     git merge origin/dev\n" in text
    assert "do NOT rebase" in text and "git rebase" not in text


@pytest.mark.parametrize("record, line", [
    ({"base": "dev", "behind": 2, "allow_behind": True},
     "🔀 Handed in 2 commit(s) behind origin/dev with --allow-behind (merges cleanly)."),
    ({"base": "dev", "behind": 2, "allow_behind": False}, ""), ({}, ""),
])
def test_render(record, line):
    assert basesync.render(record) == line
