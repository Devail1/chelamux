"""``chela restore`` — every epoch-stamped row a hard tmux death orphaned, in one report.

**The gap this closes.** :mod:`chela.epoch` solved DETECTION — a stamped ``@N`` from a dead
tmux server reads as dangling, never as a live address (CMX-77). CMX-82 then closed the
loop for exactly one row: the *orchestrator's own* registration in ``inbox.json``, which
:func:`chela.inbox.resolve_heal` re-resolves from its recorded session identity the moment
it is seen live under a new address. Every OTHER epoch-stamped row chela writes has neither
half of that: nothing checks it against the running epoch on its own initiative, and nothing
ever re-resolves it. After a hard tmux death (an OOM took the server on 2026-07-14) those
rows just sit there — correct-looking, permanently unverifiable, and invisible unless a
human happens to go looking at the exact right file.

**Three stores, none of them self-reporting:**

* ``inbox.json`` ``watches`` — windows *other than* the orchestrator that something asked
  to be told about. ``chela watching`` already flags a dangling one in its own listing, but
  only to whoever thinks to run it; the row itself is never surfaced anywhere else and never
  removed.
* the dispatcher's ``runs`` table — ``window_id``/``window_epoch`` and
  ``judge_window_id``/``judge_window_epoch``. :func:`chela.telegram.reconcile.dispatched_window_ids`
  already treats a dangling one as "not dispatched" for Telegram-binding purposes, but that
  is a side effect of one consumer, not a report: the run row itself keeps whatever status
  it had (often ``running``) forever, with no flag anywhere saying its window address is
  dead.
* ``session-ids.json`` — ``wid -> {session_id, epoch}``. :func:`chela.sessionids.session_id_for`
  quietly returns ``None`` for a dangling entry; the entry itself is never listed, counted,
  or cleaned.

**This module only reports.** It is bookkeeping, not remediation: no store is written, no
window is killed, and — critically — nothing here relaunches, spawns, or resumes an agent.
Each store's own consumer already knows how to treat a dangling row as unusable; what was
missing was a single place a human (or a future automated reconciler) can look to see every
row across the fleet that a hard death left behind, instead of needing to know all three
files exist and go check each one by hand.

Every scanner here is pure — data in, :class:`Orphan` list out — so this tests without a
live tmux server or sqlite file, the same shape as
:func:`chela.telegram.reconcile.dispatched_window_ids`.
"""
from __future__ import annotations

from dataclasses import dataclass

from chela import epoch


@dataclass(frozen=True)
class Orphan:
    """One epoch-stamped row whose stamp no longer matches the running tmux server."""

    store: str          # which of the three stores this row lives in
    wid: str             # the dangling window id the row is stamped with
    label: str           # a human-readable identifier for the row (note/task/session id)
    stamped_epoch: str | None


def scan_watches(watches: dict, now_epoch: str | None) -> list[Orphan]:
    """Dangling entries in ``inbox.json``'s ``watches`` — everything ``chela watch`` queued
    interest in that is NOT the orchestrator's own registration (that row self-heals via
    CMX-82's ``resolve_heal`` and is deliberately out of scope here).
    """
    out = []
    for wid, meta in sorted((watches or {}).items()):
        stamped = meta.get("epoch")
        if epoch.is_dangling(stamped, now_epoch):
            label = meta.get("note") or meta.get("name") or ""
            out.append(Orphan("inbox.watches", wid, label, stamped))
    return out


def scan_runs(runs: list[dict], now_epoch: str | None) -> list[Orphan]:
    """Dangling ``window_id``/``judge_window_id`` stamps on dispatcher run rows.

    A row can orphan on either half independently — an agent's window can die with the
    server while its judge has not spawned yet, or vice versa — so both are checked, and a
    row that orphans on both surfaces twice, once per address.
    """
    out = []
    for row in runs or []:
        task = row.get("task_id") or "?"
        wid = str(row.get("window_id") or "").strip()
        if wid and epoch.is_dangling(row.get("window_epoch"), now_epoch):
            label = f"{task} ({row.get('status') or '?'})"
            out.append(Orphan("dispatcher.runs", wid, label, row.get("window_epoch")))
        jwid = str(row.get("judge_window_id") or "").strip()
        if jwid and epoch.is_dangling(row.get("judge_window_epoch"), now_epoch):
            label = f"{task} judge ({row.get('judge_state') or '?'})"
            out.append(Orphan("dispatcher.runs (judge)", jwid, label, row.get("judge_window_epoch")))
    return out


def scan_session_ids(entries: dict, now_epoch: str | None) -> list[Orphan]:
    """Dangling entries in ``session-ids.json`` — session identities pinned to a ``wid``
    that has since been reissued to (or vacated by) a different server.
    """
    out = []
    for wid, meta in sorted((entries or {}).items()):
        stamped = meta.get("epoch")
        if epoch.is_dangling(stamped, now_epoch):
            out.append(Orphan("session-ids", wid, meta.get("session_id") or "", stamped))
    return out


def scan_all(watches: dict, runs: list[dict], session_entries: dict,
             now_epoch: str | None) -> list[Orphan]:
    """Every orphan across all three stores, in the order a human reads them."""
    return (scan_watches(watches, now_epoch)
            + scan_runs(runs, now_epoch)
            + scan_session_ids(session_entries, now_epoch))
