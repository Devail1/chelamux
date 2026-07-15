"""🎭 THE ATTENDED-LEASE — the supervision gate for the auto-launched orchestrator.

``docs/ESCALATION_CONTRACT.md`` is emphatic: the orchestrator stays **human-attended** until
process isolation (srt) bounds the *execution* surface — "⛔ Until (1)–(3) exist, the
orchestrator stays human-attended." CMX-90 auto-launches the orchestrator *without* waiting on
that isolation, so it needs a different, buildable-now way to keep the orchestrator's autonomous
actions **supervised**. This is it.

An **attended-lease** is a human's durable "I am here" signal with a TTL — a dead-man's switch,
the same shape as :mod:`chela.hold`. A human runs ``chela orchestrator attend`` to grant a lease
for a bounded window (default 30 min, hard-capped at 4 h); while it is *active* the orchestrator
may be auto-launched and act within its standing-auth envelope; the moment it **expires** the
auto-launch gate closes again. So the orchestrator is never *unattended* — every autonomous action
it takes happens inside a window a human explicitly opened and has not let lapse. That is the
"attended-autonomous" mode made real without isolation: not confirm-each (the human does not
approve every merge), but not boot-persistent-unattended either (nothing fires once the human
stops refreshing the lease).

⛔ **Fail-closed, exactly like the merge gate.** No lease, an expired lease, or a lease file that
cannot be parsed all read as **NO active lease** — the auto-launch simply does not fire. A byte of
corruption can never *grant* supervision it should not; at worst it withholds an auto-launch a
human then does by hand.

The lease lives in one JSON file under ``$CHELA_DIR`` so it survives a daemon restart and is
readable by the CLI that grants it and the daemon that honours it alike.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from chela import config
# Reuse the hold's battle-tested duration formatting — same grammar humans already read on the
# dispatch hold, no second copy to drift. (TTL *parsing* for the CLI reuses ``hold.parse_ttl``.)
from chela.hold import human_duration

log = logging.getLogger(__name__)

LEASE_FILE_NAME = "orchestrator-lease.json"

# A supervision window is meant to be short and deliberately re-granted, not set-and-forget:
# the whole point is that a human is *around*. Defaults mirror the dispatch hold.
DEFAULT_TTL_SECONDS = 30 * 60
MAX_TTL_SECONDS = 4 * 60 * 60


@dataclass(frozen=True)
class Lease:
    """One attended-lease. ``by`` is who opened it (a wid / pid), for the audit trail."""

    by: str
    created_at: float
    expires_at: float

    def remaining(self, now: float | None = None) -> float:
        return self.expires_at - (time.time() if now is None else now)

    def expired(self, now: float | None = None) -> bool:
        return self.remaining(now) <= 0

    def summary(self, now: float | None = None) -> str:
        """One sentence every surface prints."""
        who = self.by or "someone"
        left = self.remaining(now)
        when = (f"{human_duration(left)} left" if left > 0
                else f"EXPIRED {human_duration(-left)} ago")
        return f"attended by {who} ({when})"

    def as_dict(self, now: float | None = None) -> dict:
        return {
            "by": self.by,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "remaining": self.remaining(now),
            "expired": self.expired(now),
            "summary": self.summary(now),
        }


def path() -> Path:
    # Resolved per call, not latched at import: CHELA_DIR is monkeypatched in tests and may be
    # re-pointed by a launcher — the same rule hold.path() and inbox.store_path() follow.
    return config.CHELA_DIR / LEASE_FILE_NAME


def _default_by() -> str:
    """Who opened it, without being asked. ``CHELA_WID`` is exported into every chela session,
    so a human attending from their orchestrator pane names that window automatically."""
    return os.environ.get("CHELA_WID") or f"pid {os.getpid()}"


def grant(ttl_seconds: float = DEFAULT_TTL_SECONDS, by: str = "") -> Lease:
    """Open (or refresh) the attended-lease for ``ttl_seconds``. Re-granting extends the expiry.

    Raises ``OSError`` if the file cannot be written: a lease the daemon will never see is a
    supervision window the human believes is open but is not, so fail loudly in the granter's
    face rather than silently leaving the gate closed.
    """
    ttl = max(1.0, min(float(ttl_seconds), float(MAX_TTL_SECONDS)))
    now = time.time()
    lease = Lease(by=by.strip() or _default_by(), created_at=now, expires_at=now + ttl)
    config.CHELA_DIR.mkdir(parents=True, exist_ok=True)
    path().write_text(json.dumps({
        "by": lease.by,
        "created_at": lease.created_at,
        "expires_at": lease.expires_at,
    }) + "\n", encoding="utf-8")
    log.info("orchestrator lease granted by %s for %s", lease.by, human_duration(ttl))
    return lease


def release() -> Lease | None:
    """Close the lease now (stop attending). Returns the lease that was in force, or None.

    Idempotent: releasing when none is set is a no-op, so a human can end the session
    unconditionally.
    """
    held = read()
    try:
        path().unlink()
    except FileNotFoundError:
        return None
    except OSError as e:
        log.warning("could not remove the orchestrator lease at %s: %s", path(), e)
        return None
    return held


def read() -> Lease | None:
    """Whatever lease is on disk, expired or not — or None if there is none.

    ⛔ An unreadable or malformed file counts as NO lease. The file is written by a CLI and
    read by an unattended daemon; a byte of corruption must never be able to *grant* an
    auto-launch that a human did not, so a file we cannot parse fails closed (loudly).
    """
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
        return Lease(
            by=str(data.get("by") or ""),
            created_at=float(data["created_at"]),
            expires_at=float(data["expires_at"]),
        )
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError) as e:
        log.warning(
            "orchestrator lease at %s is unreadable (%s) — treating it as NO lease "
            "(fail-closed: no auto-launch); delete the file to silence this", path(), e,
        )
        return None


def active(now: float | None = None) -> Lease | None:
    """The lease currently in force, or None. This is the one the auto-launch gate reads."""
    held = read()
    return held if held is not None and not held.expired(now) else None
