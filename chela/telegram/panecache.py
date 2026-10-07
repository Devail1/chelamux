"""Capture a pane only when tmux says it changed (CMX-18).

The pane watch (:class:`~chela.telegram.gatewatch.PermissionGateWatcher`) reads every
bound window's pane every tick. Measured in the CMX-15 profile, that was one
``capture-pane`` spawn per window per 2 s — 2.9% of a core at 5 windows and 25% at 40 —
while most windows were idle and their panes identical tick after tick.

tmux already knows which ones moved: ``#{window_activity}`` is the time of the window's
last pane output, and it is ONE ``list-windows`` call for the whole server. So each tick
reads that once, and a window's pane is re-captured only when its stamp says something
may have been written since the last capture. Otherwise the watcher is handed the text it
captured last time.

**It serves the cached TEXT, it does not skip the window.** Every detector and every
non-pane input (the hook log's pending gate, a held answer channel, the re-post backoff,
the status line) still runs every tick, exactly as before; the only thing that changed is
where identical pane text comes from. A window can therefore lose nothing here except by
being served text that is no longer on the pane — and the rules below are about that one
hole:

* ``window_activity`` is whole SECONDS. Output written later in the same second as the
  last capture leaves the stamp where it was. So a window is clean only while its stamp
  is strictly OLDER than the second its last capture began — anything in that second or
  later is re-captured (one spare capture after a burst, never a missed one).
* A window tmux does not list (or a ``list-windows`` that fails) is captured as before.
  Unknown is not "unchanged".
* Some pane changes may write no output at all (a resize reflow, a ``clear-history``).
  A slow full sweep re-captures every window at least every ``sweep`` seconds, so the
  worst case for anything the stamp cannot see is one sweep, not forever.
"""
from __future__ import annotations

import logging
import subprocess
import time
from typing import Callable

log = logging.getLogger(__name__)

Capture = Callable[[str], str]

# The backstop: every window is re-captured at least this often, whatever its stamp says.
SWEEP_SECONDS = 30.0


def window_activity() -> dict[str, int] | None:
    """``{window_id: last output time (unix seconds)}`` for every window, in ONE tmux call.

    ``None`` when tmux cannot be asked — the caller then captures everything, as it did
    before this existed.
    """
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-a", "-F", "#{window_id} #{window_activity}"],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return parse_activity(result.stdout)


def parse_activity(text: str) -> dict[str, int]:
    """Parse ``list-windows -F '#{window_id} #{window_activity}'`` output; skip bad rows."""
    out: dict[str, int] = {}
    for line in text.splitlines():
        wid, _, stamp = line.strip().partition(" ")
        if not wid.startswith("@"):
            continue
        try:
            out[wid] = int(stamp)
        except ValueError:
            continue
    return out


class _Seen:
    __slots__ = ("pane", "activity", "cap_sec", "cap_at")

    def __init__(self, pane: str, activity: int | None, cap_sec: int, cap_at: float):
        self.pane = pane
        self.activity = activity      # the stamp tmux reported on the tick it was captured
        self.cap_sec = cap_sec        # whole second the capture BEGAN in (wall clock)
        self.cap_at = cap_at          # monotonic time of that capture, for the sweep


class ActivityGatedCapture:
    """Per-tick pane source: re-capture a window only when its activity stamp moved.

    Call :meth:`tick` once per poll with the windows about to be read; it returns the
    ``Capture`` for that tick.
    """

    def __init__(
        self,
        capture: Capture,
        *,
        activity: Callable[[], dict[str, int] | None] = window_activity,
        sweep: float = SWEEP_SECONDS,
        wall: Callable[[], float] = time.time,
        now: Callable[[], float] = time.monotonic,
    ):
        self._capture = capture
        self._activity = activity
        self._sweep = sweep
        self._wall = wall
        self._now = now
        self._seen: dict[str, _Seen] = {}

    def tick(self, window_ids) -> Capture:
        window_ids = list(window_ids)
        try:
            stamps = self._activity()
        except Exception:
            log.exception("pane cache: reading window activity failed")
            stamps = None
        # Forget windows that left the polled set, so a returning one is captured fresh.
        keep = set(window_ids)
        for wid in [w for w in self._seen if w not in keep]:
            del self._seen[wid]

        def capture(window_id: str) -> str:
            stamp = None if stamps is None else stamps.get(window_id)
            seen = self._seen.get(window_id)
            if seen is not None and not self._stale(seen, stamp):
                return seen.pane
            cap_sec = int(self._wall())
            cap_at = self._now()
            pane = self._capture(window_id)
            self._seen[window_id] = _Seen(pane, stamp, cap_sec, cap_at)
            return pane

        return capture

    def _stale(self, seen: _Seen, stamp: int | None) -> bool:
        if stamp is None:
            return True                                  # tmux didn't say: capture
        if stamp != seen.activity or stamp >= seen.cap_sec:
            return True                                  # output since (or during) the capture
        return self._now() - seen.cap_at >= self._sweep  # the backstop sweep
