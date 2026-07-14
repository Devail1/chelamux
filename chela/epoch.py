"""The tmux EPOCH — the scope inside which ``@3`` means anything at all.

**A window id is an ADDRESS, not an IDENTITY.** tmux issues ``@N`` per SERVER: within one
server the ids are unique and never recycled, so ``@3`` is a perfectly good name for a
window — for as long as that server lives. A new server starts numbering from ``@0`` again
and hands the same ids to different windows. Nothing in the id itself says which server
issued it, so a persisted ``@N`` is only meaningful next to the identity of the server that
issued it. That is the epoch, and this module is the two rules that follow from it:

* **stamp** — every ``@N`` chela writes to disk is written with the epoch it was issued in;
* **check on read** — a stamped id from another epoch is DANGLING: it does not name the
  window you meant, and tmux has very likely given that number to somebody else by now. It
  must never be acted on, and it must never be silent.

**This is measured, not hypothetical.** On 2026-07-14 an OOM killed the tmux server. The
fleet came back renumbered (the orchestrator went ``@0`` → ``@6``) and ``inbox.json`` still
read ``{"orchestrator": "@0"}``. The decisions inbox queued five ``run_review``
notifications addressed to a window that no longer existed and delivered **none** of them:
the idle gate is ``statuses.get(orch) != IDLE``, and a dead address is simply *absent* from
the status map, so the gate never opened and the queue grew in silence. No error, no
warning, no log line; ``chela doctor`` stayed green (14/14). Five finished PRs went
unreviewed until a human noticed. An address that has rotted must SHOUT — the whole cost of
that outage was that it did not.

**Never re-bind a dangling id — a wrong wid is worse than no wid (CMX-48).** The renumbered
fleet is the exact case where ``@0`` still *exists* and belongs to someone else: acting on
the stale address would paste the orchestrator's review queue into a random agent's prompt
(and, in the Feed, file a dead agent's work under a live agent's lane). An unaddressed event
is visibly ownerless; a misaddressed one is invisibly false. So a dangling id resolves to
NOTHING, loudly, and the human (or the orchestrator's next ``chela watch``) re-registers a
real one.

The epoch is the tmux server's **pid and start time** together, read back from tmux itself —
the owner of the fact (runtime_truth rule (b)). The pid alone would do until the kernel
recycles it onto a later tmux server, at which point a stale address would silently look
current again; the pair cannot collide.

An epoch that CANNOT be read (no tmux, no server) is not "no epoch": it is unknown, and an
unknown never accuses a stamp of being stale — :func:`is_dangling` needs both halves. The
surfaces that care (``chela doctor``) report the unreadable owner as CANNOT VERIFY instead,
which is the one thing a green check must never be.
"""
from __future__ import annotations

import logging
import subprocess

log = logging.getLogger(__name__)

# pid AND start time: the kernel recycles pids, and a recycled one would make a stale
# address look current again — which is precisely the failure this module exists to end.
_FORMAT = "#{pid}-#{start_time}"


def current() -> str | None:
    """The identity of the tmux server that is issuing window ids RIGHT NOW.

    ``None`` when tmux cannot be asked (not installed, no server running) — the honest
    "unknown", never a value that could be compared equal to a stamp.
    """
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", _FORMAT],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        log.debug("tmux could not be asked for the server epoch")
        return None
    if result.returncode != 0:
        return None                       # no server running — there is no epoch to be in
    stamp = result.stdout.strip()
    return stamp or None


def is_dangling(stamped: str | None, now: str | None) -> bool:
    """Was ``stamped`` issued by a tmux server that is no longer the one running?

    True ONLY when both halves are known and they differ. An unstamped id (written before
    this existed, or pinned by an operator) and an unknown current epoch are both *unknown*,
    not stale: they cannot be verified, and pretending otherwise would turn a legacy file
    into a fleet-wide false alarm. The surfaces that must not stay quiet about an
    unverifiable address say so in their own words (``chela doctor``: CANNOT VERIFY).
    """
    return bool(stamped) and bool(now) and stamped != now


def describe(stamped: str | None) -> str:
    """How an epoch reads in a message a human has to act on."""
    return f"tmux epoch {stamped}" if stamped else "no epoch recorded (pre-CMX-77)"
