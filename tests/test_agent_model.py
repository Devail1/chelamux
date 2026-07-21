"""The coding-agent model — a dashboard-WRITABLE Settings choice (CMX-91).

It rides the exact rails the permission mode already does, and the same two
properties are load-bearing:

1. **The model is a closed enum, validated server-side, fail-closed.** It is
   interpolated into the shell command that spawns an agent, and the dashboard
   is reachable over a tailnet — so an unknown value must be rejected by the API
   and must never reach a shell. A bad value on disk reads as unset and falls
   back to the built-in default (`sonnet`).
2. **⛔ The JUDGE is NOT downgraded.** The judge is the adversarial safety net;
   its command runs on the fixed capable :data:`DEFAULT_JUDGE_MODEL`, decoupled
   from the coding-agent Settings model, so a `sonnet`/`haiku` default set for the
   fleet can never reach it. This is checked both at the pure resolver AND end to
   end through the actual spawn wiring (`_spawn` / `_spawn_judge`), because the
   thing that could silently regress is the call-site passing the wrong role.
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


def _wf(tmp_path: Path | None = None, **agent) -> WorkflowDef:
    return WorkflowDef(
        path=(tmp_path or Path("/nowhere")) / "WORKFLOW.md",
        config={"project_key": "CMX", "agent": agent},
        prompt_template="go {{workspace_path}}",
    )


# --- the enum ---------------------------------------------------------------

def test_models_are_the_cli_aliases(mods):
    """The aliases `claude --model` accepts. `sonnet` is the default (cmx tasks
    rarely need Opus)."""
    _, _, dispatcher = mods
    assert set(dispatcher.AGENT_MODELS) == {"sonnet", "opus", "haiku"}
    assert dispatcher.DEFAULT_AGENT_MODEL == "sonnet"
    assert dispatcher.DEFAULT_AGENT_MODEL in dispatcher.AGENT_MODELS


def test_every_model_is_shell_inert(mods):
    """Enum values are interpolated into a shell command, so no member may carry
    shell syntax — a guard on the enum itself."""
    _, _, dispatcher = mods
    for m in (*dispatcher.AGENT_MODELS, dispatcher.DEFAULT_JUDGE_MODEL):
        assert m.isalpha(), m


# --- GUARD 1: the default is sonnet -----------------------------------------

def test_default_coding_model_is_sonnet(mods):
    """⚖️ With nothing set, the coding-agent command carries `--model sonnet`.
    Corrupt (drop the `--model` append in resolve_agent_cmd) → this goes RED."""
    _, _, dispatcher = mods
    cmd, _ = dispatcher.resolve_agent_cmd(_wf())
    assert "--model sonnet" in cmd


# --- GUARD 2: a Settings override reaches the command -----------------------

def test_settings_model_reaches_the_command(mods):
    """⚖️ userconfig `agent_model=opus` → the coding-agent command carries
    `--model opus`. Corrupt (ignore the setting) → RED."""
    _, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.AGENT_MODEL_KEY, "opus")
    assert dispatcher.settings_agent_model() == "opus"
    cmd, _ = dispatcher.resolve_agent_cmd(_wf())
    assert "--model opus" in cmd
    assert "--model sonnet" not in cmd


# --- GUARD 3: the judge is NOT downgraded (load-bearing) --------------------

def test_judge_is_not_downgraded_by_the_coding_model_setting(mods):
    """🔴 With the fleet's coding model set to a cheaper tier, the JUDGE's command
    still uses the fixed capable model, NOT the coding-agent model.

    Corrupt (let the judge inherit the coding-agent model — e.g. make
    ``agent_model_for`` ignore the role) → RED."""
    _, userconfig, dispatcher = mods
    for downgrade in ("sonnet", "haiku"):
        userconfig.set_(dispatcher.AGENT_MODEL_KEY, downgrade)
        coding, _ = dispatcher.resolve_agent_cmd(_wf(), "coding")
        judge, _ = dispatcher.resolve_agent_cmd(_wf(), "judge")
        assert f"--model {downgrade}" in coding
        assert f"--model {dispatcher.DEFAULT_JUDGE_MODEL}" in judge
        assert f"--model {downgrade}" not in judge


def test_judge_model_is_a_fixed_capable_default(mods):
    """The judge model is a v1 constant, NOT read from userconfig — no Settings
    write can touch it."""
    _, userconfig, dispatcher = mods
    assert dispatcher.DEFAULT_JUDGE_MODEL == "opus"
    # Even the userconfig key that drives the coding model leaves the judge alone.
    userconfig.set_(dispatcher.AGENT_MODEL_KEY, "haiku")
    assert dispatcher.agent_model_for("judge") == dispatcher.DEFAULT_JUDGE_MODEL


# --- GUARD 4: an invalid model falls closed to the default ------------------

@pytest.mark.parametrize("bad", [
    "sonnet; rm -rf ~",
    "--dangerously-skip-permissions",
    "SONNET",
    "gpt-4",
    "bogus",
    "",
    None,
    42,
    ["sonnet"],
])
def test_bad_stored_model_fails_closed_to_default(mods, bad):
    """⚖️ A value outside the enum reads as unset, so the resolved command is the
    built-in default (`sonnet`) and the bad value never reaches the shell.
    Corrupt (interpolate the raw stored value) → RED."""
    _, userconfig, dispatcher = mods
    userconfig._save({dispatcher.AGENT_MODEL_KEY: bad})
    assert dispatcher.settings_agent_model() is None
    cmd, _ = dispatcher.resolve_agent_cmd(_wf())
    assert "--model sonnet" in cmd


def test_corrupt_config_file_does_not_crash(mods):
    """The dispatcher runs unattended under PM2; a truncated config.json degrades
    to the default, it does not raise out of the tick loop."""
    config, _, dispatcher = mods
    path = config.CHELA_DIR / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert dispatcher.settings_agent_model() is None
    assert "--model sonnet" in dispatcher.resolve_agent_cmd(_wf())[0]


# --- precedence: agent.cmd still shadows (and carries its own --model) -------

def test_workflow_cmd_still_shadows_the_model(mods):
    """A workflow that pins agent.cmd stays authoritative — no `--model` is
    appended over it, so it may carry its own. Precedence is unchanged."""
    _, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.AGENT_MODEL_KEY, "opus")
    cmd, src = dispatcher.resolve_agent_cmd(
        _wf(cmd="claude --permission-mode acceptEdits --model haiku"))
    assert (cmd, src) == ("claude --permission-mode acceptEdits --model haiku", "workflow")


def test_model_persists_across_a_daemon_restart(mods):
    """Written by the dashboard process, read by a freshly-started dispatcher —
    the store is the file, not in-process state."""
    _, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.AGENT_MODEL_KEY, "opus")
    importlib.reload(userconfig)                      # simulate a restart
    importlib.reload(dispatcher)
    assert dispatcher.settings_agent_model() == "opus"
    assert "--model opus" in dispatcher.resolve_agent_cmd(_wf())[0]


# --- the WIRING: the actual spawn commands carry the right model ------------
#
# The pure resolver above proves the LOGIC. These prove the WIRING — that
# `_spawn` and `_spawn_judge` actually pass the right role down to it. This is
# the exact class the judge hunts: reverting the `role="judge"` call-site would
# leave the resolver tests green while silently downgrading the judge.

def _conn(dispatcher) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return dispatcher.ensure_schema(conn)


def _sent_claude_cmd(run_mock) -> str | None:
    """The `claude …` string that `_launch_agent` sent to tmux via send-keys."""
    for c in run_mock.call_args_list:
        argv = c.args[0] if c.args else c.kwargs.get("args")
        if isinstance(argv, list) and "send-keys" in argv:
            for a in argv:
                if isinstance(a, str) and a.startswith("claude"):
                    return a
    return None


def test_spawn_launches_the_coding_agent_with_the_settings_model(mods, tmp_path):
    """🔧 End to end through `_spawn`: the command tmux actually receives carries
    the coding-agent Settings model. Corrupt (drop `--model`, or pass the wrong
    role) → RED."""
    _, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.AGENT_MODEL_KEY, "opus")
    wf = _wf(tmp_path)
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
    assert sent is not None and "--model opus" in sent


def test_spawn_judge_launches_on_the_judge_model_not_the_coding_model(mods, tmp_path):
    """🔴 End to end through `_spawn_judge`, with the fleet's coding model set to a
    cheaper tier: the JUDGE's command tmux receives is on the capable judge model,
    NOT the coding-agent model. Corrupt (drop `role="judge"` at the call-site, so
    the judge inherits the coding model) → RED."""
    _, userconfig, dispatcher = mods
    from chela import judge as judge_mod
    userconfig.set_(dispatcher.AGENT_MODEL_KEY, "sonnet")
    wf = _wf(tmp_path)
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
    assert sent is not None
    assert f"--model {dispatcher.DEFAULT_JUDGE_MODEL}" in sent
    assert "--model sonnet" not in sent
    assert judge_mod is not None  # the mutation-engine module is unrelated to the launch cmd


# --- the write path (POST /api/config) --------------------------------------

@pytest.fixture(autouse=True)
def no_live_state(monkeypatch):
    """GET /api/config walks DISCOVERED workflows to report overrides, which
    queries the runs DB. Cut that off so these tests never touch the machine's
    live scheduler.db / real WORKFLOW.md files (the CMX-33 bug)."""
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


def test_post_accepts_a_valid_model(mods, client):
    config, _, dispatcher = mods
    resp = client.post("/api/config", json={"agent_model": "opus"})
    assert resp.status_code == 200
    assert resp.get_json()["agent_model_effective"] == "opus"
    assert _stored(config)[dispatcher.AGENT_MODEL_KEY] == "opus"


@pytest.mark.parametrize("bad", [
    "sonnet; rm -rf ~",
    "sonnet --dangerously-skip-permissions",
    "$(id)",
    "gpt-4",
    "bogus",
    "SONNET",
])
def test_post_rejects_an_unknown_model_and_keeps_the_current_one(mods, client, bad):
    """Server-side rejection — the UI's <select> is a convenience, not the gate."""
    config, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.AGENT_MODEL_KEY, "opus")
    resp = client.post("/api/config", json={"agent_model": bad})
    assert resp.status_code == 400
    assert resp.get_json()["valid"] == list(dispatcher.AGENT_MODELS)
    assert _stored(config)[dispatcher.AGENT_MODEL_KEY] == "opus"
    assert dispatcher.settings_agent_model() == "opus"


def test_post_empty_clears_back_to_the_built_in_default(mods, client):
    config, userconfig, dispatcher = mods
    userconfig.set_(dispatcher.AGENT_MODEL_KEY, "opus")
    resp = client.post("/api/config", json={"agent_model": ""})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["agent_model"] == ""
    assert body["agent_model_effective"] == dispatcher.DEFAULT_AGENT_MODEL
    assert dispatcher.AGENT_MODEL_KEY not in _stored(config)


def test_get_reports_the_model_enum_and_default(mods, client):
    _, _, dispatcher = mods
    body = client.get("/api/config").get_json()
    assert body["agent_models"] == list(dispatcher.AGENT_MODELS)
    assert body["agent_model"] == ""                                  # unset
    assert body["agent_model_effective"] == dispatcher.DEFAULT_AGENT_MODEL
    assert body["agent_model_default"] == dispatcher.DEFAULT_AGENT_MODEL


def test_the_judge_model_is_not_exposed_as_a_setting(mods, client):
    """v1: the judge model is fixed and NOT user-selectable — it must never appear
    as a writable/surfaced Settings field."""
    _, _, _ = mods
    body = client.get("/api/config").get_json()
    assert "judge_model" not in body
    assert "agent_judge_model" not in body
