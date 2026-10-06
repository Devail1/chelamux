"""``chela spawn <cwd>`` and ``chela close @N`` (CMX-13).

``chela spawn`` is a thin CLI over :func:`chela.spawn.spawn_window` — the same window-open
path the dashboard launcher and Telegram ``/new`` use. The one default it must NOT inherit
is ``spawn_window``'s own: no ``command`` there opens a plain SHELL, and ``chela spawn``
means an agent. ``chela close @N`` kills one chela window so nobody reaches for raw
``tmux kill-window`` — refusing an unknown/foreign window, the orchestrator's own window,
and a window a dispatched run still claims.

⛔ No real tmux: every test drives the real argparse dispatch (``main.main()``), and only
leaf I/O is faked — ``spawn_window``'s tmux touchpoints, and ``subprocess.run`` for the kill.
"""
from __future__ import annotations

import inspect
import os
import sys

import pytest

from chela import agent_manager, dispatcher, inbox, launcher, main, orchestrator, spawn

SENTINEL_CMD = "sentinel-agent --default"


def _main(argv: list[str]):
    """Run ``chela <argv>`` through the real parser; return the SystemExit code or None."""
    old = sys.argv
    sys.argv = ["chela", *argv]
    try:
        main.main()
    except SystemExit as e:
        return e.code
    finally:
        sys.argv = old
    return None


# --- chela spawn ---------------------------------------------------------------------------

class _Proc:
    def __init__(self, stdout="", returncode=0, stderr=""):
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


@pytest.fixture()
def tmux_spawn(monkeypatch, tmp_path):
    """The REAL ``spawn_window`` with only its tmux touchpoints stubbed; records what it
    types into the new window. The launcher store is redirected to ``tmp_path``."""
    monkeypatch.setattr(spawn.discovery, "ensure_session", lambda: True)
    monkeypatch.setattr(spawn.discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(spawn.envutil, "scrub_tmux_secrets", lambda: None)
    monkeypatch.setattr(spawn.agent_manager, "lock_window_name", lambda *a, **kw: None)
    monkeypatch.setattr(spawn.subprocess, "run", lambda *a, **kw: _Proc("@42"))
    monkeypatch.setattr(spawn.config, "remote_control_enabled", lambda: False)
    monkeypatch.setattr(spawn, "_record_session_id", lambda wid, sid: True)
    monkeypatch.setattr(agent_manager, "DEFAULT_LAUNCH_CMD", SENTINEL_CMD)
    monkeypatch.setattr(launcher, "_STORE", tmp_path / "launcher.json")
    sent: list[str] = []
    monkeypatch.setattr(spawn, "_send", lambda target, text: sent.append(text))
    return sent


def _launched(sent: list[str]) -> list[str]:
    return [t for t in sent if not t.startswith("export CHELA_WID=")]


def test_spawn_without_command_launches_the_default_agent_not_a_shell(tmux_spawn, tmp_path,
                                                                      capsys):
    assert _main(["spawn", str(tmp_path)]) is None
    launched = _launched(tmux_spawn)
    assert len(launched) == 1, tmux_spawn          # something WAS launched — not a bare shell
    assert launched[0] == SENTINEL_CMD             # …and it is DEFAULT_LAUNCH_CMD
    out = capsys.readouterr().out
    assert out.strip() == f"@42 {os.path.realpath(tmp_path)}"


def test_negative_control_bare_spawn_window_default_is_a_shell(tmux_spawn, tmp_path):
    """The default the CLI must not inherit: ``spawn_window(cwd)`` sends no command at all.
    If this ever stops holding, the test above stops proving the CLI does the right thing."""
    assert inspect.signature(spawn.spawn_window).parameters["command"].default is None
    assert spawn.spawn_window(str(tmp_path)).ok
    assert _launched(tmux_spawn) == []


def test_spawn_passes_an_explicit_command_through(tmux_spawn, tmp_path):
    assert _main(["spawn", str(tmp_path), "--command", "claude --model opus"]) is None
    launched = _launched(tmux_spawn)
    assert len(launched) == 1
    assert launched[0].startswith("claude")
    assert launched[0].endswith("--model opus")
    assert SENTINEL_CMD not in launched[0]


def test_spawn_records_the_cwd_in_the_launcher_recent_list(tmux_spawn, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    assert _main(["spawn", str(proj)]) is None
    assert [e["path"] for e in launcher._load()["recent"]] == [os.path.realpath(proj)]


def test_a_recent_store_failure_never_fails_the_spawn(tmux_spawn, tmp_path, monkeypatch,
                                                      capsys):
    def boom(path):
        raise OSError("disk full")
    monkeypatch.setattr(launcher, "record_recent", boom)
    assert _main(["spawn", str(tmp_path)]) is None
    assert capsys.readouterr().out.startswith("@42 ")


def test_spawn_failure_exits_nonzero_and_records_nothing(tmux_spawn, tmp_path, capsys):
    assert _main(["spawn", str(tmp_path / "nope")]) == 1
    assert "no such directory" in capsys.readouterr().err
    assert tmux_spawn == []
    assert launcher._load()["recent"] == []


# --- chela close @N ------------------------------------------------------------------------

LIVE = {"@1": "orchestrator", "@5": "proj", "@7": "cmx-9"}
RUNS = [{"task_id": "CMX-9", "status": "running", "window_id": "@7"}]


@pytest.fixture()
def close_env(monkeypatch):
    """Live windows, orchestrator identity and runs stubbed; ``subprocess.run`` spied."""
    monkeypatch.setattr(main.config, "current_session", lambda: "chela")
    monkeypatch.setattr(main.discovery, "get_windows_by_id", lambda: dict(LIVE))
    monkeypatch.setattr(orchestrator, "self_wid", lambda: "@1")
    monkeypatch.setattr(inbox, "orchestrator_wid", lambda store=None: None)
    monkeypatch.setattr(dispatcher, "list_runs", lambda: list(RUNS))
    calls: list[list[str]] = []

    def run(cmd, **kw):
        calls.append(cmd)
        return _Proc()
    monkeypatch.setattr(main.subprocess, "run", run)
    return calls


def test_close_kills_a_live_chela_window_scoped_to_the_session(close_env, capsys):
    assert _main(["close", "@5"]) is None
    assert close_env == [["tmux", "kill-window", "-t", "chela:@5"]]
    assert "closed @5 (proj)" in capsys.readouterr().out


def test_close_refuses_an_unknown_wid(close_env, capsys):
    assert _main(["close", "@99"]) == 1
    assert close_env == []                                    # nothing killed
    assert "not a live window" in capsys.readouterr().err


def test_close_refuses_the_orchestrators_own_window(close_env, capsys):
    assert _main(["close", "@1"]) == 1
    assert close_env == []
    assert "--orchestrator" in capsys.readouterr().err


def test_close_refuses_the_registered_orchestrator_window(close_env, monkeypatch):
    monkeypatch.setattr(orchestrator, "self_wid", lambda: None)
    monkeypatch.setattr(inbox, "orchestrator_wid", lambda store=None: "@1")
    assert _main(["close", "@1"]) == 1
    assert close_env == []


def test_orchestrator_flag_allows_closing_the_orchestrator_window(close_env):
    assert _main(["close", "@1", "--orchestrator"]) is None
    assert close_env == [["tmux", "kill-window", "-t", "chela:@1"]]


def test_close_refuses_a_window_an_in_flight_run_claims(close_env, capsys):
    assert _main(["close", "@7"]) == 1
    assert close_env == []
    assert "CMX-9" in capsys.readouterr().err


def test_force_kills_a_window_an_in_flight_run_claims(close_env):
    assert _main(["close", "@7", "--force"]) is None
    assert close_env == [["tmux", "kill-window", "-t", "chela:@7"]]


def test_close_reports_a_failed_kill(close_env, monkeypatch, capsys):
    monkeypatch.setattr(main.subprocess, "run",
                        lambda cmd, **kw: _Proc(returncode=1, stderr="can't find window"))
    assert _main(["close", "@5"]) == 1
    assert "can't find window" in capsys.readouterr().err


def test_run_close_still_requires_a_reason(close_env, monkeypatch, capsys):
    called = []
    monkeypatch.setattr(dispatcher, "close_run", lambda *a, **kw: called.append(a))
    assert _main(["close", "cmx-9"]) == 2
    assert called == [] and close_env == []
    assert "--reason" in capsys.readouterr().err


def test_a_run_id_still_routes_to_run_close_not_window_kill(close_env, monkeypatch):
    called = []

    def close_run(run, reason, **kw):
        called.append((run, reason))
        return {"ok": True, "task_id": "CMX-9", "reason": reason, "from_status": "failed"}
    monkeypatch.setattr(dispatcher, "close_run", close_run)
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: None)
    assert _main(["close", "cmx-9", "--reason", "superseded", "--keep-pr"]) is None
    assert called == [("cmx-9", "superseded")]
    assert close_env == []
