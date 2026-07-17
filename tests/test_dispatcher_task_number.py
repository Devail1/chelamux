"""Tests for CMX-104 guard 🔴#1 — task_number must skip slots a leftover branch

already occupies, not just slots a live `runs` row occupies.

`_prune_done_rows`/`delete_run` drop `runs` rows while leaving the branch (local or
on `origin`) behind — see `chela.worktree.ensure_worktree`'s docstring. A dispatch
that derives `task_number` from `MAX(task_number) FROM runs` alone will happily hand
a fresh task_id a number some other branch already owns: two open PRs both titled the
same `CMX-<n>` (this session produced exactly that for cmx-103), or a
`git push -u origin {branch}` rejected non-fast-forward against a same-named branch
left by a PR merged out-of-band via `gh` (bypassing `contract._squash_merge`'s
`push origin --delete`).
"""
from __future__ import annotations

import sqlite3
import subprocess
from pathlib import Path
from unittest.mock import patch

from chela import dispatcher
from chela.sources import Task
from chela.workflow import WorkflowDef

_real_run = subprocess.run


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _repo(tmp_path: Path) -> Path:
    """A real local git repo on `main`, with an `origin` remote (a bare clone)."""
    repo_path = tmp_path / "repo"
    _real_run(["git", "init", "-q", "-b", "main", str(repo_path)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _real_run(["git", "-C", str(repo_path), "config", k, v], check=True, capture_output=True)
    (repo_path / "README.md").write_text("seed\n")
    _real_run(["git", "-C", str(repo_path), "add", "README.md"], check=True, capture_output=True)
    _real_run(["git", "-C", str(repo_path), "commit", "-q", "-m", "seed"], check=True, capture_output=True)

    remote_path = tmp_path / "remote.git"
    _real_run(["git", "init", "-q", "--bare", str(remote_path)], check=True, capture_output=True)
    _real_run(["git", "-C", str(repo_path), "remote", "add", "origin", str(remote_path)],
               check=True, capture_output=True)
    _real_run(["git", "-C", str(repo_path), "push", "-q", "origin", "main"], check=True, capture_output=True)
    return repo_path


def _wf(repo_path: Path, root: Path) -> WorkflowDef:
    return WorkflowDef(
        path=repo_path / "WORKFLOW.md",
        config={"project_key": "TEST", "workspace": {"root": str(root), "base_branch": "main"}},
        prompt_template="go {{workspace_path}}",
    )


def _task(task_id: str, title: str = "do a thing") -> Task:
    return Task(id=task_id, title=title, file="TODO.md", line_number=1, raw=f"- [ ] {title}")


class _FakeTmux:
    """Fakes tmux calls; passes every other command (git) through to the real
    subprocess.run so `_spawn`'s real `ensure_worktree` + `_max_existing_task_number`
    run against the real repo built by `_repo`."""

    def __init__(self):
        self.windows: list[tuple[str, str]] = []
        self._next_id = 100

    def run(self, cmd, *args, **kwargs):
        if not (isinstance(cmd, list) and cmd[:1] == ["tmux"]):
            return _real_run(cmd, *args, **kwargs)

        class R:
            returncode = 0
            stdout = ""

        if cmd[:2] == ["tmux", "list-windows"]:
            R.stdout = "".join(f"{wid} {name}\n" for wid, name in self.windows)
        elif cmd[:2] == ["tmux", "new-window"]:
            name = cmd[cmd.index("-n") + 1]
            wid = f"@{self._next_id}"
            self._next_id += 1
            self.windows.append((wid, name))
            R.stdout = wid + "\n"
        elif cmd[:2] == ["tmux", "kill-window"]:
            target = cmd[cmd.index("-t") + 1]
            wid = target.split(":", 1)[1]
            self.windows = [(w, n) for w, n in self.windows if w != wid]
        return R()


def _spawn(wf: WorkflowDef, task: Task, conn: sqlite3.Connection) -> bool:
    fake = _FakeTmux()
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        return dispatcher._spawn(wf, task, attempt=1, conn=conn)


def test_dispatch_skips_a_number_only_a_local_branch_still_occupies(tmp_path):
    """A prior task_id's runs row is gone (pruned/deleted) but its branch `test-5`
    still exists locally with no worktree attached. A fresh task must NOT reuse 5."""
    repo = _repo(tmp_path)
    _real_run(["git", "-C", str(repo), "branch", "test-5"], check=True, capture_output=True)
    root = tmp_path / "worktrees"
    conn = _conn()

    ok = _spawn(_wf(repo, root), _task("new-task"), conn)

    assert ok is True
    row = conn.execute("SELECT task_number, branch_name FROM runs WHERE task_id='new-task'").fetchone()
    assert row["task_number"] == 6
    assert row["branch_name"] == "test-6"


def test_dispatch_skips_a_number_only_a_remote_branch_still_occupies(tmp_path):
    """Reproduces the `origin/cmx-103` push collision: a same-named branch survives on
    `origin` (a PR merged by hand via `gh`, bypassing the squash-merge's remote delete)
    with no local branch and no runs row. A fresh task must NOT reuse that number —
    landing on it would make `git push -u origin test-3` reject non-fast-forward."""
    repo = _repo(tmp_path)
    _real_run(["git", "-C", str(repo), "checkout", "-q", "-b", "test-3"], check=True, capture_output=True)
    _real_run(["git", "-C", str(repo), "push", "-q", "origin", "test-3"], check=True, capture_output=True)
    _real_run(["git", "-C", str(repo), "checkout", "-q", "main"], check=True, capture_output=True)
    _real_run(["git", "-C", str(repo), "branch", "-D", "test-3"], check=True, capture_output=True)
    root = tmp_path / "worktrees"
    conn = _conn()

    ok = _spawn(_wf(repo, root), _task("new-task"), conn)

    assert ok is True
    row = conn.execute("SELECT task_number, branch_name FROM runs WHERE task_id='new-task'").fetchone()
    assert row["task_number"] == 4
    assert row["branch_name"] == "test-4"


def test_a_retry_still_reuses_its_own_task_number(tmp_path):
    """The reused-number fix must not break the pre-existing retry path: a re-dispatch
    of the SAME task_id keeps ITS OWN task_number even though that number's branch
    (its own, from attempt 1) legitimately exists."""
    repo = _repo(tmp_path)
    root = tmp_path / "worktrees"
    conn = _conn()
    task = _task("same-task")

    assert _spawn(_wf(repo, root), task, conn) is True
    first = conn.execute("SELECT task_number FROM runs WHERE task_id='same-task'").fetchone()["task_number"]
    assert first == 1

    conn.execute("UPDATE runs SET status='failed' WHERE task_id='same-task'")
    conn.commit()
    assert _spawn(_wf(repo, root), task, conn) is True

    second = conn.execute("SELECT task_number, attempt FROM runs WHERE task_id='same-task'").fetchone()
    assert second["task_number"] == 1


def test_dispatch_with_no_leftover_branches_still_mints_one(tmp_path):
    """Sanity: with nothing but a fresh repo, the very first dispatch still gets 1 —
    the branch scan finding nothing must not itself break minting."""
    repo = _repo(tmp_path)
    root = tmp_path / "worktrees"
    conn = _conn()

    ok = _spawn(_wf(repo, root), _task("only-task"), conn)

    assert ok is True
    row = conn.execute("SELECT task_number FROM runs WHERE task_id='only-task'").fetchone()
    assert row["task_number"] == 1
