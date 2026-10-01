"""📲🧱 Outbound relay for SANDBOXED sessions — read the proxy's outbox, not a transcript.

A sandboxed share session (:mod:`chela.share_sandbox`) keeps its Claude Code transcript in
the container's tmpfs, and its hooks cannot reach the host — by design. So the ordinary
window → transcript resolution (:mod:`chela.sessions`) has nothing to find, and before
CMX-420 such a window's topic was bound, inbound worked, and outbound was silently dead.

The credential proxy in front of that session sees every model response, and writes each
completed turn to a host-side outbox as a transcript-shaped JSONL record
(:class:`chela.share_proxy.Outbox`). This module is the resolver that points the
:class:`~chela.telegram.monitor.TranscriptMonitor` at that outbox for a window that
verifies LIVE as a sandboxed session — so the relay's parser, formatting, chunking and
offset-dedup apply unchanged — and at the normal transcript for every other window.

**The sandbox check runs FIRST, not as a fallback.** A sandboxed window's pane sits in the
guest's workspace, and ``sessions``' last-resort cwd guess would happily return the newest
*host* transcript in that project dir — some other session's words, in the guest's topic.

Verification costs a tmux call, a ``/proc`` read and two ``docker inspect``s, and the
relay polls every couple of seconds, so a verdict is cached for :data:`CHECK_TTL_S`.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Callable

from chela import share_sandbox

CHECK_TTL_S = 15.0


class SandboxOutboxes:
    """Which bound windows are sandboxed sessions, and where their outbox is."""

    def __init__(
        self,
        *,
        check: Callable[[str], "str | None"] | None = None,
        clock: Callable[[], float] = time.monotonic,
        ttl: float = CHECK_TTL_S,
    ):
        self._check = check or share_sandbox.share_session_id
        self._clock = clock
        self._ttl = ttl
        self._cache: dict[str, tuple[float, str | None]] = {}

    def sid(self, window_id: str) -> str | None:
        """The window's sandboxed-session id (verified live, cached), or None."""
        now = self._clock()
        hit = self._cache.get(window_id)
        if hit is not None and now - hit[0] < self._ttl:
            return hit[1]
        sid = self._check(window_id)
        self._cache[window_id] = (now, sid)
        return sid

    def resolver(self, fallback: Callable[[str], "Path | None"]) -> Callable[[str], "Path | None"]:
        """A monitor resolver: a sandboxed window → its outbox, anything else → ``fallback``."""

        def resolve(window_id: str) -> Path | None:
            sid = self.sid(window_id)
            if sid is not None:
                return share_sandbox.outbox_path(sid)
            return fallback(window_id)

        return resolve

    def allows(self, window_id: str) -> bool:
        """False only for a sandboxed window whose relay the operator switched off."""
        sid = self.sid(window_id)
        return sid is None or share_sandbox.relay_enabled(sid)
