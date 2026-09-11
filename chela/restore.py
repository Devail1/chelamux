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
  is a side effect of one consumer, not a report: a still-open run row keeps whatever status
  it had (often ``running``) forever, with no flag anywhere saying its window address is
  dead. CMX-261: a row whose trial is already OVER (:func:`chela.dispatcher.run_is_terminal`)
  is excluded from this scan entirely — ``task-finished`` kills the window as a run's last
  step, so a closed row's dangling stamp is the expected, permanent shape of every completed
  task, not something a hard tmux death left behind.
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
address; MANUAL archives the row (:func:`chela.roster.archive`, into its own
``roster-archive.json`` — never ``roster.json`` itself, which the reconcile tick's
:func:`chela.roster.record` writes unconditionally every tick and would otherwise race) and
only then removes it from its live store — archive-before-remove so a crash between the two
steps loses nothing worse than a duplicate archive entry, never a silently vanished row. Only
called when the CLI is run with ``--apply``; the bare command is still the pure report above.

⚠️ ``telegram-bindings.json`` stays OUT of it, permanently, not just until this ticket:
``chela-telegram`` owns that file (one in-memory ``BindingRegistry`` per daemon lifetime,
saved from that same object every reconcile tick), so a second load-mutate-save would race it
and silently erase whichever side wrote last. Its rows are classified and reported here;
:func:`apply` reports them too, but never writes to that store — the daemon's own reconcile
tick is what reaps them.

**The narrow write half — :func:`retire_empty` (CMX-323).** ``--apply`` is all-or-nothing:
it re-stamps every REVIVABLE row AND archives-then-removes every MANUAL one, whether or not
that MANUAL row still carries a ``cd ... && claude --resume ...`` one-liner a human might
still act on. After a hard-enough death (two WSL distro teardowns in one day, live 2026-08-29)
the roster join comes up empty for almost every row — no cwd, no session, nothing
:func:`Verdict.manual_command` can build a command from — and that "nothing on record" case
is a DIFFERENT thing from a MANUAL row with a relaunch command sitting unread: it is not work
anyone can act on, it is bookkeeping residue that a hard death left behind. :func:`retire_empty`
is the reviewed, narrower complement: it retires ONLY the rows :func:`plan` already classified
MANUAL *and* for which ``manual_command()`` is ``None`` — never a REVIVABLE row, and never a
MANUAL row that still carries a cwd or a session, which is left completely alone for a human to
read and decide on (or for a later ``--apply``). It reuses :func:`apply`'s own per-store
writers verbatim — same archive-before-remove ordering, same RACED guard, same permanent
``telegram-bindings.json`` exclusion — so a retired row is retired by the EXACT path
``--apply`` already uses, never a second reimplementation of it.

⚖️ **Why this does not also reach ``inbox.watches`` or the dispatcher's ``runs`` table.**
Both are scanned (:func:`scan_watches` / :func:`scan_runs`) but neither is ever classified
into a :class:`Verdict` by :func:`plan` — they carry no session identity of their own to join
against the roster, only a note or a task id. :func:`retire_empty`, like :func:`apply` before
it, operates on :class:`Verdict` objects, so it cannot reach either store even in principle;
extending classification to them is a bigger, separate decision, not folded in here. And for
``runs`` specifically the standing rule is ARCHIVE-never-DELETE for a different reason: a
run row is a task's history, kept even once its window is long gone (see
:func:`scan_runs`'s own docstring on why a *terminal* row's dangling stamp is excluded, not
retired) — where a session-ids or inbox-orchestrator row is pure address bookkeeping with no
history value once nothing live claims it. That asymmetry is why the same "nothing on record"
shape gets a retire path here but must not be read as license to bulk-delete a run row too.
"""
from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass

from chela import dispatcher, epoch, inbox, resume_state, roster, sessionids, sessions, spawn as spawn_mod


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

    ⛔ A row whose trial is already OVER (:func:`chela.dispatcher.run_is_terminal` —
    merged, ``done``, or ``failed`` past every retry) is skipped even when its window
    stamp is dangling. ``chela task-finished`` kills the agent's tmux window as the LAST
    step of a run, so a closed row's window address is EXPECTED to go dead the moment the
    tmux epoch it was stamped under rolls over — forever, on every row chela has ever
    dispatched. Counting those permanently and reporting them as something ``chela
    restore`` can act on is a false positive with no fix: neither :func:`plan` nor
    :func:`apply` has ever classified this store (see the module docstring), so a
    terminal row's dangling stamp can never be resolved by running the very command this
    scan tells the operator to run.
    """
    out = []
    for row in runs or []:
        if dispatcher.run_is_terminal(row):
            continue
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
KEPT = "kept"                       # retire_empty() only — see below


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


# --- resume: the launch half (CMX-350/issue #457, liveness+retry-bound CMX-353/#468) ------

RESUMED = "resumed"                 # MANUAL, `claude --resume` relaunched successfully
RESUME_FAILED = "resume-failed"     # MANUAL, eligible, but the launch itself failed
SKIPPED = "skipped"                 # MANUAL, eligible, but a guard refused to launch it


# How `_default_check_resumed` waits for a freshly relaunched session to prove itself
# genuinely alive, not just momentarily present.
#
# ⚠️ Measured in the world (CMX-353's own `Verify` step), not guessed: `claude --resume
# <a session id claude has never heard of>` prints "No conversation found ..." and EXITS —
# but not instantly. Sending `claude --resume <bogus>` via `spawn_window` and polling
# `sessions.wid_for_session` every 0.25s showed the doomed process's OWN `--resume <sid>`
# command line (the exact evidence `wid_claiming_session`/`wid_for_session` trust) reads as
# alive from roughly t=0.5s to t=1.9s after the spawn returns, then gone for good by t=2.0s.
# A check that returns on the FIRST sighting of that command line — which is what an
# eyes-closed poll-until-true does — reads that doomed process's brief startup window as
# "resumed", the exact false positive this function exists to rule out one layer down from
# the CMX-350 defect it fixes. So this waits out `_LIVENESS_SETTLE_S` (comfortably past that
# measured window) BEFORE looking at all, then requires `_LIVENESS_CONFIRMATIONS` CONSECUTIVE
# sightings spaced `_LIVENESS_DELAY_S` apart — a session that is genuinely alive (an
# interactive `claude` sitting at its own prompt) stays alive across that whole span; one
# that already exited never re-appears to restart the streak.
_LIVENESS_SETTLE_S = 2.0
_LIVENESS_ATTEMPTS = 5
_LIVENESS_DELAY_S = 0.75
_LIVENESS_CONFIRMATIONS = 2


def _default_check_resumed(wid: str | None, session_id: str, *,
                            attempts: int = _LIVENESS_ATTEMPTS,
                            delay: float = _LIVENESS_DELAY_S,
                            settle: float = _LIVENESS_SETTLE_S,
                            confirmations: int = _LIVENESS_CONFIRMATIONS,
                            sleep=time.sleep) -> bool:
    """Issue #468's liveness half: is ``session_id`` ACTUALLY running at ``wid`` right now,
    not merely "did a tmux window get created there" — and not merely "did a doomed process
    briefly exist there" (see the module-level comment above for how that second trap was
    found and measured, not assumed).

    Reads :func:`chela.sessions.wid_for_session` — the same evidence :func:`plan` itself
    trusts to call a session REVIVABLE elsewhere (the pane's own ``claude --resume <sid>``
    command line, or the event log bounded by the process's own start time) — forcing a
    fresh pane read every time (``force=True``) so this never trusts the 1s cache a
    concurrent caller may have just populated before the resumed process even started.

    ``wid`` falsy (a tmux build that echoed no id) returns ``False`` immediately — the
    "UNKNOWN MUST NOT READ AS OK" rule this whole module's docstring opens with: without an
    address there is nothing this can confirm the session is running at.
    """
    if not wid:
        return False
    sleep(settle)
    streak = 0
    for i in range(attempts):
        if i:
            sleep(delay)
        alive = sessions.wid_for_session(session_id, sessions.panes(force=True)) == wid
        streak = streak + 1 if alive else 0
        if streak >= confirmations:
            return True
    return False


def _default_kill_window(wid: str) -> None:
    """Issue #473: close a window :func:`resume` itself just opened, after its liveness
    check concludes ``claude --resume`` never came up alive in it.

    Targets ``wid`` alone (a bare ``tmux kill-window -t @N`` — window ids are unique across
    an entire tmux server, so no session prefix is needed, and none is threaded through
    here to construct one from). Best-effort, exactly like the dispatcher's own
    ``_kill_window``: a tmux that is already gone, or a window that already closed itself,
    is not this function's problem to raise about — the row is already being reported
    ``resume-failed`` regardless of whether this cleanup succeeds.
    """
    subprocess.run(["tmux", "kill-window", "-t", wid], capture_output=True)


def _task_in_flight(wid: str, runs: list[dict]) -> str | None:
    """The ``task_id`` of a ``dispatcher.runs`` row that STILL claims ``wid`` — as either
    its agent or its judge window — with an ACTIVE status (``claimed``/``running``), or
    ``None`` if nothing does.

    The CMX-282/#353 lesson (issue #457's guard): a run the dispatcher itself still
    considers in flight is the dispatcher's own reconcile loop to reap or retry, not this
    command's to relaunch out from under it — a `Login expired` pane taught that lesson
    once already, and a manual `claude --resume` racing the SAME window a live reconcile
    tick is about to touch is the identical shape one layer up.
    """
    for row in runs or []:
        status = row.get("status")
        if status not in dispatcher.ACTIVE_STATUSES:
            continue
        if str(row.get("window_id") or "") == wid or str(row.get("judge_window_id") or "") == wid:
            return row.get("task_id")
    return None


def resume(verdicts: list[Verdict], runs: list[dict] | None = None, *,
           spawn_window=None,
           register_orchestrator=None,
           check_resumed=None,
           resume_blocked=None,
           record_resume_failure=None,
           clear_resume_failure=None,
           kill_window=None,
           **apply_kwargs) -> list[ApplyResult]:
    """CMX-350/issue #468: actually relaunch a MANUAL row's dead agent — the one write path
    in this module that starts a NEW process rather than editing a JSON store.

    ⛔⛔ **The counterweight, and the one that matters (issue #457).** A REVIVABLE verdict
    is NEVER handed to ``spawn_window`` — that session is already confirmed alive under
    ``new_wid`` (:func:`plan`'s own ``wid_for_session`` check), so relaunching it would
    fork a live agent into two processes racing one worktree, the exact CMX-346 failure
    mode. REVIVABLE (and ``telegram.bindings``, and a MANUAL row with nothing on record)
    are delegated to :func:`apply` UNCHANGED — the exact same re-stamp/archive/left-to-
    daemon paths ``--apply`` uses, never a second reimplementation.

    A MANUAL row is only ever launched when ALL of:

    * it carries a complete ``(cwd, session_id)`` — :meth:`Verdict.manual_command` is not
      ``None``. A row missing either is routed to :func:`apply`, which archives it exactly
      like :func:`retire_empty` — never launched against a guessed path.
    * its ``session_id`` matches :data:`chela.sessions.SESSION_RE` — defense in depth
      against sending anything else through a live shell pane (the launch command is sent
      via tmux `send-keys`, which the pane's own shell then parses).
    * its ``(session_id, stamped_epoch)`` has not already been resumed EARLIER IN THIS SAME
      CALL — the three stores :func:`plan` reads can independently carry the same session
      (the orchestrator's own session is often stamped in both ``inbox.json`` and
      ``session-ids.json``); a second sighting is a duplicate address for a session this
      call just relaunched, not a second dead agent. This is the "never resume more than
      once per (session_id, epoch)" bound from issue #457.
    * no ``dispatcher.runs`` row still marks a task ACTIVE against this exact dangling
      window (:func:`_task_in_flight`) — the CMX-282 lesson.
    * its ``session_id`` is not already BLOCKED by an earlier resume of it that did not
      come up alive — :func:`chela.resume_state.blocked_reason`, checked BEFORE anything
      is spawned. This is issue #468's durable half of the retry bound: unlike the
      in-process ``resumed_sessions`` dedup below (which only knows about THIS call),
      this reads a count :func:`chela.resume_state.record_failure` persisted to disk by a
      PRIOR ``chela restore --resume`` invocation, so a session that keeps coming up dead
      stops being relaunched after :data:`chela.resume_state.MAX_TRIES` attempts instead
      of forever.

    A row that fails any of these is reported :data:`SKIPPED` with why, and is left
    completely untouched (never archived, never removed) — a human still sees it plainly
    on the next ``chela restore``.

    ⛔⛔ **The other counterweight (issue #468): "window created" is not "agent running".**
    A launch that opens a tmux window and sends the resume command is only reported
    :data:`RESUMED` — and only then archived/removed — once :func:`check_resumed` confirms
    ``session_id`` is genuinely alive at the freshly spawned address (defaults to
    :func:`_default_check_resumed`, which polls :func:`chela.sessions.wid_for_session` — the
    same evidence :func:`plan` itself trusts to call a session REVIVABLE elsewhere). A
    launch whose window came up but whose session never did is reported
    :data:`RESUME_FAILED`, the row is left exactly as it was (never archived, never
    removed — the CMX-350 defect this closes), and the failure is persisted via
    :func:`record_resume_failure` so the NEXT ``chela restore --resume`` sees it as blocked
    rather than relaunching the same dead session again. A genuinely successful resume
    clears any prior failure record for that session (:func:`clear_resume_failure`) so a
    LATER, unrelated death of the same session starts its own retry count from zero.

    ⛔⛔ **Issue #473's counterweight to the counterweight: a failed liveness check must also
    close the window it just opened** (:func:`_default_kill_window`, targeting ``result.wid``
    — the exact id :func:`spawn_window` handed back, never a name lookup or a "most recent
    window" guess), or ``MAX_TRIES`` turns every permanently-failing session into a
    permanently-leaked bare ``bash`` window that ``chela status`` counts as a fleet member.
    The branch immediately above — ``spawn_window`` itself failing — opened no window and
    must keep closing nothing. And a launch that DOES come up alive must never have its
    window closed here; that window is the entire point of this function.

    On a successful, VERIFIED-ALIVE launch the row is archived (:func:`chela.roster.archive`)
    exactly as :func:`apply` does for MANUAL rows, then either re-registered
    (``inbox.orchestrator`` — via ``register_orchestrator`` at the freshly spawned wid, so
    the inbox comes back armed in the same pass) or removed from ``session-ids.json``
    (``remove_session``, an ``apply_kwargs`` DI seam, same as :func:`apply`).

    ``**apply_kwargs`` forwards to :func:`apply` for the delegated subset (same DI seam,
    same reason) and supplies ``archive``/``remove_session`` for the resumed subset here.

    ``spawn_window``/``register_orchestrator``/``check_resumed``/``resume_blocked``/
    ``record_resume_failure``/``clear_resume_failure``/``kill_window`` all default to ``None``
    and are resolved to their real implementations INSIDE the function body (rather than bound at
    import time, the way :func:`apply`'s pure-JSON writers are) — every one of them touches
    tmux, the filesystem, or both, so a caller needs to be able to fake just these leaves the
    way ``tests/test_restore_cli.py``'s own ``live_stores`` fixture fakes every OTHER
    tmux-touching call, without also stubbing the pure store writers around it.

    Returns one :class:`ApplyResult` per input verdict, same order, same contract as
    :func:`apply`/:func:`retire_empty`.
    """
    if spawn_window is None:
        spawn_window = spawn_mod.spawn_window
    if register_orchestrator is None:
        register_orchestrator = inbox.register
    if check_resumed is None:
        check_resumed = _default_check_resumed
    if resume_blocked is None:
        resume_blocked = resume_state.blocked_reason
    if record_resume_failure is None:
        record_resume_failure = resume_state.record_failure
    if clear_resume_failure is None:
        clear_resume_failure = resume_state.clear
    if kill_window is None:
        kill_window = _default_kill_window
    archive = apply_kwargs.get("archive", roster.archive)
    remove_session = apply_kwargs.get("remove_session", sessionids.remove)

    eligible = [v for v in verdicts
                if v.store != "telegram.bindings" and v.verdict == "MANUAL"
                and v.manual_command() is not None]
    eligible_ids = {id(v) for v in eligible}
    rest = [v for v in verdicts if id(v) not in eligible_ids]
    rest_results = iter(apply(rest, **apply_kwargs))

    resumed_sessions: set[tuple[str, str | None]] = set()
    resumed_by_id: dict[int, ApplyResult] = {}
    for v in eligible:
        if not sessions.SESSION_RE.match(v.session_id or ""):
            resumed_by_id[id(v)] = ApplyResult(
                v, SKIPPED, "session id does not look like a session id — refusing to "
                            "launch it")
            continue

        dedup_key = (v.session_id, v.stamped_epoch)
        if dedup_key in resumed_sessions:
            resumed_by_id[id(v)] = ApplyResult(
                v, SKIPPED, f"session {v.session_id} was already resumed earlier in this "
                            "pass")
            continue

        task_id = _task_in_flight(v.wid, runs)
        if task_id:
            resumed_by_id[id(v)] = ApplyResult(
                v, SKIPPED, f"task {task_id} is still ACTIVE in the dispatcher — refusing "
                            "to relaunch out from under it")
            continue

        blocked = resume_blocked(v.session_id)
        if blocked:
            resumed_by_id[id(v)] = ApplyResult(
                v, SKIPPED, f"resume already failed for session {v.session_id} — not "
                            f"retrying: {blocked}")
            continue

        result = spawn_window(v.cwd, command=f"claude --resume {v.session_id}")
        if not result.ok:
            record_resume_failure(v.session_id, result.error or "spawn failed")
            resumed_by_id[id(v)] = ApplyResult(v, RESUME_FAILED, result.error or "")
            continue

        if not check_resumed(result.wid, v.session_id):
            reason = (f"window {result.wid or '?'} opened but claude --resume "
                      f"{v.session_id} never came up alive")
            record_resume_failure(v.session_id, reason)
            # issue #473: the window this call just opened never came up alive — close it
            # so it does not sit around as a bare `bash` shell `chela status` reports as a
            # fleet member. Only when `spawn_window` actually returned an id: a `result.wid`
            # of `None` means there is nothing this call can safely address (see
            # `_default_kill_window`'s docstring — no name lookup, no "most recent" guess).
            if result.wid:
                kill_window(result.wid)
            resumed_by_id[id(v)] = ApplyResult(v, RESUME_FAILED, reason)
            continue

        resumed_sessions.add(dedup_key)
        clear_resume_failure(v.session_id)
        archive(_archive_entry(v))
        if v.store == "inbox.orchestrator":
            ok = bool(result.wid) and bool(register_orchestrator(result.wid).get("ok"))
            detail = (f"resumed at {result.wid}, orchestrator re-registered" if ok else
                      f"resumed at {result.wid or '?'}, but re-registering the "
                      "orchestrator failed")
        else:
            ok = remove_session(v.wid, v.session_id, v.stamped_epoch)
            detail = f"resumed at {result.wid or '?'}" + (
                "" if ok else " (row moved on before it could be removed)")
        resumed_by_id[id(v)] = ApplyResult(v, RESUMED if ok else RACED, detail)

    out: list[ApplyResult] = []
    for v in verdicts:
        if id(v) in resumed_by_id:
            out.append(resumed_by_id[id(v)])
        else:
            out.append(next(rest_results))
    return out


def retire_empty(verdicts: list[Verdict], **apply_kwargs) -> list[ApplyResult]:
    """The narrow write half (CMX-323): archive-then-remove ONLY the MANUAL rows with
    NOTHING on record — no cwd, no session, so :func:`Verdict.manual_command` cannot even
    offer a relaunch one-liner. Every other row (REVIVABLE, or MANUAL with a cwd/session
    still attached) is reported back with outcome :data:`KEPT` and left byte-for-byte
    untouched — this never re-stamps a REVIVABLE row and never removes a MANUAL row a human
    could still act on. See the module docstring for why this stays scoped to what
    :func:`plan` classifies (never ``inbox.watches`` or the dispatcher's ``runs`` table).

    Retiring itself is delegated to :func:`apply` — called with ONLY the filtered subset —
    so a retired row is archived/removed by the exact same writers, same ordering, same
    RACED guard, and same permanent ``telegram-bindings.json`` exclusion ``--apply`` uses.
    ``**apply_kwargs`` forwards straight to it (the same DI seam, for the same reason).

    Returns one :class:`ApplyResult` per input verdict, same order, exactly like
    :func:`apply` — callers that zip verdicts against results do not need to know which
    write path produced them.
    """
    targets = [v for v in verdicts
               if v.verdict == "MANUAL" and v.manual_command() is None]
    target_ids = {id(v) for v in targets}
    results = iter(apply(targets, **apply_kwargs))
    return [next(results) if id(v) in target_ids else ApplyResult(v, KEPT) for v in verdicts]
