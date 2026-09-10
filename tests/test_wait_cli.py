"""``chela wait`` CLI — exit codes and output shape (the wait logic itself is exercised
against the real event log in ``tests/test_wait.py``)."""
from __future__ import annotations

from argparse import Namespace
from unittest.mock import patch

import pytest

from chela import main, wait


def _args(target: str = "@2", until: str = "done", timeout: float | None = None,
          as_json: bool = False) -> Namespace:
    return Namespace(target=target, until=until, timeout=timeout, json=as_json)


def test_done_prints_and_exits_zero(capsys):
    with patch.object(wait, "wait_for",
                       return_value={"ok": True, "state": "done", "detail": "finished it",
                                     "event": {"type": "finished"}}) as waited, \
            patch.object(main.orchestrator, "self_wid", return_value="@1"):
        main.cmd_wait(_args())
    waited.assert_called_once_with("@2", "done", None, by="@1")
    assert "DONE — finished it" in capsys.readouterr().out


def test_timeout_exits_nonzero_on_stderr(capsys):
    with patch.object(wait, "wait_for",
                       return_value={"ok": False, "state": "timeout",
                                     "detail": "timed out waiting for done", "event": None}), \
            patch.object(main.orchestrator, "self_wid", return_value="@1"):
        with pytest.raises(SystemExit) as exc:
            main.cmd_wait(_args(timeout=1))
    assert exc.value.code == 1
    assert "TIMED OUT" in capsys.readouterr().err


def test_unknown_outcome_exits_nonzero_and_is_distinct_from_timeout(capsys):
    with patch.object(wait, "wait_for",
                       return_value={"ok": False, "state": "unknown",
                                     "detail": "tmux restarted", "event": {}}), \
            patch.object(main.orchestrator, "self_wid", return_value="@1"):
        with pytest.raises(SystemExit) as exc:
            main.cmd_wait(_args())
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "OUTCOME UNKNOWN" in err
    assert "TIMED OUT" not in err


def test_json_output(capsys):
    payload = {"ok": True, "state": "blocked", "detail": "x", "event": {"type": "blocked"}}
    with patch.object(wait, "wait_for", return_value=payload), \
            patch.object(main.orchestrator, "self_wid", return_value="@1"):
        main.cmd_wait(_args(until="blocked", as_json=True))
    out = capsys.readouterr().out
    assert '"state": "blocked"' in out
