"""The queue hold — "claim nothing, I am rewriting the queue."

**The dispatcher wins every race for the queue.** Not by a narrow margin, and not by
accident — structurally: a PR merges, reconciliation frees the concurrency slot, and the
orchestrator then starts *writing* the next task, which takes minutes because it is
reviewing what just landed. The dispatcher's tick fires long before that and claims
whatever is top of the OLD queue. With ``concurrency.max: 1`` that wrong claim does not
merely reorder work, it occupies the only slot for a full agent run. It happened twice in
one day.

That is the same two-writer problem this repo already met on the *write* axis (CMX-36 made
the dispatcher the tracker's sole writer, because agents and the orchestrator were editing
one file and conflicting on every PR). This is that problem on the **ordering** axis, and
re-reading the tracker later cannot fix it: the orchestrator's edit genuinely lands
*after* the slot freed. No amount of freshness beats an edit that does not exist yet.

So ordering is decided by **intent** instead of by who is faster. Before it rewrites the
queue the orchestrator takes a hold; the dispatcher claims nothing while it is held; the
orchestrator pushes and releases, and the very next tick claims the *new* top item.

Three properties this hold must have, and the reasons they are not negotiable:

* **It lives in a FILE** (``$CHELA_DIR/dispatch-hold.json``), not in module state. The
  daemon runs under PM2, in a different process from the CLI that takes the hold — a hold
  that exists only in one process's memory is not a hold (the CMX-42 trap) — and it must
  survive the daemon being restarted underneath it.
* **It cannot strand the fleet.** An orchestrator that crashes mid-rewrite must not leave
  dispatch paused for eternity, so every hold carries an **expiry** (default
  :data:`DEFAULT_TTL_SECONDS`, hard-capped at :data:`MAX_TTL_SECONDS`). An expired hold
  self-releases and says so loudly. A corrupt or unreadable hold file is treated as **no
  hold** — a file we cannot parse must never be able to stop the fleet — and warns.
  (There is deliberately no pid-check: unlike ``daemon.json``, whose pid is a long-running
  process, the holder here is a CLI invocation that exits immediately. Its pid is recorded
  for a human to read, and gates nothing.)
* **It pauses CLAIMS ONLY — never reconciliation.** CMX-53's lesson was that dispatch and
  reconcile ride the same tick and went dark together. Pausing dispatch must not also stop
  merged PRs from closing out their runs and freeing their slots, or the hold would jam
  the very slot the orchestrator is trying to fill.

**Preemption is OUT OF SCOPE, by decision.** A run that has already been claimed is NOT
killed and re-dispatched when a higher-priority task lands seconds later. Killing a live
agent is its own hazard class — this repo has killed its own fleet three times, and the
``tmux -L <socket>`` isolation exists precisely because of that — and a half-preemption
that leaves an orphaned worktree, branch or tmux window is strictly worse than a run that
finished the wrong task first. The hold is the remedy: it is taken *before* the slot is
contested, which is the only moment at which the reorder is free. If preemption is ever
revisited it needs its own answer to "has the agent written anything, opened a PR, touched
the worktree?" and a clean abandonment path for all three; none of that exists here, and
nothing in this module pretends otherwise.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)

HOLD_FILE_NAME = "dispatch-hold.json"

# Long enough for a human (or an orchestrator agent) to review a merge and rewrite the
# queue, short enough that forgetting to release it costs one tick's worth of patience
# rather than a night of idle fleet.
DEFAULT_TTL_SECONDS = 30 * 60
MAX_TTL_SECONDS = 4 * 60 * 60

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*([smh]?)\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"": 1, "s": 1, "m": 60, "h": 3600}


def parse_ttl(value: str | int | float) -> float:
    """``"30m"`` / ``"2h"`` / ``"900"`` → seconds, clamped to (0, MAX_TTL_SECONDS].

    Raises ``ValueError`` on anything else: a hold whose duration we had to guess at is a
    hold whose expiry we cannot promise, and the expiry is the only thing standing between
    a crashed orchestrator and a fleet that never dispatches again.
    """
    m = _DURATION_RE.match(str(value))
    if not m:
        raise ValueError(f"not a duration: {value!r} (try 900, 30m, 2h)")
    seconds = float(m.group(1)) * _UNIT_SECONDS[m.group(2).lower()]
    if seconds <= 0:
        raise ValueError("a hold with no duration is not a hold; use --resume to release")
    return min(seconds, float(MAX_TTL_SECONDS))


@dataclass(frozen=True)
class Hold:
    reason: str
    by: str
    pid: int
    created_at: float
    expires_at: float

    def remaining(self, now: float | None = None) -> float:
        return self.expires_at - (time.time() if now is None else now)

    def expired(self, now: float | None = None) -> bool:
        return self.remaining(now) <= 0

    def age(self, now: float | None = None) -> float:
        return (time.time() if now is None else now) - self.created_at

    def summary(self, now: float | None = None) -> str:
        """One sentence a human can act on — this is what every surface prints."""
        who = self.by or "someone"
        why = f" — {self.reason}" if self.reason else ""
        left = self.remaining(now)
        when = (f"expires in {human_duration(left)}" if left > 0
                else f"EXPIRED {human_duration(-left)} ago")
        return f"held by {who}{why} ({when})"

    def as_dict(self, now: float | None = None) -> dict:
        return {
            "reason": self.reason,
            "by": self.by,
            "pid": self.pid,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "remaining": self.remaining(now),
            "expired": self.expired(now),
            "summary": self.summary(now),
        }


def human_duration(seconds: float) -> str:
    """``95`` → ``"1m"``. Durations are read by humans; print them that way."""
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def path() -> Path:
    # Resolved per call, not at import: CHELA_DIR is monkeypatched in tests and may be
    # re-pointed by a launcher, exactly as capabilities.state_file() does it.
    return config.CHELA_DIR / HOLD_FILE_NAME


def take(reason: str = "", ttl_seconds: float = DEFAULT_TTL_SECONDS, by: str = "") -> Hold:
    """Pause claiming. Overwrites any existing hold (re-taking extends the expiry).

    Raises ``OSError`` if the file cannot be written — a hold the daemon will never see is
    worse than no hold at all, because the caller would go on to rewrite the queue
    believing it was protected. Fail loudly, at the CLI, in the caller's face.
    """
    ttl = max(1.0, min(float(ttl_seconds), float(MAX_TTL_SECONDS)))
    now = time.time()
    hold = Hold(
        reason=reason.strip(),
        by=by.strip() or _default_by(),
        pid=os.getpid(),
        created_at=now,
        expires_at=now + ttl,
    )
    config.CHELA_DIR.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps({
        "reason": hold.reason,
        "by": hold.by,
        "pid": hold.pid,
        "created_at": hold.created_at,
        "expires_at": hold.expires_at,
    }) + "\n", encoding="utf-8")
    return hold


def _default_by() -> str:
    """Who took it, in the words of whoever is looking. ``CHELA_WID`` is exported into
    every dispatched agent's window, so an orchestrator that holds the queue names itself
    without being asked to."""
    return os.environ.get("CHELA_WID") or f"pid {os.getpid()}"


def release() -> Hold | None:
    """Resume claiming. Returns the hold that was released, or None if none was in force.

    Idempotent — releasing an already-released hold is a no-op, so the orchestrator can
    (and should) release unconditionally in its own error path.
    """
    held = read()
    try:
        path().unlink()
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("could not remove the dispatch hold at %s: %s", path(), e)
        return None
    return held


def read() -> Hold | None:
    """Whatever hold is on disk, expired or not, or None if there is none.

    An unreadable or malformed file counts as NO hold: it is written by a CLI and read by
    an unattended daemon, and a byte of corruption must not be able to park the fleet
    forever. It is loud about it, because a hold nobody can parse is also a hold nobody
    can release.
    """
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return Hold(
            reason=str(data.get("reason") or ""),
            by=str(data.get("by") or ""),
            pid=int(data.get("pid") or 0),
            created_at=float(data["created_at"]),
            expires_at=float(data["expires_at"]),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as e:
        log.warning(
            "dispatch hold at %s is unreadable (%s) — treating it as NO hold and "
            "dispatching normally; delete the file to silence this", path(), e,
        )
        return None


def active(now: float | None = None) -> Hold | None:
    """The hold currently in force, or None. This is the one every caller wants."""
    held = read()
    return held if held is not None and not held.expired(now) else None


def expire_if_stale(now: float | None = None) -> Hold | None:
    """Self-release an expired hold and return it (so the caller can say so LOUDLY).

    Called from the dispatch tick — an expired hold is not a quiet fact. Somebody paused
    the queue and never came back, which means the queue they meant to rewrite is probably
    not the queue we are about to claim from.
    """
    held = read()
    if held is None or not held.expired(now):
        return None
    try:
        path().unlink()
    except OSError:
        pass       # still expired, so still not held; we just say it again next tick
    return held
