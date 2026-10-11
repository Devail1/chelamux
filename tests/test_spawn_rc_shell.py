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
    """A bin dir with stub ``claude`` (argv → one line per arg, exits 7) and ``tmux`` (logs
    each call one line, every argument bracketed so a word-split shows; ``display-message``
    answers ``live_name``)."""
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    argv_log, tmux_log = tmp_path / "claude.argv", tmp_path / "tmux.log"
    for f in (argv_log, tmux_log):
        f.unlink(missing_ok=True)       # a second `_type` in one test must not read the first's
    (bindir / "claude").write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done > {argv_log}\nexit 7\n')
    (bindir / "tmux").write_text(
        f'#!/bin/sh\nfor a in "$@"; do printf "[%s]" "$a"; done >> {tmux_log}\n'
        f'echo >> {tmux_log}\n'
        f'[ "$1" = display-message ] && printf "%s" {live_name!r}\nexit 0\n')
    for f in ("claude", "tmux"):
        (bindir / f).chmod(0o755)
    return bindir, argv_log, tmux_log


def _type(tmp_path: Path, definition: str, typed: str, live_name: str = "",
          after: str = ""):
    """Define the wrapper, then run ``typed`` as the human would (then ``after``, whose
    stdout is returned too). Returns (argv, tmux log, stdout)."""
    bindir, argv_log, tmux_log = _stubs(tmp_path, live_name)
    # FUNCNEST=1: the wrapper must reach the real binary through `command claude`. Calling
    # itself instead is argv-identical (the inner call sees --remote-control and passes
    # through), so only a nesting limit tells the two apart.
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FUNCNEST": "1"}
    # `typed` must hand back the stub's exit status (7) — the wrapper is transparent to `$?`.
    script = f'{definition}\n{typed}\n[ $? -eq 7 ] || exit 99\n{after}'
    proc = _REAL_RUN(["bash", "-c", script], env=env,
                     capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    argv = argv_log.read_text().splitlines() if argv_log.exists() else None
    calls = tmux_log.read_text() if tmux_log.exists() else ""
    return argv, calls, proc.stdout


def _tmux(*args: str) -> str:
    """One logged tmux call, in the stub's bracketed form."""
    return "".join(f"[{a}]" for a in args)


def _new_shell(monkeypatch, tmp_path, *, rc: bool, command: str | None = None,
               wid: str = "@42") -> list[str]:
    """Open a window through the real ``spawn_window`` — a "+ New shell" one (no command)
    unless ``command`` is given."""
    sent = _patch_tmux(monkeypatch, wid=wid, remote_control=rc)
    # Re-spy `_send` keeping the TARGET too: every keystroke must go into the new window.
    targets: list[str] = []
    monkeypatch.setattr(spawn, "_send",
                        lambda target, text: (targets.append(target), sent.append(text)))
    workdir = tmp_path / "proj"
    workdir.mkdir()
    assert spawn.spawn_window(workdir, command=command).ok
    assert sent and set(targets) == {f"{spawn.config.current_session()}:proj"}, targets
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
    argv, calls, _ = _type(tmp_path, definition, "claude")
    assert argv == ["--remote-control", "proj"]
    assert _tmux("set-window-option", "-t", "@42", rc_rename.PUSHED_OPTION, "proj") in calls


def test_typed_claude_keeps_its_own_flags_after_the_insert(monkeypatch, tmp_path):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, _, _ = _type(tmp_path, definition, "claude --model opus -c")
    assert argv == ["--remote-control", "proj", "--model", "opus", "-c"]


def test_typed_claude_uses_the_windows_live_name_after_a_rename(monkeypatch, tmp_path):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, calls, _ = _type(tmp_path, definition, "claude", live_name="renamed")
    assert argv == ["--remote-control", "renamed"]
    assert _tmux(rc_rename.PUSHED_OPTION, "renamed") in calls


def test_a_live_name_with_spaces_stays_one_argument(monkeypatch, tmp_path):
    """A human may rename the window to anything — the name is ONE argv word both to claude
    and to the tmux mark."""
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, calls, _ = _type(tmp_path, definition, "claude -c", live_name="my  proj")
    assert argv == ["--remote-control", "my  proj", "-c"]
    assert calls.splitlines()[-1] == _tmux(
        "set-window-option", "-t", "@42", rc_rename.PUSHED_OPTION, "my  proj")


def test_a_flag_inside_an_argument_value_does_not_skip(monkeypatch, tmp_path):
    """The skip scan reads whole arguments: ``-p`` inside a quoted VALUE is not ``-p``."""
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, _, _ = _type(tmp_path, definition, "claude --append-system-prompt 'use -p never'")
    assert argv == ["--remote-control", "proj", "--append-system-prompt", "use -p never"]


def test_typed_claude_reads_and_marks_this_windows_id(monkeypatch, tmp_path):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    _, calls, _ = _type(tmp_path, definition, "claude", live_name="renamed")
    assert calls.splitlines() == [
        _tmux("display-message", "-p", "-t", "@42", "#W"),
        _tmux("set-window-option", "-t", "@42", rc_rename.PUSHED_OPTION, "renamed"),
    ]


def test_typed_claude_passes_an_argument_with_spaces_through_as_one(monkeypatch, tmp_path):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, _, _ = _type(tmp_path, definition,
                       "claude --append-system-prompt 'be  brief' --model opus")
    assert argv == ["--remote-control", "proj", "--append-system-prompt", "be  brief",
                    "--model", "opus"]


def test_wrapper_leaks_no_variables_into_the_users_shell(monkeypatch, tmp_path):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    _, _, out = _type(tmp_path, definition, "claude",
                      after='echo "[${_chela_n-unset}|${_chela_a-unset}]"')
    assert out.strip() == "[unset|unset]"


def test_no_window_id_falls_back_to_the_session_target(monkeypatch, tmp_path):
    """tmux gave no id ⇒ the wrapper reads and marks ``<session>:<name>`` instead."""
    monkeypatch.setattr(spawn.config, "current_session", lambda: "chela")
    sent = _new_shell(monkeypatch, tmp_path, rc=True, wid="")
    assert not any(t.startswith("export CHELA_WID") for t in sent)
    _, calls, _ = _type(tmp_path, _definition(sent), "claude")
    assert calls.splitlines() == [
        _tmux("display-message", "-p", "-t", "chela:proj", "#W"),
        _tmux("set-window-option", "-t", "chela:proj", rc_rename.PUSHED_OPTION, "proj"),
    ]


# --- a window chela opens WITH a command (agent window for a human) ---------------------

def test_agent_window_defines_the_wrapper_before_its_launch(monkeypatch, tmp_path):
    """The wrapper must be in place BEFORE chela's own `claude` is typed — typed after, it
    would land in claude's TUI as a prompt, and the shell would never have it."""
    sent = _new_shell(monkeypatch, tmp_path, rc=True, command="claude")
    launch = next(t for t in sent if t.startswith("claude"))
    assert sent.index(_definition(sent)) < sent.index(launch)


def test_chelas_own_launch_through_the_wrapper_carries_one_remote_control(
        monkeypatch, tmp_path):
    """End to end: the exact line chela sends, run through the wrapper chela defined first,
    starts claude with a single ``--remote-control <name>`` — never a doubled flag."""
    sent = _new_shell(monkeypatch, tmp_path, rc=True, command="claude --model opus")
    launch = next(t for t in sent if t.startswith("claude"))
    argv, calls, _ = _type(tmp_path, _definition(sent), launch)
    assert argv.count("--remote-control") == 1
    assert argv[argv.index("--remote-control") + 1] == "proj"
    assert argv[-2:] == ["--model", "opus"]
    assert calls == ""                 # chela's launch already marked it, in Python


# --- argv the wrapper must leave alone -------------------------------------------------

@pytest.mark.parametrize("typed", [
    "claude -p hello",
    "claude -p 'two  words'",
    "claude --remote-control 'my  name' -c",
    "claude --print hello",
    "claude --model opus -p hello",
    "claude --remote-control mine",
    "claude --remote-control=mine",
    "claude --version",
    "claude -v",
    "claude --help",
    "claude -h",
    "claude --model opus --remote-control",
    "claude mcp list",
    "claude 'fix the bug'",
])
def test_non_interactive_or_already_flagged_claude_is_unchanged(monkeypatch, tmp_path, typed):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, calls, _ = _type(tmp_path, definition, typed)
    expected, _, _ = _type(tmp_path, "", typed)       # the same line with no wrapper at all
    assert argv == expected
    assert calls == ""                 # no live-name read, no mark — tmux never touched


# --- off: no wrapper at all -------------------------------------------------------------

@pytest.mark.parametrize("command", [None, "claude"])
def test_rc_off_defines_no_wrapper(monkeypatch, tmp_path, command):
    sent = _new_shell(monkeypatch, tmp_path, rc=False, command=command)
    assert "export CHELA_WID=@42" in sent
    assert not any("claude {" in t or "function" in t for t in sent), sent


def test_dispatched_agent_window_gets_no_wrapper(monkeypatch, tmp_path):
    """A dispatched agent/judge window must never surface in the desktop app — even with
    the setting on, nothing the dispatcher types into its window defines the wrapper.

    Documents the separation: ``dispatcher._spawn`` opens its windows itself and never
    calls ``spawn_window``, so this pins that the dispatcher grew no wrapper of its own."""
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
