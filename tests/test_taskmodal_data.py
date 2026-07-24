"""Backend data guards for the task-detail modal (Work-view redesign).

The modal needs the task's brief text on BOTH sides of the claim boundary:

  * an un-dispatched task only exists as an ``open_tasks`` entry, so
    ``/api/dispatcher`` must hand back ``Task.raw`` (chela.sources.Task) for it —
    see ``test_open_tasks_include_raw``.
  * once claimed, the task drops out of ``open_tasks`` (it becomes a run), so the
    brief has to be COPIED onto the run row at claim time (``dispatcher._spawn``)
    into the new ``runs.brief`` column, or the modal would go blank the instant a
    task starts running — see ``test_spawn_persists_brief_from_task_raw`` and
    ``test_spawn_failure_path_also_persists_brief``.

Both additions are additive-only: no status/lifecycle logic changes, and a
pre-migration row simply reads ``brief IS NULL`` (dispatcher.ensure_schema's
idempotent ALTER TABLE pattern — see the ``brief`` migration tuple).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher
from chela.dashboard import app as dash
from chela.sources import Task
from chela.workflow import WorkflowDef

_real_run = __import__("subprocess").run


# --- /api/dispatcher: open_tasks carry raw ----------------------------------

@pytest.fixture
def client():
    return dash.app.test_client()


def _no_repo_workflow(monkeypatch):
    monkeypatch.setattr(dash, "_repo_root_workflow", lambda: None)


def test_open_tasks_include_raw(monkeypatch, client, tmp_path):
    """🔴 GUARD: drop the `"raw": t.raw` line from api_dispatcher's open_tasks
    comprehension (or revert to hand-picking id/title/file/line_number only, as
    it was before) and this goes RED — an un-dispatched task would have nothing
    for the task-detail modal's brief pane to render."""
    _no_repo_workflow(monkeypatch)
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "WORKFLOW.md").write_text(
        "---\nproject_key: XYZ\ntracker:\n  kind: markdown\n  path: TODO.md\n---\nprompt\n"
    )
    (repo / "TODO.md").write_text("## Open\n\n- [ ] ship it\n")
    wf_path = (repo / "WORKFLOW.md").resolve()
    monkeypatch.setattr(dash, "DISPATCH_WORKFLOWS", [wf_path])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])

    resp = client.get("/api/dispatcher")
    data = resp.get_json()
    tasks = data["workflows"][0]["open_tasks"]
    assert len(tasks) == 1
    assert tasks[0]["raw"] == "- [ ] ship it"


# --- dispatcher._spawn: brief persisted at claim time -----------------------

def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _repo(tmp_path: Path) -> Path:
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


class _FakeTmux:
    """Same fake as tests/test_dispatcher_task_number.py — tmux calls are faked,
    every other command (git) passes through to the real subprocess.run."""

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


def _spawn(wf: WorkflowDef, task: Task, conn: sqlite3.Connection, attempt: int = 1) -> bool:
    fake = _FakeTmux()
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        return dispatcher._spawn(wf, task, attempt=attempt, conn=conn)


def test_spawn_persists_brief_from_task_raw(tmp_path):
    """🔴 GUARD: the claim-path INSERT in dispatcher._spawn must copy
    `task.raw` into the new `brief` column. Drop the `brief` column/value from
    that INSERT (or from the ON CONFLICT UPDATE, which a retry exercises) and
    this goes RED — a claimed run would carry NULL where the modal expects the
    brief that a moment ago was visible in open_tasks."""
    repo = _repo(tmp_path)
    root = tmp_path / "worktrees"
    conn = _conn()
    raw_text = "- [ ] **Do the thing.** OBJECTIVE: build X. BOUNDARIES: only file Y."
    task = Task(id="brief-task", title="Do the thing", file="TODO.md", line_number=1, raw=raw_text)

    assert _spawn(_wf(repo, root), task, conn) is True

    row = conn.execute("SELECT brief FROM runs WHERE task_id='brief-task'").fetchone()
    assert row["brief"] == raw_text


def test_spawn_retry_refreshes_brief_via_on_conflict(tmp_path):
    """The ON CONFLICT DO UPDATE branch (a failed→retry re-dispatch of the SAME
    task_id) must also carry `brief` — not just the first INSERT. Drop
    `brief=excluded.brief` from the ON CONFLICT clause and this goes RED."""
    repo = _repo(tmp_path)
    root = tmp_path / "worktrees"
    conn = _conn()
    task = Task(id="retry-task", title="Retry me", file="TODO.md", line_number=1,
                raw="- [ ] Retry me")

    assert _spawn(_wf(repo, root), task, conn, attempt=1) is True
    conn.execute("UPDATE runs SET status='failed', brief=NULL WHERE task_id='retry-task'")
    conn.commit()

    assert _spawn(_wf(repo, root), task, conn, attempt=2) is True
    row = conn.execute("SELECT brief FROM runs WHERE task_id='retry-task'").fetchone()
    assert row["brief"] == "- [ ] Retry me"


def test_brief_column_defaults_null_for_a_pre_migration_row(tmp_path):
    """Additive-migration guard: a row written before `brief` existed (simulated
    here by inserting without it) reads back NULL, not an error — the schema
    change must degrade gracefully, exactly like every other ALTER TABLE
    migration in ensure_schema."""
    conn = _conn()
    conn.execute(
        "INSERT INTO runs (task_id, workflow_path, title, status) VALUES (?, ?, ?, ?)",
        ("legacy-task", "/x/WORKFLOW.md", "legacy", "done"),
    )
    conn.commit()
    row = conn.execute("SELECT brief FROM runs WHERE task_id='legacy-task'").fetchone()
    assert row["brief"] is None
