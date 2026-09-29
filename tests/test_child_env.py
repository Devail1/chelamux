"""🧨 CMX-390 — every hook, suite and tmux launch chela makes gets ``envutil.child_env()``,
never the daemon's raw ``os.environ``.

pm2 forks ``chela-daemon`` through Node's ``child_process.fork``, so the daemon carries
``NODE_CHANNEL_FD=3`` + ``NODE_CHANNEL_SERIALIZATION_MODE=json``. Any Node program a hook runs
(``pnpm --version`` was the measured one) treats fd 3 as its IPC channel and aborts 134 —
killing ``before_run``, and with it every agent and judge launch.

Each spy below reads the env a call site ACTUALLY passed, treating a missing ``env=`` as
``os.environ`` (which is what ``subprocess`` does with it) — so both "dropped the kwarg" and
"passed ``os.environ``" are the corruption, and both go red.

⛔ No live tmux, no ``~/.chela``: every tmux call is faked; the one real subprocess (the
end-to-end hook) is ``node`` in ``tmp_path``.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
from types import SimpleNamespace

import pytest

from chela import envutil
from chela.workflow import WorkflowDef

_LEAK = {
    "NODE_CHANNEL_FD": "3",
    "NODE_CHANNEL_SERIALIZATION_MODE": "json",
    "NODE_UNIQUE_ID": "1",
    "NODE_APP_INSTANCE": "0",
    "pm_id": "7",
    "pm_exec_path": "/opt/chela/daemon",
    "PM2_USAGE": "CLI",
}
_KEEP = {"PM2_HOME": "/srv/pm2-home", "CHELA_TMUX_SESSION": "chela-test"}

# ⚠️ NOT `process.exit(0)`: an explicit exit skips the IPC-channel teardown, so node exits 0
# even with a stale NODE_CHANNEL_FD and the probe would pass under the very leak it tests.
# A NATURAL exit is what aborts 134 (measured on node 20 and 24) — as `pnpm --version` did.
_NODE_PROBE = 'node -e "process.exitCode = 0"'


@pytest.fixture
def leaky_env(monkeypatch):
    """The daemon's env as pm2 hands it over: the leak plus what must survive."""
    for k, v in {**_LEAK, **_KEEP}.items():
        monkeypatch.setenv(k, v)


def _effective(kwargs) -> dict:
    env = kwargs.get("env")
    return dict(os.environ if env is None else env)


def _assert_clean(env: dict) -> None:
    leaked = sorted(set(_LEAK) & set(env))
    assert not leaked, f"child env still carries pm2's IPC leak: {leaked}"
    # (d) never an environment reset — PATH and the explicitly-set vars pass through
    assert env.get("PATH") == os.environ["PATH"]
    assert env.get("HOME") == os.environ["HOME"]
    for k, v in _KEEP.items():
        assert env.get(k) == v, f"{k} was stripped — a child legitimately needs it"


# --- the helper itself -------------------------------------------------------

def test_child_env_drops_the_leak_and_keeps_everything_else():
    base = {**_LEAK, **_KEEP, "PATH": "/usr/bin", "HOME": "/home/x", "name": "chela-daemon"}
    env = envutil.child_env(base=base)
    assert set(env) == {"PM2_HOME", "CHELA_TMUX_SESSION", "PATH", "HOME", "name"}
    assert envutil.child_env({"NODE_CHANNEL_FD": "5", "X": "1"}, base=base)["NODE_CHANNEL_FD"] == "5"


def test_child_env_returns_a_copy():
    base = {"PATH": "/usr/bin"}
    envutil.child_env(base=base)["PATH"] = "mutated"
    assert base["PATH"] == "/usr/bin"


# --- (a) the dispatcher's hooks + the agent/judge window --------------------

def _wf(tmp_path, hooks):
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={"project_key": "CMX", "agent": {}, "hooks": hooks},
        prompt_template="go {{workspace_path}}",
    )


def _launch(monkeypatch, tmp_path, hooks, *, real_hooks=False, fresh=False):
    import chela.dispatcher as dispatcher

    real_run = subprocess.run
    calls: list[tuple] = []

    def fake_run(argv, *a, **k):
        calls.append((argv, k))
        if isinstance(argv, str):               # a shell hook
            if real_hooks:
                return real_run(argv, *a, **k)
            return SimpleNamespace(stdout="", returncode=0)
        if argv[:2] == ["tmux", "new-window"]:
            return SimpleNamespace(stdout="@100\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(dispatcher.subprocess, "run", fake_run)
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *_a, **_k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    conn = dispatcher.ensure_schema(sqlite3.connect(":memory:"))
    wt = tmp_path / "wt"
    wt.mkdir(exist_ok=True)
    dispatcher._launch_agent(
        _wf(tmp_path, hooks), "t1", "cmx-1", wt, "go", conn,
        hook_vars={}, fresh_worktree=fresh,
    )
    return calls


def test_before_run_hook_gets_the_sanitised_env(monkeypatch, tmp_path, leaky_env):
    """🔴 Pass `os.environ` (or drop `env=`) at the before_run call site ⇒ RED."""
    calls = _launch(monkeypatch, tmp_path, {"before_run": "echo before"})
    hook = [k for argv, k in calls if argv == "echo before"]
    assert len(hook) == 1
    _assert_clean(_effective(hook[0]))


def test_after_create_hook_gets_the_sanitised_env(monkeypatch, tmp_path, leaky_env):
    calls = _launch(monkeypatch, tmp_path, {"after_create": "echo created"}, fresh=True)
    hook = [k for argv, k in calls if argv == "echo created"]
    assert len(hook) == 1
    _assert_clean(_effective(hook[0]))


def test_agent_window_new_window_gets_the_sanitised_env(monkeypatch, tmp_path, leaky_env):
    """(b) the tmux client that creates the agent/judge window."""
    calls = _launch(monkeypatch, tmp_path, {})
    nw = [k for argv, k in calls if isinstance(argv, list) and argv[:2] == ["tmux", "new-window"]]
    assert len(nw) == 1
    _assert_clean(_effective(nw[0]))


def test_after_done_hook_gets_the_sanitised_env(monkeypatch, tmp_path, leaky_env):
    import chela.dispatcher as dispatcher

    seen: list[dict] = []
    monkeypatch.setattr(dispatcher.subprocess, "Popen", lambda cmd, **k: seen.append(k))
    dispatcher._fire_after_done(_wf(tmp_path, {"after_done": "pm2 restart x"}))
    assert len(seen) == 1
    _assert_clean(_effective(seen[0]))


# --- (b) the judge's suite + provisioning -----------------------------------

def test_judge_suite_subprocess_gets_the_sanitised_env(monkeypatch, tmp_path, leaky_env):
    """🔴 Build `_no_color_env` from `dict(os.environ)` again ⇒ RED on the pm2 bookkeeping."""
    import chela.judge as judge

    seen: list[dict] = []

    def fake_run(cmd, **k):
        seen.append(k)
        return SimpleNamespace(stdout="1 passed\n", stderr="", returncode=0)

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    judge.run_suite("true", tmp_path)
    assert len(seen) == 1
    env = _effective(seen[0])
    # The suite env is STRICTER than child_env: CMX-391's `_suite_env` also strips every
    # `CHELA_*` var (then points CHELA_DIR at a scratch dir), so a test run can never reach
    # the live install or the live tmux session. Assert the pm2 leak is gone and the
    # non-chela survivors pass through; `CHELA_TMUX_SESSION` must NOT reach a suite.
    leaked = sorted(set(_LEAK) & set(env))
    assert not leaked, f"suite env still carries pm2's IPC leak: {leaked}"
    assert env.get("PATH") == os.environ["PATH"]
    assert env.get("HOME") == os.environ["HOME"]
    assert env.get("PM2_HOME") == _KEEP["PM2_HOME"]
    assert "CHELA_TMUX_SESSION" not in env


def test_judge_npm_ci_provisioning_gets_the_sanitised_env(monkeypatch, tmp_path, leaky_env):
    import chela.judge as judge

    (tmp_path / "package-lock.json").write_text("{}")
    monkeypatch.setattr(judge, "_provision_python_env", lambda *a, **k: "")
    monkeypatch.setattr(judge, "declared_npm_packages", lambda wt: ["jsdom"])
    monkeypatch.setattr(judge, "_unresolvable", lambda wt, names: list(names))
    seen: list[tuple] = []

    def fake_run(argv, **k):
        seen.append((argv, k))
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    judge.provision_suite_env(tmp_path)
    npm = [k for argv, k in seen if argv[:2] == ["npm", "ci"]]
    assert len(npm) == 1
    _assert_clean(_effective(npm[0]))


def test_judge_uv_sync_provisioning_gets_the_sanitised_env(monkeypatch, tmp_path, leaky_env):
    import chela.judge as judge

    (tmp_path / "pyproject.toml").write_text("")
    monkeypatch.setattr(judge.shutil, "which", lambda name: "/usr/bin/uv")
    seen: list[tuple] = []

    def fake_run(argv, **k):
        seen.append((argv, k))
        return SimpleNamespace(stdout="", stderr="", returncode=1)

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    judge._provision_python_env(tmp_path, 5)
    uv = [k for argv, k in seen if argv[:2] == ["uv", "sync"]]
    assert len(uv) == 1
    _assert_clean(_effective(uv[0]))


def test_judge_parse_check_node_check_gets_the_sanitised_env(monkeypatch, tmp_path, leaky_env):
    """The mutated-JS parse check runs ``node --check`` — the one Node child the judge spawns
    per mutation. Under the leak it would treat fd 3 as IPC and abort, turning every JS
    mutation INVALID."""
    import chela.judge as judge

    js = tmp_path / "mutated.js"
    js.write_text("let x = 1;\n")
    seen: list[tuple] = []

    def fake_run(argv, **k):
        seen.append((argv, k))
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(judge.subprocess, "run", fake_run)
    ok, _ = judge.parse_check(js)
    assert ok
    node = [k for argv, k in seen if argv[:2] == ["node", "--check"]]
    assert len(node) == 1
    _assert_clean(_effective(node[0]))
    assert _effective(node[0])["PM2_HOME"] == _KEEP["PM2_HOME"]


# --- (b) the other tmux launches: a human's window, and the server itself ---

def test_spawn_window_new_window_gets_the_sanitised_env(monkeypatch, tmp_path, leaky_env):
    from chela import agent_manager, discovery, spawn

    monkeypatch.setattr(discovery, "ensure_session", lambda *a, **k: True)
    monkeypatch.setattr(discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(agent_manager, "lock_window_name", lambda *a, **k: None)
    seen: list[tuple] = []

    def fake_run(argv, **k):
        seen.append((argv, k))
        return SimpleNamespace(stdout="@5\n", stderr="", returncode=0)

    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    assert spawn.spawn_window(tmp_path).ok
    nw = [k for argv, k in seen if argv[:2] == ["tmux", "new-window"]]
    assert len(nw) == 1
    _assert_clean(_effective(nw[0]))


def test_ensure_session_new_session_gets_the_sanitised_env(monkeypatch, leaky_env):
    """The call that can START the tmux server — whose env every later window inherits."""
    from chela import discovery

    state = {"exists": False}
    monkeypatch.setattr(discovery, "session_exists", lambda s: state["exists"])
    seen: list[tuple] = []

    def fake_run(argv, **k):
        seen.append((argv, k))
        state["exists"] = True
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(discovery.subprocess, "run", fake_run)
    assert discovery.ensure_session("chela-test")
    ns = [k for argv, k in seen if argv[:2] == ["tmux", "new-session"]]
    assert len(ns) == 1
    _assert_clean(_effective(ns[0]))


# --- (c) ⭐ end-to-end: a real Node hook through chela's real launch path ----

def _require_discriminating_node():
    if not shutil.which("node"):
        pytest.skip("node is not on PATH — the end-to-end Node hook check cannot run here")
    raw = subprocess.run(_NODE_PROBE, shell=True, capture_output=True,
                         env={**os.environ, **_LEAK})
    if raw.returncode == 0:
        pytest.skip("this node does not abort on a stale NODE_CHANNEL_FD — the end-to-end "
                    "probe cannot tell a sanitised env from a leaky one here")


def test_e2e_a_node_before_run_hook_survives_a_leaked_node_channel_fd(
        monkeypatch, tmp_path, leaky_env):
    """⭐ ACCEPTED path: NODE_CHANNEL_FD=3 in the parent, a before_run that runs real `node`,
    and `_launch_agent` must get through it (`check=True` would raise on the 134)."""
    _require_discriminating_node()
    calls = _launch(monkeypatch, tmp_path, {"before_run": _NODE_PROBE}, real_hooks=True)
    assert any(argv == _NODE_PROBE for argv, _ in calls)


def test_e2e_a_node_judge_suite_survives_a_leaked_node_channel_fd(tmp_path, leaky_env):
    from chela import judge

    _require_discriminating_node()
    result = judge.run_suite(_NODE_PROBE, tmp_path)
    assert result.exit_code == 0, result
