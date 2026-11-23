"""``chela drive`` — issue #456's counterweight to herdr's atomic ``agent.prompt``.

A window sitting ``WAITING`` is mid permission/question prompt: prose typed into it is
not read as an answer, it races whatever the prompt is actually waiting on (the same
hazard the decisions inbox's idle gate exists to avoid — see ``inbox.py``'s module
docstring). So ``chela drive`` refuses to send into a target that is already blocked,
by default, BEFORE anything is typed — never guessed at. ``--force`` is the deliberate
override. ``--wait`` makes the send atomic with a `chela wait` (mirrors herdr's
``agent.prompt``'s optional ``wait``).
"""
from __future__ import annotations

from argparse import Namespace
from unittest.mock import patch

import pytest

from chela import inbox, main, messenger, wait

_LIVE = {"@2": "cmx-9", "@1": "orchestrator"}


def _args(wid: str = "@2", *, force: bool = False, wait_until: str | None = None,
          timeout: float | None = None) -> Namespace:
    return Namespace(wid=wid, message="go", force=force, wait=wait_until, timeout=timeout)


_SENT = messenger.PeerSendResult(True, "sent")
_UNREACHABLE = messenger.PeerSendResult(False, None)


def _run(args: Namespace, *, status: str = inbox.IDLE, peer=_SENT,
         self_wid: str | None = "@1"):
    """Drive cmd_drive. Returns (SystemExit code or None, the send_peer mock, the
    send_tmux spy — which no path may call, CMX-482)."""
    with patch.object(main.discovery, "get_windows_by_id", return_value=dict(_LIVE)), \
            patch.object(inbox.agent_manager, "status_by_wid",
                         return_value={"@1": inbox.IDLE, "@2": status}), \
            patch.object(main.orchestrator, "self_wid", return_value=self_wid), \
            patch.object(messenger, "send_peer", return_value=peer) as send, \
            patch.object(messenger, "send_tmux", return_value=True) as paste:
        try:
            main.cmd_drive(args)
        except SystemExit as e:
            return e.code, send, paste
        return None, send, paste


def test_idle_target_is_driven_over_the_peer_socket_never_the_pane(capsys):
    """CMX-482 negative control: drive sends a native peer message ``from`` the driving
    window. The old path typed it with send_tmux and never tried the socket."""
    code, send, paste = _run(_args(), status=inbox.IDLE)
    assert code is None
    send.assert_called_once_with("@2", "@1", "[@1] go")
    paste.assert_not_called()
    assert "Sent to @2" in capsys.readouterr().out


def test_busy_target_is_driven_normally(capsys):
    code, send, paste = _run(_args(), status=inbox.BUSY)
    assert code is None
    send.assert_called_once()
    paste.assert_not_called()


def test_a_driver_with_no_window_signs_as_orchestrator(capsys):
    code, send, _paste = _run(_args(), self_wid=None)
    assert code is None
    send.assert_called_once_with("@2", "orchestrator", "[orchestrator] go")


def test_unreachable_socket_exits_nonzero_naming_the_window_and_never_pastes(capsys):
    code, send, paste = _run(_args(), peer=_UNREACHABLE)
    assert code == 1
    send.assert_called_once()
    paste.assert_not_called()
    err = capsys.readouterr().err
    assert err.startswith("@2: message NOT delivered — unreachable")
    assert "Sent to" not in err


@pytest.mark.parametrize("status", ["held", "denied", "expired"])
def test_an_adverse_receipt_exits_nonzero_and_never_pastes(capsys, status):
    code, _send, paste = _run(_args(), peer=messenger.PeerSendResult(True, status))
    assert code == 1
    paste.assert_not_called()
    assert f"crossSessionInbound gate {status} it" in capsys.readouterr().err


# --- the guard: refuse rather than type into a BLOCKED target -------------------

def test_blocked_target_is_refused_by_default_and_never_typed_into(capsys):
    code, send, paste = _run(_args(), status=inbox.WAITING)

    assert code == 1
    send.assert_not_called()                       # ⛔ nothing sent at all
    paste.assert_not_called()
    err = capsys.readouterr().err
    assert "BLOCKED on a prompt" in err
    assert "--force" in err                         # tells the caller the way out


def test_force_overrides_the_blocked_refusal(capsys):
    code, send, _paste = _run(_args(force=True), status=inbox.WAITING)

    assert code is None
    send.assert_called_once_with("@2", "@1", "[@1] go")
    assert "Sent to @2" in capsys.readouterr().out


# --- --wait: atomic send + wait --------------------------------------------------

def test_wait_flag_blocks_after_a_successful_send(capsys):
    with patch.object(main.discovery, "get_windows_by_id", return_value=dict(_LIVE)), \
            patch.object(inbox.agent_manager, "status_by_wid",
                         return_value={"@1": inbox.IDLE, "@2": inbox.IDLE}), \
            patch.object(main.orchestrator, "self_wid", return_value="@1"), \
            patch.object(messenger, "send_peer", return_value=_SENT), \
            patch.object(wait, "wait_for",
                         return_value={"ok": True, "state": "done",
                                       "detail": "finished", "event": {}}) as waited:
        try:
            main.cmd_drive(_args(wait_until="done", timeout=5))
        except SystemExit as e:
            pytest.fail(f"unexpected exit {e.code}")

    waited.assert_called_once_with("@2", "done", 5, by="@1")
    assert "DONE" in capsys.readouterr().out


def test_wait_flag_exits_nonzero_on_timeout():
    with patch.object(main.discovery, "get_windows_by_id", return_value=dict(_LIVE)), \
            patch.object(inbox.agent_manager, "status_by_wid",
                         return_value={"@1": inbox.IDLE, "@2": inbox.IDLE}), \
            patch.object(main.orchestrator, "self_wid", return_value="@1"), \
            patch.object(messenger, "send_peer", return_value=_SENT), \
            patch.object(wait, "wait_for",
                         return_value={"ok": False, "state": "timeout",
                                       "detail": "timed out", "event": None}):
        with pytest.raises(SystemExit) as exc:
            main.cmd_drive(_args(wait_until="done"))
    assert exc.value.code == 1


def test_dead_window_still_fails_loudly():
    code, send, paste = _run(_args(wid="@99"), status=inbox.IDLE)
    assert code == 1
    send.assert_not_called()
    paste.assert_not_called()


def test_wait_is_not_entered_after_a_failed_send():
    with patch.object(wait, "wait_for") as waited:
        code, _send, _paste = _run(_args(wait_until="done"), peer=_UNREACHABLE)
    assert code == 1
    waited.assert_not_called()
