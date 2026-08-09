"""🔌 CMX-223 objective 2 — agents launch with a chela-owned, deterministic
``--messaging-socket-path``.

``messenger._peer_socket_path`` used to guess a target's socket purely from OUR OWN
environment (``XDG_RUNTIME_DIR``/``TMPDIR``/``getuid()``) plus its pid — a guess that
happens to hold today only because the daemon and every agent it launches share one
``XDG_RUNTIME_DIR``. Launching with an explicit ``--messaging-socket-path`` retires
that guess for any window launched this way: the dispatcher and the auto-launched
orchestrator both know the exact path up front, because they chose it.
"""
from __future__ import annotations

import sqlite3
from types import SimpleNamespace

from chela.workflow import WorkflowDef


def _wf(tmp_path):
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "CMX", "agent": {}},
        prompt_template="go {{workspace_path}}",
    )


def _conn(dispatcher):
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _capture_send_keys(monkeypatch, dispatcher, *, window_id="@100"):
    sent: list[str] = []

    def fake_run(argv, *a, **k):
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout=f"{window_id}\n", returncode=0)
        if argv[:2] == ["tmux", "send-keys"] and len(argv) > 4:
            sent.append(argv[4])
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    return sent


def test_launch_agent_carries_the_deterministic_messaging_socket_flag(monkeypatch, tmp_path):
    import chela.dispatcher as dispatcher
    from chela import config
    from chela.messenger import deterministic_peer_socket_path

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    sent = _capture_send_keys(monkeypatch, dispatcher)

    wf = _wf(tmp_path)
    conn = _conn(dispatcher)
    dispatcher._launch_agent(
        wf, "t1", "cmx-1", tmp_path / "wt", "go", conn,
        hook_vars={}, fresh_worktree=False,
    )

    claude_line = next(line for line in sent if line.startswith("claude"))
    expected = deterministic_peer_socket_path("@100")
    assert expected == config.CHELA_DIR / "socks" / "100.sock"
    assert f"--messaging-socket-path {expected}" in claude_line


def test_launch_agent_omits_the_flag_when_the_socket_path_would_overflow(monkeypatch, tmp_path):
    """🔴 The ~104-byte sun_path ceiling: an oversized path must not be handed to
    `claude` at all — a launcher that doesn't check would bind a socket at a path
    the kernel refuses, and the flag would either crash the launch or silently no-op."""
    import chela.dispatcher as dispatcher

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "messaging_socket_launch_arg", lambda _wid: None)
    sent = _capture_send_keys(monkeypatch, dispatcher)

    wf = _wf(tmp_path)
    conn = _conn(dispatcher)
    dispatcher._launch_agent(
        wf, "t1", "cmx-1", tmp_path / "wt", "go", conn,
        hook_vars={}, fresh_worktree=False,
    )

    claude_line = next(line for line in sent if line.startswith("claude"))
    assert "--messaging-socket-path" not in claude_line


def test_spawn_judge_also_carries_the_messaging_socket_flag(monkeypatch, tmp_path):
    """`_spawn_judge` calls `_launch_agent` too — same wiring, no second launch path."""
    import chela.dispatcher as dispatcher

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    sent = _capture_send_keys(monkeypatch, dispatcher)

    wf = _wf(tmp_path)
    conn = _conn(dispatcher)
    conn.execute(
        "INSERT INTO runs (task_id, workflow_path, title, status, branch_name, "
        "task_number, pr_url) VALUES (?, ?, ?, 'awaiting_review', ?, ?, ?)",
        ("abc123", str(wf.path), "do a thing", "cmx-1", 1, "https://x/pull/1"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM runs WHERE task_id=?", ("abc123",)).fetchone()

    monkeypatch.setattr(dispatcher, "detached_worktree",
                         lambda *_a, **_k: (tmp_path / "judge-abc123", True))
    assert dispatcher._spawn_judge(wf, row, "deadbeef", conn) is True

    claude_line = next(line for line in sent if line.startswith("claude"))
    assert "--messaging-socket-path" in claude_line


def test_spawn_orchestrator_window_carries_the_messaging_socket_flag(monkeypatch, tmp_path):
    import chela.personas.autolaunch as autolaunch
    from chela import config

    sent: list[str] = []

    def fake_run(argv, *a, **k):
        if argv[:2] == ["tmux", "kill-window"]:
            return SimpleNamespace(stdout="", returncode=0)
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout="@200\n", returncode=0)
        if argv[:2] == ["tmux", "send-keys"] and len(argv) > 4:
            sent.append(argv[4])
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(autolaunch.subprocess, "run", fake_run)
    (tmp_path / "ORCHESTRATOR.md").write_text("be the orchestrator")
    monkeypatch.setattr(autolaunch, "ORCHESTRATOR_PROMPT", str(tmp_path / "ORCHESTRATOR.md"))

    target = autolaunch._spawn_orchestrator_window(str(tmp_path))
    assert target == "@200"

    claude_line = next(line for line in sent if line.startswith("claude"))
    assert f"--messaging-socket-path {config.CHELA_DIR / 'socks' / '200.sock'}" in claude_line
