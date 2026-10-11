"""🔒 CMX-45 (was CMX-115) — a dispatched agent/judge inherits an env ALLOWLIST, nothing else.

The daemon carries `CHELA_NOTIFY_URL` (`~/.chela/chela.env`) so PRODUCTION can push real
phone notifications, and an agent's pane starts from the tmux server's global environment —
which also holds pm2's bookkeeping (`args`, `env`, `status`, a var named after the pm2
process…) and whatever else chela.env or an operator's shell put there. CMX-115 stripped the
one name it knew about (`unset CHELA_NOTIFY_URL`); CMX-45 flips that to default-deny:
`_launch_agent` runs the agent command under `env -i` with only `envutil.AGENT_ENV_ALLOW`
(+ the forwarded credentials) passed through. `_spawn_judge` calls `_launch_agent` too —
there is no second launch path to fix.

The guards below run the EXACT lines `_launch_agent` types into the pane through a real
shell, so they measure what the agent process would actually see, not a re-implementation.
"""
from __future__ import annotations

import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from chela import envutil
from chela.workflow import WorkflowDef

SENTINEL = "CHELA_CMX45_SENTINEL"
# `_capture_send_keys` patches the shared `subprocess.run`; the real shell needs the original.
_real_run = subprocess.run


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


def _stub_launch(monkeypatch, dispatcher):
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_scrub_node_ipc_env", lambda: None)
    return _capture_send_keys(monkeypatch, dispatcher)


def _launch(monkeypatch, dispatcher, tmp_path, **kwargs):
    sent = _stub_launch(monkeypatch, dispatcher)
    dispatcher._launch_agent(
        _wf(tmp_path), "t1", "cmx-1", tmp_path / "wt", "go", _conn(dispatcher),
        hook_vars={}, fresh_worktree=False, **kwargs,
    )
    return sent


def _agent_line(sent):
    return next(line for line in sent if "claude" in line or line.startswith("env "))


def _pane_env(monkeypatch, dispatcher, tmp_path, shell, daemon_env):
    """Launch with the agent command swapped for `env -0` (prints the env the agent process
    would start with), then type every captured send-keys line into a real ``shell`` whose
    own environment is ``daemon_env``. Returns that agent env as a dict."""
    monkeypatch.setattr(dispatcher, "resolve_agent_cmd", lambda wf, role="coding": ("env -0", "test"))
    monkeypatch.setattr(dispatcher, "messaging_socket_launch_arg", lambda _wid: None)
    sent = _launch(monkeypatch, dispatcher, tmp_path)
    out = _real_run([shell, "-c", "\n".join(sent)], env=daemon_env,
                         capture_output=True, text=True, timeout=20, check=True)
    return dict(e.partition("=")[::2] for e in out.stdout.split("\0") if e)


_SHELLS = [s for s in ("bash", "dash", "zsh") if shutil.which(s)]


@pytest.mark.parametrize("shell", _SHELLS)
def test_a_daemon_var_is_absent_in_the_agent_pane_while_wid_and_path_survive(
        monkeypatch, tmp_path, shell):
    """🔴 THE GUARD. A sentinel set in the daemon env must be ABSENT for the agent — as must
    the CMX-115 notify URL and pm2's lowercase bookkeeping, which no deny-pattern catches —
    while CHELA_WID (exported into the pane just before launch) and PATH survive. Reverting
    to the old `unset CHELA_NOTIFY_URL` strip-list turns this RED on the sentinel."""
    import chela.dispatcher as dispatcher

    daemon_env = {
        SENTINEL: "must-not-reach-the-agent",
        "CHELA_NOTIFY_URL": "https://ntfy.example/not-a-real-topic",
        "status": "online",  # pm2 bookkeeping, measured in the live tmux global env
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(tmp_path),
        "CHELA_TMUX_SESSION": "chela",
        "CHELA_ENV_FILE": "",
    }
    agent = _pane_env(monkeypatch, dispatcher, tmp_path, shutil.which(shell), daemon_env)

    assert SENTINEL not in agent
    assert "CHELA_NOTIFY_URL" not in agent
    assert "status" not in agent
    assert agent.get("CHELA_WID") == "@100"
    assert agent.get("PATH") == daemon_env["PATH"]
    assert agent.get("HOME") == str(tmp_path)
    # set-to-empty stays set (chela reads CHELA_ENV_FILE="" as "no env file"); unset stays unset
    assert agent.get("CHELA_ENV_FILE") == ""
    assert "TZ" not in agent


def test_notify_is_disabled_for_an_agent_launched_from_a_notifying_daemon(
        monkeypatch, tmp_path):
    """🔴 Ties the allowlist to the gate `notify.send` checks: an agent/judge process booting
    under the launched env must see `notify.enabled() is False`."""
    import chela.dispatcher as dispatcher
    import chela.notify as notify

    daemon_env = {"CHELA_NOTIFY_URL": "https://ntfy.example/not-a-real-topic",
                  "PATH": "/usr/bin:/bin"}
    agent = _pane_env(monkeypatch, dispatcher, tmp_path, shutil.which("sh"), daemon_env)
    assert agent.get("PATH"), "the agent env came back empty — this would pass vacuously"

    monkeypatch.setattr(notify, "NOTIFY_URL", agent.get("CHELA_NOTIFY_URL", ""))
    assert notify.enabled() is False


def test_the_allowlist_wraps_inside_memcap_exec(monkeypatch, tmp_path):
    """`exec` is a shell builtin `env` cannot run, so the allowlist must sit INSIDE memcap's
    `exec systemd-run … --` wrapper, directly in front of the agent command."""
    import chela.dispatcher as dispatcher

    monkeypatch.setattr(dispatcher.memcap, "wrap_launch_cmd",
                        lambda cmd: f"exec systemd-run --user --scope -- {cmd}")
    line = _agent_line(_launch(monkeypatch, dispatcher, tmp_path))
    assert line.startswith("exec systemd-run --user --scope -- env -i ")
    assert " claude " in line


def test_spawn_judge_launches_under_the_allowlist_too(monkeypatch, tmp_path):
    """🔴 `_spawn_judge` calls `_launch_agent` — same wiring, same exposure (the judge runs
    the FULL pytest suite, and any test reaching `notify.send` unmocked would push real)."""
    import chela.dispatcher as dispatcher

    sent = _stub_launch(monkeypatch, dispatcher)
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

    line = _agent_line(sent)
    assert line.startswith("env -i ")
    assert line.index("env -i ") < line.index("claude")


def test_operator_forward_names_pass_but_a_non_identifier_is_dropped():
    """`CHELA_CHILD_ENV_FORWARD` extends the allowlist by name — but a name is interpolated
    into a shell line, so anything that is not a plain identifier never reaches it."""
    src = {envutil.FORWARD_ENV: "HTTPS_PROXY, $(touch pwned) ;rm"}
    names = envutil.agent_env_names(src)
    assert "HTTPS_PROXY" in names
    assert not [n for n in names if not n.replace("_", "").isalnum()]
    cmd = envutil.agent_env_cmd("claude", src)
    assert "pwned" not in cmd and ";rm" not in cmd


def test_the_allowlist_names_no_secret_beyond_the_forwarded_credentials():
    """The allowlist is for what an agent needs to RUN; a credential belongs in the
    deliberately short `FORWARDED_SECRET_VARS` (or the operator's forward list), not here."""
    assert not [n for n in envutil.AGENT_ENV_ALLOW if envutil.is_secret(n)]
    assert "CHELA_NOTIFY_URL" not in envutil.agent_env_names({})


def test_the_suite_itself_cannot_push_a_real_notification(monkeypatch):
    """🔴 THE FENCE. The allowlist keeps `CHELA_NOTIFY_URL` from every tmux-spawned agent and
    judge — but NOT from a maintainer running `pytest` or `chela judge run` in their own
    shell, which is how this suite is normally run by hand. That gap paged the operator 109
    times on 2026-09-01, from
    `test_auto_apply_sweep_never_calls_apply_with_a_bespoke_repo_arg` — which drives the
    real `auto_apply_sweep()` and, alone among its three siblings, never stubbed `notify`.

    conftest's `_no_live_notifications` shuts both doors. This pins both halves: delete
    the `NOTIFY_URL` blanking and the first assert goes red on any machine with a notifier
    configured; delete the `_post` guard and the second does, everywhere.
    """
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
