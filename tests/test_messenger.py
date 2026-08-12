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
import re
import socket as socket_module
import threading
from unittest import mock
from unittest.mock import patch

from chela import config, messenger


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


_UNREACHABLE = messenger.PeerSendResult(False, None)
_SENT = messenger.PeerSendResult(True, "sent")
_HELD = messenger.PeerSendResult(True, "held")


def test_send_message_to_busy_agent_by_wid_is_delivered():
    # The exact live failure: a working agent addressed by its window id. Busy is
    # not a failure mode — nothing here may consult claude_pid/session status.
    with _with_windows(), patch.object(messenger, "send_peer", return_value=_UNREACHABLE), \
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
    with _with_windows(), patch.object(messenger, "send_peer", return_value=_UNREACHABLE), \
            patch.object(messenger, "send_tmux", return_value=False):
        assert messenger.send_message("orchestrator", "@32", "ping") is False


def test_send_message_prefers_peer_socket_and_skips_tmux():
    # The core of CMX-222: agent-to-agent delivery goes over the peer socket when
    # it's reachable, and send_tmux (the paste-into-a-pane transport) is never
    # touched — as long as no adverse receipt comes back.
    with _with_windows(), patch.object(messenger, "send_peer", return_value=_SENT) as peer, \
            patch.object(messenger, "send_tmux") as send:
        assert messenger.send_message("orchestrator", "@32", "ping") is True
    peer.assert_called_once_with("@32", "orchestrator", "[orchestrator] ping")
    send.assert_not_called()


def test_send_message_false_when_peer_socket_handoff_gets_an_adverse_receipt():
    # THE fail-open bug this ticket exists to close (CMX-223): a socket accepting
    # the bytes is a HANDOFF, not a delivery. A `held` receipt means the receiver's
    # own gate dropped the message — send_message must NOT report success, and
    # must NOT paper over the drop by falling back to tmux (that would route
    # around a safety setting the receiver chose, not recover a lost transport).
    with _with_windows(), patch.object(messenger, "send_peer", return_value=_HELD), \
            patch.object(messenger, "send_tmux") as send:
        assert messenger.send_message("orchestrator", "@32", "ping") is False
    send.assert_not_called()


def test_broadcast_skips_own_window_and_reaches_colliding_names():
    with _with_windows(), patch.object(messenger, "send_peer", return_value=_UNREACHABLE), \
            patch.object(messenger, "send_tmux", return_value=True) as send:
        results = messenger.broadcast("@7", "standup")
    targets = sorted(c.args[0] for c in send.call_args_list)
    assert targets == ["@32", "@9"]           # @7 (the sender) is skipped — no self-loop
    assert results == {"cmx-43": True, "cmx-43 (@9)": True}  # both, despite one name


def test_broadcast_skips_own_window_when_sender_is_a_name():
    with _with_windows(), patch.object(messenger, "send_peer", return_value=_UNREACHABLE), \
            patch.object(messenger, "send_tmux", return_value=True) as send:
        messenger.broadcast("orchestrator", "standup")
    assert sorted(c.args[0] for c in send.call_args_list) == ["@32", "@9"]


# --- send_peer / _peer_socket_path — the Claude Code peer-messaging socket ----
#
# Claude Code 2.1.224+ binds a per-session Unix socket at
# $CLAUDE_CODE_MESSAGING_SOCKET and reads newline-delimited JSON off it. These
# tests drive send_peer against a REAL AF_UNIX socket (not a mocked one) so the
# wire format is verified end to end, not just "some bytes were written".

_UUID4_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$", re.I)


def _fake_peer_target(sock_path, *, reply_status=None):
    """Bind ``sock_path``, accept ONE connection, capture the ndjson line it sends.

    When ``reply_status`` is given, replies with a
    ``{"type":"control","action":"peer_message_status", ...}`` receipt to the
    ``from`` address in the received payload — a REAL round trip over a second
    AF_UNIX socket, the same shape Claude Code's own receipts use. Returns
    ``(received_dict, thread)``; caller joins the thread and reads
    ``received["data"]`` after :func:`messenger.send_peer` returns.
    """
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    received = {}

    def run():
        conn, _ = server.accept()
        with conn:
            buf = b""
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                buf += chunk
        received["data"] = buf.decode()
        server.close()
        if reply_status is not None:
            payload = json.loads(received["data"].rstrip("\n"))
            reply_addr = payload["from"][len("uds:"):]
            receipt = {"type": "control", "action": "peer_message_status",
                       "status": reply_status, "orig_msg_id": payload["msg_id"]}
            with socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM) as r:
                r.connect(reply_addr)
                r.sendall((json.dumps(receipt) + "\n").encode())

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return received, t


def test_send_peer_false_when_pid_unknown():
    # No process to address — must short-circuit before even looking for a
    # socket file (asserted directly: a stub _peer_socket_path that returns
    # something truthy would otherwise mask a missing `pid is None` guard).
    with patch("chela.agent_manager.claude_pid", return_value=None), \
            patch.object(messenger, "_peer_socket_path") as sock_path:
        assert messenger.send_peer("@1", "orchestrator", "hi") == messenger.PeerSendResult(False, None)
    sock_path.assert_not_called()


def test_send_peer_false_when_no_socket_file():
    with patch("chela.agent_manager.claude_pid", return_value=12345), \
            patch.object(messenger, "_peer_socket_path", return_value=None):
        assert messenger.send_peer("@1", "orchestrator", "hi") == messenger.PeerSendResult(False, None)


def test_send_peer_delivers_expected_ndjson_over_a_real_socket(tmp_path):
    sock_path = tmp_path / "12345.sock"
    received, t = _fake_peer_target(sock_path)  # accepts, never replies -> "sent"
    try:
        with patch("chela.agent_manager.claude_pid", return_value=12345), \
                patch.object(messenger, "_peer_socket_path", return_value=sock_path):
            result = messenger.send_peer("@1", "orchestrator", "hi")
    finally:
        t.join(timeout=2)

    # No adverse receipt inside the wait window == "sent", the accept path's own
    # signature (an accepted message produces no receipt at all — see the
    # _await_receipt docstring).
    assert result == messenger.PeerSendResult(True, "sent")

    # Exactly one ndjson line, matching the shape Claude Code's uds-messaging
    # listener parses (verified against the installed 2.1.226 binary). content is
    # sent EXACTLY as given — send_peer no longer adds its own "[from] " wrap
    # (send_message does that itself now, so a caller with an already-formatted,
    # already-attributed prompt — rooms' build_prompt — isn't double-wrapped).
    line = received["data"].rstrip("\n")
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["type"] == "user"
    assert payload["message"] == {"role": "user", "content": "hi"}
    # msg_id MUST be a real uuid4 — a non-UUID id comes back with orig_msg_id
    # ABSENT (measured), breaking correlation silently.
    assert _UUID4_RE.match(payload["msg_id"])
    # `from` names OUR OWN listening socket, in the SAME DIRECTORY as the
    # target's socket — the receipt is skipped silently otherwise (measured).
    assert payload["from"].startswith(f"uds:{sock_path.parent}/")


def test_send_peer_false_when_socket_refuses_connection(tmp_path):
    # A stale socket FILE with nothing listening — connect() must fail fast,
    # not hang, and send_peer must report unreachable so the caller falls back.
    sock_path = tmp_path / "99999.sock"
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    server.close()

    with patch("chela.agent_manager.claude_pid", return_value=99999), \
            patch.object(messenger, "_peer_socket_path", return_value=sock_path):
        assert messenger.send_peer("@1", "orchestrator", "hi") == messenger.PeerSendResult(False, None)


def test_send_peer_reports_held_receipt_over_a_real_round_trip(tmp_path):
    # The receipt path end to end: our reply socket really is reachable from a
    # second process (a real connect, not a mock), and the status it sends back
    # is what send_peer reports — NOT collapsed to "sent" or to a bare True/False.
    sock_path = tmp_path / "1.sock"
    received, t = _fake_peer_target(sock_path, reply_status="held")
    try:
        with patch("chela.agent_manager.claude_pid", return_value=1), \
                patch.object(messenger, "_peer_socket_path", return_value=sock_path):
            result = messenger.send_peer("@1", "orchestrator", "hi")
    finally:
        t.join(timeout=2)
    assert result == messenger.PeerSendResult(True, "held")


def test_send_peer_ignores_a_receipt_for_a_different_msg_id():
    # Guard for the uuid4 correlation rule: a receipt whose orig_msg_id does not
    # match ours must NOT be reported as our status — corrupting the correlation
    # check (e.g. dropping the comparison) would make this return "denied"
    # instead of "sent" for a receipt that was never ours to begin with.
    receipt = {"type": "control", "action": "peer_message_status",
               "status": "denied", "orig_msg_id": "not-our-msg-id"}
    conn = mock.Mock()
    conn.recv.return_value = (json.dumps(receipt) + "\n").encode()
    server = mock.Mock()
    server.accept.return_value = (conn, None)
    assert messenger._await_receipt(server, "our-real-msg-id") == "sent"


def test_send_peer_content_is_not_escaped_or_routed_through_tmux(tmp_path):
    # BOUNDARIES: slash-command injection stays tmux (a peer message is routed
    # skipSlashCommands:true by Claude Code itself, per CMX-222's spike). Prove
    # send_peer never takes the tmux Escape-then-slash detour and never mutates a
    # leading "/" — a regression that routed "/"-prefixed content through tmux
    # would trip the subprocess.run patch below.
    sock_path = tmp_path / "1.sock"
    received, t = _fake_peer_target(sock_path)
    try:
        with patch("chela.agent_manager.claude_pid", return_value=1), \
                patch.object(messenger, "_peer_socket_path", return_value=sock_path), \
                patch("subprocess.run") as tmux_run:
            result = messenger.send_peer("@1", "orchestrator", "/status")
    finally:
        t.join(timeout=2)
    assert result == messenger.PeerSendResult(True, "sent")
    tmux_run.assert_not_called()
    assert json.loads(received["data"])["message"]["content"] == "/status"


def test_deterministic_peer_socket_path_is_keyed_on_window_id(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    assert messenger.deterministic_peer_socket_path("@42") == tmp_path / "socks" / "42.sock"


def test_messaging_socket_launch_arg_none_when_over_sun_path_ceiling(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / ("x" * 90))
    assert messenger.messaging_socket_launch_arg("@1") is None


def test_messaging_socket_launch_arg_carries_the_deterministic_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    arg = messenger.messaging_socket_launch_arg("@42")
    assert arg == f"--messaging-socket-path {tmp_path / 'socks' / '42.sock'}"


def test_peer_socket_path_prefers_the_deterministic_path_when_it_exists(tmp_path, monkeypatch):
    # CMX-223: a window launched with --messaging-socket-path is found there
    # FIRST, without even trying the legacy pid-derived guess.
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    det_dir = tmp_path / "socks"
    det_dir.mkdir()
    det_file = det_dir / "1.sock"
    det_file.touch()
    assert messenger._peer_socket_path("@1", 555) == det_file


def test_peer_socket_path_prefers_xdg_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-home")  # no socks/ dir here
    sock_dir = tmp_path / "cc-socks"
    sock_dir.mkdir()
    sock_file = sock_dir / "555.sock"
    sock_file.touch()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert messenger._peer_socket_path("@1", 555) == sock_file


def test_peer_socket_path_falls_back_to_tmp_dir_when_no_xdg_runtime_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-home")
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    fallback_dir = tmp_path / f"cc-socks-{os.getuid()}"
    fallback_dir.mkdir()
    sock_file = fallback_dir / "555.sock"
    sock_file.touch()
    assert messenger._peer_socket_path("@1", 555) == sock_file


def test_peer_socket_path_none_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-home")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert messenger._peer_socket_path("@1", 555) is None


# --- CMX-255: peer_socket_path_for_pid / send_peer_to_pid — the windowless address ----
# No window id anywhere in these: a windowless session was never launched with
# --messaging-socket-path, so only the legacy pid-derived guess ever applies to it.

def test_peer_socket_path_for_pid_finds_the_xdg_runtime_dir_guess(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    sock_dir = tmp_path / "cc-socks"
    sock_dir.mkdir()
    sock_file = sock_dir / "777.sock"
    sock_file.touch()
    assert messenger.peer_socket_path_for_pid(777) == sock_file


def test_peer_socket_path_for_pid_never_checks_the_deterministic_window_keyed_path(
        tmp_path, monkeypatch):
    # A pid that happens to numerically match a window's deterministic socket file must
    # NOT be matched by it — that path is chela's own window-keyed launch flag, and a
    # windowless session was never launched with it.
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    det_dir = tmp_path / "socks"
    det_dir.mkdir()
    (det_dir / "777.sock").touch()
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path / "elsewhere"))
    assert messenger.peer_socket_path_for_pid(777) is None


def test_peer_socket_path_for_pid_none_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert messenger.peer_socket_path_for_pid(777) is None


def test_send_peer_to_pid_false_when_no_socket_file():
    with patch.object(messenger, "peer_socket_path_for_pid", return_value=None):
        assert (messenger.send_peer_to_pid(777, "chela-inbox", "hi")
                == messenger.PeerSendResult(False, None))


def test_send_peer_to_pid_delivers_expected_ndjson_over_a_real_socket(tmp_path):
    sock_path = tmp_path / "777.sock"
    received, t = _fake_peer_target(sock_path)
    try:
        with patch.object(messenger, "peer_socket_path_for_pid", return_value=sock_path):
            result = messenger.send_peer_to_pid(777, "chela-inbox", "hi")
    finally:
        t.join(timeout=2)

    assert result == messenger.PeerSendResult(True, "sent")
    payload = json.loads(received["data"].rstrip("\n"))
    assert payload["message"] == {"role": "user", "content": "hi"}
    assert _UUID4_RE.match(payload["msg_id"])


def test_send_peer_to_pid_reports_an_adverse_receipt(tmp_path):
    sock_path = tmp_path / "778.sock"
    received, t = _fake_peer_target(sock_path, reply_status="denied")
    try:
        with patch.object(messenger, "peer_socket_path_for_pid", return_value=sock_path):
            result = messenger.send_peer_to_pid(778, "chela-inbox", "hi")
    finally:
        t.join(timeout=2)
    assert result == messenger.PeerSendResult(True, "denied")


def test_send_peer_to_pid_false_when_socket_refuses_connection(tmp_path):
    sock_path = tmp_path / "779.sock"
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    server.close()  # bound, then abandoned: a file with nobody listening
    with patch.object(messenger, "peer_socket_path_for_pid", return_value=sock_path):
        assert (messenger.send_peer_to_pid(779, "chela-inbox", "hi")
                == messenger.PeerSendResult(False, None))


# --- peer_socket_reachable / peer_transport_kind — CMX-224's rework ------------
#
# `_peer_socket_path` is existence-only: a stale socket FILE surviving its process
# (the process was SIGKILLed, so its own unlink never ran) still passes `.exists()`
# forever. These prove the doctor-facing seam actually tries the connection instead
# of trusting the file's mere presence.

def test_peer_socket_reachable_false_when_file_does_not_exist(tmp_path):
    assert messenger.peer_socket_reachable(tmp_path / "nope.sock") is False


def test_peer_socket_reachable_true_for_a_real_listener(tmp_path):
    sock_path = tmp_path / "1.sock"
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    try:
        assert messenger.peer_socket_reachable(sock_path, timeout=0.25) is True
    finally:
        server.close()


def test_peer_socket_reachable_false_for_a_stale_file_nothing_is_listening_on(tmp_path):
    # Bind, listen, close — never unlink. The special file is still there; nothing
    # is behind it. This is the exact shape a SIGKILLed agent leaves.
    sock_path = tmp_path / "1.sock"
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    server.close()
    assert messenger.peer_socket_reachable(sock_path, timeout=0.25) is False


def test_peer_socket_reachable_sends_zero_bytes(tmp_path):
    # A doctor probe must never be able to hand the target a turn — connect and
    # close immediately, with nothing ever read off the wire on the far end.
    sock_path = tmp_path / "1.sock"
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    received = {}

    def run():
        conn, _ = server.accept()
        with conn:
            conn.settimeout(1)
            try:
                received["data"] = conn.recv(4096)
            except OSError:
                received["data"] = b""

    t = threading.Thread(target=run, daemon=True)
    t.start()
    try:
        assert messenger.peer_socket_reachable(sock_path, timeout=0.25) is True
    finally:
        t.join(timeout=2)
        server.close()
    assert received["data"] == b""


def test_peer_transport_kind_deterministic_when_that_path_is_reachable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    det = messenger.deterministic_peer_socket_path("@1")
    det.parent.mkdir(parents=True)
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(det))
    server.listen(1)
    try:
        assert messenger.peer_transport_kind("@1", 555, timeout=0.25) == "deterministic"
    finally:
        server.close()


def test_peer_transport_kind_default_when_only_the_legacy_path_is_reachable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-home")  # no socks/ dir
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    sock_dir = tmp_path / "cc-socks"
    sock_dir.mkdir()
    sock_path = sock_dir / "555.sock"
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(sock_path))
    server.listen(1)
    try:
        assert messenger.peer_transport_kind("@1", 555, timeout=0.25) == "default"
    finally:
        server.close()


def test_peer_transport_kind_tmux_fallback_when_nothing_exists(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path / "chela-home")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    assert messenger.peer_transport_kind("@1", 555, timeout=0.25) == "tmux fallback"


def test_peer_transport_kind_tmux_fallback_for_a_stale_deterministic_file(tmp_path, monkeypatch):
    # A stale FILE at the chela-owned path must not be reported "deterministic" —
    # the whole point of the reachability probe is that existence isn't enough.
    monkeypatch.setattr(config, "CHELA_DIR", tmp_path)
    det = messenger.deterministic_peer_socket_path("@1")
    det.parent.mkdir(parents=True)
    server = socket_module.socket(socket_module.AF_UNIX, socket_module.SOCK_STREAM)
    server.bind(str(det))
    server.listen(1)
    server.close()
    assert messenger.peer_transport_kind("@1", 555, timeout=0.25) == "tmux fallback"
