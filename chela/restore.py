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

**Classify and act — :func:`plan` / :func:`apply`.** The scanners above only REPORT; they
cannot say whether a dangling row is a one-command fix or needs a human, because they never
look past the dead address itself. :func:`plan` does: for each dangling row in the three
STAMPED-WITH-A-SESSION stores (``inbox.json``'s orchestrator registration,
``telegram-bindings.json``, ``session-ids.json``), it joins the row's dead epoch + wid to
:mod:`chela.roster`'s snapshot of what that address used to be, and asks
:func:`chela.sessions.wid_for_session` — the SAME resolver CMX-82's ``resolve_heal`` uses,
never reimplemented here — whether that session is alive under a new address right now. Two
outcomes: **REVIVABLE** (the session is live elsewhere — the row just needs its address
updated) or **MANUAL** (nothing live claims that session — a human decides, with the exact
``cd <cwd> && CHELA_WID=@N claude --resume <sid>`` one-liner to do it). :func:`apply` is the
only thing in this module that writes: it re-stamps REVIVABLE rows in place and ARCHIVES
MANUAL rows into :mod:`chela.roster`'s dead-epoch record before removing them from their
live store — nothing here is ever destroyed without first being written down. Neither
function ever touches tmux itself: no window is relaunched, spawned, resumed, or killed.
"""
from __future__ import annotations

from dataclasses import dataclass

from chela import epoch, roster, sessions


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


# --- classify: REVIVABLE / MANUAL, via chela.roster + sessions.wid_for_session -------

@dataclass(frozen=True)
class Verdict:
    """A dangling row, classified. ``REVIVABLE`` means the recorded Claude session is alive
    under ``new_wid`` right now; ``MANUAL`` means nothing live claims it and a human decides.
    """

    store: str
    wid: str                    # the dangling address the row is stamped with
    stamped_epoch: str | None
    verdict: str                 # "REVIVABLE" | "MANUAL"
    session_id: str | None = None
    new_wid: str | None = None   # set only when verdict == "REVIVABLE"
    cwd: str | None = None       # from the roster join, for the MANUAL one-liner
    label: str = ""

    def manual_command(self) -> str | None:
        """The exact one-liner a human runs to revive a MANUAL row by hand — ``None`` when
        there isn't enough to build one (no cwd, or no session to resume)."""
        if self.verdict != "MANUAL" or not self.cwd or not self.session_id:
            return None
        return f"cd {self.cwd} && CHELA_WID=@N claude --resume {self.session_id}"


def _classify(store: str, wid: str, stamped_epoch: str | None, session_id: str | None,
              now_epoch: str | None, roster_lookup, wid_for_session) -> Verdict | None:
    """One row → a :class:`Verdict`, or ``None`` if it isn't dangling at all.

    ⛔ Session id is the ONLY automatic path: a row whose ``cwd``/``name`` happen to match a
    live window but whose SESSION does not is still MANUAL. Only ``wid_for_session`` — never
    a cwd/name fallback — can turn a row REVIVABLE. Scoped to this module: CMX-194's
    unique-name heal inside ``inbox.resolve_heal`` is a separate, deliberately narrower
    authority this function does not touch or assert anything about.
    """
    if not epoch.is_dangling(stamped_epoch, now_epoch):
        return None
    roster_row = roster_lookup(stamped_epoch, wid) or {}
    sid = session_id or roster_row.get("session_id")
    cwd = roster_row.get("cwd")
    label = roster_row.get("name") or ""
    new_wid = wid_for_session(sid) if sid else None
    if new_wid:
        return Verdict(store, wid, stamped_epoch, "REVIVABLE", sid, new_wid, cwd, label)
    return Verdict(store, wid, stamped_epoch, "MANUAL", sid, None, cwd, label)


def plan(orchestrator: dict, bindings: dict, session_entries: dict,
         now_epoch: str | None, roster_lookup=roster.window,
         wid_for_session=sessions.wid_for_session) -> list[Verdict]:
    """Classify every dangling row across the three session-stamped stores.

    ``orchestrator`` is ``inbox.load()``'s store dict (only its ``orchestrator``/
    ``orchestrator_epoch``/``orchestrator_session`` fields are read); ``bindings`` is
    ``{window_id: stamped_epoch}`` from ``telegram-bindings.json``; ``session_entries`` is
    :func:`chela.sessionids.entries`. ``roster_lookup`` and ``wid_for_session`` are DI seams
    (default to the real :mod:`chela.roster` / :mod:`chela.sessions`) so this tests without
    tmux, ``/proc``, or a live roster file.

    ``now_epoch`` unknown (``None``) classifies NOTHING and writes nothing — the same
    two-known-halves rule :func:`chela.epoch.is_dangling` itself follows: an unverifiable
    epoch is not license to guess.
    """
    if not now_epoch:
        return []
    out: list[Verdict] = []
    wid = orchestrator.get("orchestrator")
    if wid:
        v = _classify("inbox.orchestrator", wid, orchestrator.get("orchestrator_epoch"),
                      orchestrator.get("orchestrator_session"), now_epoch,
                      roster_lookup, wid_for_session)
        if v:
            out.append(v)
    for bwid, bepoch in sorted((bindings or {}).items()):
        v = _classify("telegram.bindings", bwid, bepoch, None, now_epoch,
                      roster_lookup, wid_for_session)
        if v:
            out.append(v)
    for swid, meta in sorted((session_entries or {}).items()):
        v = _classify("session-ids", swid, meta.get("epoch"), meta.get("session_id"),
                      now_epoch, roster_lookup, wid_for_session)
        if v:
            out.append(v)
    return out


def apply(verdicts: list[Verdict], now_epoch: str | None) -> dict:
    """Act on a :func:`plan`: re-stamp REVIVABLE rows, archive-then-remove MANUAL ones.

    The only tmux-facing thing here is reading the current bindings file — no window is
    relaunched, spawned, resumed, or killed (the ticket's hard boundary; see the module
    docstring). MANUAL rows are archived into :func:`chela.roster.archive` BEFORE they are
    removed from their live store, so a row this drops is never lost, only moved.
    """
    from chela import inbox, sessionids
    from chela.telegram.bindings import BindingRegistry

    revived: list[Verdict] = []
    archived: list[Verdict] = []
    bindings_reg = None
    bindings_dirty = False

    def _bindings():
        nonlocal bindings_reg
        if bindings_reg is None:
            bindings_reg = BindingRegistry.load()
        return bindings_reg

    for v in verdicts:
        if v.verdict == "REVIVABLE":
            if v.store == "inbox.orchestrator":
                inbox.register(v.new_wid)
            elif v.store == "telegram.bindings":
                reg = _bindings()
                thread = reg.thread_for_window(v.wid)
                if thread is not None:
                    reg.bind(v.new_wid, thread, epoch=now_epoch)
                    bindings_dirty = True
            elif v.store == "session-ids":
                sessionids.remove(v.wid)
                if v.session_id:
                    sessionids.set_session_id(v.new_wid, v.session_id)
            revived.append(v)
        elif v.verdict == "MANUAL":
            roster.archive(v.stamped_epoch, v.wid,
                           {"store": v.store, "session_id": v.session_id,
                            "cwd": v.cwd, "label": v.label})
            if v.store == "inbox.orchestrator":
                inbox.unregister(v.wid)
            elif v.store == "telegram.bindings":
                if _bindings().unbind(v.wid):
                    bindings_dirty = True
            elif v.store == "session-ids":
                sessionids.remove(v.wid)
            archived.append(v)

    if bindings_dirty:
        _bindings().save()
    return {"revived": revived, "archived": archived}
