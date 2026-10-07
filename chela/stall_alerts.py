"""Stall alerts (CMX-26) — a held inbox, or a clean PR nobody merges, is no longer silent.

On 2026-10-07 the orchestrator session got NO inbox deliveries for 4h25m. A background
task in it (a watcher whose ``pgrep -f`` exit check matched its own argv) kept
``claude agents --json`` reporting the session ``busy`` between turns, and
:func:`chela.inbox.deliver` — correctly — writes only to an ``idle`` orchestrator. So it
held nine events, among them three judge-clean verdicts: one PR sat clean and mergeable for
about four hours while two others went CONFLICTING. The idle gate was right. What was
missing is that a held queue says NOTHING to the human.

Two edge-triggered pushes, both evaluated inside :func:`chela.inbox.tick`'s store lock
(the state they latch lives in ``inbox.json`` beside the queue it describes) and SENT
outside it (:func:`send` — an HTTP POST must never hold the lock ``chela watch`` needs):

* **held** — the queue's OLDEST event has waited longer than
  ``config.inbox_held_alert_s()``. Once per held EPISODE: an episode ends only when the
  queue drains to empty, so a queue that delivers some events and keeps the rest held is
  still the same episode, and is not announced again.
* **clean-unmerged** — a run in ``awaiting_review`` whose judge-clean verdict was recorded
  on the PR's CURRENT head, unmerged for longer than ``config.clean_unmerged_alert_s()``.
  Once per run. A verdict on a STALE head is not a clean PR — it is a PR the judge has not
  seen yet — and never starts the clock.

**Only the announcer sends (CMX-9).** Gated on :data:`chela.notify._owner`, the same lease
``notify.check_waiting`` uses. A non-holder does not even EVALUATE: the latches live in the
shared store, so a standby that latched without sending would swallow the holder's push.
"""
from __future__ import annotations

import logging

from chela import config, judge, notify
from chela.hold import human_duration

log = logging.getLogger(__name__)

# The store keys this module owns. Absent in an older inbox.json → read as their defaults.
HELD_KEY = "held_alert"
CLEAN_KEY = "clean_unmerged"

HELD_TITLE = "chela: the decisions inbox is held"
CLEAN_TITLE = "chela: a clean PR is waiting to be merged"


def oldest_held(store: dict) -> dict | None:
    """The queue's oldest event that carries a timestamp, or None. ``min`` rather than
    ``queue[0]``: the queue is append-ordered, but an event without a ``ts`` (recorded
    before events were stamped) must not hide one that has one."""
    stamped = [e for e in store.get("queue") or [] if isinstance(e.get("ts"), (int, float))]
    return min(stamped, key=lambda e: e["ts"]) if stamped else None


def describe_event(event: dict) -> str:
    """``run_judge_clean CMX-23`` — what a human needs to recognise the held event."""
    task = (event.get("payload") or {}).get("task_id")
    return f"{event.get('kind') or '?'} {task}" if task else (event.get("kind") or "?")


def _orchestrator_status(store: dict, statuses: dict[str, str]) -> tuple[str, str]:
    """``(who, status)`` of the orchestrator the queue is held for, as this tick sees it."""
    from chela import inbox                          # lazy: inbox imports this module

    wid = inbox.orchestrator_wid(store)
    if wid:
        return wid, statuses.get(wid) or "absent"
    peer = inbox.orchestrator_peer(store)
    if peer:
        return f"pid {peer.get('pid')}", "a windowless peer"
    return "(none)", "unregistered"


def _held(store: dict, statuses: dict[str, str], now: float) -> dict | None:
    state = store.get(HELD_KEY) or {}
    if not store.get("queue"):
        store[HELD_KEY] = None                       # drained: the next held queue is news
        return None
    who, status = _orchestrator_status(store, statuses)
    seen = list(state.get("statuses") or [])
    if status not in seen:
        seen.append(status)
    state["statuses"] = seen
    store[HELD_KEY] = state
    if state.get("alerted"):
        return None                                  # this episode already pushed
    oldest = oldest_held(store)
    if oldest is None:
        return None
    age = now - oldest["ts"]
    if age < config.inbox_held_alert_s():
        return None
    state["alerted"] = True
    n = len(store["queue"])
    how = (f"has been {seen[0]} the whole time" if len(seen) == 1
           else f"has been {'/'.join(seen)} meanwhile")
    return {"title": HELD_TITLE,
            "message": f"chela inbox: {n} notice(s) held for {human_duration(age)} — "
                       f"orchestrator {who} {how}. Oldest: {describe_event(oldest)}."}


def _current_head(run: dict, live_heads: dict[str, str] | None) -> str | None:
    """The PR's head as of THIS tick: the live GitHub read the inbox already made for every
    clean/cannot-verify run (:func:`chela.inbox._live_judge_heads`), else the row's cache."""
    return (live_heads or {}).get(run.get("task_id")) or run.get("pr_head_sha")


def _clean_unmerged(store: dict, runs: list[dict], live_heads: dict[str, str] | None,
                    now: float) -> list[dict]:
    prev: dict = store.get(CLEAN_KEY) or {}
    nxt: dict = {}
    alerts: list[dict] = []
    for run in runs:
        task = run.get("task_id")
        if not task or run.get("status") != "awaiting_review":
            continue                                 # merged / closed / reworking: forget it
        entry = prev.get(task)
        if entry and entry.get("alerted"):
            nxt[task] = entry                        # once per run — even if the head moves
            continue
        judge_sha = run.get("judge_sha")
        head = _current_head(run, live_heads)
        if (run.get("judge_state") != judge.J_CLEAN or not judge_sha or not head
                or judge_sha != head):
            continue                                 # not clean on the head it would merge
        if not entry or entry.get("sha") != judge_sha:
            entry = {"sha": judge_sha, "since": now, "alerted": False}
        nxt[task] = entry
        age = now - entry["since"]
        if age < config.clean_unmerged_alert_s():
            continue
        entry["alerted"] = True
        pr = f" {run['pr_url']}" if run.get("pr_url") else ""
        alerts.append({"title": CLEAN_TITLE,
                       "message": f"{task} has been judge-clean on its current head "
                                  f"({judge_sha[:7]}) for {human_duration(age)} and is still "
                                  f"unmerged.{pr}"})
    store[CLEAN_KEY] = nxt
    return alerts


def evaluate(store: dict, statuses: dict[str, str], runs: list[dict] | None,
             live_heads: dict[str, str] | None, now: float,
             owner: notify.OwnerLock | None = None) -> list[dict]:
    """Latch and return the pushes this tick owes. Called UNDER the inbox store lock;
    the caller sends the result with :func:`send` once the lock is released.

    Nothing is evaluated (and nothing latched) when notifications are off or this process
    is not the announcer — so turning notifications on, or the holder dying and a standby
    taking the lease, still announces an episode that was already under way."""
    if not notify.enabled():
        return []
    if not (owner or notify._owner).acquire():
        return []
    alerts = []
    held = _held(store, statuses, now)
    if held:
        alerts.append(held)
    alerts += _clean_unmerged(store, runs or [], live_heads, now)
    return alerts


def send(alerts: list[dict]) -> None:
    """Publish what :func:`evaluate` latched — OUTSIDE the store lock. ``notify.send``
    swallows its own failures, so a flaky notifier cannot take the inbox tick down."""
    for alert in alerts:
        log.warning("stall alert: %s", alert["message"])
        notify.send(alert["message"], title=alert["title"])
