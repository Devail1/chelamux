"""📵🔒 CMX-115 — a dispatched agent/judge window must never hold CHELA_NOTIFY_URL.

The daemon carries `CHELA_NOTIFY_URL` (`~/.chela/chela.env`) so PRODUCTION can push real
phone notifications. `_launch_agent` spawns every agent AND judge window inside the same
tmux session, which inherits that same environment — so without a strip, any agent that
runs inbox/notify code (cmx-113 did), a manual repro, or `chela` command reaching
`notify.send` pushes a REAL ntfy to the human's phone with whatever test fixture happens
to be sitting in the worktree. `_spawn_judge` is exposed the same way because it calls
`_launch_agent` too — there is no second launch path to fix.
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


def _capture_send_keys(monkeypatch, dispatcher):
    """Fake `subprocess.run` for `_launch_agent`'s tmux calls; records every send-keys line."""
    sent: list[str] = []

    def fake_run(argv, *a, **k):
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout="@100\n", returncode=0)
        if argv[:2] == ["tmux", "send-keys"] and len(argv) > 4:
            sent.append(argv[4])
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    return sent


def _launch(monkeypatch, dispatcher, tmp_path, **kwargs):
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    sent = _capture_send_keys(monkeypatch, dispatcher)
    wf = _wf(tmp_path)
    conn = _conn(dispatcher)
    dispatcher._launch_agent(
        wf, "t1", "cmx-1", tmp_path / "wt", "go", conn,
        hook_vars={}, fresh_worktree=False, **kwargs,
    )
    return sent


def test_launch_agent_strips_notify_url_before_the_agent_command(monkeypatch, tmp_path):
    """🔴 The `unset` line must land in the window BEFORE `claude …` is typed — a strip that
    ran after the agent started would already be too late for the process it launched."""
    import chela.dispatcher as dispatcher

    sent = _launch(monkeypatch, dispatcher, tmp_path)

    unset_idx = next(i for i, line in enumerate(sent) if line.startswith("unset"))
    claude_idx = next(i for i, line in enumerate(sent) if line.startswith("claude"))
    assert unset_idx < claude_idx
    assert "CHELA_NOTIFY_URL" in sent[unset_idx]


def test_the_strip_is_surgical_not_a_blanket_wipe(monkeypatch, tmp_path):
    """🔴 Simulates applying the exact `unset ...` line `_launch_agent` sends against a
    daemon env where CHELA_NOTIFY_URL is set for real production pushes: the resulting
    agent env must have lost the URL but kept everything else the agent needs. A "fix"
    that unsets everything (or nothing) must fail this."""
    import chela.dispatcher as dispatcher

    sent = _launch(monkeypatch, dispatcher, tmp_path)
    unset_line = next(line for line in sent if line.startswith("unset"))
    stripped_vars = unset_line.split()[1:]
    assert stripped_vars, "the unset line names no variables"

    daemon_env = {
        "CHELA_NOTIFY_URL": "https://ntfy.sh/real-liav-topic",
        "CHELA_TMUX_SESSION": "chela",
        "PATH": "/usr/bin:/bin",
    }
    agent_env = dict(daemon_env)
    for var in stripped_vars:
        agent_env.pop(var, None)

    assert not agent_env.get("CHELA_NOTIFY_URL")
    assert agent_env["CHELA_TMUX_SESSION"] == "chela"
    assert agent_env["PATH"] == "/usr/bin:/bin"


def test_notify_is_disabled_once_the_launch_env_strips_the_url(monkeypatch, tmp_path):
    """🔴 Ties the strip to the actual gate `notify.send` checks: an agent/judge process
    booting under the stripped env must see `notify.enabled() is False`."""
    import chela.dispatcher as dispatcher
    import chela.notify as notify

    sent = _launch(monkeypatch, dispatcher, tmp_path)
    unset_line = next(line for line in sent if line.startswith("unset"))
    stripped_vars = unset_line.split()[1:]

    daemon_env = {"CHELA_NOTIFY_URL": "https://ntfy.sh/real-liav-topic"}
    agent_env = dict(daemon_env)
    for var in stripped_vars:
        agent_env.pop(var, None)

    monkeypatch.setattr(notify, "NOTIFY_URL", agent_env.get("CHELA_NOTIFY_URL", ""))
    assert notify.enabled() is False


def test_spawn_judge_strips_notify_url_too(monkeypatch, tmp_path):
    """🔴 `_spawn_judge` calls `_launch_agent` — same wiring, same exposure (the judge runs
    the FULL pytest suite, and any test reaching `notify.send` unmocked would push real)."""
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

    unset_idx = next(i for i, line in enumerate(sent) if line.startswith("unset"))
    claude_idx = next(i for i, line in enumerate(sent) if line.startswith("claude"))
    assert unset_idx < claude_idx
    assert "CHELA_NOTIFY_URL" in sent[unset_idx]


def test_the_suite_itself_cannot_push_a_real_notification(monkeypatch):
    """🔴 THE FENCE. CMX-115 (above) strips `CHELA_NOTIFY_URL` from every tmux-spawned
    agent and judge — but NOT from a maintainer running `pytest` or `chela judge run` in
    their own shell, which is how this suite is normally run by hand. That gap paged the
    operator 109 times on 2026-09-01, from
    `test_auto_apply_sweep_never_calls_apply_with_a_bespoke_repo_arg` — which drives the
    real `auto_apply_sweep()` and, alone among its three siblings, never stubbed `notify`.

    conftest's `_no_live_notifications` shuts both doors. This pins both halves: delete
    the `NOTIFY_URL` blanking and the first assert goes red on any machine with a notifier
    configured; delete the `_post` guard and the second does, everywhere.
    """
    import pytest

    import chela.notify as notify

    # Door 1: every `if notify.enabled()` call site is gated off, even though the
    # operator's real environment has CHELA_NOTIFY_URL set.
    assert notify.enabled() is False, (
        "conftest did not blank NOTIFY_URL — every `if notify.enabled():` call site in the "
        "product is live, and the suite can page the operator"
    )

    # Door 2: and a test that sets its own URL still cannot reach the network. `send()`
    # wraps its transport in `except Exception` and returns False, so a guard deriving
    # from Exception would be SWALLOWED and read as an ordinary send failure — the escape
    # must propagate.
    monkeypatch.setattr(notify, "NOTIFY_URL", "https://ntfy.sh/not-a-real-topic")
    assert notify.enabled() is True  # the fixture's blanking is genuinely overridden here

    with pytest.raises(BaseException) as excinfo:
        notify.send("this must never leave the machine", title="chela: test escape")

    assert type(excinfo.value).__name__ == "LiveNotificationEscape", (
        f"send() did not raise the fence's escape (got {type(excinfo.value).__name__}) — "
        "if it returned False instead, the guard derives from Exception and send() ate it"
    )
