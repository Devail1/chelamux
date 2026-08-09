"""Tests for ``messenger.send_tmux`` — the low-level tmux delivery primitive.

Focus: the paste-buffer branch must reliably submit. Claude Code collapses a
pasted multi-line block into a ``[Pasted text #N +K lines]`` chip that the first
Enter only acknowledges, so a stranded chip needs a SECOND Enter. The
single-line branch already submits, so it must NEVER get a blind second Enter
(that would fire an empty prompt and interrupt the agent).

All tmux calls are stubbed — no live tmux.
"""
from __future__ import annotations

import json
import os
import socket as socket_module
import threading
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


def _run_send(text: str, pane_after_paste: str, pane_before: str | None = None):
    """Drive send_tmux with subprocess.run stubbed.

    ``pane_before`` is what the FIRST capture-pane returns — the input-mode guard's
    read of the pane, before anything is sent (defaults to a safe prose prompt).
    ``pane_after_paste`` is what every later capture-pane returns (the pane state
    after the first Enter). Returns (result, list_of_argv).
    """
    captures = []

    def fake_run(cmd, *a, **kw):
        if cmd[:2] == ["tmux", "capture-pane"]:
            captures.append(cmd)
            first = len(captures) == 1
            return _FakeResult(stdout=(pane_before if first and pane_before is not None
                                       else pane_after_paste))
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
    # Expected sequence: capture-pane (input-mode guard), load-buffer, paste-buffer,
    # Enter, capture-pane (chip guard), Enter.
    assert cmds[0][:2] == ["tmux", "capture-pane"]
    assert cmds[1][:2] == ["tmux", "load-buffer"]
    assert cmds[2][:2] == ["tmux", "paste-buffer"]
    assert cmds[3] == ["tmux", "send-keys", "-t", "chela:@1", "Enter"]
    assert cmds[4][:2] == ["tmux", "capture-pane"]
    assert cmds[5] == ["tmux", "send-keys", "-t", "chela:@1", "Enter"]
    # Exactly two Enter presses — no more.
    enters = [c for c in cmds if c[:2] == ["tmux", "send-keys"] and c[-1] == "Enter"]
    assert len(enters) == 2


def test_paste_submitted_no_second_enter():
    ok, cmds = _run_send("line one\nline two", _PANE_SUBMITTED)
    assert ok is True
    # Chip already gone: the chip-guard capture-pane happens, but NO second Enter.
    assert cmds[3] == ["tmux", "send-keys", "-t", "chela:@1", "Enter"]
    assert cmds[4][:2] == ["tmux", "capture-pane"]
    enters = [c for c in cmds if c[:2] == ["tmux", "send-keys"] and c[-1] == "Enter"]
    assert len(enters) == 1


def test_single_line_sends_literal_text_then_separate_enter():
    ok, cmds = _run_send("just one line", _PANE_STRANDED)
    assert ok is True
    # Single-line branch: literal text (-l, NO Enter) then a SEPARATE Enter, so
    # the TUI settles the (possibly long) blob before the Enter lands — a
    # combined text+Enter strands long messages wrapped on the ❯ input line.
    assert cmds == [
        ["tmux", "capture-pane", "-p", "-t", "chela:@1"],   # the input-mode guard
        ["tmux", "send-keys", "-t", "chela:@1", "-l", "just one line"],
        ["tmux", "send-keys", "-t", "chela:@1", "Enter"],
    ]
    # Exactly one Enter — no second-Enter chip guard (that's paste-only), and exactly
    # one capture-pane (the mode guard — the chip guard must not run on this branch).
    enters = [c for c in cmds if c[:2] == ["tmux", "send-keys"] and c[-1] == "Enter"]
    assert len(enters) == 1
    assert len([c for c in cmds if c[:2] == ["tmux", "capture-pane"]]) == 1


def test_single_line_key_name_sent_literally():
    # A message containing a tmux key name must be typed verbatim, not
    # interpreted as a keypress — that's what ``-l`` guarantees.
    ok, cmds = _run_send("Enter the code Up top", _PANE_STRANDED)
    assert ok is True
    assert cmds[1] == ["tmux", "send-keys", "-t", "chela:@1", "-l", "Enter the code Up top"]
    assert cmds[2] == ["tmux", "send-keys", "-t", "chela:@1", "Enter"]


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


def test_send_key_sends_named_key_without_enter():
    # The control-key keyboard's tmux primitive: a bare key name, no Enter.
    with patch.object(messenger.subprocess, "run", return_value=_FakeResult()) as m, \
            patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.send_key("@2", "C-c") is True
    assert _cmds(m.call_args_list) == [
        ["tmux", "send-keys", "-t", "chela:@2", "C-c"]
    ]


def test_send_key_returns_false_on_tmux_error():
    import subprocess

    def boom(cmd, *a, **kw):
        raise subprocess.CalledProcessError(1, cmd, stderr=b"no server")

    with patch.object(messenger.subprocess, "run", side_effect=boom), \
            patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.send_key("@2", "Up") is False


# --- resend_enter — retry a dropped Enter WITHOUT re-pasting the seed ----------
#
# A late startup redraw (an MCP-auth notice, `gh auth login`, any splash that redraws
# after the paste) can eat the separately-sent Enter, stranding the prompt text on the
# ❯ line unsubmitted. Retrying with a full send_tmux would type that prompt a SECOND
# time on top of itself; resend_enter fires Enter alone.

_PANE_PROMPT = (
    "╭───────────────────────────────────╮\n"
    "│ ❯ seed prompt text                │\n"
    "╰───────────────────────────────────╯\n"
)
_PANE_BASH = (
    "╭───────────────────────────────────╮\n"
    "│ ! rm -rf /                        │\n"
    "╰───────────────────────────────────╯\n"
)


def test_resend_enter_sends_only_enter_into_a_prose_prompt():
    def fake_run(cmd, *a, **kw):
        if cmd[:2] == ["tmux", "capture-pane"]:
            return _FakeResult(stdout=_PANE_PROMPT)
        return _FakeResult()

    with patch.object(messenger.subprocess, "run", side_effect=fake_run) as m, \
            patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.resend_enter("@1") is True
    assert _cmds(m.call_args_list) == [
        ["tmux", "capture-pane", "-p", "-t", "chela:@1"],   # the input-mode guard
        ["tmux", "send-keys", "-t", "chela:@1", "Enter"],
    ]


def test_resend_enter_refuses_an_unsafe_input_mode_and_sends_nothing():
    def fake_run(cmd, *a, **kw):
        if cmd[:2] == ["tmux", "capture-pane"]:
            return _FakeResult(stdout=_PANE_BASH)
        return _FakeResult()

    with patch.object(messenger.subprocess, "run", side_effect=fake_run) as m, \
            patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.resend_enter("@1") is False
    # Only the mode-check read happened — no Enter was sent into a bash-input pane.
    assert _cmds(m.call_args_list) == [["tmux", "capture-pane", "-p", "-t", "chela:@1"]]


def test_resend_enter_returns_false_on_tmux_error():
    import subprocess

    def fake_run(cmd, *a, **kw):
        if cmd[:2] == ["tmux", "capture-pane"]:
            return _FakeResult(stdout=_PANE_PROMPT)
        raise subprocess.CalledProcessError(1, cmd, stderr=b"no server")

    with patch.object(messenger.subprocess, "run", side_effect=fake_run), \
            patch.object(messenger.config, "current_session", return_value="chela"):
        assert messenger.resend_enter("@1") is False


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


# --- resolve_window / send_message — the "@32 is offline" bug -----------------
#
# `chela msg @32` reported a live, BUSY agent as offline and dropped the message:
# the recipient was resolved by window NAME only, so a window *id* — the identity
# the wall, /api/agents, `chela peek` and `chela drive` all show — matched nothing.
# Liveness now comes from the one authority (the live tmux window table), which is
# what /api/agents walks too, and it accepts ids as well as names.

_LIVE = {"@32": "cmx-43", "@7": "orchestrator", "@9": "cmx-43"}  # note: colliding names


def _with_windows(windows=None):
    return patch.object(messenger, "get_windows_by_id", return_value=dict(windows or _LIVE))


def test_resolve_window_accepts_window_id_bare_number_and_name():
    with _with_windows():
        assert messenger.resolve_window("@32") == "@32"   # the case that failed live
        assert messenger.resolve_window("32") == "@32"    # bare number = same window
        assert messenger.resolve_window("orchestrator") == "@7"  # names still work
        assert messenger.resolve_window(" @32 ") == "@32"


def test_resolve_window_none_for_dead_window_and_empty():
    with _with_windows():
        assert messenger.resolve_window("@99") is None    # genuinely not live
        assert messenger.resolve_window("ghost-agent") is None
        assert messenger.resolve_window("") is None
        assert messenger.resolve_window(None) is None


def test_send_message_to_busy_agent_by_wid_is_delivered():
    # The exact live failure: a working agent addressed by its window id. Busy is
    # not a failure mode — nothing here may consult claude_pid/session status.
    with _with_windows(), patch.object(messenger, "send_peer", return_value=False), \
            patch.object(messenger, "send_tmux", return_value=True) as send:
        assert messenger.send_message("orchestrator", "@32", "ping") is True
    send.assert_called_once_with("@32", "[orchestrator] ping")


def test_send_message_to_dead_window_is_not_delivered_and_never_sends():
    with _with_windows(), patch.object(messenger, "send_peer") as peer, \
            patch.object(messenger, "send_tmux") as send:
        assert messenger.send_message("orchestrator", "@99", "ping") is False
    peer.assert_not_called()
    send.assert_not_called()


def test_send_message_false_when_both_transports_fail():
    with _with_windows(), patch.object(messenger, "send_peer", return_value=False), \
            patch.object(messenger, "send_tmux", return_value=False):
        assert messenger.send_message("orchestrator", "@32", "ping") is False


def test_send_message_prefers_peer_socket_and_skips_tmux():
    # The core of this ticket: agent-to-agent delivery goes over the peer socket
    # when it's reachable, and send_tmux (the paste-into-a-pane transport) is
    # never touched.
    with _with_windows(), patch.object(messenger, "send_peer", return_value=True) as peer, \
            patch.object(messenger, "send_tmux") as send:
        assert messenger.send_message("orchestrator", "@32", "ping") is True
    peer.assert_called_once_with("@32", "orchestrator", "ping")
    send.assert_not_called()


def test_broadcast_skips_own_window_and_reaches_colliding_names():
    with _with_windows(), patch.object(messenger, "send_peer", return_value=False), \
            patch.object(messenger, "send_tmux", return_value=True) as send:
        results = messenger.broadcast("@7", "standup")
    targets = sorted(c.args[0] for c in send.call_args_list)
    assert targets == ["@32", "@9"]           # @7 (the sender) is skipped — no self-loop
    assert results == {"cmx-43": True, "cmx-43 (@9)": True}  # both, despite one name


def test_broadcast_skips_own_window_when_sender_is_a_name():
    with _with_windows(), patch.object(messenger, "send_peer", return_value=False), \
            patch.object(messenger, "send_tmux", return_value=True) as send:
        messenger.broadcast("orchestrator", "standup")
    assert sorted(c.args[0] for c in send.call_args_list) == ["@32", "@9"]


# --- send_peer / _peer_socket_path — the Claude Code peer-messaging socket ----
#
# Claude Code 2.1.224+ binds a per-session Unix socket at
# $CLAUDE_CODE_MESSAGING_SOCKET and reads newline-delimited JSON off it. These
# tests drive send_peer against a REAL AF_UNIX socket (not a mocked one) so the
# wire format is verified end to end, not just "some bytes were written".

def test_send_peer_false_when_pid_unknown():
    # No process to address — must short-circuit before even looking for a
    # socket file (asserted directly: a stub _peer_socket_path that returns
    # something truthy would otherwise mask a missing `pid is None` guard).
    with patch("chela.agent_manager.claude_pid", return_value=None), \
            patch.object(messenger, "_peer_socket_path") as sock_path:
        assert messenger.send_peer("@1", "orchestrator", "hi") is False
    sock_path.assert_not_called()


def test_send_peer_false_when_no_socket_file():
    with patch("chela.agent_manager.claude_pid", return_value=12345), \
            patch.object(messenger, "_peer_socket_path", return_value=None):
        assert messenger.send_peer("@1", "orchestrator", "hi") is False


def test_send_peer_delivers_expected_ndjson_over_a_real_socket(tmp_path):
    sock_path = tmp_path / "12345.sock"
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    received = {}

    def accept():
        conn, _ = server.accept()
        with conn:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
        received["data"] = buf.decode()

    t = threading.Thread(target=accept, daemon=True)
    t.start()
    try:
        with patch("chela.agent_manager.claude_pid", return_value=12345), \
                patch.object(messenger, "_peer_socket_path", return_value=sock_path):
            assert messenger.send_peer("@1", "orchestrator", "hi") is True
    finally:
        t.join(timeout=2)
        server.close()

    # Exactly one ndjson line, matching the shape Claude Code's uds-messaging
    # listener parses (verified against the installed 2.1.226 binary).
    line = received["data"].rstrip("\n")
    assert "\n" not in line
    assert json.loads(line) == {
        "type": "user",
        "message": {"role": "user", "content": "[orchestrator] hi"},
        "from": "orchestrator",
    }


def test_send_peer_false_when_socket_refuses_connection(tmp_path):
    # A stale socket FILE with nothing listening — connect() must fail fast,
    # not hang, and send_peer must report False so the caller falls back.
    sock_path = tmp_path / "99999.sock"
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    server.close()

    with patch("chela.agent_manager.claude_pid", return_value=99999), \
            patch.object(messenger, "_peer_socket_path", return_value=sock_path):
        assert messenger.send_peer("@1", "orchestrator", "hi") is False


def test_peer_socket_path_prefers_xdg_runtime_dir(tmp_path, monkeypatch):
    sock_dir = tmp_path / "cc-socks"
    sock_dir.mkdir()
    sock_file = sock_dir / "555.sock"
    sock_file.touch()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert messenger._peer_socket_path(555) == sock_file


def test_peer_socket_path_falls_back_to_tmp_dir_when_no_xdg_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    fallback_dir = tmp_path / f"cc-socks-{os.getuid()}"
    fallback_dir.mkdir()
    sock_file = fallback_dir / "555.sock"
    sock_file.touch()
    assert messenger._peer_socket_path(555) == sock_file


def test_peer_socket_path_none_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert messenger._peer_socket_path(555) is None
