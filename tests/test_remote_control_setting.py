"""CMX-382: ``--remote-control`` is a dashboard-writable setting, read PER CALL.

``config.remote_control_enabled()`` resolves ``CHELA_REMOTE_CONTROL`` (env) →
``remote_control`` in ``~/.chela/config.json`` → ON, on every call — so the dashboard,
Telegram and daemon processes all see a Settings toggle without a restart. Nothing here
monkeypatches the resolver itself: every test drives the REAL precedence through a real
(scratch) ``config.json`` and the real env, and reads what ``spawn_window`` actually sent.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chela import config, spawn, userconfig
from chela.workflow import WorkflowDef


@pytest.fixture(autouse=True)
def scratch_config(tmp_path, monkeypatch):
    """A scratch config.json (``userconfig._PATH`` is latched at import) and no env."""
    path = tmp_path / "config.json"
    monkeypatch.setattr(userconfig, "_PATH", path)
    monkeypatch.delenv("CHELA_REMOTE_CONTROL", raising=False)
    return path


class _Proc:
    returncode = 0
    stdout = "@42"
    stderr = ""


def _spawn_launch(monkeypatch, tmp_path) -> str:
    """Run the real ``spawn_window`` with tmux stubbed; return the sent claude command."""
    monkeypatch.setattr(spawn.discovery, "ensure_session", lambda: True)
    monkeypatch.setattr(spawn.discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(spawn.agent_manager, "lock_window_name", lambda *a, **kw: None)
    monkeypatch.setattr(spawn.subprocess, "run", lambda *a, **kw: _Proc())
    monkeypatch.setattr(spawn.sessionids, "set_session_id", lambda wid, sid: None)
    monkeypatch.setattr(spawn.sessionids, "session_id_for", lambda wid: None)
    sent: list[str] = []
    monkeypatch.setattr(spawn, "_send", lambda target, text: sent.append(text))
    workdir = tmp_path / "proj"
    workdir.mkdir(exist_ok=True)
    assert spawn.spawn_window(workdir, command="claude").ok
    launch = [t for t in sent if t.startswith("claude")]
    assert len(launch) == 1, sent
    return launch[0]


# --- (e) the default is unchanged: no env, no config ⇒ ON --------------------

def test_no_env_no_config_defaults_on(monkeypatch, tmp_path, scratch_config):
    assert not scratch_config.exists()
    assert config.remote_control_setting() == (True, "default")
    assert "--remote-control" in _spawn_launch(monkeypatch, tmp_path)


# --- (a) config.json drives it, read per call (never latched) ----------------

def test_config_json_flip_is_seen_between_two_calls_in_one_process(monkeypatch, tmp_path):
    userconfig.set_(config.REMOTE_CONTROL_KEY, False)
    assert "--remote-control" not in _spawn_launch(monkeypatch, tmp_path)
    userconfig.set_(config.REMOTE_CONTROL_KEY, True)
    assert "--remote-control" in _spawn_launch(monkeypatch, tmp_path)
    userconfig.set_(config.REMOTE_CONTROL_KEY, False)
    assert "--remote-control" not in _spawn_launch(monkeypatch, tmp_path)


def test_orchestrator_autolaunch_reads_the_setting_per_call(monkeypatch, scratch_config):
    import subprocess
    from types import SimpleNamespace

    from chela.personas import autolaunch

    def launch() -> str:
        sent: list[str] = []

        def fake_run(argv, *a, **kw):
            if argv[:2] == ["tmux", "new-window"]:
                return SimpleNamespace(stdout="@7\n", returncode=0)
            if argv[:2] == ["tmux", "send-keys"]:
                sent.append(argv[4])
            return SimpleNamespace(stdout="", returncode=0)

        monkeypatch.setattr(subprocess, "run", fake_run)
        autolaunch._spawn_orchestrator_window("/tmp/repo")
        return next(s for s in sent if s.startswith("claude"))

    userconfig.set_(config.REMOTE_CONTROL_KEY, False)
    assert "--remote-control" not in launch()
    userconfig.set_(config.REMOTE_CONTROL_KEY, True)
    assert "--remote-control" in launch()


# --- (b) env beats config.json ------------------------------------------------

def test_env_false_beats_config_true(monkeypatch, tmp_path):
    userconfig.set_(config.REMOTE_CONTROL_KEY, True)
    monkeypatch.setenv("CHELA_REMOTE_CONTROL", "false")
    assert config.remote_control_setting() == (False, "env")
    assert "--remote-control" not in _spawn_launch(monkeypatch, tmp_path)


@pytest.mark.parametrize("raw,expected", [
    ("true", True), ("1", True), ("yes", True), ("on", True), ("ON", True),
    ("false", False), ("0", False), ("no", False), ("off", False), (" Off ", False),
])
def test_env_accepts_every_boolean_spelling(monkeypatch, raw, expected):
    monkeypatch.setenv("CHELA_REMOTE_CONTROL", raw)
    assert config.remote_control_setting() == (expected, "env")


def test_garbage_env_falls_through_to_config(monkeypatch):
    userconfig.set_(config.REMOTE_CONTROL_KEY, False)
    monkeypatch.setenv("CHELA_REMOTE_CONTROL", "maybe")
    assert config.remote_control_setting() == (False, "dashboard")


# --- (c) /api/config persists + reports value and the env lock ---------------

@pytest.fixture()
def client(monkeypatch):
    from chela.dashboard import app as dash
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [])
    return dash.app.test_client()


def _stored(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def test_api_post_persists_and_get_reports(client, scratch_config):
    resp = client.post("/api/config", json={"remote_control": False})
    assert resp.status_code == 200
    assert _stored(scratch_config)[config.REMOTE_CONTROL_KEY] is False
    body = client.get("/api/config").get_json()
    assert body["remote_control"] is False
    assert body["remote_control_source"] == "dashboard"
    assert body["remote_control_env_locked"] is False

    client.post("/api/config", json={"remote_control": True})
    assert _stored(scratch_config)[config.REMOTE_CONTROL_KEY] is True
    assert client.get("/api/config").get_json()["remote_control"] is True


def test_api_get_reports_env_lock(client, monkeypatch):
    userconfig.set_(config.REMOTE_CONTROL_KEY, True)
    monkeypatch.setenv("CHELA_REMOTE_CONTROL", "off")
    body = client.get("/api/config").get_json()
    assert body["remote_control"] is False
    assert body["remote_control_env_locked"] is True
    assert body["remote_control_env"] == "CHELA_REMOTE_CONTROL"


def test_api_rejects_a_non_boolean_and_keeps_the_stored_value(client, scratch_config):
    userconfig.set_(config.REMOTE_CONTROL_KEY, False)
    resp = client.post("/api/config", json={"remote_control": "sometimes"})
    assert resp.status_code == 400
    assert _stored(scratch_config)[config.REMOTE_CONTROL_KEY] is False


def test_api_empty_clears_back_to_default(client, scratch_config):
    userconfig.set_(config.REMOTE_CONTROL_KEY, False)
    client.post("/api/config", json={"remote_control": None})
    assert config.REMOTE_CONTROL_KEY not in _stored(scratch_config)
    assert client.get("/api/config").get_json()["remote_control_source"] == "default"


# --- (f) a dispatcher-built agent command never gains --remote-control -------

def test_dispatcher_agent_cmd_never_carries_remote_control(monkeypatch):
    from chela import dispatcher
    userconfig.set_(config.REMOTE_CONTROL_KEY, True)
    monkeypatch.setenv("CHELA_REMOTE_CONTROL", "true")
    assert config.remote_control_enabled() is True
    wf = WorkflowDef(path=Path("/nowhere/WORKFLOW.md"),
                     config={"project_key": "CMX", "agent": {}}, prompt_template="")
    for role in (None, "judge"):
        cmd, _ = dispatcher.resolve_agent_cmd(wf, role) if role else dispatcher.resolve_agent_cmd(wf)
        assert "--remote-control" not in cmd, cmd
