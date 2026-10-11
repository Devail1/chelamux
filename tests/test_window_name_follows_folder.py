"""CMX-83 — a still-auto window name follows its session's folder.

The defect: a window spawned in ``~`` is named ``<login>-3``; its Claude then works in
``~/projects/cute-animals``, the sidebar groups it there, and the name never moves. These
pin :func:`chela.agent_manager.follow_folder_names`: an auto name is re-derived (debounced
one tick), and a manual name, the orchestrator and a dispatched window are never touched.
The rename must reach both downstream paths — Remote Control (``rc_rename.request``) and
the Telegram topic (which titles itself from the live window name).

Synthetic tmux via a stateful monkeypatched ``subprocess.run`` — no live tmux.
"""
from __future__ import annotations

import subprocess
import types

import pytest

from chela import agent_manager, config, inbox, rc_rename
from chela.telegram.bindings import BindingRegistry
from chela.telegram.reconcile import reconcile_bindings

HOME = "/home/op"
CUTE = "/home/op/projects/cute-animals"


class _FakeTmux:
    """A tiny stateful tmux: ``list-windows`` reflects every ``rename-window`` and
    ``set-window-option`` made so far."""

    def __init__(self, windows):
        # wid -> {name, start, name_cwd, manual}
        self.windows = {w["wid"]: dict(w) for w in windows}
        self.calls: list[list[str]] = []

    def __call__(self, cmd, **kw):
        self.calls.append(cmd)
        if cmd[:2] == ["tmux", "list-windows"]:
            out = "".join(
                "\t".join([wid, w["name"], w.get("start", ""), w.get("name_cwd", ""),
                           w.get("manual", "")]) + "\n"
                for wid, w in self.windows.items())
            return types.SimpleNamespace(returncode=0, stdout=out, stderr="")
        if cmd[:2] == ["tmux", "rename-window"]:
            self.windows[cmd[3].split(":")[-1]]["name"] = cmd[4]
        if cmd[:2] == ["tmux", "set-window-option"] and cmd[4] == agent_manager.NAME_CWD_OPTION:
            self.windows[cmd[3].split(":")[-1]]["name_cwd"] = cmd[5]
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")

    def name(self, wid):
        return self.windows[wid]["name"]


@pytest.fixture
def env(monkeypatch):
    """Patch tmux, the session feed and the RC hook; returns a setup function."""
    rc_calls: list[tuple[str, str]] = []
    state = {"cwd": {}, "orch": None}

    def setup(*windows):
        fake = _FakeTmux(windows)
        monkeypatch.setattr(subprocess, "run", fake)
        return fake

    monkeypatch.setattr(config, "current_session", lambda: "sess")
    monkeypatch.setenv("HOME", HOME)
    monkeypatch.setattr(agent_manager, "_login", lambda: "op")
    monkeypatch.setattr(agent_manager.os.path, "realpath", lambda p: p)
    monkeypatch.setattr(agent_manager, "session_status_map", lambda force=False: {})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: wid)
    monkeypatch.setattr(
        agent_manager, "session_entry",
        lambda cpid, m: {"cwd": state["cwd"][cpid]} if cpid in state["cwd"] else None)
    monkeypatch.setattr(inbox, "orchestrator_wid", lambda store=None: state["orch"])
    monkeypatch.setattr(rc_rename, "request", lambda wid, name: rc_calls.append((wid, name)))
    return types.SimpleNamespace(setup=setup, state=state, rc=rc_calls)


def _tick_twice(pending):
    first = agent_manager.follow_folder_names(pending)
    return first, agent_manager.follow_folder_names(pending)


def test_auto_named_window_follows_its_session_from_home_to_cute_animals(env):
    fake = env.setup({"wid": "@183", "name": "op-3", "start": HOME},
                     {"wid": "@1", "name": "other", "start": "/srv/other"})
    env.state["cwd"]["@183"] = CUTE
    pending: dict[str, str] = {}

    first, second = _tick_twice(pending)

    assert first == []                       # debounced: the move must hold one tick
    assert second == ["op-3 -> cute-animals"]
    assert fake.name("@183") == "cute-animals"
    assert fake.windows["@183"]["name_cwd"] == CUTE
    # A third tick is steady state — derived-from is now cute-animals, nothing to do.
    assert agent_manager.follow_folder_names(pending) == []


def test_a_move_that_does_not_hold_a_tick_is_not_renamed(env):
    fake = env.setup({"wid": "@183", "name": "op-3", "start": HOME})
    pending: dict[str, str] = {}
    env.state["cwd"]["@183"] = CUTE
    agent_manager.follow_folder_names(pending)
    env.state["cwd"]["@183"] = "/home/op/projects/elsewhere"
    assert agent_manager.follow_folder_names(pending) == []
    assert fake.name("@183") == "op-3"


def test_taken_folder_name_gets_a_collision_suffix(env):
    fake = env.setup({"wid": "@1", "name": "cute-animals", "start": CUTE, "manual": "1"},
                     {"wid": "@183", "name": "op-3", "start": HOME})
    env.state["cwd"]["@183"] = CUTE
    _tick_twice({})
    assert fake.name("@183") == "cute-animals-2"


def test_manual_name_is_never_touched(env):
    # Same name shape as an auto one — the explicit flag, not the shape, decides.
    fake = env.setup({"wid": "@183", "name": "op-3", "start": HOME, "manual": "1"})
    env.state["cwd"]["@183"] = CUTE
    assert _tick_twice({}) == ([], [])
    assert fake.name("@183") == "op-3"
    assert env.rc == []


def test_a_name_that_is_not_the_spawn_folders_is_never_touched(env):
    # A hand `tmux rename-window` (no manual flag): it no longer matches its folder.
    fake = env.setup({"wid": "@183", "name": "my-work", "start": HOME})
    env.state["cwd"]["@183"] = CUTE
    assert _tick_twice({}) == ([], [])
    assert fake.name("@183") == "my-work"


def test_orchestrator_window_is_not_renamed(env):
    fake = env.setup({"wid": "@0", "name": "op", "start": HOME})
    env.state["cwd"]["@0"] = CUTE
    env.state["orch"] = "@0"
    assert _tick_twice({}) == ([], [])
    assert fake.name("@0") == "op"


def test_dispatched_and_judge_windows_are_not_renamed(env):
    # Branch names (`owner/cmx-N-…`) and judge names: the dispatcher finds them BY NAME.
    branch = "owner/cmx-9-x"
    fake = env.setup({"wid": "@5", "name": branch, "start": "/w/owner/cmx-9-x",
                      "name_cwd": "/w/owner/cmx-9-x"},
                     {"wid": "@6", "name": "judge-9", "start": "/w/judge-9"})
    env.state["cwd"].update({"@5": CUTE, "@6": CUTE})
    assert _tick_twice({}) == ([], [])
    assert fake.name("@5") == branch
    assert fake.name("@6") == "judge-9"


def test_the_rename_reaches_the_remote_control_path(env):
    env.setup({"wid": "@183", "name": "op-3", "start": HOME})
    env.state["cwd"]["@183"] = CUTE
    _tick_twice({})
    assert env.rc == [("@183", "cute-animals")]


def test_the_rename_reaches_the_topic_rename_path(env):
    fake = env.setup({"wid": "@183", "name": "op-3", "start": HOME})
    env.state["cwd"]["@183"] = CUTE
    reg = BindingRegistry("777")
    reg.bind("@183", "100")
    reg.set_topic_name("@183", "op-3")

    _tick_twice({})

    class _Api:
        renamed: list = []

        def rename_topic(self, thread, name):
            self.renamed.append((str(thread), name))
            return True

    api = _Api()
    reconcile_bindings(reg, {"@183": fake.name("@183")}, {"@183"}, api,
                       cwd_for=lambda wid: CUTE)
    assert api.renamed == [("100", "cute-animals")]
