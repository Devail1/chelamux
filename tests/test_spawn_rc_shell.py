"""CMX-84: a ``claude`` typed BY HAND into a chela window gets Remote Control too.

``spawn_window`` defines a ``claude`` shell function in every window it opens while the
setting is on (:func:`chela.spawn._rc_shell_function`). These tests run that exact line in a
real ``bash`` with a stub ``claude`` and a stub ``tmux`` first on ``PATH`` — no live tmux
server — and read back the argv the stub ``claude`` was started with and the tmux calls the
wrapper made. ``CHELA_DIR`` is pointed at scratch so nothing reaches real state.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from chela import dispatcher, rc_rename, spawn
from tests.test_dispatcher_spawn_retry import _conn, _FakeTmux, _task, _wf
from tests.test_spawn import _patch_tmux

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

_MARKER = "function claude"
# `_patch_tmux` stubs `subprocess.run` process-wide (spawn.subprocess IS the module), so the
# real bash run below goes through the reference taken here, before any stub.
_REAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def _scratch_chela_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))


def _stubs(tmp_path: Path, live_name: str = "") -> tuple[Path, Path, Path]:
    """A bin dir with stub ``claude`` (argv → one line per arg) and ``tmux`` (logs each call;
    ``display-message`` answers ``live_name``)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    argv_log, tmux_log = tmp_path / "claude.argv", tmp_path / "tmux.log"
    (bindir / "claude").write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > {argv_log}\n')
    (bindir / "tmux").write_text(
        f'#!/bin/sh\necho "$*" >> {tmux_log}\n'
        f'[ "$1" = display-message ] && printf "%s" {live_name!r}\nexit 0\n')
    for f in ("claude", "tmux"):
        (bindir / f).chmod(0o755)
    return bindir, argv_log, tmux_log


def _type(tmp_path: Path, definition: str, typed: str, live_name: str = ""):
    """Define the wrapper, then run ``typed`` as the human would. Returns (argv, tmux log)."""
    bindir, argv_log, tmux_log = _stubs(tmp_path, live_name)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}"}
    proc = _REAL_RUN(["bash", "-c", f"{definition}\n{typed}"], env=env,
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    argv = argv_log.read_text().splitlines() if argv_log.exists() else None
    calls = tmux_log.read_text() if tmux_log.exists() else ""
    return argv, calls


def _new_shell(monkeypatch, tmp_path, *, rc: bool) -> list[str]:
    """Open a "+ New shell" window (no command) through the real ``spawn_window``."""
    sent = _patch_tmux(monkeypatch, wid="@42", remote_control=rc)
    workdir = tmp_path / "proj"
    workdir.mkdir()
    assert spawn.spawn_window(workdir).ok
    return sent


def _definition(sent: list[str]) -> str:
    defs = [t for t in sent if t.startswith(_MARKER)]
    assert len(defs) == 1, sent
    return defs[0]


# --- RC on: the function is defined, and a hand-typed `claude` gets the flag ----------

def test_new_shell_window_defines_the_wrapper_after_the_wid_export(monkeypatch, tmp_path):
    sent = _new_shell(monkeypatch, tmp_path, rc=True)
    assert sent.index("export CHELA_WID=@42") < sent.index(_definition(sent))


def test_typed_claude_launches_with_remote_control_and_marks_the_window(
        monkeypatch, tmp_path):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, calls = _type(tmp_path, definition, "claude")
    assert argv == ["--remote-control", "proj"]
    assert f"set-window-option -t @42 {rc_rename.PUSHED_OPTION} proj" in calls


def test_typed_claude_keeps_its_own_flags_after_the_insert(monkeypatch, tmp_path):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, _ = _type(tmp_path, definition, "claude --model opus -c")
    assert argv == ["--remote-control", "proj", "--model", "opus", "-c"]


def test_typed_claude_uses_the_windows_live_name_after_a_rename(monkeypatch, tmp_path):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, calls = _type(tmp_path, definition, "claude", live_name="renamed")
    assert argv == ["--remote-control", "renamed"]
    assert f"{rc_rename.PUSHED_OPTION} renamed" in calls


# --- argv the wrapper must leave alone -------------------------------------------------

@pytest.mark.parametrize("typed", [
    "claude -p hello",
    "claude --print hello",
    "claude --model opus -p hello",
    "claude --remote-control mine",
    "claude --remote-control=mine",
    "claude --version",
    "claude --help",
    "claude mcp list",
    "claude 'fix the bug'",
])
def test_non_interactive_or_already_flagged_claude_is_unchanged(monkeypatch, tmp_path, typed):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, calls = _type(tmp_path, definition, typed)
    expected, _ = _type(tmp_path, "", typed)       # the same line with no wrapper at all
    assert argv == expected
    assert rc_rename.PUSHED_OPTION not in calls


# --- off: no wrapper at all -------------------------------------------------------------

def test_rc_off_defines_no_wrapper(monkeypatch, tmp_path):
    sent = _new_shell(monkeypatch, tmp_path, rc=False)
    assert "export CHELA_WID=@42" in sent
    assert not any(_MARKER in t for t in sent)


def test_dispatched_agent_window_gets_no_wrapper(monkeypatch, tmp_path):
    """A dispatched agent/judge window must never surface in the desktop app — even with
    the setting on, nothing the dispatcher types into its window defines the wrapper."""
    from unittest.mock import patch
    monkeypatch.setenv("CHELA_REMOTE_CONTROL", "true")
    fake = _FakeTmux()
    with patch.object(dispatcher, "ensure_worktree", return_value=(tmp_path / "wt", False)), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        assert dispatcher._spawn(_wf(tmp_path), _task(tmp_path), attempt=1, conn=_conn())
    typed = [" ".join(c) for c in fake.calls if c[:2] == ["tmux", "send-keys"]]
    assert any("export CHELA_WID=" in t for t in typed), typed   # the spy saw the sends
    assert not any(_MARKER in t for t in typed)
