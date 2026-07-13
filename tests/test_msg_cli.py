"""``chela msg`` — a message must never be lost quietly.

The live bug: `chela msg @32 "..."` printed "@32 offline — not delivered" and
exited ZERO while /api/agents reported that same window busy and working. Two
things were wrong and both are covered here: the recipient was resolved by window
NAME only (so a window *id* never matched), and a non-delivery was a chatty line
on stdout with a success exit code — a silently dropped message.

The contract now: live window (busy or idle) → delivered; genuinely dead window →
loud on stderr, exit 1; own window → refused (messaging yourself is a loop).
"""
from __future__ import annotations

from argparse import Namespace
from unittest.mock import patch

import pytest

from chela import main, messenger

_LIVE = {"@32": "cmx-43", "@7": "orchestrator"}


def _args(agent: str) -> Namespace:
    return Namespace(agent=agent, message="ping", from_agent="orchestrator",
                     priority="normal")


def _run(agent: str, *, self_wid: str | None = "@7", sent: bool = True):
    """Drive cmd_msg with tmux stubbed. Returns (SystemExit code or None, send calls)."""
    with patch.object(messenger, "get_windows_by_id", return_value=dict(_LIVE)), \
            patch.object(main.discovery, "get_windows_by_id", return_value=dict(_LIVE)), \
            patch.object(main.orchestrator, "self_wid", return_value=self_wid), \
            patch.object(messenger, "send_tmux", return_value=sent) as send:
        try:
            main.cmd_msg(_args(agent))
        except SystemExit as e:
            return e.code, send
        return None, send


def test_busy_agent_addressed_by_wid_is_messageable(capsys):
    # THE regression: @32 is live and working; the message must land, exit 0.
    code, send = _run("@32")
    assert code is None
    send.assert_called_once_with("@32", "[orchestrator] ping")
    assert "Sent to @32" in capsys.readouterr().out


def test_dead_window_fails_loudly_with_the_real_reason(capsys):
    code, send = _run("@99")
    assert code == 1                      # non-zero — the caller cannot miss it
    send.assert_not_called()
    err = capsys.readouterr().err
    assert "NOT delivered" in err
    assert "not a live window" in err     # the real reason, not a vague "offline"
    assert "@32 cmx-43" in err            # and what IS live, so the fix is obvious


def test_self_notify_is_still_refused(capsys):
    # An orchestrator messaging its own window feeds the message back to itself.
    code, send = _run("@7")
    assert code == 1
    send.assert_not_called()
    assert "refusing to message myself" in capsys.readouterr().err


def test_tmux_send_failure_is_reported_apart_from_a_dead_window(capsys):
    code, send = _run("@32", sent=False)
    assert code == 1
    send.assert_called_once()
    err = capsys.readouterr().err
    assert "live but the tmux send FAILED" in err


@pytest.mark.parametrize("token", ["@32", "32", "cmx-43"])
def test_every_identity_the_wall_shows_you_resolves(token):
    code, send = _run(token)
    assert code is None
    assert send.call_args.args[0] == "@32"
