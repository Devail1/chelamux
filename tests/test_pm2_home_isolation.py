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
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

# ``conftest``, not ``tests.conftest``: tests/ has no __init__.py, so pytest imports the
# conftest as a TOP-LEVEL module (see tests/test_isolation.py for the same note).
from conftest import (
    PM2_SANDBOX_OWNER_FILE,
    PM2_SANDBOX_PREFIX,
    SANDBOX_PM2_HOME,
    _reap_pm2_sandbox,
    _reap_stale_pm2_sandboxes,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

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


def test_the_sandbox_pm2_home_is_removed_when_the_process_that_created_it_exits():
    """The two tests above only prove `PM2_HOME` points somewhere throwaway *while the
    suite runs* — neither touches teardown. `conftest.py` claims that throwaway home "has
    its own atexit teardown that kills that home's daemon... so nothing survives the run"
    (see the comment above `SANDBOX_PM2_HOME`), but nothing in this file exercised that:
    `atexit.register(_kill_sandbox_pm2_daemon)` is a statement with no return value any
    other test observes, so swapping its argument for a no-op (`atexit.register(lambda:
    None)`) — the exact corruption a judge round found surviving — changed nothing any
    other assertion here could see, because nothing else in this suite runs at process
    exit.

    `atexit` exposes no public "what's registered" query, and calling
    `_kill_sandbox_pm2_daemon()` (or `atexit._run_exitfuncs()`) in-process would tear down
    the ONE `SANDBOX_PM2_HOME` every other test in this session shares. So this test
    doesn't inspect the registration — it reproduces the real event: a fresh interpreter
    imports `conftest.py` (the same module-level code pytest itself runs, executed no
    differently here) and then exits normally, letting CPython's actual atexit machinery
    fire. If `_kill_sandbox_pm2_daemon` is truly the registered handler, that throwaway
    home is gone by the time this subprocess returns; if the registration were a no-op (or
    anything else), the directory it created would still be sitting on disk.
    """
    script = (
        "import sys; sys.path.insert(0, 'tests'); import conftest; "
        "print(conftest.SANDBOX_PM2_HOME)"
    )
    cp = subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    assert cp.returncode == 0, f"subprocess failed: {cp.stderr}"
    lines = cp.stdout.strip().splitlines()
    sandbox_dir = Path(lines[-1])
    assert not sandbox_dir.exists(), (
        f"{sandbox_dir} still exists after the interpreter that created it exited — "
        "_kill_sandbox_pm2_daemon did not run at atexit, so a throwaway pm2 home (and "
        "any daemon it spawned) survives every process that ever imports conftest.py."
    )


# ── CMX-115: a KILLED run must not leak its sandbox (or its God Daemon) ─────────────────


def _dead_pid() -> int:
    """A PID that just existed and is gone (its parent — this process — reaped it)."""
    cp = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid())"],
        capture_output=True, text=True, timeout=30, check=True,
    )
    return int(cp.stdout.strip())


def _sandbox(root: Path, name: str, owner: int | None) -> Path:
    home = root / f"{PM2_SANDBOX_PREFIX}{name}"
    home.mkdir()
    if owner is not None:
        (home / PM2_SANDBOX_OWNER_FILE).write_text(str(owner))
    (home / "pm2.pid").write_text("0")   # "a daemon may be running here"
    return home


def test_this_sessions_sandbox_is_marked_with_its_own_pid():
    """The marker is what the NEXT session's reaper keys on: without it, a run killed
    with SIGKILL leaves a sandbox nothing will ever provably own, and so never reap."""
    marker = SANDBOX_PM2_HOME / PM2_SANDBOX_OWNER_FILE
    assert marker.read_text().strip() == str(os.getpid())


def test_a_sandbox_whose_owner_is_dead_is_reaped(tmp_path):
    stale = _sandbox(tmp_path, "stale", _dead_pid())
    killed = []
    reaped = _reap_stale_pm2_sandboxes(tmp_path, kill=killed.append)
    assert reaped == [stale]
    assert killed == [stale], "the stale home's daemon was never `pm2 kill`ed"
    assert not stale.exists()


def test_a_sandbox_whose_owner_is_alive_is_left_alone(tmp_path):
    """A concurrent pytest session (another judge, an xdist worker) owns this one."""
    live = _sandbox(tmp_path, "live", os.getpid())
    killed = []
    assert _reap_stale_pm2_sandboxes(tmp_path, kill=killed.append) == []
    assert killed == []
    assert (live / PM2_SANDBOX_OWNER_FILE).is_file()


def test_an_unmarked_sandbox_is_never_scanned_as_reapable(tmp_path):
    legacy = _sandbox(tmp_path, "legacy", owner=None)
    killed = []
    assert _reap_stale_pm2_sandboxes(tmp_path, kill=killed.append) == []
    assert killed == [] and legacy.exists()


def test_the_reaper_refuses_any_path_without_the_marker(tmp_path):
    """The live ``~/.pm2`` has neither the prefix nor the marker; a prefixed dir with no
    marker is not provably a test sandbox either. Both refused BEFORE anything is killed."""
    killed = []
    with pytest.raises(ValueError):
        _reap_pm2_sandbox(REAL_PM2_HOME, kill=killed.append)
    unmarked = _sandbox(tmp_path, "unmarked", owner=None)
    with pytest.raises(ValueError):
        _reap_pm2_sandbox(unmarked, kill=killed.append)
    lookalike = tmp_path / ".pm2"
    lookalike.mkdir()
    (lookalike / PM2_SANDBOX_OWNER_FILE).write_text(str(_dead_pid()))
    with pytest.raises(ValueError):
        _reap_pm2_sandbox(lookalike, kill=killed.append)
    assert killed == []
    assert unmarked.exists() and lookalike.exists()


def _pm2_daemon_pid(home: Path) -> int:
    subprocess.run(
        ["pm2", "ping"], env={**os.environ, "PM2_HOME": str(home)},
        capture_output=True, timeout=30, check=True,
    )
    return int((home / "pm2.pid").read_text().strip())


def _gone_within(pid: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        time.sleep(0.05)
    return False


def test_session_start_reaps_a_dead_owners_sandbox_and_kills_its_daemon():
    """The real event, end to end: a fresh interpreter imports ``conftest.py`` (session
    start) with ``TMPDIR`` pointed here. A sandbox left by a dead owner — with a REAL pm2
    God Daemon still running in it, the leak itself — is gone afterwards, daemon and dir;
    a live owner's sandbox and an unmarked one are untouched.

    A SHORT root, not ``tmp_path``: pm2 puts its unix sockets in its home, and an xdist
    worker's ``tmp_path`` pushes ``…/rpc.sock`` past the 108-byte ``sun_path`` limit."""
    if shutil.which("pm2") is None:
        pytest.skip("pm2 not installed on this host")
    tmp_path = Path(tempfile.mkdtemp(prefix="cmx115-"))
    stale = tmp_path / f"{PM2_SANDBOX_PREFIX}stale"
    stale.mkdir()
    (stale / PM2_SANDBOX_OWNER_FILE).write_text(str(_dead_pid()))
    live = _sandbox(tmp_path, "live", os.getpid())
    legacy = _sandbox(tmp_path, "legacy", owner=None)
    daemon = _pm2_daemon_pid(stale)
    try:
        cp = subprocess.run(
            [sys.executable, "-c", "import sys; sys.path.insert(0, 'tests'); import conftest"],
            cwd=REPO_ROOT, env={**os.environ, "TMPDIR": str(tmp_path)},
            capture_output=True, text=True, timeout=60,
        )
        assert cp.returncode == 0, f"subprocess failed: {cp.stderr}"
        assert not stale.exists(), "a dead owner's sandbox survived the next session start"
        assert _gone_within(daemon, 10), f"the stale sandbox's pm2 daemon {daemon} still runs"
        assert live.exists() and legacy.exists()
    finally:
        if stale.exists():
            subprocess.run(["pm2", "kill"], env={**os.environ, "PM2_HOME": str(stale)},
                           capture_output=True, timeout=30)
        shutil.rmtree(tmp_path, ignore_errors=True)


_SLEEPER = (
    "import sys, time; sys.path.insert(0, 'tests'); import conftest; "
    "print(conftest.SANDBOX_PM2_HOME, flush=True); time.sleep(60)"
)


def test_a_sigtermed_run_still_tears_its_sandbox_down(tmp_path):
    """`atexit` does not run on SIGTERM's default action — a `timeout`/judge kill skipped
    the teardown entirely. The handler must run it, and the process must still die OF
    SIGTERM (the caller's exit-status semantics are unchanged)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", _SLEEPER], cwd=REPO_ROOT,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        sandbox = Path(proc.stdout.readline().strip())
        assert sandbox.name.startswith(PM2_SANDBOX_PREFIX), proc.stderr.read()
        assert sandbox.exists()
        proc.send_signal(signal.SIGTERM)
        assert proc.wait(timeout=30) == -signal.SIGTERM
        assert not sandbox.exists(), "a SIGTERMed run left its pm2 sandbox behind"
    finally:
        proc.kill()
        proc.wait()


def test_a_forked_child_dying_does_not_tear_down_its_parents_sandbox(tmp_path):
    """The SIGTERM handler and the atexit entry are inherited across ``fork`` —
    ``multiprocessing`` terminates pool workers with exactly SIGTERM. Only the process
    that created the sandbox may remove it."""
    script = (
        "import os, signal, sys; sys.path.insert(0, 'tests'); import conftest; "
        "pid = os.fork()\n"
        "if pid == 0:\n    os.kill(os.getpid(), signal.SIGTERM); os._exit(0)\n"
        "os.waitpid(pid, 0); print(conftest.SANDBOX_PM2_HOME.exists())"
    )
    cp = subprocess.run(
        [sys.executable, "-c", script], cwd=REPO_ROOT,
        env={**os.environ, "TMPDIR": str(tmp_path)},
        capture_output=True, text=True, timeout=60,
    )
    assert cp.returncode == 0, cp.stderr
    assert cp.stdout.strip().splitlines()[-1] == "True", (
        "a forked child's SIGTERM removed its parent's live pm2 sandbox"
    )
