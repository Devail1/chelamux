"""``/pauseall`` and ``/resumeall``: ``chela dispatch --pause --all`` / ``--resume`` from the
phone (CMX-252).

The same two steps as the CLI (``chela.main.cmd_dispatch_hold``), in the same order: the
hold FIRST, then every dispatched agent (:func:`chela.fleet.pause_all`); on resume, the
fleet first, while the hold still stands, then the hold (:func:`chela.fleet.resume_fleet`).
Each returns the reply text; the bridge runs them off its event loop.

``/resumeall``, not ``/resume``: Claude Code owns ``/resume``, and a bridge command would
shadow it in every topic.
"""
from __future__ import annotations

import logging

from chela import fleet, hold

log = logging.getLogger(__name__)

BY = "telegram"


def pause_all(reason: str = "") -> str:
    """Take the hold, then pause every dispatched agent. The reply says what stopped."""
    try:
        held = hold.take(reason=reason, by=BY)
    except OSError as e:
        # A hold the daemon will never see is worse than none: say so, pause nothing.
        return f"❌ Could not take the hold ({e}). Dispatch is NOT paused."
    try:
        state = fleet.pause_all(by=held.by)
    except Exception as e:  # noqa: BLE001 — the reply must say the hold stands
        log.exception("fleet pause failed")
        return (f"⚠️ Dispatch is held ({held.summary()}), but pausing the agents "
                f"failed: {e}")
    return "\n".join([f"⏸ Dispatch HELD: {held.summary()}.", *fleet.status_lines(state),
                      "Resume: /resumeall"])


def resume_all() -> str:
    """Resume the paused fleet, then release the hold."""
    try:
        resumed, released = fleet.resume_fleet(by=BY)
    except Exception as e:  # noqa: BLE001 — the reply must say the hold stands
        log.exception("fleet resume failed")
        return f"❌ Resuming the agents failed: {e}. Dispatch is still held."
    lines = []
    if resumed is not None:
        sent = resumed["resumed"]
        lines.append(f"▶️ Fleet RESUMED: sent 'resume' to {sum(sent.values())} of "
                     f"{len(sent)} agent(s).")
        lines += [f"⚠️ {task}: 'resume' was NOT delivered. Tell it by hand."
                  for task, ok in sorted(sent.items()) if not ok]
        lines += [f"judge {task}: " + ("thawed" if ok else "⚠️ NOT fully thawed. Check it.")
                  for task, ok in sorted(resumed["thawed"].items())]
    if released is None:
        lines.append("Dispatch was not held. Nothing to resume.")
    else:
        lines.append(f"▶️ Dispatch RESUMED: released a hold "
                     f"{hold.human_duration(released.age())} old ({released.by}).")
    return "\n".join(lines)
