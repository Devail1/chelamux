"""⏳ CMX-494: a run that waits too long for a suite slot is reported, and the queue is visible.

On 11-23 an Urgent task sat 4 h in the suite queue and nobody was told: a slot wait is not a
hang, so the stuck detector skips it, and nothing else looked. Two surfaces, both over
:func:`chela.suite_guard.held` (the holders) and :func:`chela.suite_guard.queued` (the waiters,
in the order they will be served):

* ``chela slots`` (:func:`lines`);
* one ``run_slot_wait`` inbox notice per overdue waiter (:func:`due`): an Urgent or High task
  past :func:`chela.config.slot_wait_alert_s`, any waiter past
  :func:`chela.config.slot_wait_any_alert_s`, repeated at most once per :data:`REPEAT_S`.

A stopped waiter (a fleet pause) is listed but never reported: the operator stopped it.
Nor is one held by its blocked hours (CMX-479): nothing is wrong, and it starts when they end.
"""
from __future__ import annotations

from chela import config, suite_guard
from chela.hold import human_duration

KIND = "run_slot_wait"
REPEAT_S = 3600.0
STORE_KEY = "slot_waits"          # {waiter's cwd: when it was last reported}


def label(entry: dict) -> str:
    """``CMX-473 (Urgent)`` — a holder or waiter, with its rank in words."""
    return f"{entry['name']} ({suite_guard.rank_label(entry['rank'], entry['task_rank'])})"


def _labels(entries: list[dict]) -> str:
    return ", ".join(map(label, entries))


def lines(slots: int, holders: list[dict], waiters: list[dict], now: float) -> list[str]:
    """What ``chela slots`` prints."""
    def age(since) -> str:
        return human_duration(now - since) if isinstance(since, (int, float)) else "?"

    if slots == 0:
        return ["Suite slots: off (CHELA_SUITE_SLOTS=0) — runs do not queue"]
    out = [f"Suite slots: {len(holders)} of {slots} held, {len(waiters)} waiting"]
    out += [f"  ▶ {label(h)}  pid {h['pid'] if h['pid'] is not None else '?'}  "
            f"held {age(h['since'])}" for h in holders]
    out += [f"  {i}. {label(w)}  rank {w['rank']}  pid {w['pid']}  waiting {age(w['since'])}"
            + ("  (stopped)" if w["stopped"] else "")
            + (f"  (blocked hours until {w['blocked_until']})" if w["blocked_until"] else "") for i, w in enumerate(waiters, 1)]
    return out


def due(store: dict, holders: list[dict], waiters: list[dict], now: float) -> list[dict]:
    """The overdue waiters this tick owes a notice, as ``{waiter, waited, text}``. Latches each
    in ``store`` by worktree, so a waiter that restarts inside the hour is not told twice."""
    prev = store.get(STORE_KEY) or {}
    said = {cwd: at for cwd, at in prev.items() if now - at < REPEAT_S}
    out = []
    for w in waiters:
        waited = now - w["since"]
        limit = config.slot_wait_any_alert_s()
        if w["task_rank"] in (suite_guard.RANK_URGENT, suite_guard.RANK_HIGH):
            limit = min(limit, config.slot_wait_alert_s())
        if w["stopped"] or w["blocked_until"] or waited <= limit:
            continue
        if w["cwd"] in said:          # within the last REPEAT_S
            continue
        said[w["cwd"]] = now
        others = _labels([o for o in waiters if o is not w])
        out.append({"waiter": w, "waited": waited, "text": (
            f"⏳ {label(w)} has waited {human_duration(waited)} for the suite slot. "
            f"Holder: {_labels(holders) or 'none'}, queued: {others or 'nobody else'}")})
    store[STORE_KEY] = said
    return out
