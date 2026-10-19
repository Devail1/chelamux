"""CMX-84: a ``claude`` typed BY HAND into a chela window gets Remote Control too.

``spawn_window`` types ONE setup line into every window it opens (CMX-269): the
``CHELA_WID`` export and, while the setting is on, ``. claude-rc.sh <name>``
(:data:`chela.spawn.RC_SHELL_FILE`). These tests run that exact line in a real ``bash`` AND
``zsh`` with a stub ``claude``, ``tmux`` and ``clear`` first on ``PATH`` — no live tmux
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
# real shell runs below go through the reference taken here, before any stub.
_REAL_RUN = subprocess.run
_SHELLS = [pytest.param(["bash", "-c"], id="bash"),
           pytest.param(["zsh", "-f", "-c"], id="zsh", marks=pytest.mark.skipif(
               shutil.which("zsh") is None, reason="needs zsh"))]

# The CMX-84 wrapper exactly as chela typed it inline (for window @42 named "proj") before
# CMX-269 moved it into a file — the parity reference for the sourced one.
_INLINE_CMX84 = (
    "function claude { local _chela_a _chela_n; "
    'case "${1:-}" in ""|-*) ;; *) command claude "$@"; return;; esac; '
    'for _chela_a in "$@"; do case "$_chela_a" in '
    "--remote-control|--remote-control=*|-p|--print|-h|--help|-v|--version) "
    'command claude "$@"; return;; esac; done; '
    "_chela_n=$(command tmux display-message -p -t @42 '#W' 2>/dev/null); "
    '[ -n "$_chela_n" ] || _chela_n=proj; '
    'command tmux set-window-option -t @42 @chela_rc_name "$_chela_n" >/dev/null 2>&1; '
    'command claude --remote-control "$_chela_n" "$@"; }'
)


@pytest.fixture(autouse=True)
def _scratch_chela_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_DIR", str(tmp_path / "chela"))


@pytest.fixture(params=_SHELLS)
def shell(request):
    return request.param


def _stubs(tmp_path: Path, live_name: str = "") -> tuple[Path, Path, Path]:
    """A bin dir with stub ``claude`` (argv → one line per arg, exits 7), ``tmux`` (logs
    each call one line, every argument bracketed so a word-split shows; ``display-message``
    answers ``live_name``) and ``clear`` (a no-op)."""
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
    (bindir / "clear").write_text("#!/bin/sh\nexit 0\n")
    for f in ("claude", "tmux", "clear"):
        (bindir / f).chmod(0o755)
    return bindir, argv_log, tmux_log


def _type(tmp_path: Path, definition: str, typed: str, live_name: str = "",
          after: str = "", shell: list[str] = ("bash", "-c")):
    """Run the setup line, then ``typed`` as the human would (then ``after``, whose stdout
    is returned too). Returns (argv, tmux log, stdout)."""
    bindir, argv_log, tmux_log = _stubs(tmp_path, live_name)
    # FUNCNEST=1: the wrapper must reach the real binary through `command claude`. Calling
    # itself instead is argv-identical (the inner call sees --remote-control and passes
    # through), so only a nesting limit tells the two apart. No inherited CHELA_WID (this
    # suite may itself run in a chela window); TMUX_PANE is this pane, as tmux sets it.
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "FUNCNEST": "1",
           "TMUX_PANE": "%7"}
    env.pop("CHELA_WID", None)
    # `typed` must hand back the stub's exit status (7) — the wrapper is transparent to `$?`.
    script = f'{definition}\n{typed}\n[ $? -eq 7 ] || exit 99\n{after}'
    proc = _REAL_RUN([*shell, script], env=env, capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)
    argv = argv_log.read_text().splitlines() if argv_log.exists() else None
    calls = tmux_log.read_text() if tmux_log.exists() else ""
    return argv, calls, proc.stdout


def _tmux(*args: str) -> str:
    """One logged tmux call, in the stub's bracketed form."""
    return "".join(f"[{a}]" for a in args)


def _new_shell(monkeypatch, tmp_path, *, rc: bool, command: str | None = None,
               wid: str = "@42", tmux_calls: list | None = None) -> list[str]:
    """Open a window through the real ``spawn_window`` — a "+ New shell" one (no command)
    unless ``command`` is given. ``tmux_calls`` records every other tmux argv."""
    sent = _patch_tmux(monkeypatch, wid=wid, remote_control=rc)
    if tmux_calls is not None:
        monkeypatch.setattr(spawn.subprocess, "run", lambda argv, *a, **kw: (
            tmux_calls.append(list(argv)), spawn.subprocess.CompletedProcess(argv, 0, wid, ""))[1])
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
    """The setup line — the first send, and the only one before any command."""
    assert sent[0].startswith(" "), sent
    return sent[0]


# --- CMX-269: one quiet line, then a clean screen ----------------------------------------

@pytest.mark.parametrize("rc,wid,expected", [
    (True, "@42", f" export CHELA_WID=@42; . {spawn.RC_SHELL_FILE} proj; clear"),
    (False, "@42", " export CHELA_WID=@42; clear"),
    (True, "", f" . {spawn.RC_SHELL_FILE} proj; clear"),
])
@pytest.mark.parametrize("command", [None, "claude"])
def test_one_setup_line_starting_with_a_space_then_the_scrollback_is_cleared(
        monkeypatch, tmp_path, rc, wid, expected, command):
    calls: list[list[str]] = []
    sent = _new_shell(monkeypatch, tmp_path, rc=rc, wid=wid, command=command, tmux_calls=calls)
    assert sent[0] == expected
    assert len(sent) == (2 if command else 1)
    assert sent[-1].startswith("claude") if command else True
    assert not any(_MARKER in t for t in sent)
    assert ["tmux", "clear-history", "-t", f"{spawn.config.current_session()}:proj"] in calls


def test_no_window_id_and_rc_off_sends_nothing_and_clears_nothing(monkeypatch, tmp_path):
    calls: list[list[str]] = []
    monkeypatch.setattr(spawn, "_send", lambda *a: None)
    sent = _patch_tmux(monkeypatch, wid="", remote_control=False)
    monkeypatch.setattr(spawn.subprocess, "run", lambda argv, *a, **kw: (
        calls.append(list(argv)), spawn.subprocess.CompletedProcess(argv, 0, "", ""))[1])
    workdir = tmp_path / "proj"
    workdir.mkdir()
    assert spawn.spawn_window(workdir).ok
    assert sent == [] and not any("clear-history" in c for c in calls)


def test_the_shipped_file_lives_inside_the_package():
    assert spawn.RC_SHELL_FILE.is_file()
    assert spawn.RC_SHELL_FILE.parent.parent == Path(spawn.__file__).parent


def test_a_window_name_with_a_quote_reaches_the_file_as_one_argument(tmp_path):
    line = spawn._setup_line("@42", "it's", rc=True)
    _, calls, _ = _type(tmp_path, line, "claude", live_name="")
    assert calls.splitlines()[-1] == _tmux(
        "set-window-option", "-t", "@42", rc_rename.PUSHED_OPTION, "it's")


@pytest.mark.parametrize("err", [FileNotFoundError("tmux"),
                                 subprocess.TimeoutExpired("tmux", 10)])
def test_a_clear_history_failure_is_logged_never_raised(monkeypatch, caplog, err):
    def boom(*a, **kw):
        raise err
    monkeypatch.setattr(spawn.subprocess, "run", boom)
    spawn._clear_history("chela:proj")
    assert "clear-history on chela:proj failed" in caplog.text


@pytest.mark.parametrize("typed", [
    "claude", "claude --model opus -c", "claude -p hello", "claude --version",
    "claude x", "claude --remote-control=mine", "claude --append-system-prompt 'use -p'",
])
@pytest.mark.parametrize("live_name", ["", "renamed"])
def test_the_sourced_file_matches_the_cmx84_inline_wrapper(tmp_path, shell, typed, live_name):
    """Same argv to ``claude``, same tmux calls, as the function chela used to type."""
    new = _type(tmp_path, spawn._setup_line("@42", "proj", rc=True), typed, live_name,
                shell=shell)
    old = _type(tmp_path, _INLINE_CMX84, typed, live_name, shell=shell)
    assert new[:2] == old[:2]
    assert new[0] is not None


# --- RC on: the function is defined, and a hand-typed `claude` gets the flag ----------

def test_typed_claude_launches_with_remote_control_and_marks_the_window(
        monkeypatch, tmp_path, shell):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    argv, calls, _ = _type(tmp_path, definition, "claude", shell=shell)
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


def test_wrapper_leaks_no_variables_into_the_users_shell(monkeypatch, tmp_path, shell):
    definition = _definition(_new_shell(monkeypatch, tmp_path, rc=True))
    _, _, out = _type(tmp_path, definition, "claude", shell=shell,
                      after='echo "[${_chela_n-unset}|${_chela_a-unset}|${_chela_t-unset}]"')
    assert out.strip() == "[unset|unset|unset]"


def test_no_window_id_falls_back_to_this_pane(monkeypatch, tmp_path, shell):
    """tmux gave no id ⇒ the wrapper reads and marks its own pane (``$TMUX_PANE``)."""
    sent = _new_shell(monkeypatch, tmp_path, rc=True, wid="")
    assert "CHELA_WID" not in _definition(sent)
    _, calls, _ = _type(tmp_path, _definition(sent), "claude", shell=shell)
    assert calls.splitlines() == [
        _tmux("display-message", "-p", "-t", "%7", "#W"),
        _tmux("set-window-option", "-t", "%7", rc_rename.PUSHED_OPTION, "proj"),
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
    assert sent[0] == " export CHELA_WID=@42; clear"
    assert not any("claude-rc" in t or "function" in t for t in sent), sent


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
