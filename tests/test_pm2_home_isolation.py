"""Issue #466: the suite must never be able to reach the operator's real pm2 daemon.

``tests/conftest.py`` points ``PM2_HOME`` at a throwaway home for the whole session so
that *every* ``pm2`` subprocess this suite (or a judge's mutation cycle) can possibly
spawn — through ``chela.update._sh``, through the real daemon
``tests/test_graceful_shutdown.py`` launches, through a dashboard background thread that
outlives its test — lands somewhere that isn't ``~/.pm2``. ``tests/conftest.py``'s own
``_no_live_pm2_restart`` fixture is a second, narrower, in-process fence on top of that;
this file tests the structural layer on its own, deliberately without going anywhere near
``chela.update`` or that fixture, so it still holds with that fence bypassed or deleted
entirely — exactly the case issue #466 says the old design could not survive.

Only a *read* (``pm2 jlist``) is used here, never a restart: an assertion in this file
must stay safe to run even under a mutation that has broken the isolation it is checking,
because the judge's self-check re-executes the whole suite against exactly that mutation.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

# ``conftest``, not ``tests.conftest``: tests/ has no __init__.py, so pytest imports the
# conftest as a TOP-LEVEL module (see tests/test_isolation.py for the same note).
from conftest import SANDBOX_PM2_HOME

REAL_PM2_HOME = (Path.home() / ".pm2").resolve()

# The operator's own pm2-managed services (see chela/update.py's `_online_chela_services`)
# — the exact names a leaked restart hits. Never restarted here; only ever compared
# against a `pm2 jlist` READ's output.
_LIVE_SERVICE_NAMES = {
    "chela-daemon", "chela-dashboard", "chela-agent-terminals", "chela-telegram",
}


def test_pm2_home_is_redirected_away_from_the_operators_real_pm2_home():
    """The mechanism itself: whatever a `pm2` subprocess resolves via `$PM2_HOME` must not
    be the operator's real one. `os.environ.get` (not `os.environ["PM2_HOME"]`) mirrors
    how a missing var reads on any process that inherits this environment — pm2 itself
    falls back to `~/.pm2` when the var is entirely absent, so "absent" must fail this
    exactly like "present but real" does."""
    home = os.environ.get("PM2_HOME")
    assert home, "PM2_HOME is unset — every pm2 subprocess this suite spawns defaults to the operator's real ~/.pm2"
    assert Path(home).resolve() != REAL_PM2_HOME, (
        f"PM2_HOME ({home!r}) IS the operator's real pm2 home — nothing isolates a `pm2` "
        "subprocess spawned anywhere in this suite from the live fleet."
    )
    assert Path(home).resolve() == SANDBOX_PM2_HOME.resolve()


def test_a_bare_pm2_jlist_subprocess_never_sees_the_operators_real_fleet():
    """Drive a genuine subprocess — not `chela.update._sh`, so this is provably
    independent of `_no_live_pm2_restart` and of anything `chela.update` does — and prove
    it lands on an empty, throwaway daemon rather than the box's real one. A restart
    reaching this same throwaway daemon would 404 exactly the way this read comes back
    empty; a restart reaching the REAL daemon would instead find these exact names, which
    is what this assertion is actually watching for.
    """
    if shutil.which("pm2") is None:
        pytest.skip("pm2 not installed on this host")
    cp = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=30)
    assert cp.returncode == 0, f"pm2 jlist failed: {cp.stderr}"
    # The FIRST `pm2` call against a fresh PM2_HOME prints a big ASCII banner before the
    # JSON as it spawns its daemon; the array is always pm2's own last line regardless.
    lines = cp.stdout.strip().splitlines()
    procs = json.loads(lines[-1] if lines else "[]")
    names = {p.get("name") for p in procs if isinstance(p, dict)}
    assert not (names & _LIVE_SERVICE_NAMES), (
        f"a bare `pm2 jlist` subprocess saw the operator's REAL chela-* services "
        f"{sorted(names & _LIVE_SERVICE_NAMES)!r} — PM2_HOME is not isolating this "
        "suite from the live daemon."
    )
