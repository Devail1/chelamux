"""CMX-473 — ``discovery.open_window``: no anchor window.

A missing session is created WITH the spawned window as its first window
(``new-session -d -s <session> -n <name> …``) instead of an empty ``shell-1`` followed by
``new-window``. Every spawn path goes through it: the dashboard launcher and Telegram
``/new`` (``spawn.spawn_window``), a sandboxed share (``spawn.spawn_sandbox_window``),
the dispatcher's agent and judge windows (``dispatcher._new_window``) and the
auto-launched orchestrator (``personas.autolaunch``).

No tmux here: ``subprocess.run`` is a recorder. The real-tmux half (the session really is
born with that one window; readers see no windows after the last one closes) lives in
``tests/test_terminals_selfheal.py``.
"""
from __future__ import annotations

import subprocess

import pytest

from chela import discovery, envutil

SESSION = "cmx473-sess"


class _Tmux:
    """Recorder for ``subprocess.run``: ``has-session`` answers from ``exists`` (a list,
    consumed per call, last value sticky); ``new-session`` / ``new-window`` answer
    ``rc`` and print ``@9``."""

    def __init__(self, exists, create_rc=0, window_rc=0):
        self.exists = list(exists)
        self.create_rc = create_rc
        self.window_rc = window_rc
        self.calls: list[tuple[list, dict]] = []

    def __call__(self, argv, **kw):
        self.calls.append((argv, kw))
        if argv[:2] == ["tmux", "has-session"]:
            ok = self.exists.pop(0) if len(self.exists) > 1 else self.exists[0]
            return subprocess.CompletedProcess(argv, 0 if ok else 1, "", "")
        rc = self.create_rc if argv[1] == "new-session" else self.window_rc
        return subprocess.CompletedProcess(argv, rc, "@9\n" if rc == 0 else "",
                                           "" if rc == 0 else "duplicate session: x")

    def creates(self):
        return [(a, k) for a, k in self.calls if a[:2] == ["tmux", "new-session"]]

    def windows(self):
        return [(a, k) for a, k in self.calls if a[:2] == ["tmux", "new-window"]]


@pytest.fixture
def tmux(monkeypatch):
    def make(**kw):
        rec = _Tmux(**kw)
        monkeypatch.setattr(discovery.subprocess, "run", rec)
        return rec
    monkeypatch.delenv("TMUX_TMPDIR", raising=False)
    monkeypatch.setattr(envutil, "server_env", lambda: {"ENV": "server"})
    monkeypatch.setattr(envutil, "child_env", lambda: {"ENV": "child"})
    scrubs: list[int] = []
    monkeypatch.setattr(envutil, "scrub_tmux_secrets", lambda: scrubs.append(1) or [])
    make.scrubs = scrubs
    return make


def test_a_missing_session_is_created_with_the_spawned_window_first(tmux):
    rec = tmux(exists=[False])
    proc = discovery.open_window("agent", "/work", ["sleep", "5"], session=SESSION)
    assert proc.returncode == 0 and proc.stdout == "@9\n"
    [(argv, kw)] = rec.creates()
    assert argv == ["tmux", "new-session", "-d", "-s", "cmx473-sess", "-n", "agent",
                    "-c", "/work", "-P", "-F", "#{window_id}", "--", "sleep", "5"]
    assert kw == {"capture_output": True, "text": True, "timeout": 10,
                  "env": {"ENV": "server"}}
    assert rec.windows() == []           # no second window, no anchor
    assert "shell-1" not in str(rec.calls)
    assert tmux.scrubs == [1]            # the create may have started the server


def test_negative_control_without_a_command_there_is_no_separator(tmux):
    rec = tmux(exists=[False])
    discovery.open_window("sh", "/w", session=SESSION, timeout=3)
    [(argv, kw)] = rec.creates()
    assert argv == ["tmux", "new-session", "-d", "-s", "cmx473-sess", "-n", "sh",
                    "-c", "/w", "-P", "-F", "#{window_id}"]
    assert kw["timeout"] == 3


def test_a_live_session_gets_a_plain_new_window(tmux):
    rec = tmux(exists=[True])
    proc = discovery.open_window("agent", "/work", session=SESSION, timeout=7)
    assert proc.returncode == 0
    assert rec.creates() == []
    [(argv, kw)] = rec.windows()
    assert argv == ["tmux", "new-window", "-t", "=cmx473-sess:", "-n", "agent",
                    "-c", "/work", "-P", "-F", "#{window_id}"]
    assert kw == {"capture_output": True, "text": True, "timeout": 7,
                  "env": {"ENV": "child"}}
    assert tmux.scrubs == []


def test_a_lost_create_race_falls_back_to_new_window(tmux):
    """Missing at the gate, `new-session` refused (another spawner won), there now."""
    rec = tmux(exists=[False, True], create_rc=1)
    proc = discovery.open_window("agent", "/work", ["x"], session=SESSION)
    assert proc.returncode == 0 and proc.stdout == "@9\n"
    assert len(rec.creates()) == 1
    [(argv, _)] = rec.windows()
    assert argv[:3] == ["tmux", "new-window", "-t"] and argv[-2:] == ["--", "x"]
    assert tmux.scrubs == []             # no create succeeded: nothing to scrub


def test_a_failed_create_with_the_session_still_missing_fails(tmux):
    rec = tmux(exists=[False, False], create_rc=1)
    proc = discovery.open_window("agent", "/work", session=SESSION)
    assert proc.returncode == 1
    assert rec.windows() == []           # never a new-window against a missing session
    with pytest.raises(subprocess.CalledProcessError):
        discovery.open_window("agent", "/work", session=SESSION, check=True)


@pytest.mark.parametrize("exists, create_rc, gates", [
    ([False], 0, 1),            # this spawn created the session (and maybe the server)
    ([True], 0, 0),             # a live session: its server was gated when it was made
    ([False, True], 1, 0),      # lost the create race: the winner gates it
])
def test_creating_the_session_installs_the_size_gate(tmux, monkeypatch, exists, create_rc,
                                                     gates):
    """CMX-503: the server that holds the session never lets a tiny client size a window."""
    from chela import winsize
    installed = []
    monkeypatch.setattr(winsize, "install_gate", lambda: installed.append(1) or True)
    tmux(exists=exists, create_rc=create_rc)
    discovery.open_window("agent", "/work", session=SESSION)
    assert len(installed) == gates


def test_a_missing_tmux_tmpdir_refuses_before_any_tmux_call(tmux, monkeypatch, tmp_path):
    rec = tmux(exists=[False])
    monkeypatch.setenv("TMUX_TMPDIR", str(tmp_path / "gone"))
    proc = discovery.open_window("agent", "/work", session=SESSION)
    assert proc.returncode == 1 and "TMUX_TMPDIR" in proc.stderr
    assert rec.calls == []
    with pytest.raises(subprocess.CalledProcessError):
        discovery.open_window("agent", "/work", session=SESSION, check=True)


def test_the_session_defaults_to_the_current_one(tmux, monkeypatch):
    rec = tmux(exists=[False])
    monkeypatch.setattr(discovery.config, "current_session", lambda: "cur-sess")
    discovery.open_window("agent", "/work")
    assert rec.creates()[0][0][4] == "cur-sess"
    assert rec.calls[0][0] == ["tmux", "has-session", "-t", "=cur-sess"]


# --- wiring: every spawn path goes through open_window -----------------------------------

def test_the_dispatcher_recreates_a_missing_session_with_its_agent_window(tmux, monkeypatch):
    from chela import dispatcher
    rec = tmux(exists=[False])
    monkeypatch.setattr(dispatcher, "TMUX_SESSION", SESSION)
    monkeypatch.setattr(dispatcher, "_scrub_node_ipc_env", lambda: None)
    assert dispatcher._new_window("cmx-1", "/wt") == "@9"
    [(argv, _)] = rec.creates()
    assert argv[:9] == ["tmux", "new-session", "-d", "-s", SESSION, "-n", "cmx-1", "-c", "/wt"]
    assert rec.windows() == []
    assert rec.creates()[0][1]["timeout"] is None      # never a TimeoutExpired mid-dispatch


def test_the_dispatcher_raises_when_the_window_cannot_open(tmux, monkeypatch):
    from chela import dispatcher
    tmux(exists=[False, False], create_rc=1)
    monkeypatch.setattr(dispatcher, "TMUX_SESSION", SESSION)
    monkeypatch.setattr(dispatcher, "_scrub_node_ipc_env", lambda: None)
    with pytest.raises(subprocess.CalledProcessError):
        dispatcher._new_window("cmx-1", "/wt")


def _stub_spawn(monkeypatch):
    from chela import agent_manager
    monkeypatch.setattr(discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(discovery.config, "current_session", lambda: SESSION)
    monkeypatch.setattr(agent_manager, "lock_window_name", lambda *a, **k: None)


def test_a_sandboxed_share_recreates_a_missing_session_with_its_window(
        tmux, monkeypatch, tmp_path):
    from chela import share_sandbox, spawn
    rec = tmux(exists=[False])
    _stub_spawn(monkeypatch)
    monkeypatch.setattr(share_sandbox, "preflight", lambda real, net="none": "")
    monkeypatch.setattr(share_sandbox, "new_session_id", lambda: "sid")
    monkeypatch.setattr(share_sandbox, "launcher_argv",
                        lambda sid, real, net: ["docker", "run", sid, net])
    result = spawn.spawn_sandbox_window(tmp_path)
    assert (result.ok, result.name, result.wid) == (True, "sandbox-1", "@9"), result.error
    [(argv, _)] = rec.creates()
    assert argv == ["tmux", "new-session", "-d", "-s", SESSION, "-n", "sandbox-1",
                    "-c", str(tmp_path.resolve()), "-P", "-F", "#{window_id}",
                    "--", "docker", "run", "sid", "none"]
    assert rec.windows() == []


def test_a_human_spawn_recreates_a_missing_session_with_its_window(tmux, monkeypatch, tmp_path):
    from chela import spawn
    rec = tmux(exists=[False])
    _stub_spawn(monkeypatch)
    monkeypatch.setattr(spawn.config, "remote_control_enabled", lambda: False)
    result = spawn.spawn_window(tmp_path, name="mine")
    assert (result.ok, result.name, result.wid) == (True, "mine", "@9"), result.error
    [(argv, _)] = rec.creates()
    assert argv == ["tmux", "new-session", "-d", "-s", SESSION, "-n", "mine",
                    "-c", str(tmp_path.resolve()), "-P", "-F", "#{window_id}"]
    assert rec.windows() == []


def test_the_autolaunched_orchestrator_recreates_a_missing_session(tmux, monkeypatch):
    from chela.personas import autolaunch
    rec = tmux(exists=[False])
    monkeypatch.setattr(autolaunch, "TMUX_SESSION", SESSION)
    monkeypatch.setattr(autolaunch.config, "remote_control_enabled", lambda: False)
    autolaunch._spawn_orchestrator_window("/repo")
    [(argv, kw)] = rec.creates()
    assert argv == ["tmux", "new-session", "-d", "-s", SESSION, "-n", autolaunch.WINDOW_NAME,
                    "-c", "/repo", "-P", "-F", "#{window_id}"]
    assert kw["timeout"] is None
    assert rec.windows() == []


def test_the_orchestrator_autolaunch_opens_through_open_window(monkeypatch):
    from chela.personas import autolaunch
    seen = []

    def fake_open(name, cwd, command=(), **kw):
        seen.append((name, cwd, kw))
        return subprocess.CompletedProcess([], 0, "@9\n", "")
    monkeypatch.setattr(autolaunch.discovery, "open_window", fake_open)
    monkeypatch.setattr(autolaunch.subprocess, "run",
                        lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
    monkeypatch.setattr(autolaunch.envutil, "scrub_tmux_secrets", lambda: [])
    monkeypatch.setattr(autolaunch.config, "remote_control_enabled", lambda: False)
    autolaunch._spawn_orchestrator_window("/repo")
    assert seen == [(autolaunch.WINDOW_NAME, "/repo",
                     {"session": autolaunch.TMUX_SESSION, "check": True, "timeout": None})]
