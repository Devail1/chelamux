"""Request-scoped batching of the per-window tmux/process probes (CMX-17).

The dashboard's polled endpoints (``/api/agents``, ``/api/agents/context``,
``/api/orchestrator/status``) used to ask tmux and ``pgrep`` about every window one at a
time: a ``display-message`` plus a ``pgrep -P`` for the claude pid, another
``display-message`` for the foreground command, a ``list-windows`` + ``display-message``
for the cwd, and an ``epoch.current()`` round trip per window on the transcript path. The
CMX-15 profile (``docs/perf/2026-10-profile.md``) measured that as 94% of the dashboard's
process spawns, growing linearly with the fleet.

Inside :func:`batch`, those same probes answer from ONE shared snapshot instead:
:func:`chela.sessions.panes` (one ``tmux list-windows`` plus ``/proc``, already TTL-cached
and single-flighted across threads), and :func:`shared` for the tmux epoch. Outside a
batch, nothing changes — every other caller (the daemon, the hooks, the CLI) still gets a
fresh per-window probe, so no freshness assumption anywhere else moves.

The seam is deliberately the ORIGINAL functions (``agent_manager.claude_pid``,
``agent_manager.pane_command``, ``discovery.get_window_cwd``, ``epoch.current``): each
checks :func:`active` and reads the snapshot when a batch is open. Their answers are the
same facts read a different way — ``Pane.direct_claude_pid`` reproduces
``pgrep -P <pane_pid> -f claude`` exactly — so the endpoints' output is byte-identical
(``tests/test_probe_batch.py`` holds that against a scratch tmux server).

Freshness: a window that is not in the cached snapshot (created since it was taken) forces
ONE re-read for the request, so a new window shows on the very next poll; a closed window
is simply never asked about, because the endpoints still list windows fresh. What can lag
by up to :data:`chela.sessions._TTL` (1 s) is a claude starting or exiting inside a window
that already existed — well inside the 30 s the busy/idle feed itself is cached for.
"""
from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

# How long a :func:`shared` value is trusted across requests and threads.
TTL = 1.0


class Batch:
    """One request's view of the pane map — taken once, re-read at most once."""

    def __init__(self, windows: dict[str, str] | None = None):
        # {name: window_id} as the endpoint listed it, so a name → cwd lookup needs no
        # second `list-windows` (discovery.get_window_cwd resolves names through this).
        self.windows = windows
        self._panes: dict | None = None
        self._forced = False

    def pane(self, wid: str):
        """The :class:`chela.sessions.Pane` for ``wid``, or None if tmux has no such window.

        A wid missing from the shared (possibly ~1 s old) snapshot forces one fresh read —
        it is either brand new, which the caller must see now, or genuinely gone.
        """
        from chela import sessions          # deferred: sessions sits above this module

        if self._panes is None:
            self._panes = sessions.panes()
        found = self._panes.get(wid)
        if found is None and not self._forced:
            self._forced = True
            self._panes = sessions.panes(force=True)
            found = self._panes.get(wid)
        return found


_ACTIVE: ContextVar[Batch | None] = ContextVar("chela_probe_batch", default=None)


@contextmanager
def batch(windows: dict[str, str] | None = None) -> Iterator[Batch]:
    """Answer per-window probes from one shared snapshot for the duration of the block.

    A ContextVar, so each request thread has its own batch and nested/overlapping requests
    never see each other's.
    """
    token = _ACTIVE.set(Batch(windows))
    try:
        yield _ACTIVE.get()
    finally:
        _ACTIVE.reset(token)


def active() -> Batch | None:
    """The open batch for this thread/context, or None outside one."""
    return _ACTIVE.get()


_shared_cache: dict[str, tuple[float, Any]] = {}
_shared_lock = threading.Lock()


def shared(key: str, fn: Callable[[], Any], ttl: float = TTL) -> Any:
    """``fn()``, cached for ``ttl`` seconds across requests and threads, single-flighted.

    For a fact that is the same for every window and every caller (the tmux epoch), so a
    burst of concurrent polls collapses to one subprocess.
    """
    hit = _shared_cache.get(key)
    if hit is not None and time.monotonic() - hit[0] < ttl:
        return hit[1]
    with _shared_lock:
        hit = _shared_cache.get(key)
        if hit is not None and time.monotonic() - hit[0] < ttl:
            return hit[1]                    # a concurrent caller refreshed it
        value = fn()
        _shared_cache[key] = (time.monotonic(), value)
        return value


def clear() -> None:
    """Drop every :func:`shared` value (tests; a caller that knows the fact moved)."""
    with _shared_lock:
        _shared_cache.clear()
