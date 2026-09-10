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


def _run(args: Namespace, *, status: str = inbox.IDLE, sent: bool = True,
         self_wid: str | None = "@1"):
    with patch.object(main.discovery, "get_windows_by_id", return_value=dict(_LIVE)), \
            patch.object(inbox.agent_manager, "status_by_wid",
                         return_value={"@1": inbox.IDLE, "@2": status}), \
            patch.object(main.orchestrator, "self_wid", return_value=self_wid), \
            patch.object(messenger, "send_tmux", return_value=sent) as send:
        try:
            main.cmd_drive(args)
        except SystemExit as e:
            return e.code, send
        return None, send


def test_idle_target_is_driven_normally(capsys):
    code, send = _run(_args(), status=inbox.IDLE)
    assert code is None
    send.assert_called_once_with("@2", "[@1] go")
    assert "Sent to @2" in capsys.readouterr().out


def test_busy_target_is_driven_normally(capsys):
    code, send = _run(_args(), status=inbox.BUSY)
    assert code is None
    send.assert_called_once()


# --- the guard: refuse rather than type into a BLOCKED target -------------------

def test_blocked_target_is_refused_by_default_and_never_typed_into(capsys):
    code, send = _run(_args(), status=inbox.WAITING)

    assert code == 1
    send.assert_not_called()                       # ⛔ never typed into the pane
    err = capsys.readouterr().err
    assert "BLOCKED on a prompt" in err
    assert "--force" in err                         # tells the caller the way out


def test_force_overrides_the_blocked_refusal(capsys):
    code, send = _run(_args(force=True), status=inbox.WAITING)

    assert code is None
    send.assert_called_once_with("@2", "[@1] go")
    assert "Sent to @2" in capsys.readouterr().out


# --- --wait: atomic send + wait --------------------------------------------------

def test_wait_flag_blocks_after_a_successful_send(capsys):
    with patch.object(main.discovery, "get_windows_by_id", return_value=dict(_LIVE)), \
            patch.object(inbox.agent_manager, "status_by_wid",
                         return_value={"@1": inbox.IDLE, "@2": inbox.IDLE}), \
            patch.object(main.orchestrator, "self_wid", return_value="@1"), \
            patch.object(messenger, "send_tmux", return_value=True), \
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
            patch.object(messenger, "send_tmux", return_value=True), \
            patch.object(wait, "wait_for",
                         return_value={"ok": False, "state": "timeout",
                                       "detail": "timed out", "event": None}):
        with pytest.raises(SystemExit) as exc:
            main.cmd_drive(_args(wait_until="done"))
    assert exc.value.code == 1


def test_dead_window_still_fails_loudly():
    code, send = _run(_args(wid="@99"), status=inbox.IDLE)
    assert code == 1
    send.assert_not_called()
