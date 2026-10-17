"""🔀 CMX-189 — task-finished refuses a branch behind/conflicting with origin/<base>."""
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
    """``wt``: a run on ``feature`` (edits a.txt) off a bare ``origin``; ``seed`` moves dev."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    remote, seed, wt = tmp_path / "origin.git", tmp_path / "seed", tmp_path / "wt"
    _git(tmp_path, "init", "-q", "--bare", "-b", "dev", str(remote))
    _git(tmp_path, "clone", "-q", str(remote), str(seed))
    _advance(seed, "a.txt", "a\n")
    _advance(seed, "b.txt", "b\n")
    _git(tmp_path, "clone", "-q", "-b", "dev", str(remote), str(wt))
    _git(wt, "checkout", "-qb", "feature")
    (wt / "a.txt").write_text("feature\n")
    _git(wt, "commit", "-qam", "feature")
    _run_row(tmp_path, wt)
    return seed, wt


def _advance(seed: Path, name: str, body: str) -> None:
    (seed / name).write_text(body)
    _git(seed, "add", name)
    _git(seed, "commit", "-qm", f"dev {name}")
    _git(seed, "push", "-q", "origin", "dev")


def test_a_conflicting_branch_is_refused_listing_the_file(repo):
    _advance(repo[0], "a.txt", "dev\n")
    for allow in (False, True):         # --allow-behind never lets a conflict through
        out = dispatcher.check_base_sync("cmx-88", allow)
        assert out["ok"] is False
        assert "CONFLICTS with origin/dev in 1 file(s):\n     a.txt\n" in out["error"]
        assert "--allow-behind" not in out["error"]


def test_behind_but_clean_is_refused_without_the_flag_and_recorded_with_it(repo):
    _advance(repo[0], "b.txt", "dev\n")
    out = dispatcher.check_base_sync("cmx-88")
    assert out["ok"] is False
    assert out["error"].startswith("this branch is 1 commit(s) behind origin/dev")
    assert "pass --allow-behind (recorded)" in out["error"]
    assert "     git fetch origin dev\n     git merge origin/dev\n" in out["error"]
    assert "do NOT rebase" in out["error"] and "git rebase" not in out["error"]
    assert dispatcher.check_base_sync("cmx-88", True) == {
        "ok": True, "record": {"base": "dev", "behind": 1, "allow_behind": True}}


def test_up_to_date_is_accepted_and_a_missing_remote_fails_closed(repo, tmp_path):
    assert dispatcher.check_base_sync("cmx-88") == {"ok": True, "record": None}
    _git(repo[1], "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    out = dispatcher.check_base_sync("cmx-88", True)
    assert out["ok"] is False
    assert out["error"].startswith("could not check this branch against its base — "
                                   "`git fetch origin dev` failed: ")
    assert "Unknown is not in sync" in out["error"]
    assert dispatcher.check_base_sync("cmx-1")["error"] == \
        "no run with a worktree on record for cmx-1"


@pytest.mark.parametrize("name, extra, said", [
    ("a.txt", ("--allow-behind",), "CONFLICTS with origin/dev"),
    ("b.txt", (), "is 1 commit(s) behind origin/dev")])   # only the flag lets this through
def test_cli_refuses_before_the_size_gate_self_check_and_sweep(repo, capsys, name, extra, said):
    _advance(repo[0], name, "dev\n")
    with patch.object(dispatcher, "check_hand_in_size") as size, \
            patch.object(dispatcher, "verify_sweep") as swept, \
            patch.object(dispatcher, "request_task_finished") as req:
        assert _task_finished(*extra) == 1
    assert f"task-finished: 🔀 this branch {said}" in capsys.readouterr().out
    assert not size.called and not swept.called and not req.called


@pytest.mark.parametrize("extra, record", [
    ((), None), (("--allow-behind",), {"base": "dev", "behind": 1, "allow_behind": True})])
def test_cli_hands_the_record_to_the_marker(repo, extra, record, capsys):
    if record:
        _advance(repo[0], "b.txt", "dev\n")
    with patch.object(dispatcher, "check_no_new_guards", return_value=False), \
            patch.object(dispatcher, "verify_sweep", return_value={"ok": True, "skipped": "x"}):
        assert _task_finished(*extra) is None
    marker = json.loads(dispatcher._task_finished_request_path(wt := repo[1]).read_text())
    assert marker.get("base_sync") == record
    assert dispatcher._read_task_finished_size_gate(wt, "base_sync") == record


@pytest.mark.parametrize("record", [{"base": "dev", "behind": 3, "allow_behind": True}, None])
def test_the_judge_sees_a_recorded_hand_in(repo, record):
    with dispatcher._db() as conn:      # tick() persisting it: test_task_finished_handoff
        conn.execute("UPDATE runs SET base_sync=?", (record and json.dumps(record),))
        row = conn.execute("SELECT * FROM runs").fetchone()
    wf = parse_workflow(Path("W.md"), "---\nproject_key: TT\n---\n")
    prompt = dispatcher.render_prompt(dispatcher.JUDGE_PROMPT,
                                      dispatcher._judge_vars(wf, row, repo[1], "deadbeef"))
    line = "🔀 Handed in 3 commit(s) behind origin/dev with --allow-behind (merges cleanly)."
    assert (line in prompt) is bool(record) and ("Handed in" in prompt) is bool(record)


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
    ok, out = subprocess.CompletedProcess, "abc\nx.py\ny.py\nx.py\n\nAuto-merging z.py\nCONFLICT (content): x.py\n"
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

