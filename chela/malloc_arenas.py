"""Cap glibc's per-thread malloc arenas from INSIDE a long-running service (CMX-14).

CMX-163 put ``MALLOC_ARENA_MAX=2`` in ``scripts/run-chela.sh``, but glibc only reads
that variable when the process starts, so the cap applied ONLY to services started
through that launcher. A PM2 entry that predates the launcher
(``script: .venv/bin/python``, ``args: -m chela.main dashboard``) never got it. Measured
on such a dashboard 31h after start: RSS 1.06 GB, of which **906 MB sat in 34
glibc thread-arena heaps** and only 34 MB in the main heap. Werkzeug's ``threaded=True``
serves every request on a fresh thread, glibc hands contending threads their own arena,
and each arena keeps its own free-list, so RSS ratchets up to a high-water mark and stays
there. It is fragmentation, not a Python-object leak.

``mallopt(M_ARENA_MAX, n)`` sets the same cap at runtime. Called before the service starts
its threads, it holds no matter how the process was launched.
"""
from __future__ import annotations

import ctypes
import logging
import os

log = logging.getLogger(__name__)

# The long-running commands: each one is multi-threaded and lives for days. A one-shot
# `chela status` exits long before fragmentation could matter.
SERVICE_COMMANDS = frozenset({"run", "dashboard", "telegram", "collab"})
DEFAULT_ARENA_MAX = 2
M_ARENA_MAX = -8          # <malloc.h>: the mallopt() param MALLOC_ARENA_MAX maps to


def wanted(environ=None) -> int | None:
    """The arena cap to apply: ``$MALLOC_ARENA_MAX`` when set (``chela.env`` sources into
    the environment only AFTER glibc has read it, so it has to be re-applied here), else
    :data:`DEFAULT_ARENA_MAX`. ``0`` means "glibc's default" (no cap), like
    ``examples/chela.env`` documents, and so does a value that is not a positive integer."""
    environ = os.environ if environ is None else environ
    raw = environ.get("MALLOC_ARENA_MAX")
    if raw is None or raw.strip() == "":
        return DEFAULT_ARENA_MAX
    try:
        n = int(raw)
    except ValueError:
        log.warning("MALLOC_ARENA_MAX=%r is not an integer; leaving glibc's default", raw)
        return None
    return n if n > 0 else None


def cap(environ=None, libc=None) -> int | None:
    """Apply the cap via ``mallopt``. Returns the cap applied, or None if none was (opted
    out, or not glibc: macOS has no ``mallopt``, and musl's is a stub returning 0)."""
    n = wanted(environ)
    if n is None:
        return None
    try:
        libc = ctypes.CDLL(None) if libc is None else libc
        mallopt = libc.mallopt
    except (OSError, AttributeError):
        return None
    if mallopt(M_ARENA_MAX, n) != 1:
        return None
    return n
