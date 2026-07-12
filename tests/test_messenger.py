"""Tests for ``messenger.send_tmux`` — the low-level tmux delivery primitive.

Focus: the paste-buffer branch must reliably submit. Claude Code collapses a
pasted multi-line block into a ``[Pasted text #N +K lines]`` chip that the first
Enter only acknowledges, so a stranded chip needs a SECOND Enter. The
single-line branch already submits, so it must NEVER get a blind second Enter
(that would fire an empty prompt and interrupt the agent).

All tmux calls are stubbed — no live tmux.
"""
from __future__ import annotations

from unittest.mock import patch

from chela import messenger


class _FakeResult:
    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = b""


def _cmds(calls):
    """The tmux argv list (first positional arg) of each recorded run() call."""
    return [c.args[0] for c in calls]


def _run_send(text: str, pane_after_paste: str):
    """Drive send_tmux with subprocess.run stubbed.

    ``pane_after_paste`` is what capture-pane returns (the pane state after the
    first Enter). Returns (result, list_of_argv).
    """
    def fake_run(cmd, *a, **kw):
        if cmd[:2] == ["tmux", "capture-pane"]:
            return _FakeResult(stdout=pane_after_paste)
        return _FakeResult()

    with patch.object(messenger.subprocess, "run", side_effect=fake_run) as m, \
            patch.object(messenger.time, "sleep"), \
            patch.object(messenger.config, "current_session", return_value="chela"):
        ok = messenger.send_tmux("@1", text)
    return ok, _cmds(m.call_args_list)


# A collapsed paste chip still sitting on the prompt line — needs a 2nd Enter.
_PANE_STRANDED = (
    "╭───────────────────────────────────╮\n"
    "│ ❯ [Pasted text #1 +5 lines]       │\n"
    "╰───────────────────────────────────╯\n"
)
# The paste submitted cleanly — prompt is empty, agent is working.
_PANE_SUBMITTED = (
    "● Working on it…\n"
    "╭───────────────────────────────────╮\n"
    "│ ❯                                 │\n"
    "╰───────────────────────────────────╯\n"
)


def test_paste_stranded_chip_gets_second_enter():
    ok, cmds = _run_send("line one\nline two", _PANE_STRANDED)
    assert ok is True
    # Expected sequence: load-buffer, paste-buffer, Enter, capture-pane, Enter.
    assert cmds[0][:2] == ["tmux", "load-buffer"]
    assert cmds[1][:2] == ["tmux", "paste-buffer"]
    assert cmds[2] == ["tmux", "send-keys", "-t", "chela:@1", "Enter"]
    assert cmds[3][:2] == ["tmux", "capture-pane"]
    assert cmds[4] == ["tmux", "send-keys", "-t", "chela:@1", "Enter"]
    # Exactly two Enter presses — no more.
    enters = [c for c in cmds if c[:2] == ["tmux", "send-keys"] and c[-1] == "Enter"]
    assert len(enters) == 2


def test_paste_submitted_no_second_enter():
    ok, cmds = _run_send("line one\nline two", _PANE_SUBMITTED)
    assert ok is True
    # Chip already gone: capture-pane happens, but NO second Enter.
    assert cmds[2] == ["tmux", "send-keys", "-t", "chela:@1", "Enter"]
    assert cmds[3][:2] == ["tmux", "capture-pane"]
    enters = [c for c in cmds if c[:2] == ["tmux", "send-keys"] and c[-1] == "Enter"]
    assert len(enters) == 1


def test_single_line_never_double_submits():
    ok, cmds = _run_send("just one line", _PANE_STRANDED)
    assert ok is True
    # Single-line branch: one combined send-keys with text + Enter, nothing else.
    assert cmds == [["tmux", "send-keys", "-t", "chela:@1", "just one line", "Enter"]]


def test_pane_has_unsubmitted_paste_guards_empty_prompt():
    assert messenger._pane_has_unsubmitted_paste(_PANE_STRANDED)
    assert not messenger._pane_has_unsubmitted_paste(_PANE_SUBMITTED)
    # A response body that merely mentions the phrase (no prompt glyph) is safe.
    assert not messenger._pane_has_unsubmitted_paste("I copied the Pasted text earlier\n")


# --- capture_pane / send_escape — the tmux primitives the bridge commands use ---

def test_capture_pane_targets_session_window_and_returns_text():
    def fake_run(cmd, *a, **kw):
        assert cmd == ["tmux", "capture-pane", "-p", "-t", "chela:@2"]
        return _FakeResult(stdout="pane contents\n")

    with patch.object(messenger.subprocess, "run", side_effect=fake_run), \
            patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.capture_pane("@2") == "pane contents\n"


def test_capture_pane_ansi_adds_dash_e():
    def fake_run(cmd, *a, **kw):
        assert cmd == ["tmux", "capture-pane", "-e", "-p", "-t", "chela:@2"]
        return _FakeResult(stdout="\x1b[31mred\x1b[0m\n")

    with patch.object(messenger.subprocess, "run", side_effect=fake_run), \
            patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.capture_pane("@2", ansi=True) == "\x1b[31mred\x1b[0m\n"


def test_capture_pane_returns_empty_on_error():
    with patch.object(
        messenger.subprocess, "run", return_value=_FakeResult(returncode=1)
    ), patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.capture_pane("@2") == ""


def test_send_escape_sends_escape_without_enter():
    with patch.object(messenger.subprocess, "run", return_value=_FakeResult()) as m, \
            patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.send_escape("@1") is True
    assert _cmds(m.call_args_list) == [
        ["tmux", "send-keys", "-t", "chela:@1", "Escape"]
    ]


def test_send_escape_returns_false_on_tmux_error():
    import subprocess

    def boom(cmd, *a, **kw):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"no server")

    with patch.object(messenger.subprocess, "run", side_effect=boom), \
            patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.send_escape("@1") is False
