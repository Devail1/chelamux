"""``chela.spawn`` — session-id pinning at spawn time (docs/AGENT_IDENTITY.md slice 2a).

No tmux: ``spawn_window``'s own tmux calls (``subprocess.run``, ``_send``) and its
collaborators (``discovery.ensure_session``, ``discovery.get_all_windows``,
``agent_manager.lock_window_name``) are all stubbed, so this exercises exactly the
wiring slice 2a adds — the uuid pinned into the sent command versus the id recorded
in the window-binding store — without depending on a real tmux server (see
``tests/test_telegram_new_launch_bind.py`` for that end-to-end coverage).
"""
from __future__ import annotations

import re

import pytest

from chela import spawn
from chela.telegram.bindings import BindingRegistry

_SESSION_RE = re.compile(r"--session-id ([0-9a-f-]{36})$")


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


def test_pin_session_id_appends_a_uuid_to_a_bare_command():
    to_send, recorded = spawn._pin_session_id("claude", "36358c6b-1111-4a11-8888-abc123456789")
    assert to_send == "claude --session-id 36358c6b-1111-4a11-8888-abc123456789"
    # Byte-identical: what got sent and what would be recorded are the SAME string.
    assert recorded == "36358c6b-1111-4a11-8888-abc123456789"
    assert to_send.endswith(recorded)


@pytest.mark.parametrize("command", [
    "claude --session-id existing-id",
    "claude --resume abc-123",
    "claude --continue",
])
def test_pin_session_id_leaves_an_override_untouched_and_records_nothing(command):
    to_send, recorded = spawn._pin_session_id(command, "generated-uuid")
    assert to_send == command          # not modified
    assert recorded is None            # NULL, not a fabricated id


def test_spawn_window_pins_and_records_a_session_id(monkeypatch, tmp_path):
    sent = _patch_tmux(monkeypatch, wid="@42")

    result = spawn.spawn_window(tmp_path, command="claude")

    assert result.ok
    launch = [t for t in sent if t.startswith("claude")]
    assert len(launch) == 1
    m = _SESSION_RE.search(launch[0])
    assert m, launch

    registry = BindingRegistry.load()
    assert registry.session_id_for("@42") == m.group(1)


def test_spawn_window_with_an_override_command_records_no_session_id(monkeypatch, tmp_path):
    sent = _patch_tmux(monkeypatch, wid="@42")

    result = spawn.spawn_window(tmp_path, command="claude --resume some-prior-session")

    assert result.ok
    launch = [t for t in sent if t.startswith("claude")]
    assert launch == ["claude --resume some-prior-session"]   # untouched

    registry = BindingRegistry.load()
    assert registry.session_id_for("@42") is None


def test_spawn_window_without_a_command_records_nothing(monkeypatch, tmp_path):
    _patch_tmux(monkeypatch, wid="@42")

    result = spawn.spawn_window(tmp_path)

    assert result.ok
    registry = BindingRegistry.load()
    assert registry.session_id_for("@42") is None
