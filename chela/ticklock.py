"""CMX-95: a restart of ``chela-daemon`` waits for the CURRENT tick, never for the fleet.

Agents live in the tmux server and judges run detached (``judge.spawn_detached``,
``start_new_session=True``), so a daemon restart interrupts neither. What it CAN break is a
tick caught half-way (claim → worktree → spawn window → DB write, or a verdict/merge write):
PM2 SIGKILLs a process still running after its ``kill_timeout``, and one dispatcher phase
can run longer than that.

So the daemon holds an exclusive ``flock`` on ``$CHELA_DIR/daemon-tick.lock`` for each tick
(:func:`begin_tick` / :func:`end_tick`), and ``update._restart`` takes the same lock before
it ``pm2 restart``s the daemon (:func:`between_ticks`), holding it so no new tick starts in
the gap. The kernel drops a ``flock`` when its holder dies, so it can never wedge.
"""

from __future__ import annotations

import contextlib
import fcntl
import logging
import os
import time
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)

# The longest a restart waits for the current tick (normally seconds) before refusing.
DAEMON_TICK_WAIT_SECONDS = 60
_POLL_SECONDS = 0.1

# True while THIS process is inside a tick: the daemon's own auto-update (called from the
# tick) must not wait on itself.
_held_here = False


def lock_path() -> Path:
    return config.CHELA_DIR / "daemon-tick.lock"


def _open() -> int:
    config.CHELA_DIR.mkdir(parents=True, exist_ok=True)
    return os.open(lock_path(), os.O_RDWR | os.O_CREAT, 0o600)


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def begin_tick(wait) -> int | None:
    """Daemon side: take the tick lock, polling with the shutdown-aware ``wait(seconds) ->
    bool`` while a restart holds it. Returns ``None`` once ``wait`` reports a shutdown (the
    restart is for us — leave without a tick), else the fd for :func:`end_tick`, or ``-1``
    when the lock file cannot be opened (tick anyway; the restart side refuses on the same
    error, so nothing races unprotected)."""
    global _held_here
    try:
        fd = _open()
    except OSError as e:
        log.warning("daemon tick lock unavailable (%s) — ticking without it", e)
        return -1
    while not _try_lock(fd):
        if wait(_POLL_SECONDS):
            os.close(fd)
            return None
    _held_here = True
    return fd


def end_tick(fd: int | None) -> None:
    """Release what :func:`begin_tick` took. Closing the fd drops the ``flock``."""
    global _held_here
    _held_here = False
    if fd is not None and fd >= 0:
        os.close(fd)


@contextlib.contextmanager
def between_ticks(timeout: float = DAEMON_TICK_WAIT_SECONDS):
    """Restart side: yield ``None`` once the daemon is between ticks, holding the lock for
    the body. Yields an error string instead (holding nothing) when the tick outlives
    ``timeout`` or the lock cannot be opened — unknown never reads as "safe to restart"."""
    if _held_here:
        yield None       # restarting from inside our own tick: a known point by construction
        return
    try:
        fd = _open()
    except OSError as e:
        yield f"cannot tell whether chela-daemon is mid-tick ({e}) — not restarting it"
        return
    try:
        deadline = time.monotonic() + timeout
        while not _try_lock(fd):
            if time.monotonic() >= deadline:
                yield (f"chela-daemon is still mid-tick after {int(timeout)}s — not "
                       "restarting it; try again shortly")
                return
            time.sleep(_POLL_SECONDS)
        yield None
    finally:
        os.close(fd)
