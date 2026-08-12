"""CMX-262: `_git`'s opt-in `raise_on_timeout` — a timed-out git call must be tellable
apart from every other failure (a missing binary, a non-zero exit) so a caller that needs
to say "this was a timeout" (chela.update's network calls) can, without changing the
default None-on-any-failure contract the ~20 other `_git` call sites in dispatcher.py rely
on.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher


def test_timeout_returns_none_by_default():
    """`raise_on_timeout` defaults off — an existing caller that never asked for the
    distinction keeps getting a plain None on a hang, same as before this rework."""
    with patch.object(
        subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=1),
    ):
        assert dispatcher._git(Path("/tmp"), "fetch", timeout=1) is None


def test_timeout_raises_when_opted_in():
    with patch.object(
        subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd=["git"], timeout=1),
    ):
        with pytest.raises(dispatcher.GitTimeout) as exc_info:
            dispatcher._git(Path("/tmp"), "fetch", timeout=1, raise_on_timeout=True)
    assert "timed out after 1s" in str(exc_info.value)


def test_missing_binary_still_returns_none_even_when_opted_in():
    """Negative control: a non-timeout failure must never claim to be one — `raise_on_timeout`
    only fires on an actual `subprocess.TimeoutExpired`."""
    with patch.object(subprocess, "run", side_effect=FileNotFoundError("no git")):
        assert dispatcher._git(Path("/tmp"), "fetch", timeout=1, raise_on_timeout=True) is None
