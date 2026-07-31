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

**Classify — :func:`plan`.** The scanners above only REPORT; they cannot say whether a
dangling row is a one-command fix or needs a human, because they never look past the dead
address itself. :func:`plan` does: for each dangling row in the three
STAMPED-WITH-A-SESSION stores (``inbox.json``'s orchestrator registration,
``telegram-bindings.json``, ``session-ids.json``), it joins the row's dead epoch + wid to
:mod:`chela.roster`'s snapshot of what that address used to be, and asks
:func:`chela.sessions.wid_for_session` — the SAME resolver CMX-82's ``resolve_heal`` uses,
never reimplemented here — whether that session is alive under a new address right now. Two
outcomes: **REVIVABLE** (the session is live elsewhere — the row just needs its address
updated) or **MANUAL** (nothing live claims that session — a human decides, with the exact
``cd <cwd> && CHELA_WID=@N claude --resume <sid>`` one-liner to do it).

⛔ **``scan_*``/``plan`` never write.** Every scanner and :func:`plan` stay pure reports: no
store is mutated, and tmux is never touched — no window is relaunched, spawned, resumed or
killed. ``chela restore`` itself stays read-only by default for exactly that reason.

**The write half — :func:`apply` (CMX-196).** Takes the ``Verdict`` list :func:`plan` already
computed and acts on it, one row at a time: REVIVABLE re-stamps the row at its new, live
address; MANUAL archives the row (:func:`chela.roster.archive`) and only then removes it from
its live store — archive-before-remove so a crash between the two steps loses nothing worse
than a duplicate archive entry, never a silently vanished row. Only called when the CLI is
run with ``--apply``; the bare command is still the pure report above.

⚠️ ``telegram-bindings.json`` stays OUT of it, permanently, not just until this ticket:
``chela-telegram`` owns that file (one in-memory ``BindingRegistry`` per daemon lifetime,
saved from that same object every reconcile tick), so a second load-mutate-save would race it
and silently erase whichever side wrote last. Its rows are classified and reported here;
:func:`apply` reports them too, but never writes to that store — the daemon's own reconcile
tick is what reaps them.
"""
from __future__ import annotations

from dataclasses import dataclass

from chela import epoch, inbox, roster, sessionids, sessions


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


# --- apply: act on a Verdict list — the write half (CMX-196) ---------------------------

LEFT_TO_DAEMON = "left-to-daemon"   # telegram.bindings — never written here, see module docs
REVIVED = "revived"                 # REVIVABLE, re-stamped at its new address
ARCHIVED = "archived"               # MANUAL, archived then removed from its live store
RACED = "raced"                     # the row moved on between plan() and apply() — skipped


@dataclass(frozen=True)
class ApplyResult:
    """What :func:`apply` did with one :class:`Verdict`."""

    verdict: Verdict
    action: str      # LEFT_TO_DAEMON | REVIVED | ARCHIVED | RACED
    detail: str = ""


def _archive_entry(v: Verdict) -> dict:
    return {"store": v.store, "wid": v.wid, "session_id": v.session_id, "cwd": v.cwd,
            "label": v.label, "stamped_epoch": v.stamped_epoch}


def apply(verdicts: list[Verdict], *,
          readdress_orchestrator=inbox.readdress,
          unregister_orchestrator=inbox.unregister_dangling,
          rekey_session=sessionids.rekey,
          remove_session=sessionids.remove,
          archive=roster.archive) -> list[ApplyResult]:
    """Act on every row :func:`plan` classified — REVIVABLE re-stamped, MANUAL archived then
    removed. Only called from ``chela restore --apply``; the bare command never calls this.

    ``telegram.bindings`` rows are reported (:class:`ApplyResult` with ``action ==
    LEFT_TO_DAEMON``) but **never written** — see the module docstring for why: that store
    belongs to ``chela-telegram``'s own in-memory registry, and a second writer here would
    race its next reconcile save.

    Every writer is a DI seam (defaults to the real :mod:`chela.inbox` /
    :mod:`chela.sessionids` / :mod:`chela.roster` calls) so this tests without touching a real
    store. Each default is itself guarded to no-op — reported as ``RACED`` — if the row has
    moved on since :func:`plan` computed it, rather than blindly clobbering whatever is there
    now; see :func:`chela.inbox.readdress` / :func:`chela.inbox.unregister_dangling` /
    :func:`chela.sessionids.rekey` / :func:`chela.sessionids.remove`.

    MANUAL rows are archived BEFORE they are removed, deliberately: a crash between the two
    steps then loses nothing worse than a duplicate archive entry, never a silently vanished
    row with no trace either store still holds.
    """
    out: list[ApplyResult] = []
    for v in verdicts:
        if v.store == "telegram.bindings":
            out.append(ApplyResult(v, LEFT_TO_DAEMON,
                                    "chela-telegram owns telegram-bindings.json; its own "
                                    "reconcile tick reaps this row"))
            continue

        if v.verdict == "REVIVABLE":
            if v.store == "inbox.orchestrator":
                r = readdress_orchestrator(v.wid, v.stamped_epoch, v.new_wid)
                ok = bool(r.get("ok"))
            else:  # session-ids
                ok = rekey_session(v.wid, v.new_wid, v.session_id, v.stamped_epoch)
            out.append(ApplyResult(v, REVIVED if ok else RACED,
                                    f"re-stamped {v.wid} -> {v.new_wid}" if ok else
                                    "the row moved on before it could be re-stamped"))
            continue

        # MANUAL — archive first, remove only after the archive write has landed.
        archive(_archive_entry(v))
        if v.store == "inbox.orchestrator":
            r = unregister_orchestrator(v.wid, v.stamped_epoch)
            ok = bool(r.get("ok"))
        else:  # session-ids
            ok = remove_session(v.wid, v.session_id, v.stamped_epoch)
        out.append(ApplyResult(v, ARCHIVED if ok else RACED,
                                "" if ok else
                                "archived, but the row moved on before it could be removed"))
    return out
