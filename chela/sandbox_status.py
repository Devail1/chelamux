"""🚦🧱 Working/idle for SANDBOXED windows, from their credential proxy (CMX-436).

chela's status for a window is Claude's own (``claude agents --json``, keyed by the pane's
claude pid — :func:`chela.agent_manager.session_status_map`). A sandboxed session's Claude
runs in a container: it is not in that list, its hooks cannot reach the host and its
transcript is in the container's tmpfs, so its pill read "unknown" forever. Its proxy
sidecar sees every model request, though, and records them in the session's activity
file (:class:`chela.share_proxy.Activity`) — a signal chela already owns.

:func:`resolve` is the one place ``peek`` and ``/api/agents`` apply it. It only ever fills
a status Claude did NOT report, and only for a window that verifies LIVE as a sandboxed
session — an ordinary window's status is returned untouched, with no source.
The proxy cannot see a permission prompt, so ``waiting`` never comes from here.

Verification costs a tmux call and a ``/proc`` read (plus two ``docker inspect``s for a
real sandboxed pane), and the Wall polls every few seconds, so a verdict is cached for
:data:`CHECK_TTL_S`, the same as the Telegram relay's (:mod:`chela.telegram.sandboxrelay`).
"""
from __future__ import annotations

import threading
import time
from typing import Callable

from chela import share_sandbox

# What a proxy-derived status is labelled as, on the wire (`status_source`).
PROXY_SOURCE = "sandbox-proxy"
CHECK_TTL_S = 15.0

_cache: dict[str, tuple[float, str | None]] = {}
_lock = threading.Lock()


def _sid(wid: str, check: Callable[[str], "str | None"], clock: Callable[[], float]) -> str | None:
    now = clock()
    with _lock:
        hit = _cache.get(wid)
    if hit is not None and now - hit[0] < CHECK_TTL_S:
        return hit[1]
    sid = check(wid)
    with _lock:
        _cache[wid] = (now, sid)
    return sid


def resolve(
    wid: str,
    native: str | None,
    *,
    check: Callable[[str], "str | None"] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str | None, str | None]:
    """``(status, source)`` for a window. ``source`` is None for Claude's own status (or
    none at all) and :data:`PROXY_SOURCE` when the sandbox proxy supplied it."""
    if native is not None:
        return native, None
    sid = _sid(wid, check or share_sandbox.share_session_id, clock)
    if sid is None:
        return None, None
    status = share_sandbox.proxy_activity_status(sid)
    return (status, PROXY_SOURCE) if status else (None, None)
