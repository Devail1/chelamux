"""🧹 CMX-191 — pm2 exports each process's ``pm2_env`` (``args``, ``exec_mode``, ``name``,
``pm_id``, …) into its env, and a ``pm2 start`` run from a descendant reads them OVER the
ecosystem file: an inherited empty ``args=`` started every chela service bare (2026-10-17).

Every window chela opens therefore starts without them — both the env the tmux call is
handed and the tmux server's global table each window inherits — while an unrelated
lowercase var the operator set survives. ``chela doctor`` warns when THIS shell carries them.

⛔ No real tmux or pm2: every call is faked and the tests assert on the env it would get.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from chela import envutil, runtime_truth
from chela.runtime_truth import OK, WARN

_PM2 = {"args": "", "exec_mode": "fork_mode", "name": "x", "pm_id": "3"}
_MINE = {"my_setting": "keep-me"}


@pytest.fixture
def pm2_parent(monkeypatch):
    for k, v in {**_PM2, **_MINE}.items():
        monkeypatch.setenv(k, v)


def test_spawn_window_hands_tmux_an_env_without_pm2_app_keys(monkeypatch, tmp_path, pm2_parent):
    from chela import agent_manager, discovery, spawn

    monkeypatch.setattr(discovery, "ensure_session", lambda *a, **k: True)
    monkeypatch.setattr(discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(agent_manager, "lock_window_name", lambda *a, **k: None)
    seen: list[tuple] = []

    def fake_run(argv, **k):
        seen.append((argv, k))
        if argv[:2] == ["tmux", "show-environment"]:
            return SimpleNamespace(stdout="", stderr="", returncode=0)
        return SimpleNamespace(stdout="@5\n", stderr="", returncode=0)

    monkeypatch.setattr(spawn.subprocess, "run", fake_run)
    monkeypatch.setattr(envutil.subprocess, "run", fake_run)
    assert spawn.spawn_window(tmp_path).ok
    nw = [k for argv, k in seen if argv[:2] == ["tmux", "new-window"]]
    assert len(nw) == 1
    env = nw[0]["env"]
    assert not set(_PM2) & set(env), sorted(set(_PM2) & set(env))
    assert env["my_setting"] == "keep-me"


def test_the_tmux_global_table_scrub_unsets_pm2_app_keys_and_keeps_mine(monkeypatch):
    """A window inherits the tmux SERVER's table, not the client's env — so the scrub
    before every spawn is what actually keeps `args=` out of the new pane."""
    calls: list[tuple] = []
    table = "args=\nexec_mode=fork_mode\nname=x\npm_id=3\nmy_setting=keep-me\nPATH=/bin\n"

    def fake_run(argv, **k):
        calls.append((argv, k))
        return SimpleNamespace(stdout=table, stderr="", returncode=0)

    monkeypatch.setattr(envutil.subprocess, "run", fake_run)
    assert envutil.scrub_tmux_secrets() == ["args", "exec_mode", "name", "pm_id"]
    assert calls[0] == (["tmux", "show-environment", "-g"],
                        {"capture_output": True, "text": True, "timeout": 10})
    assert [argv for argv, _ in calls[1:]] == [
        ["tmux", "set-environment", "-gu", n] for n in ("args", "exec_mode", "name", "pm_id")]


def test_child_env_drops_pm2_app_keys_and_keeps_an_unrelated_lowercase_var():
    base = {**_PM2, **_MINE, "PATH": "/usr/bin", "PM2_HOME": "/srv/pm2"}
    assert envutil.child_env(base=base) == {
        "my_setting": "keep-me", "PATH": "/usr/bin", "PM2_HOME": "/srv/pm2"}


@pytest.mark.parametrize("key", [
    "args", "exec_mode", "name", "status", "cwd", "namespace", "node_args", "unique_id",
    "autorestart", "km_link", "windowsHide", "axm_monitor", "axm_actions", "vizion",
    "vizion_running", "pm_out_log_path", "pm_exec_path",
])
def test_known_pm2_env_keys_are_pm2_app_keys(key):
    assert envutil.is_pm2_app_key(key)


@pytest.mark.parametrize("key", [
    "my_setting", "ARGS", "Name", "names", "xargs", "argss", "axm", "vizio", "pm2", "PATH",
    "PM2_HOME", "http_proxy",
])
def test_unrelated_names_are_not_pm2_app_keys(key):
    assert not envutil.is_pm2_app_key(key)


def test_pm2_app_keys_lists_only_pm2_keys_sorted(monkeypatch):
    env = {**_PM2, **_MINE, "PATH": "/bin"}
    assert envutil.pm2_app_keys(env) == ["args", "exec_mode", "name", "pm_id"]
    assert envutil.pm2_app_keys({}) == []
    for k in _PM2:
        monkeypatch.setenv(k, _PM2[k])
    assert set(_PM2) <= set(envutil.pm2_app_keys())


# --- chela doctor -------------------------------------------------------------

def _audit(monkeypatch):
    for k in list(envutil.pm2_app_keys()):
        monkeypatch.delenv(k, raising=False)
    return runtime_truth.audit(runtime_truth.fact("process.pm2_app_env"))


def test_doctor_warns_when_this_shell_carries_pm2_keys(monkeypatch):
    for k in list(envutil.pm2_app_keys()):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**_PM2, **_MINE}.items():
        monkeypatch.setenv(k, v)
    [f] = runtime_truth.audit(runtime_truth.fact("process.pm2_app_env"))
    assert f.level == WARN
    assert "args, exec_mode, name, pm_id" in f.title
    assert "my_setting" not in f.title
    assert 'env -i HOME="$HOME" PATH="$PATH" pm2' in f.detail


def test_doctor_is_silent_without_pm2_keys(monkeypatch):
    monkeypatch.setenv("my_setting", "keep-me")
    [f] = _audit(monkeypatch)
    assert f.level == OK


@pytest.mark.parametrize("managed, applies", [(True, False), (False, True)])
def test_doctor_skips_a_pm2_managed_service(monkeypatch, managed, applies):
    """A pm2-run service's own env IS its pm2_env — not a finding (CMX-313's gate)."""
    monkeypatch.setattr(runtime_truth, "_pm2_manages_this_process", lambda: managed)
    assert runtime_truth.fact("process.pm2_app_env").applies() is applies
