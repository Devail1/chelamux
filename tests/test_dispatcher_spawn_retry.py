"""Tests for the spawn-retry orphan-window fix in dispatcher._spawn.

Regression cover: a retry re-entering _spawn with the same window_name made
tmux create a *second* window of that name; the by-name target
`<session>:<name>` then went ambiguous, send-keys exited non-zero, and the run
flapped `failed` while duplicate windows orphaned on the wall.

The fix: (1) kill any pre-existing same-name window(s) before new-window, and
(2) create the window with `-P -F '#{window_id}'` and target THIS spawn by the
captured @id rather than the bare name.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from chela import dispatcher
from chela.sources import Task
from chela.workflow import WorkflowDef


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE runs (
            task_id TEXT PRIMARY KEY, workflow_path TEXT, title TEXT, status TEXT,
            window_name TEXT, worktree_path TEXT, branch_name TEXT,
            started_at TEXT, ended_at TEXT, attempt INTEGER, last_error TEXT,
            pr_url TEXT, pr_state TEXT, pr_mergeable TEXT, task_number INTEGER,
            idle_nudged_at TEXT
        )"""
    )
    return conn


def _wf(tmp_path: Path) -> WorkflowDef:
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "TEST"},
        prompt_template="go {{workspace_path}}",
    )


def _task(tmp_path: Path) -> Task:
    return Task(
        id="abc123", title="do a thing", file=str(tmp_path / "TODO.md"),
        line_number=7, raw="- [ ] do a thing",
    )


class _FakeTmux:
    """Minimal in-memory tmux stand-in driving subprocess.run for _spawn.

    Records the ordered command stream and models duplicate-name windows: each
    new-window appends a window (id, name); list-windows reports them; the @id
    handed back by `-P -F '#{window_id}'` is what we expect the caller to target.
    """

    def __init__(self, preexisting: list[tuple[str, str]] | None = None):
        # (window_id, window_name) pairs, oldest first.
        self.windows: list[tuple[str, str]] = list(preexisting or [])
        self._next_id = 100
        self.calls: list[list[str]] = []

    def run(self, cmd, *args, **kwargs):
        # Hooks run with shell=True and a string cmd — none configured here, so
        # every call in these tests is a tmux argv list.
        assert isinstance(cmd, list), cmd
        self.calls.append(cmd)

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

    # Convenience accessors over the recorded call stream.
    def _kinds(self) -> list[str]:
        return [c[1] for c in self.calls if c[0] == "tmux"]

    def killed_targets(self) -> list[str]:
        return [
            c[c.index("-t") + 1]
            for c in self.calls
            if c[:2] == ["tmux", "kill-window"]
        ]

    def send_keys_targets(self) -> list[str]:
        return [
            c[c.index("-t") + 1]
            for c in self.calls
            if c[:2] == ["tmux", "send-keys"]
        ]


def test_retry_kills_stale_window_before_new_window(tmp_path):
    """A retry finds a leftover window named like the branch; it's killed before
    new-window runs, and only one window of that name remains afterwards."""
    wf, task = _wf(tmp_path), _task(tmp_path)
    worktree = tmp_path / "wt"
    branch = "test-1"  # = project_key.lower() + "-" + task_number
    fake = _FakeTmux(preexisting=[("@42", branch)])

    captured: dict = {}

    def _send(window_id, text):
        captured["send_tmux_target"] = window_id
        return True

    with patch.object(dispatcher, "ensure_worktree", return_value=(worktree, False)), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "send_tmux", side_effect=_send), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        ok = dispatcher._spawn(wf, task, attempt=2, conn=_conn())

    assert ok is True

    # The stale @42 window was killed, and it happened before new-window.
    kinds = fake._kinds()
    assert "kill-window" in kinds
    assert "new-window" in kinds
    assert kinds.index("kill-window") < kinds.index("new-window")
    assert f"{dispatcher.TMUX_SESSION}:@42" in fake.killed_targets()

    # No duplicate-named windows survive: exactly one window named `branch`.
    assert sum(1 for _, n in fake.windows if n == branch) == 1


def test_spawn_targets_captured_id_not_bare_name(tmp_path):
    """The agent-cmd send-keys and the prompt send target the fresh window's
    @id (<session>:@<id>), never the ambiguous bare name."""
    wf, task = _wf(tmp_path), _task(tmp_path)
    worktree = tmp_path / "wt"
    fake = _FakeTmux()

    captured: dict = {}

    def _send(window_id, text):
        captured["send_tmux_target"] = window_id
        return True

    ready_targets: list[str] = []

    def _ready(window_id, *a, **k):
        ready_targets.append(window_id)
        return True

    with patch.object(dispatcher, "ensure_worktree", return_value=(worktree, False)), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "send_tmux", side_effect=_send), \
         patch.object(dispatcher, "_wait_for_ready", side_effect=_ready):
        dispatcher._spawn(wf, task, attempt=1, conn=_conn())

    # new-window returned @100 (FakeTmux's first id).
    new_id = "@100"
    # agent-cmd send-keys targeted <session>:@100, not <session>:test-1.
    assert f"{dispatcher.TMUX_SESSION}:{new_id}" in fake.send_keys_targets()
    assert f"{dispatcher.TMUX_SESSION}:test-1" not in fake.send_keys_targets()
    # readiness poll and prompt send used the bare @id (helpers prepend session).
    assert ready_targets == [new_id]
    assert captured["send_tmux_target"] == new_id


def test_runs_row_stores_human_window_name(tmp_path):
    """Display/reconcile stay by-name: the runs row records `branch`, not @id."""
    wf, task = _wf(tmp_path), _task(tmp_path)
    worktree = tmp_path / "wt"
    fake = _FakeTmux()
    conn = _conn()

    with patch.object(dispatcher, "ensure_worktree", return_value=(worktree, False)), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        dispatcher._spawn(wf, task, attempt=1, conn=conn)

    row = conn.execute("SELECT window_name, status FROM runs WHERE task_id=?", (task.id,)).fetchone()
    assert row["window_name"] == "test-1"
    assert row["status"] == "running"


def test_kill_windows_named_only_matches_exact_name(tmp_path):
    """_kill_windows_named kills every same-name window and leaves others."""
    fake = _FakeTmux(preexisting=[
        ("@1", "test-1"),
        ("@2", "test-1"),   # the stacked duplicate
        ("@3", "other"),
        ("@4", "test-10"),  # prefix overlap must NOT match
    ])
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        dispatcher._kill_windows_named("test-1")

    surviving = {n for _, n in fake.windows}
    assert "test-1" not in surviving
    assert surviving == {"other", "test-10"}
    assert fake.killed_targets() == [
        f"{dispatcher.TMUX_SESSION}:@1",
        f"{dispatcher.TMUX_SESSION}:@2",
    ]


def test_new_window_falls_back_to_name_when_id_unparseable(tmp_path):
    """If new-window yields no parseable @id, _new_window degrades to the name
    rather than targeting a garbage id."""
    class _R:
        returncode = 0
        stdout = "not-an-id\n"

    with patch.object(dispatcher.subprocess, "run", return_value=_R()):
        assert dispatcher._new_window("test-1", "/tmp/wt") == "test-1"
