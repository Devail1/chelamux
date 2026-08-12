"""Decisions inbox — the orchestration loop's missing half.

An orchestrator agent is a Claude Code session: it can only act when a human
messages it, or when a background task it started exits. So an agent FINISHING is
structurally invisible to it — on 2026-07-13 an agent was dispatched, finished, and
nothing told the orchestrator; it polled the pane, and the human became the message
bus ("he's done"). This closes that loop: agent/run events are pushed straight into
the orchestrator's session, so it wakes up and acts.

**Push, gated on idle.** An event is delivered — peer socket first
(:func:`chela.messenger.send_peer`), :func:`chela.messenger.send_tmux` as fallback —
ONLY when the orchestrator's window is ``idle``. Otherwise it queues (durably) and
goes out on the next idle tick. Two rules make that safe:

* ``idle`` is checked STRICTLY — ``not busy`` is not good enough. A ``waiting``
  session is sitting on a permission/question prompt, and pasting into it would
  ANSWER THE GATE with our notification. Only a session that is genuinely idle at
  its prompt is ever written to.
* ⛔ **And ``idle`` is not "the prompt will treat this as prose".** The status
  authority models whether a session is THINKING; it says nothing about the INPUT
  MODE its prompt is in. On 2026-07-15 the orchestrator's idle pane was in ``!``
  bash-input mode, this inbox typed a notification into it, and ``/bin/bash`` RAN
  IT — surviving only because the summary contained ``(rework 1)`` and died on the
  parens. The mode is now read off the TUI and an unsafe one is REFUSED
  (:func:`chela.messenger.refuses_paste`, which holds the event in the queue), and —
  because a refusal is a guess about somebody else's pane, while the text is the
  thing we control — every summary is neutralised (:func:`_event`) so it cannot
  execute in ANY mode. Two independent layers, because the chain is fully
  agent-controlled: an agent writes a PR title, this builds a summary from it, this
  types it at the prompt of the one session with merge authority and a real shell.
* The orchestrator is identified EXPLICITLY (:func:`orchestrator_wid`) — registered
  by the orchestrator itself via ``chela watch``, or pinned with
  ``$CHELA_ORCHESTRATOR_WID``. It is never guessed, so a notification can never land
  in a random agent's session. With no registration the whole feature is inert.

**Watches — why busy→idle alone is unusable.** EVERY agent turn ends busy→idle,
including the orchestrator's own replies to the human, so firing on the raw
transition would spam the orchestrator into a loop. Events are therefore scoped to
work the orchestrator actually DELEGATED: it registers interest when it dispatches
(``chela watch @3 --note "fix the parser"``), and only watched windows can produce
agent events. This also covers ad-hoc ``tmux send-keys`` dispatches — which are how
an orchestrator really works, are not dispatcher runs, and have no run-state event
of their own. The watch is cleared when the completion fires, so one dispatch yields
exactly one "finished".

**A gone window is not a dead agent.** An agent that finishes normally EXITS, and its
tmux window goes away — so "window gone" was reporting every SUCCESSFUL dispatch as
``DIED mid-task``, in the same tick that the run's ``awaiting_review`` event went out.
A vanished window is now corroborated against the runs DB (:func:`_gone_event`) before
any claim is made: settled run → the window was meant to go (silent, watch cleared);
still running with no PR → a genuine death, reported loudly; no run row at all → the
outcome is honestly reported as unknown.

**The loop cannot run away.** Delivering a push makes the orchestrator busy and then
idle — which is precisely the transition that would re-trigger. It can't: the
orchestrator's own window is excluded from the event scan, and :func:`watch` refuses
to watch it, so no busy→idle of the orchestrator is ever an event source. Events
originate only from OTHER windows the orchestrator explicitly asked about, and from
the runs DB.

**An event is a RECORD, not a sentence.** Each event carries a ``kind``, a one-line
``summary`` (what the tmux push renders) and a structured ``payload`` (run id, window,
PR url, task title, timestamps). It used to be a single pre-rendered ``text`` string
built at queue time, which had two consequences, both observed live on 2026-07-13:
the notification was the *entire* TODO item pasted into the orchestrator's window
(``title`` on a run row is the whole ``- [ ]`` line), and nothing downstream could
filter, re-check or re-render it — a string is not a fact. The summary is for the
human/orchestrator; the payload is for the log and the UI that will consume it next.

**`@0` IS AN ADDRESS, NOT AN IDENTITY — so every persisted one carries its tmux EPOCH.**
On 2026-07-14 an OOM killed the tmux server. The fleet came back renumbered (the
orchestrator went ``@0`` → ``@6``) and this file still read ``{"orchestrator": "@0"}``. The
inbox queued five ``run_review`` notifications addressed to a window that no longer existed
and delivered NONE of them — in complete silence, because the idle gate reads
``statuses.get(orch) != IDLE`` and a dead address is simply *absent* from the status map, so
it never opened. No error, no warning, no log line; ``chela doctor`` green 14/14. Five
finished PRs sat unreviewed until a human noticed. Now the orchestrator's address, every
watch and every queued event is stamped with :func:`chela.epoch.current`, an address that
did not come from the running tmux server is **never acted on** (it names a stranger's
window now — a wrong wid is worse than no wid, CMX-48), and being undeliverable is
**LOUD**: an ``ERROR`` every tick, a durable ``inbox_undeliverable`` event, a phone
notification, and a red ``chela doctor`` (``inbox.address``). The orchestrator re-registers
with ``chela watch`` — which is what any dispatch already does.

**AND IT NOW SELF-HEALS — an address is not an identity, so it re-resolves from one.** CMX-77
made a renumbered address LOUD, but the recovery still waited on a human (or the orchestrator's
next dispatch) to re-run ``chela watch``: the inbox was keyed on a bare ``@N`` with nothing to
re-resolve it from. That is the 4th face of one bug — an address used as a key — that CMX-48
(the event log), CMX-70 (the relay) and CMX-77 (the epoch) each fixed in one other consumer.
The last one is fixed the same way: the orchestrator's stable claude SESSION id is recorded at
registration (:func:`_identity_of`), and when the address rots the tick re-resolves it to the
window running that session TODAY (:func:`resolve_heal` → :func:`chela.sessions.wid_for_session`,
the same wid↔session evidence CMX-48/70/77 trust) and re-points itself — no human, no lost
queue. It is still never a guess: an identity that cannot be re-resolved to a live window leaves
the address exactly as dangling-and-loud as CMX-77 left it. Recovery is announced once
(:func:`_announce_heal`); the held queue then flows on the next idle tick.

**A queued event is a claim about the PAST, so it is re-checked at DELIVERY time.**
Delivery is deliberately deferred until the orchestrator is idle, and the world moves
in the meantime: an ``awaiting_review`` event was delivered *after* its PR had already
been reviewed and merged, handing the orchestrator work that was already done. Before
a run event goes out it is re-validated against the CURRENT runs DB
(:func:`stale_reason`) and dropped — loudly, in the log — if the fact it asserts no
longer holds. The check is a lookup in the runs list the tick already fetched, so it
costs no I/O and holds no lock across one.

State (watches, the queue, and the run-status marks) lives in one JSON file under
``$CHELA_DIR`` so a daemon restart neither loses a pending event nor re-fires an old
one. Turn the whole thing off with ``CHELA_INBOX_ENABLED=false``.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

from chela import agent_manager, discovery, epoch, event_log, judge, messenger, notify, sessions, transcripts
from chela import config
from chela.config import INBOX_ENABLED
from chela.tui_text import sanitize_prompt

log = logging.getLogger(__name__)

# Statuses `claude agents --json` reports (see agent_manager.session_status_map).
BUSY, IDLE, WAITING = "busy", "idle", "waiting"

# How many events go out per tick. ONE: a delivery makes the orchestrator busy, and
# a second paste would land mid-thought (or, worse, race its status back to idle).
# The rest of the queue drains on subsequent idle ticks, oldest first.
MAX_DELIVERIES_PER_TICK = 1

# Run states that mean "this window was SUPPOSED to go away" — the dispatcher itself
# kills the window on `task-finished`, and a failed run has already been announced by
# run_events(). A watched window vanishing while its run sits in one of these is
# completion, not death.
#
# `changes_requested` and `needs_human` belong here for exactly the same reason: both are
# reached FROM awaiting_review, whose window the agent already killed on its way out. A
# window that is gone because its agent finished and its PR then failed review is not a
# corpse — reporting it as one is the false-DIED bug wearing a new state's hat.
SETTLED_RUN_STATES = ("awaiting_review", "changes_requested", "needs_human", "done", "failed")

# How long a vanished window's run row gets to settle before we call it a death.
# The window dies a moment BEFORE the row lands: `chela task-finished` flips the run
# to awaiting_review and kills the tmux window, and the daemon can easily sample the
# gone window while the write is still in flight. Deciding on the first sample is what
# made a successful agent get reported as DIED. So the first tick that sees the window
# gone only STAMPS it; the claim is made a tick later, re-reading the run state.
DEATH_CONFIRM_SECONDS = 30

# How long a watched window must read `idle` CONTINUOUSLY before "idle" is trusted for a
# finished decision. `now == IDLE` is one sample of Claude Code's own native status, and
# that status can read `idle` for a single tick in the GAP BETWEEN TWO TOOL CALLS of an
# agent that is nowhere near done — observed live 2026-07-28 (CMX-193): a `finished` fired
# mid-task, because idle was sampled in exactly that gap rather than at genuine end-of-turn.
# CMX-191 fixed WHOSE work `did_work_since` credits; this fixes WHETHER `idle` itself can be
# believed on sight — the scoping call in CMX-191's own ticket ("do not touch the busy→idle
# edge detector") was wrong, and this is the fourth bug in the family to prove it. The fix
# mirrors `gone_since`/DEATH_CONFIRM_SECONDS immediately below: the first idle sample only
# stamps `idle_since`, and "idle" only counts once it has held for this long without a busy
# sample in between. A window that goes back to busy before then clears the stamp — it was
# mid-task, not done — and the confirmation restarts from scratch on its next idle sample.
IDLE_CONFIRM_SECONDS = 30

# A run's `title` is the WHOLE tracker line — for this repo, a multi-paragraph brief
# with landmines and references. It is a task body, not a notification: pushing it
# verbatim pasted an essay into the orchestrator's window. The summary carries this
# much of it; the payload carries all of it.
SUMMARY_TITLE_CHARS = 60

_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:[/?#]|$)")
# `*` and backticks only. NOT `_` or `~`: a tracker title is full of snake_case
# identifiers and approximations ("~4096 chars", "pr_state"), and stripping those
# characters mangles the words rather than unwrapping emphasis.
_MARKUP_RE = re.compile(r"[*`]+")


def enabled() -> bool:
    return INBOX_ENABLED


def store_path() -> Path:
    # ``config.CHELA_DIR`` per call, not latched at import — see event_log.log_path().
    return Path(os.environ.get("CHELA_INBOX_FILE") or (config.CHELA_DIR / "inbox.json"))


def _empty() -> dict:
    # `orchestrator_epoch` is the tmux server that ISSUED `orchestrator` — the half of the
    # address that used to be missing, and without which `@0` is just a number that means
    # something different after every tmux restart (chela.epoch). `orchestrator_session` is
    # the orchestrator's stable IDENTITY: the claude session id an ``@N`` re-resolves TO after
    # a restart renumbers the fleet (CMX-82 self-heal) — the thing this address used to be
    # keyed on with no way back. `orchestrator_name` is what that window was CALLED, a human
    # label for the alarm when even the identity cannot be re-resolved. `address_alarm`
    # de-dups the undeliverable alarm — it must be loud, not a per-tick flood of identical
    # rows in the event log.
    # `address_alarm_since`/`address_alarm_pushed` are the CMX-113 grace window: the durable
    # event still fires the instant the address is seen dead (below), but the phone push waits
    # to see whether the SAME outage is still true after `config.INBOX_ALARM_GRACE_SECONDS` —
    # a reboot/tmux-restart/handoff self-heals in seconds and should never buzz a pocket.
    return {"orchestrator": None, "orchestrator_epoch": None, "orchestrator_session": None,
            "orchestrator_name": None, "watches": {}, "queue": [], "runs_seen": {},
            "address_alarm": None, "address_alarm_since": None, "address_alarm_pushed": False}


def _clear_address_alarm(store: dict) -> None:
    """The failure is over — the next one (even the same kind) is news again, from scratch."""
    store["address_alarm"] = None
    store["address_alarm_since"] = None
    store["address_alarm_pushed"] = False


def load() -> dict:
    """Read the durable store. A missing/corrupt file reads as empty, never raises."""
    try:
        data = json.loads(store_path().read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    base = _empty()
    base.update({k: data[k] for k in base if k in data and data[k] is not None})
    return base


def save(store: dict) -> None:
    """Persist atomically (tmp + rename) so a crash can't truncate the queue."""
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(store, indent=2))
    tmp.replace(path)


@contextmanager
def locked_store():
    """Read-modify-write the store under an exclusive lock. THE fix for lost updates.

    The daemon and the CLI mutate the same file CONCURRENTLY — that is the normal case
    here, since you run ``chela watch`` from an agent session while the daemon ticks in
    the background. Plain load→modify→save loses writes: the daemon loads the store,
    spends a second probing statuses, then saves its stale copy back — silently erasing
    a ``chela watch`` that landed in between. Live, that looked like "my first watch
    never activates until I restart the daemon"; the watch was simply gone (reproduced
    deterministically: the CLI reports ok, the store comes back ``{}``).

    An advisory flock over the whole read-modify-write serialises them, so a concurrent
    watch either happens fully before the tick's read or fully after its write. Callers
    must keep the critical section short — do slow work (status probes, the runs query)
    BEFORE taking the lock.
    """
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            store = load()
            yield store
            save(store)
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


# --- who the orchestrator is (explicit, never guessed) -------------------------

# What the recorded orchestrator address is WORTH, right now. The gate `deliver` opens on,
# and the fact `chela doctor` audits (runtime_truth: inbox.address).
ADDR_OK = "ok"                  # a live claude window, issued by the tmux server now running
ADDR_NONE = "unregistered"      # nobody has called `chela watch` — the inbox is inert by design
ADDR_DANGLING = "dangling"      # ⛔ issued by a DEAD tmux server: `@N` now names a stranger
ADDR_GONE = "gone"              # this epoch's `@N`, but no claude is running in it any more
ADDR_UNSTAMPED = "unstamped"    # recorded before CMX-77, or pinned by env — unverifiable

# The states in which a push must NOT go out. `dangling` and `gone` are the loud ones: there
# IS work to hand over and the address cannot take it. (`unregistered` is not an error — the
# feature is simply not in use; `unstamped` is delivered, warily, see address_state.)
UNDELIVERABLE = (ADDR_DANGLING, ADDR_GONE)


def orchestrator_wid(store: dict | None = None) -> str | None:
    """The ADDRESS we may push into: ``$CHELA_ORCHESTRATOR_WID``, else the registered one.

    An env pin always wins (an operator overriding the fleet's wiring). Otherwise it
    is whatever session registered itself by calling ``chela watch`` — i.e. the
    session that actually delegates work. None means "nobody is listening", and the
    inbox stays completely inert: we never fall back to a guess.

    ⛔ This is the address AS RECORDED — it is not a promise that the window is there, or
    that it is still the window that recorded it. ``@N`` is only meaningful inside the tmux
    server that issued it (:mod:`chela.epoch`), and this one may have been issued by a
    server that is now dead. :func:`address_state` is what says whether it is worth
    anything, and :func:`deliver` refuses to write to one that is not.
    """
    env = (os.environ.get("CHELA_ORCHESTRATOR_WID") or "").strip()
    if env:
        return env
    store = load() if store is None else store
    return store.get("orchestrator")


def orchestrator_epoch(store: dict | None = None) -> str | None:
    """The tmux server that issued the registered address — None if it is unstamped.

    An **env pin carries no epoch**, and cannot: it is a bare ``@N`` an operator exported.
    That is honest rather than convenient — a pin baked into a long-lived service env
    (a PM2 process that outlives the tmux server) is precisely an address that will one day
    name a stranger, and it must not masquerade as verified. It reads as ``unstamped``.
    """
    if (os.environ.get("CHELA_ORCHESTRATOR_WID") or "").strip():
        return None
    store = load() if store is None else store
    return store.get("orchestrator_epoch")


def orchestrator_session(store: dict | None = None) -> str | None:
    """The recorded session IDENTITY of the orchestrator — what a renumbered ``@N`` re-resolves to.

    An **env pin carries no identity**, exactly as it carries no epoch (:func:`orchestrator_epoch`):
    a bare ``@N`` an operator exported has no session behind it that chela recorded, so there is
    nothing to re-resolve and self-heal simply does not apply to a pin. None here means the
    address cannot self-heal — it was registered before CMX-82, or the orchestrator's session
    could not be established at registration; either way the CMX-77 loud-and-wait path still holds.
    """
    if (os.environ.get("CHELA_ORCHESTRATOR_WID") or "").strip():
        return None
    store = load() if store is None else store
    return store.get("orchestrator_session")


def _identity_of(wid: str | None) -> str | None:
    """The orchestrator's stable claude session id — the identity a renumbered address heals to.

    Best-effort and never fatal: if the session cannot be established (a brand-new orchestrator
    that has fired no hook and was not resumed), the address is recorded WITHOUT an identity and
    self-heal is unavailable until the next ``chela watch`` — exactly the pre-CMX-82 behaviour,
    never worse. Reads tmux + /proc via :mod:`chela.sessions`, so callers run it outside the
    store lock.
    """
    if not wid:
        return None
    try:
        return sessions.session_of_window(wid)
    except Exception:
        log.debug("inbox: could not resolve the orchestrator's session for %s", wid,
                  exc_info=True)
        return None


def address_state(store: dict, statuses: dict[str, str],
                  now_epoch: str | None = None) -> tuple[str, str]:
    """Is the recorded orchestrator address worth writing to? — ``(state, why)``.

    Three ways an address rots, and only one of them was ever visible:

    * **dangling** — the tmux server that issued it is gone (an OOM, a reboot, a
      ``kill-server``). The fleet came back renumbered, so ``@0`` either does not exist or,
      worse, belongs to somebody else now. Never written to: a wrong wid is worse than no
      wid (CMX-48) — a review notification pasted into an agent's prompt is an instruction
      that agent will act on.
    * **gone** — this epoch's ``@N``, but nothing is running claude in it any more (the
      orchestrator exited). Nothing to deliver to.
    * **unstamped** — recorded before CMX-77, or pinned via ``$CHELA_ORCHESTRATOR_WID``.
      It cannot be verified either way, so it is still delivered (refusing would break every
      store that predates this, and cry wolf on every operator pin) — but it is reported, and
      one ``chela watch`` from the orchestrator replaces it with an address that CAN be
      verified.

    ``now_epoch`` unknown (no tmux, no server) means nothing can be compared: an unreadable
    owner never accuses a stamp of being stale — it is what ``chela doctor`` reports as
    CANNOT VERIFY, which is the one thing that must never read as green.

    An EMPTY ``statuses`` map is not evidence that the orchestrator is gone: it is what a
    hiccup in ``claude agents --json`` looks like, and alarming on it would cry wolf about
    the whole fleet at once. Liveness is only asserted against a map that saw *something*.
    """
    wid = orchestrator_wid(store)
    if not wid:
        return ADDR_NONE, ("no session has registered as the orchestrator (`chela watch`), "
                           "so the inbox has nobody to push to")
    stamped = orchestrator_epoch(store)
    if epoch.is_dangling(stamped, now_epoch):
        name = store.get("orchestrator_name") or "?"
        return ADDR_DANGLING, (
            f"{wid} was issued by {epoch.describe(stamped)}, and tmux is now running "
            f"{epoch.describe(now_epoch)} — the server RESTARTED and renumbered the fleet. "
            f"That id does not name the orchestrator ({name!r}) any more, and may well name "
            "another agent, so nothing will be written to it. Re-register from the "
            "orchestrator's session: `chela watch` (any dispatch does it for you). "
            "`chela watch` only works if that session is CURRENTLY running inside a tmux "
            "window — a session restarted outside tmux (e.g. by hand, after a reboot) "
            "cannot bind at all, and `chela watch` there fails with 'no window id'. If "
            "nothing is running that session anywhere, run `chela restore` instead — it "
            "needs no live window and hands back the exact fix: REVIVABLE re-addresses "
            "automatically, MANUAL gives the precise `CHELA_WID=@N claude --resume <sid>` "
            "command to relaunch it, which is the only remedy in that case.")
    if statuses and wid not in statuses:
        return ADDR_GONE, (
            f"tmux has no claude running in {wid} — the session that registered as the "
            "orchestrator is gone. Its queue is intact and will go out to whichever session "
            "registers next (`chela watch`). But `chela watch` needs a LIVE session to run "
            "it from — if the orchestrator process itself is dead (crashed, or never "
            "relaunched inside tmux), there is none, and `chela restore` is the fallback: "
            "no live window required, and it hands back the exact relaunch command for a "
            "session that has to be started by hand.")
    if not stamped and now_epoch:
        return ADDR_UNSTAMPED, (
            f"{wid} carries no tmux epoch (recorded before CMX-77, or pinned with "
            "$CHELA_ORCHESTRATOR_WID), so chela cannot tell whether it still names the "
            "session that registered it. Re-register with `chela watch` to stamp it.")
    return ADDR_OK, ""


# --- watches: the orchestrator registers interest when it delegates -------------

def watch(wid: str, note: str = "", *, by: str | None = None) -> dict:
    """Register interest in ``wid``: report when it finishes, blocks, or dies.

    ``by`` is the caller's own window (``$CHELA_WID`` via
    :func:`chela.orchestrator.self_wid`) and registers it as THE orchestrator — the
    session that delegates is the session that gets told. Watching your own window is
    refused: that is the self-notify loop, and it is closed here at the source.

    **Both ids are stamped with the tmux epoch they were issued in** (:mod:`chela.epoch`).
    This is the one moment the stamp can be taken honestly — the windows are live and in
    front of us — and it is what lets every later reader tell "the window I meant" from "the
    number that window used to have". It also makes this the RECOVERY path: after a tmux
    restart the orchestrator's next dispatch re-registers a valid address, and the queue that
    piled up while the old one dangled goes out.
    """
    names = discovery.get_windows_by_id()          # slow-ish (tmux) — do it OUTSIDE the lock
    if wid not in names:
        return {"ok": False, "error": f"no such window: {wid}"}
    now = epoch.current()
    # The orchestrator's stable identity, resolved OUTSIDE the lock (tmux + /proc): this is what
    # a renumbered address re-resolves to (CMX-82). Only meaningful when we are (re)registering.
    session = _identity_of(by) if by else None
    with locked_store() as store:                  # ...so a concurrent daemon tick can't
        if by:                                     #    clobber the watch we are writing
            store["orchestrator"] = by
            store["orchestrator_epoch"] = now
            store["orchestrator_session"] = session
            store["orchestrator_name"] = names.get(by)
            _clear_address_alarm(store)            # a fresh address: any old alarm is spent
        target = orchestrator_wid(store)
        if target and wid == target:
            return {"ok": False, "error": "refusing to watch the orchestrator's own window"}
        # `since` is the completion evidence line: work the transcript shows AFTER this
        # instant is work this dispatch caused (see agent_events). `name` outlives the
        # window itself and is what links it back to its run row (see run_for_window).
        # `epoch` is what makes the id itself trustworthy a day later.
        store["watches"][wid] = {"note": note.strip(), "since": time.time(),
                                 "name": names[wid], "epoch": now}
    return {"ok": True, "wid": wid, "note": note.strip(), "orchestrator": target,
            "epoch": now, "session": session}


def register(by: str) -> dict:
    """Register ``by`` as THE orchestrator without watching anything.

    The recovery command (``chela watch`` with no window): after tmux restarts, the stored
    address is dangling and the inbox is holding a queue it refuses to misdeliver. This
    re-stamps it — from the session that runs it, so it is still never a guess.
    """
    names = discovery.get_windows_by_id()
    if by not in names:
        return {"ok": False, "error": f"no such window: {by}"}
    now = epoch.current()
    session = _identity_of(by)                      # the identity self-heal re-resolves to (CMX-82)
    with locked_store() as store:
        store["orchestrator"] = by
        store["orchestrator_epoch"] = now
        store["orchestrator_session"] = session
        store["orchestrator_name"] = names.get(by)
        _clear_address_alarm(store)
        queued = len(store["queue"])
    return {"ok": True, "orchestrator": by, "epoch": now, "session": session, "queued": queued}


def unregister(wid: str) -> dict:
    """Clear the recorded orchestrator address — the inverse of :func:`register`.

    Used by orchestrator teardown so a killed window never leaves a *dead address* registered:
    :func:`orchestrator_wid` would keep returning it, and :func:`deliver` would refuse to write
    to it (ADDR_GONE) while the queue silently backed up. Clearing it to ``None`` returns the
    inbox to the inert ADDR_NONE state — the queue is durable and waits for the next registrant.

    ⛔ Guarded: a no-op unless the address CURRENTLY names ``wid``. A human may have re-registered
    their own session in the meantime (or the epoch renumbered); we never clear someone else's
    registration out from under them.
    """
    with locked_store() as store:
        if store.get("orchestrator") != wid:
            return {"ok": False, "wid": wid, "orchestrator": store.get("orchestrator")}
        store["orchestrator"] = None
        store["orchestrator_epoch"] = None
        store["orchestrator_session"] = None
        store["orchestrator_name"] = None
        _clear_address_alarm(store)
    return {"ok": True, "wid": wid}


def readdress(old_wid: str, old_epoch: str | None, new_wid: str) -> dict:
    """Move the orchestrator's registered address from a dangling ``old_wid`` to ``new_wid``.

    CMX-196's applied form of a REVIVABLE row's remedy — ``chela restore`` says the
    orchestrator's recorded session is alive under ``new_wid`` right now
    (:func:`chela.restore.plan`), and this does the ``chela watch``-from-there a human would
    otherwise run by hand: re-resolves the identity fresh (never trusts the plan's session id
    — defense in depth, the same reason :func:`register` always re-derives it) and stamps the
    current epoch.

    ⛔ Guarded, atomically, inside the same lock as the write: a no-op unless the recorded
    address is STILL exactly ``old_wid`` issued by ``old_epoch``. A human may have
    re-registered in the meantime, or a further restart may have reissued ``old_wid`` to a
    genuinely different session — either way that registration is not the one classification
    saw, and must not be clobbered by a plan computed before it happened.
    """
    names = discovery.get_windows_by_id()
    if new_wid not in names:
        return {"ok": False, "error": f"no such window: {new_wid}"}
    now = epoch.current()
    session = _identity_of(new_wid)
    with locked_store() as store:
        if store.get("orchestrator") != old_wid or store.get("orchestrator_epoch") != old_epoch:
            return {"ok": False, "error": "orchestrator address moved on since classification"}
        store["orchestrator"] = new_wid
        store["orchestrator_epoch"] = now
        store["orchestrator_session"] = session
        store["orchestrator_name"] = names.get(new_wid)
        _clear_address_alarm(store)
    return {"ok": True, "orchestrator": new_wid, "epoch": now, "session": session}


def unregister_dangling(wid: str, stamped_epoch: str | None) -> dict:
    """:func:`unregister`'s epoch-guarded twin — CMX-196's applied form of a MANUAL row's
    archive-then-remove.

    ``unregister`` only checks the address, which is enough for teardown (a window clearing
    its OWN just-registered address). This is used against a row a *stale* classification
    named, potentially long after it was computed, so the address alone is not enough: a
    further tmux restart can reissue ``wid``'s number to a completely different, genuinely
    live registration, and matching on the number alone would clear that instead of the
    archived dangling one. A no-op (nothing cleared) unless the recorded epoch is STILL
    exactly the dead one classification saw.
    """
    with locked_store() as store:
        if store.get("orchestrator") != wid or store.get("orchestrator_epoch") != stamped_epoch:
            return {"ok": False, "wid": wid}
        store["orchestrator"] = None
        store["orchestrator_epoch"] = None
        store["orchestrator_session"] = None
        store["orchestrator_name"] = None
        _clear_address_alarm(store)
    return {"ok": True, "wid": wid}


def unwatch(wid: str) -> dict:
    with locked_store() as store:
        existed = store["watches"].pop(wid, None) is not None
    return {"ok": existed, "wid": wid}


def watches() -> dict:
    return load()["watches"]


# --- event generation (pure — no tmux, no send) ---------------------------------

def _line(wid: str, name: str, body: str, note: str = "") -> str:
    """One compact, actionable line. The orchestrator reads it as an instruction.

    The framing punctuation is ``·`` and curly quotes, NOT parentheses and ``"``. Those are
    shell metacharacters, and every summary is neutralised before it is typed at a prompt
    (:func:`_event`), so a frame built from them would just have its own punctuation stripped
    back out — it was ``(name)``, and the live bash-mode execution died on exactly those
    parens. Framing that is already inert renders identically before and after the sanitizer.
    """
    tail = f" — note: “{note}”" if note else ""
    return f"📥 {wid} · {name} {body}{tail}"


def _event(kind: str, summary: str, payload: dict, *, wid: str | None = None,
           clear_watch: bool = False, silent: bool = False,
           watch_key: str | None = None) -> dict:
    """The queued event record: what happened, in one line, plus the facts behind it.

    ``summary`` is the ONLY thing ever pushed into a session — one line, no newlines.
    ``payload`` is the structured record (run id, wid, PR url, full title, timestamps)
    that a log, a filter, a de-dup or a UI can actually work with, and that
    :func:`stale_reason` re-checks at delivery. Keeping the two apart is what stops a
    notification from being an essay and stops a fact from being un-re-checkable.

    ⛔ The summary is built from AGENT-AUTHORED text — a PR title, a tracker line, a CI
    error — and it is TYPED AT A PROMPT. So it is neutralised HERE, at the source, before it
    is ever durable: no shell metacharacters, no control bytes, no mode-switching first
    character (:func:`chela.tui_text.sanitize_prompt`). CMX-79: an inbox summary was executed
    as a shell command by an orchestrator pane sitting in ``!`` bash-input mode; it died on
    the parens in "(rework 1)", and a PR title containing ``$(…)`` would not have. The
    payload keeps the raw title — a record is read, not typed.

    ``wid`` ATTRIBUTES the event (it is the Feed's lane), so it must only ever hold an id
    that names the agent this event is about IN THE CURRENT EPOCH. ``watch_key`` is the
    bookkeeping id instead — the key to retire in ``watches`` — for the one event whose
    subject is precisely that its id no longer names anybody (``watch_epoch_lost``).
    """
    event = {"kind": kind, "summary": sanitize_prompt(summary), "payload": payload,
             "wid": wid, "clear_watch": clear_watch, "ts": time.time()}
    if silent:
        event["silent"] = True
    if watch_key:
        event["watch_key"] = watch_key
    return event


def render(event: dict) -> str:
    """The one line an event renders to. Legacy queues held a pre-rendered ``text``.

    An event queued by an older daemon (before events were records) is still sitting in
    ``inbox.json`` across the upgrade, and must still be deliverable — hence the
    fallback. New events never set ``text``.

    Neutralised AGAIN here, deliberately: ``inbox.json`` survives the upgrade, so the live
    queue already holds summaries built before :func:`_event` sanitized anything — and those
    are exactly the ones written while nothing was watching for ``$(…)``. Sanitizing only at
    queue time would leave the one queue that holds the observed payload as the thing the fix
    misses. :func:`chela.tui_text.sanitize_prompt` is idempotent, so a clean summary is
    unchanged by the second pass.
    """
    return sanitize_prompt(event.get("summary") or event.get("text") or "")


def _short_title(title: str, limit: int = SUMMARY_TITLE_CHARS) -> str:
    """A tracker line, cut down to something you can read in a notification.

    Strips markdown emphasis (the tracker's titles are mostly ``**bold**``), collapses
    the whitespace, and truncates on a word boundary. The full title is in the payload.
    """
    text = " ".join(_MARKUP_RE.sub("", title or "").split())
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0] or text[:limit]
    return head + "…"


def pr_ref(pr_url: str | None) -> str:
    """``PR #47`` when the url looks like a PR, else the url itself (or nothing)."""
    if not pr_url:
        return ""
    m = _PR_NUMBER_RE.search(pr_url)
    return f"PR #{m.group(1)}" if m else pr_url


def did_work_since(wid: str, since: float) -> bool:
    """Did THIS window's transcript gain an ASSISTANT turn after ``since``?

    The completion evidence that makes detection independent of the poll rate. The
    busy→idle edge alone cannot see a task shorter than the sampling interval: a 15s
    delegation between two 30s ticks is sampled ``idle, idle``, the daemon never
    observes ``busy``, and the completion is dropped FOREVER. Quick delegations are
    exactly what this feature exists to catch, so "finished" must not depend on having
    caught the window mid-flight.

    An assistant turn written after the watch was registered is proof the agent did
    work for THIS dispatch; combined with the window now being idle, it is finished.
    We require an *assistant* turn specifically: the dispatched prompt itself lands as
    a *user* record, so counting any activity would read "your prompt arrived" as "the
    agent replied".

    ⛔ Resolved by ``wid`` via :func:`chela.sessions.transcript_for_window`, NOT by cwd.
    A cwd-keyed lookup (the original implementation) hands back whichever transcript in
    that directory wrote the newest record — so a SIBLING window in the same cwd that
    happens to still be mid-tool-call gets its assistant turn credited to the window this
    watch is actually about, and an agent that has done no work at all is reported
    "finished". Live-observed 2026-07-28 (CMX-191): ``@122`` was reported finished 38s
    after dispatch, wedged between a `pre_tool_use` and its own matching `post_tool_use`
    — provably still mid-tool-call, with no commit and no output to show for it. Same root
    cause as CMX-190 (`read`/`peek`), same fix (resolve by window, refuse when the cwd is
    ambiguous — see :mod:`chela.sessions`).

    Best-effort — a window whose transcript cannot be resolved (no transcript yet, or the
    cwd is shared and no stronger signal disambiguates it) simply yields False, leaving
    the busy→idle edge as the detector.
    """
    path = sessions.transcript_for_window(wid)
    if path is None:
        return False
    last = transcripts.last_assistant_activity_at(path)
    return last is not None and last > since


def run_for_window(name: str | None, runs: list[dict]) -> dict | None:
    """The dispatcher run that owns the window called ``name`` (newest first), if any.

    The dispatcher names an agent's window after its branch (``window_name`` on the
    run row), which is the only handle a *gone* window still gives us — its id is
    meaningless once tmux has reaped it. A retry re-uses the name, so we take the
    newest matching row; :func:`chela.dispatcher.list_runs` already sorts DESC.
    An ad-hoc ``tmux send-keys`` dispatch has no row at all → None.
    """
    if not name:
        return None
    for run in runs:
        if run.get("window_name") == name:
            return run
    return None


def _gone_event(wid: str, name: str, note: str, run: dict | None) -> dict | None:
    """What a vanished watched window actually MEANS, given its run row.

    A window disappearing is not evidence of death — an agent that finishes normally
    EXITS, and `chela task-finished` kills the window on purpose. Inferring death from
    the disappearance alone reported every successful dispatch as ``DIED mid-task``,
    while the same tick queued that run's ``awaiting_review`` event: two contradictory
    messages for one run, with the false one being the alarming one.

    So the disappearance is corroborated against the run state:

    * settled run (``awaiting_review`` / ``done`` / ``failed``) or a PR exists →
      the window was *supposed* to go: emit nothing (``run_events`` already announced
      it — a second event here is the self-contradiction) and clear the watch.
    * run still ``claimed`` / ``running`` with no PR → a genuine mid-task death. This
      is the whole point of the watch and it must still shout.
    * no run row (ad-hoc dispatch) → we simply do not know. Say that, rather than
      asserting an outcome we cannot see.
    """
    payload = _window_payload(wid, name, note, run)
    if run is not None and (run.get("status") in SETTLED_RUN_STATES or run.get("pr_url")):
        return _event("completed_gone", _line(wid, name, "completed (window closed)", note),
                      payload, wid=wid, clear_watch=True, silent=True)
    if run is None:
        return _event("gone_unknown",
                      _line(wid, name, "window closed — no run state to confirm the "
                                       "outcome; check before assuming either way.", note),
                      payload, wid=wid, clear_watch=True)
    return _event("died",
                  _line(wid, name, "DIED mid-task (window gone) — the work was not "
                                   "finished.", note),
                  payload, wid=wid, clear_watch=True)


def _window_payload(wid: str, name: str, note: str, run: dict | None = None) -> dict:
    """The facts behind a window event — the run row's identity, not its prose."""
    payload = {"wid": wid, "window_name": name, "note": note}
    if run is not None:
        payload.update({"task_id": run.get("task_id"), "run_status": run.get("status"),
                        "pr_url": run.get("pr_url"), "task_title": run.get("title")})
    return payload


def wid_for_window_name(name: str | None, windows: dict[str, str]) -> str | None:
    """The live window called ``name`` — or None. It is NEVER a guess.

    A run event knows its ``window_name`` (the dispatcher names the agent's window after
    its branch) but was queued with ``wid=None``, so 17 of the log's ``run_review`` rows
    landed in no agent's lane despite naming their agent in the payload. The window is
    still live at the instant the event is queued, so the id is resolvable *then* — which
    is where it must happen: after the window is reaped, nothing can recover it.

    ⛔ Ambiguity resolves to None, not to a plausible pick. Two windows sharing a name
    (a retry racing its predecessor's exit) is exactly the case where a wrong ``wid``
    would attribute an agent's events to a different agent — and CMX-48's lesson is that
    a wrong ``wid`` is worse than no ``wid``: an unattributed event is visibly ownerless,
    a misattributed one is invisibly false.
    """
    if not name:
        return None
    matches = [wid for wid, wname in (windows or {}).items() if wname == name]
    return matches[0] if len(matches) == 1 else None


def run_wid(run: dict, windows: dict[str, str] | None = None,
            now_epoch: str | None = None) -> str | None:
    """The window a run was dispatched into — from the RUN ROW, not from live tmux.

    The row records ``window_id`` at spawn (``dispatcher._spawn``), which is the only
    lossless moment: a dispatched agent ends by calling ``chela task-finished``, which
    KILLS ITS OWN WINDOW, and only *then* does the run reconcile to ``awaiting_review``
    and this event get queued. So by the time :func:`wid_for_window_name` looks, the
    window is already reaped — that was not the edge case, it was every case, and every
    ``run_review`` (the "your agent finished, go review the PR" row — the single most
    important one in the Feed) landed in the ``chela itself`` lane.

    The name lookup remains the FALLBACK: it is still right for a run that predates the
    recorded id, or one whose window is somehow still alive. Neither path guesses — a run
    with no recorded id and no unambiguous live window stays ``None`` and belongs to
    chela itself. ⛔ An id is never inferred from a branch, a worktree, or a reused
    window: tmux recycles ids, and filing a dead agent's work under a *live* agent is the
    worst version of this bug (CMX-48 — a wrong wid is worse than no wid).

    ⛔ **And "tmux recycles ids" is not a figure of speech — it happens wholesale every time
    the server restarts** (CMX-77). A row written before the 2026-07-14 OOM claims ``@3``;
    ``@3`` today is a different agent entirely. So the recorded id is used only when the row
    carries the epoch it was issued in and that epoch is still the one running
    (``window_epoch``, stamped at spawn). Otherwise the id is DROPPED — back to the name
    lookup, which reads live tmux and therefore cannot name a dead window at all — and an
    event that cannot be attributed stays honestly ownerless.
    """
    recorded = (run.get("window_id") or "").strip()
    if re.fullmatch(r"@\d+", recorded) and not epoch.is_dangling(
            run.get("window_epoch"), now_epoch):
        return recorded
    return wid_for_window_name(run.get("window_name"), windows or {})


def _epoch_lost_event(wid: str, meta: dict, stamped: str | None,
                      now_epoch: str | None) -> dict:
    """A watched window whose id was issued by a tmux server that is now DEAD.

    Every status this watch could be evaluated against is a lie: the agent it was registered
    for died with the server, and the ``@N`` it was registered under has been handed out
    again — so ``busy``, ``idle`` and "gone" all now describe a STRANGER. Reading any of them
    would report someone else's work as this dispatch finishing (the false-DIED bug's evil
    twin: a false FINISHED, which the orchestrator would act on).

    So the watch is retired and the truth is reported: the outcome is unknown, and the run
    row is where to look. ``wid=None`` deliberately — the event is not ABOUT the window that
    holds that id today, and attributing it there is exactly the misattribution this whole
    change exists to end. ``watch_key`` carries the stale id for the bookkeeping.
    """
    name = meta.get("name") or wid
    note = meta.get("note", "")
    tail = f' — note: "{note}"' if note else ""
    return _event(
        "watch_epoch_lost",
        f"📥 the tmux SERVER restarted ({epoch.describe(stamped)} → "
        f"{epoch.describe(now_epoch)}): the agent you were watching in {wid} ({name}) died "
        f"with it, and {wid} now belongs to a different window. Outcome UNKNOWN — check its "
        f"run/PR before assuming either way.{tail}",
        {"wid": wid, "window_name": name, "note": note, "epoch": stamped,
         "now_epoch": now_epoch},
        wid=None, clear_watch=True, watch_key=wid)


def agent_events(prev: dict[str, str], cur: dict[str, str], store: dict,
                 runs: list[dict] | None = None,
                 windows: dict[str, str] | None = None,
                 now_epoch: str | None = None) -> list[dict]:
    """Events from agent status transitions, scoped to WATCHED windows only.

    Edge-triggered (mirrors :func:`chela.notify.check_waiting`) so a window that sits
    idle — or sits waiting — across many ticks is announced exactly once.

    Completion is detected TWO ways, because the edge alone is not enough. The
    busy→idle transition is the fast, precise signal when we catch it; but a task that
    starts and finishes BETWEEN two polls is never observed as busy, so it would be
    missed forever. So an idle watched window that has done work since its watch was
    registered (:func:`did_work_since`) is also finished. The evidence path needs no
    baseline at all, which is why it also survives a daemon restart mid-task.

    ⛔ Neither path trusts a single ``now == IDLE`` sample — see ``IDLE_CONFIRM_SECONDS``.
    Claude Code's own native status can read ``idle`` for one tick in the gap between two
    tool calls of a still-running agent (CMX-193), so "idle" only counts once it has held,
    with no busy sample in between, for the confirm window. This writes ``idle_since`` back
    onto the watch, cleared the instant a busy sample is seen — the same stamp-then-confirm
    shape as ``gone_since`` just below.

    A window that is GONE is corroborated against ``runs`` (see :func:`_gone_event`)
    rather than being called dead on sight, and only after ``DEATH_CONFIRM_SECONDS``,
    so the run row racing the window's exit cannot produce a false death. This writes
    ``name``/``gone_since`` back onto the watch — the caller persists the store.

    The orchestrator's own window is never a source, so the busy→idle its own reply
    produces (including the reply to one of our own pushes) can never become an event.

    ``windows`` is the live ``{wid: name}`` table; the caller passes the one it already
    fetched (:func:`tick` reads it once, outside the store lock) so a tick costs one
    ``tmux list-windows``, not two.
    """
    orch = orchestrator_wid(store)
    names = windows if windows is not None else discovery.get_windows_by_id()
    runs = runs or []
    now_ts = time.time()
    out: list[dict] = []
    for wid, meta in sorted(store["watches"].items()):
        if wid == orch:
            continue                      # never notify about the orchestrator itself
        meta = meta or {}
        note = meta.get("note", "")
        since = meta.get("since") or 0.0
        was, now = prev.get(wid), cur.get(wid)

        # ⛔ FIRST, before any status is believed: was this id issued by the tmux server
        # that is running now? If not, it names somebody else and every branch below would
        # be reasoning about the wrong window.
        stamped = meta.get("epoch")
        if epoch.is_dangling(stamped, now_epoch):
            out.append(_epoch_lost_event(wid, meta, stamped, now_epoch))
            continue

        if wid not in names:
            # Gone. Remember the name we last saw it under: it is the ONLY link back to
            # the run row (window ids die with the window), and it is why the false
            # death report could only ever say "@6 (@6)".
            name = meta.get("name") or wid
            gone_since = meta.get("gone_since")
            if not gone_since:
                meta["gone_since"] = now_ts   # first sample only stamps; never decides
                store["watches"][wid] = meta
            run = run_for_window(meta.get("name"), runs)
            settled = run is not None and (
                run.get("status") in SETTLED_RUN_STATES or run.get("pr_url"))
            if not settled and now_ts - (gone_since or now_ts) < DEATH_CONFIRM_SECONDS:
                continue                  # let the run row catch up before we accuse
            event = _gone_event(wid, name, note, run)
            if event:
                out.append(event)
            continue

        meta["name"] = names[wid]         # keep the id→name link fresh while it lives
        meta.pop("gone_since", None)      # it came back (or tmux blipped) — not gone
        name = names[wid]

        # ⛔ A lone `now == IDLE` sample is not proof this watch is done — see
        # IDLE_CONFIRM_SECONDS. Stamp-then-confirm, exactly like `gone_since` above: the
        # first idle sample only records when the idle run STARTED; a busy sample at any
        # point resets it, because that idle run was a blip in an ongoing task, not the end
        # of one.
        if now == IDLE:
            if not meta.get("idle_since"):
                meta["idle_since"] = now_ts   # first sample only stamps; never decides
        else:
            meta.pop("idle_since", None)      # busy/waiting again — that idle was a blip
        if now == BUSY or was == BUSY:
            # persisted for the LIFE of the watch, not just this tick: `was` alone is only
            # the immediately-previous sample, which the confirm delay has long since rolled
            # past by the time idle confirms — but `was` still matters HERE, for a watch
            # whose very first observed tick is already the busy->idle transition itself.
            meta["saw_busy"] = True
        store["watches"][wid] = meta
        idle_since = meta.get("idle_since")
        confirmed_idle = (
            now == IDLE and idle_since is not None
            and now_ts - idle_since >= IDLE_CONFIRM_SECONDS
        )

        # `was == BUSY` only ever looks at the tick immediately before this one — but by
        # the time confirmed_idle is true, several ticks of continuous idle have already
        # elapsed, so `was` is IDLE, never BUSY, and this edge could never fire. `saw_busy`
        # instead remembers, for the LIFE of the watch, whether a busy sample was ever seen
        # — the same stamp-on-meta discipline `idle_since` uses just above.
        finished_edge = meta.get("saw_busy") and confirmed_idle
        # The not-missed path: confirmed idle, and the transcript proves it worked for us.
        # Gated on confirmed idle so an agent still mid-task — busy, waiting, or merely
        # caught in the gap between two tool calls — is never called done.
        finished_evidence = confirmed_idle and did_work_since(wid, since)
        if finished_edge or finished_evidence:
            out.append(_event("finished",
                              _line(wid, name, "finished the task you dispatched — "
                                               "verify + commit.", note),
                              _window_payload(wid, name, note,
                                              run_for_window(name, runs)),
                              wid=wid, clear_watch=True))
            continue
        if was is None:
            continue                      # no baseline — the transition below needs one
        if was != WAITING and now == WAITING:
            # Keep the watch: it is blocked, not done — it still owes you the work.
            out.append(_event("blocked",
                              _line(wid, name, "is BLOCKED on a prompt (permission/"
                                               "question) — answer it.", note),
                              _window_payload(wid, name, note,
                                              run_for_window(name, runs)),
                              wid=wid))
    return out


def run_events(runs: list[dict], seen: dict[str, str],
               windows: dict[str, str] | None = None,
               now_epoch: str | None = None) -> tuple[list[dict], dict[str, str]]:
    """Events from the dispatcher runs DB: → ``awaiting_review``, ``needs_human``, ``failed``.

    Edge-triggered on the run's status, against a DURABLE mark: a run parked in
    awaiting_review must announce once, not once per 30s tick, and not again after a
    daemon restart. Runs are delegated work by definition, so these need no watch.

    The event's ``summary`` is one line — a label, the state, the PR, and a *snippet*
    of the title. The run's ``title`` is the whole tracker line (here: a brief with
    landmines and a verify plan), so putting it in the notification pasted the entire
    task body into the orchestrator's window. It lives in the payload instead.

    **These events are ATTRIBUTED from the run row** (:func:`run_wid`), which recorded
    the window's ``@id`` at spawn. That is what makes attribution survive the window's
    death — and the window is ALWAYS dead by now, because a dispatched agent finishes by
    killing its own window. Resolving the id against live tmux at this point (which is
    what this used to do) never fired. A run with no recorded id and no unambiguous live
    window stays ``wid=None`` and belongs to chela itself: an honest ownerless event, not
    a hole.
    """
    out: list[dict] = []
    fresh: dict[str, str] = {}
    for run in runs:
        task_id, status = run.get("task_id"), run.get("status")
        if not task_id:
            continue
        # ⛔ CMX-197: a run that reaches `awaiting_review` announces ONCE, on the status
        # edge — but a CLEAN (or cannot-verify) judge verdict lands on that SAME status:
        # `judge.judge_run` never moves the row (only a BLOCKED verdict does, through
        # `request_changes`, which already fires `run_changes_requested` below). Measured
        # live twice (cmx-195, cmx-196): the judge posted "every guard held" and the
        # orchestrator never heard about it — it only ever hears about FAILURES, because
        # only a failure moves `status`. So for `awaiting_review` the dedup mark carries
        # `judge_state` too: the run re-announces, once, the moment the judge SETTLES
        # (clean or cannot_verify) — not on every transient `running`/retry sample, which
        # would just be noise.
        judge_state = run.get("judge_state") or ""
        # ⚖️🔔 CMX-229 Objective 1: `cannot_verify` is tracked even OFF `awaiting_review` —
        # see the branch below for why (the CAS-refused race, measured live on CMX-227).
        # ⚖️🧊 CMX-239: `J_BLOCKED_RACE` is tracked unconditionally, for the same reason —
        # it is the SAME CAS-refused race, for a verdict `judge.judge_run` records as its
        # OWN distinct value (never plain `J_BLOCKED`, see its CMX-239 comment) precisely
        # so it can never be confused with an ordinary blocked run that later moved on, and
        # so this branch can raise it at full severity instead of a shrug.
        mark = (
            f"{status}:{judge_state}"
            if status == "awaiting_review" or judge_state in (
                judge.J_CANNOT_VERIFY, judge.J_BLOCKED_RACE)
            else status
        )
        prev_mark = seen.get(task_id)
        # Was the run already SITTING in awaiting_review as of the last mark? (Not just
        # "is judge_state new" — a fresh task_id, or one arriving straight from a
        # different status, has never had the plain `run_review` event fire either.)
        was_already_awaiting_review = (prev_mark or "").split(":", 1)[0] == "awaiting_review"
        fresh[task_id] = mark
        if prev_mark == mark:
            continue                      # already announced at this status (+judge verdict)
        wid = run_wid(run, windows, now_epoch)
        title = run.get("title") or ""
        # The branch is the handle a human recognises ("cmx-38"); the id is the handle
        # the dispatcher does. Prefer the branch, fall back to the id.
        label = run.get("branch_name") or task_id
        snippet = _short_title(title)
        payload = {"task_id": task_id, "run_status": status, "title": title,
                   "branch_name": run.get("branch_name"),
                   "window_name": run.get("window_name"),
                   "window_id": run.get("window_id"),
                   "window_epoch": run.get("window_epoch"),
                   "pr_url": run.get("pr_url"),
                   "pr_state": run.get("pr_state"), "attempt": run.get("attempt"),
                   "started_at": run.get("started_at"), "ended_at": run.get("ended_at")}
        if judge_state == judge.J_CANNOT_VERIFY and status != "awaiting_review":
            # ⚖️🔔 CMX-229 Objective 1. `chela/judge.py`'s CAS-refused path: the run left
            # `awaiting_review` (a human merged it, CI got there first, a fresh review sent
            # it back) WHILE the judge was still working, so `request_changes`'s own CAS
            # correctly refused to resurrect the row — but `set_judge_state` still recorded
            # CANNOT_VERIFY on it, and the `awaiting_review`-gated branch below NEVER fires
            # again for a row that has already moved on. Measured live on CMX-227: `chela
            # events --type run_judge_cannot_verify` showed nothing for that run — the
            # state sat in the row and the inbox never fired; the only reason anyone knew
            # is that a human happened to look. Every OTHER judge outcome only matters
            # while still `awaiting_review` (a clean verdict is moot once merged, a rework
            # verdict is superseded by the next dispatch) — but "the judge could not do its
            # job" is, if anything, MORE urgent once the code already shipped: it is the
            # one case nothing else will ever surface.
            payload["judge_state"] = judge_state
            payload["judge_detail"] = run.get("judge_detail")
            payload["judge_sha"] = run.get("judge_sha")
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            detail = str(run.get("judge_detail") or "")[:140]
            out.append(_event(
                "run_judge_cannot_verify",
                f"⚖️ {label} — judge CANNOT VERIFY (the run already moved to {status!r} "
                f"while it was working) — needs a human look"
                f"{': ' + detail if detail else ''} — {ref}"
                f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
        elif judge_state == judge.J_BLOCKED_RACE:
            # ⚖️🧊 CMX-239. The twin of the CANNOT_VERIFY branch above, for the CAS-refused
            # race on a BLOCKING verdict: the run left `awaiting_review` (a human merged it,
            # or CI got there first) WHILE the judge was mid-run, so `request_changes`'s CAS
            # correctly refused to send it back — but `judge.judge_run` records this as its
            # OWN state (`J_BLOCKED_RACE`, never plain `J_BLOCKED` — that value also sits on
            # a row long after an ORDINARY blocked run settles, through rework rounds and
            # even an eventual `needs_human` escalation, so reusing it here would make that
            # unrelated later status change misread as this race — see judge.py's comment).
            # No status check needed: `J_BLOCKED_RACE` is set from exactly one place and
            # means exactly one thing, unlike `J_CANNOT_VERIFY` above (which also arises
            # from an ordinary settle while still `awaiting_review`). Ordinarily a `blocked`
            # verdict IS a `changes_requested` transition (`request_changes` sets both
            # atomically) — reaching this branch means that transition was refused, so a
            # guard SURVIVED CORRUPTION on a commit that already shipped, or is about to,
            # with nobody told. That is a strictly more urgent outcome than "cannot verify":
            # the judge did its job and the answer was bad.
            payload["judge_state"] = judge_state
            payload["judge_detail"] = run.get("judge_detail")
            payload["judge_sha"] = run.get("judge_sha")
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            detail = str(run.get("judge_detail") or "")[:140]
            out.append(_event(
                "run_judge_blocked_race",
                f"⚖️⚠️ {label} — a guard SURVIVED CORRUPTION but the run already moved to "
                f"{status!r} before it could be sent back — needs a human look NOW "
                f"(check whether this already shipped)"
                f"{': ' + detail if detail else ''} — {ref}"
                f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
        elif status == "awaiting_review" and judge_state in (judge.J_CLEAN, judge.J_CANNOT_VERIFY):
            # The judge settled while `status` sat still. This is the ONLY place either
            # verdict becomes visible to the orchestrator — `comment_body` already posted
            # it to the PR, but a PR comment is not a push, and CMX-195/196 both sat mute
            # for hours until a human happened to look.
            payload["judge_state"] = judge_state
            payload["judge_detail"] = run.get("judge_detail")
            # ⛔ CMX-197 review: a verdict is only meaningful against the commit it judged.
            # This event can sit in the queue for a while behind a busy orchestrator, and
            # the head can move in the meantime (a rework agent, a human's own push, a
            # `chela reopen`) — see `stale_reason`'s live-head check, which re-checks this
            # AT DELIVERY, not here.
            payload["judge_sha"] = run.get("judge_sha")
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            if judge_state == judge.J_CLEAN:
                out.append(_event(
                    "run_judge_clean",
                    f"⚖️ {label} — every guard held, clean and MERGEABLE — {ref}"
                    f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
            else:
                detail = str(run.get("judge_detail") or "")[:140]
                out.append(_event(
                    "run_judge_cannot_verify",
                    f"⚖️ {label} — judge CANNOT VERIFY, needs a human look"
                    f"{': ' + detail if detail else ''} — {ref}"
                    f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
        elif status == "awaiting_review" and not was_already_awaiting_review:
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            out.append(_event("run_review",
                              f"📥 {label} awaiting review — {ref}"
                              f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
        # else: still awaiting_review, and the judge_state moved between two NON-terminal
        # values (e.g. "" → "running", or one retry's "running" to the next) — silently
        # absorbed. `fresh[task_id]` is already updated above, so this transition itself
        # never re-fires.
        elif status == "needs_human":
            # The rework loop gave up (CMX-68): the PR was sent back MAX_REWORKS times and
            # still fails review. This is the one run state a human MUST see — the loop is
            # bounded precisely so it surfaces here instead of spinning — so it carries the
            # HISTORY: every verdict this run ever received, not just the last thing said.
            # Nothing has been thrown away: the branch, the worktree and the PR are intact.
            reviews = _reviews(run)
            payload["rework_count"] = run.get("rework_count") or 0
            payload["reviews"] = reviews
            payload["last_error"] = run.get("last_error")
            payload["worktree_path"] = run.get("worktree_path")
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            out.append(_event(
                "run_needs_human",
                # No parens, no `(s)`: the summary is sanitized before it is typed at a
                # prompt (_event), and bracket punctuation comes back out mid-word.
                f"📥 {label} NEEDS A HUMAN — reworks: {payload['rework_count']} · verdicts "
                f"on the row: {len(reviews)} · the PR still fails review — {ref}"
                f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
        elif status == "changes_requested":
            # ⛔ NOT a silent state (CMX-68 review). A run sits here waiting for a dispatcher
            # tick to re-spawn it — and if the queue is HELD, the WORKFLOW.md does not parse,
            # or the workflow was dropped from CHELA_DISPATCH_WORKFLOWS, that tick never
            # comes and the run parks here forever. Announcing the edge is what makes the
            # silence audible: the verdict landed, and the loop is supposed to turn. If it
            # doesn't, `chela doctor` says so (runtime_truth._parked_report) — but the
            # orchestrator hears about the state at all only because of this line.
            payload["rework_count"] = run.get("rework_count") or 0
            payload["reviews"] = _reviews(run)
            payload["last_error"] = run.get("last_error")
            payload["worktree_path"] = run.get("worktree_path")
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            nxt = (f"rework {payload['rework_count'] + 1}" if not run.get("last_error")
                   else f"RETRY after: {str(run['last_error'])[:60]}")
            out.append(_event(
                "run_changes_requested",
                f"📥 {label} sent back for rework — {nxt} — the next dispatcher tick "
                f"re-spawns it in its own worktree — {ref}"
                f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
        elif status == "failed":
            err = (run.get("last_error") or "").splitlines()
            payload["last_error"] = run.get("last_error")
            out.append(_event("run_failed",
                              f"📥 {label} FAILED{' — ' + err[0][:120] if err else ''}"
                              f"{' · ' + snippet if snippet else ''}", payload, wid=wid))
    return out, fresh


def _reviews(run: dict) -> list[dict]:
    """The run's verdict history (``dispatcher.reviews_of``), tolerantly.

    Parsed here rather than imported so ``run_events`` stays a pure function over plain
    dicts — it is fed a runs snapshot, not a DB. A row written by an older dispatcher has
    no history and reads as an empty list.
    """
    try:
        parsed = json.loads(run.get("review_history") or "[]")
    except (json.JSONDecodeError, TypeError, ValueError):
        return []
    return [r for r in parsed if isinstance(r, dict)] if isinstance(parsed, list) else []


# --- the daemon tick ------------------------------------------------------------

def status_snapshot() -> dict[str, str]:
    """``{wid: busy|idle|waiting}`` for every live window running claude."""
    return agent_manager.status_by_wid()


def stale_reason(event: dict, runs: list[dict],
                 live_heads: dict[str, str] | None = None) -> str | None:
    """Why this queued event is no longer TRUE — or None if it still is.

    A queued event is a claim about the past. Delivery is deferred until the
    orchestrator is idle, and the world does not wait: live on 2026-07-13 an
    ``awaiting_review`` event was pushed *after* its PR had been reviewed and merged,
    so the orchestrator was handed an instruction to do work that was already done.
    The event was true when queued and rotted in the queue — which is exactly why the
    check belongs HERE, at delivery, and not only at queue time.

    Only claims that can rot are re-checked, and only against the runs list the tick
    already fetched: no network, no DB read, no I/O of any kind, so this stays safe to
    call inside the store lock. Window events (finished/died/blocked) assert something
    that already happened and are left alone.

    ``live_heads`` is the one exception to "no I/O here" — it is a ``{task_id: sha}`` map
    the CALLER fetched live from GitHub (:func:`_live_judge_heads`), OUTSIDE this lock,
    the same way ``runs``/``windows``/``statuses`` are: slow work happens before
    :func:`locked_store` is entered, never inside it. A judge verdict
    (``run_judge_clean``/``run_judge_cannot_verify``) is a claim about a SPECIFIC commit,
    not just about the run's status — ``request_changes``/``merge`` never touch ``status``
    on a clean verdict, so a push that supersedes the judged commit rots the verdict
    while the status-based checks above stay silent (CMX-197 review: "clean and
    MERGEABLE" about a commit that may no longer be the head — the merge gate itself
    (``dispatcher.py`` ``reopen``'s new-commit gate) already treats an unrefreshed
    cached sha as untrustworthy for exactly this reason). ``live_heads`` defaults to
    ``None`` (not "empty dict") so a caller that never fetched it — a unit test exercising
    only the status-staleness path — leaves this check completely inert, rather than
    having every judge event read as unverifiable and drop. A task id that IS present in
    a supplied map but resolves to nothing (the live read failed) is treated the same
    way: best-effort, like every other GitHub read in this codebase — it does not itself
    manufacture staleness, it only catches an ACTUAL, OBSERVED mismatch.

    ⚖️🔔 CMX-229 Objective 1 (and ⚖️🧊 CMX-239's twin, ``run_judge_blocked_race``):
    ``run_judge_cannot_verify`` is handled SEPARATELY from its sibling ``run_judge_clean``
    — it is a judge outcome that must reach someone regardless of what happened to the run
    afterward (see :func:`run_events`'s matching comment), so it is exempt from the
    status/pr_state rot below on purpose. Only the live-head supersession check still
    applies to it. ``run_judge_blocked_race`` — the SAME CAS-refused race, but for a
    CONFIRMED blocking finding rather than an unknown — gets the identical treatment: it
    is strictly the more urgent of the two, never the less.
    """
    kind = event.get("kind")
    if kind not in ("run_review", "run_failed", "run_needs_human", "run_changes_requested",
                     "run_judge_clean", "run_judge_cannot_verify", "run_judge_blocked_race"):
        return None
    payload = event.get("payload") or {}
    task_id = payload.get("task_id")
    if not task_id:
        return None                        # legacy/unstructured event — deliver it
    run = next((r for r in runs if r.get("task_id") == task_id), None)
    if run is None:
        return "run row is gone"
    status, pr_state = run.get("status"), run.get("pr_state")
    if kind in ("run_judge_cannot_verify", "run_judge_blocked_race"):
        # ⚖️🔔 CMX-229 Objective 1 / ⚖️🧊 CMX-239. Unlike `run_review`/`run_judge_clean`,
        # neither "the judge could not verify this commit" nor "a guard SURVIVED
        # corruption" is superseded by whatever happened to the run afterward — it is not a
        # request to go look at `awaiting_review` (there may be nothing there to look at any
        # more), it is a fact about a commit. Dropping it once the run left `awaiting_review`
        # (or `changes_requested`, for the blocking twin) or its PR merged/closed is exactly
        # the failure CMX-229 closed for the unknown case and CMX-239 closes for the
        # confirmed one: that is the CAS-refused race (CMX-227, measured live) where the run
        # moves on WHILE the judge is still working, and the resulting verdict is the one
        # record that a SURVIVED-mutation finding might exist, undetected, on a commit
        # already shipped. Only a genuinely superseded commit (a newer head already judged)
        # rots this claim.
        if live_heads is not None:
            judged_sha = payload.get("judge_sha")
            live_sha = live_heads.get(task_id)
            if judged_sha and live_sha and live_sha != judged_sha:
                return (f"PR head moved past the judged commit ({judged_sha[:12]} -> "
                        f"{live_sha[:12]}) — the verdict no longer applies to the current head")
        return None
    if kind in ("run_review", "run_judge_clean"):
        if status != "awaiting_review":
            # Includes the rework loop's own transition: an `awaiting_review` event that
            # was still queued when the reviewer sent the PR back is a claim about a run
            # that has moved on, and delivering it would ask for a review twice. A judge
            # verdict is the same claim ("this PR is sitting in awaiting_review, look at
            # it") with extra detail, so it rots the same way.
            return f"run is now {status!r}, not awaiting_review"
        if pr_state in ("merged", "closed"):
            # The row can lag the PR by a reconcile tick: pr_state is refreshed before
            # awaiting_review → done. A merged PR is not awaiting review either way.
            return f"PR is {pr_state}"
        if kind == "run_judge_clean" and live_heads is not None:
            judged_sha = payload.get("judge_sha")
            live_sha = live_heads.get(task_id)
            if judged_sha and live_sha and live_sha != judged_sha:
                return (f"PR head moved past the judged commit ({judged_sha[:12]} -> "
                        f"{live_sha[:12]}) — the verdict no longer applies to the current head")
    elif kind == "run_failed" and status != "failed":
        return f"run is now {status!r}, not failed"
    elif kind == "run_needs_human":
        if status != "needs_human":
            return f"run is now {status!r}, not needs_human"
        if pr_state in ("merged", "closed"):
            return f"PR is {pr_state}"      # a human merged it anyway — nothing to escalate
    elif kind == "run_changes_requested":
        # This one rots FAST and by design: the dispatcher re-spawns a sent-back run on the
        # very next tick, so an event that waited for an idle orchestrator usually finds the
        # run already `running` again. That is the loop working, and saying so would be
        # noise. It is delivered only while the run is still waiting.
        if status != "changes_requested":
            return f"run is now {status!r}, not changes_requested"
        if pr_state in ("merged", "closed"):
            return f"PR is {pr_state}"      # merged despite the verdict — the loop is moot
    return None


def _undeliverable(store: dict, state: str, wid: str, why: str,
                   queued: int, alarms: list | None, now: float | None = None) -> None:
    """An address that cannot take the work SHOUTS. This is CMX-77's whole point.

    The 2026-07-14 outage was not that the address rotted — addresses rot; a tmux server can
    be OOM-killed at any moment. It was that NOTHING SAID SO. Five ``run_review`` events
    queued behind a dead ``@0``, the daemon logged nothing, doctor stayed green, and the
    orchestrator sat waiting for an inbox that had quietly stopped existing. So this fires on
    every surface a human or an agent actually watches:

    * ``ERROR`` in the daemon log — every tick, for as long as it is true: this is not a
      transient, it does not fix itself, and a once-only line scrolls away in a minute;
    * a durable ``inbox_undeliverable`` event — the Feed and the audit trail;
    * a red ``chela doctor`` (``inbox.address``), which is what any of this is checked with;
    * a phone push, if notifications are configured AND the address is still dead after
      ``config.INBOX_ALARM_GRACE_SECONDS`` — see the CMX-113 note below.

    The log line repeats; the durable event does NOT — it is de-duped on the address ITSELF,
    in the store, so an alarm that stays true for a day is one row in the Feed, not 2,880.
    Handed back to the caller (``alarms``) rather than sent from here: this runs under the
    store lock, and a push is an HTTP POST with a ten-second timeout — doing it here would
    hold the lock across the network and block the very command that FIXES this
    (``chela watch``, which takes the same lock).

    **De-duped on ``wid`` alone — never on ``state`` too.** ``gone`` and ``dangling`` are not
    two different failures; they are two READINGS of the SAME dead address, and a live fleet
    flips between them (a status map that hiccups empty for one tick reads as ``dangling``
    instead of ``gone``, an epoch probe that momentarily fails to compare reads the other way).
    Observed live 2026-07-19: one stale ``@1`` produced a burst of alternating "is gone" /
    "is dangling" pushes for a single days-old address because the OLD key
    (``f"{state}:{wid}"``) changed on every flip and re-armed the alarm. The address is what
    the human has to go fix (``chela watch``); the reading is commentary on why, and belongs
    in ``why``/the log line, never in the de-dup key.

    **CMX-113: the push has its OWN, later, gate — the durable record does not.** A reboot /
    tmux-restart / orchestrator handoff makes the address dangle for exactly as long as it
    takes the next session to run ``chela watch`` (or any dispatch) — CMX-82's self-heal often
    beats that anyway. That is an EXPECTED, SELF-HEALING blip, and Liav ate a phone buzz for
    every single one of them on 2026-07-19, even with CMX-110's per-address de-dup, because the
    old code pushed on the very first tick that saw the address dead. The durable event / log
    ERROR / doctor still fire on that same first tick, unconditionally — a human mid-debugging
    session must still see this instantly, which is CMX-77's whole point and is NOT being
    loosened here. Only the proactive, phone-in-pocket push waits: the first sighting of a dead
    address stamps ``address_alarm_since`` and pushes nothing; every later tick re-checks the
    SAME address and fires the push (once — ``address_alarm_pushed`` latches it) only once it
    has stayed dead for the whole grace window, i.e. only once it has stopped looking like the
    blip it usually is.
    """
    log.error("inbox: UNDELIVERABLE (%s) — %d event(s) queued for %s: %s",
              state, queued, wid, why)
    now = time.time() if now is None else now
    key = wid
    payload = {"orchestrator": wid, "state": state, "detail": why, "queued": queued,
               "epoch": store.get("orchestrator_epoch"),
               "orchestrator_name": store.get("orchestrator_name")}
    if store.get("address_alarm") != key:
        # First sighting of THIS dead address: the durable record fires now, unconditionally —
        # the Feed/audit-trail/doctor must never wait on the grace window. The push does not;
        # it only starts the clock.
        store["address_alarm"] = key
        store["address_alarm_since"] = now
        store["address_alarm_pushed"] = False
        if alarms is not None:
            alarms.append({
                "summary": f"📥 THE DECISIONS INBOX CANNOT DELIVER — {queued} event(s) are "
                           f"queued for {wid} and that address is {state}. {why}",
                "payload": payload,
                "durable": True,
                "push": False,
            })
        return
    # Same address as last tick: already durably recorded. The only open question is whether
    # it has now outlasted the grace window and earns the (one-time) phone push.
    if store.get("address_alarm_pushed"):
        return                             # already buzzed for this outage
    since = store.get("address_alarm_since")
    if since is None or now - since < config.INBOX_ALARM_GRACE_SECONDS:
        return                             # still inside the grace window — could be the blip
    store["address_alarm_pushed"] = True
    if alarms is None:
        return
    elapsed = int(now - since)
    alarms.append({
        "summary": f"📥 THE DECISIONS INBOX STILL CANNOT DELIVER after {elapsed}s — "
                   f"{queued} event(s) are queued for {wid} and that address is {state}. {why}",
        "payload": {**payload, "elapsed_seconds": elapsed},
        "durable": False,
        "push": True,
    })


def raise_alarms(alarms: list[dict]) -> None:
    """Publish the undeliverable alarms a tick raised — OUTSIDE the store lock.

    Each alarm says which surface it is for (:func:`_undeliverable`): the durable Feed/audit
    record fires the instant the address is seen dead, the phone push only once it has stayed
    dead past ``config.INBOX_ALARM_GRACE_SECONDS`` (CMX-113) — never both from the same alarm.
    ``notify.send`` swallows its own failures, and ``event_log.append`` never raises, so an
    alarm about a broken inbox cannot itself take the daemon down.
    """
    for alarm in alarms:
        if alarm.get("durable"):
            event_log.append("inbox_undeliverable", alarm["summary"], alarm["payload"])
        if alarm.get("push") and notify.enabled():
            notify.send(alarm["summary"],
                        title="chela: the decisions inbox is not being delivered")


def deliver(store: dict, statuses: dict[str, str],
            runs: list[dict] | None = None,
            now_epoch: str | None = None,
            alarms: list[dict] | None = None,
            live_heads: dict[str, str] | None = None) -> list[dict]:
    """Push queued events into the orchestrator — ONLY if its window is ``idle``.

    The gate is a strict equality against ``idle``. ``waiting`` must never be written
    to: that session is sitting on a permission/question prompt, and our paste would
    be consumed as the ANSWER to that prompt. ``busy`` we leave alone by design (never
    interrupt a session mid-thought) — the event just waits for the next idle tick.

    **And the address itself is checked BEFORE the status is** (:func:`address_state`). That
    order is the CMX-77 fix: ``statuses.get(orch) != IDLE`` is also what a DEAD address looks
    like — a window that no longer exists is simply absent from the status map, which is
    indistinguishable from a busy orchestrator, and reads as "wait for the next tick" forever.
    That silence cost five unreviewed PRs. An address that cannot take the work is now refused
    LOUDLY (:func:`_undeliverable`), and an address from a dead tmux epoch is refused even if
    something IS running under that number now — especially then, because that something is
    another agent, and a review instruction pasted into its prompt is one it will act on.

    A live, idle pane in an unsafe INPUT MODE (``!`` bash, ``#`` memory) is the last unsafe
    state, and it is refused by :func:`chela.messenger.send_tmux` itself — one authority,
    every sender. The refusal reaches us as a failed send, which is exactly the behaviour we
    want: the event stays at the head of the durable queue and goes out on a later tick. It
    is HELD, never dropped — the notification still matters once the pane is prose again.
    CMX-77 says WHICH window may be written to; this says WHAT may be typed into it.

    Every event is re-validated against the CURRENT runs (:func:`stale_reason`) on its
    way out; one that has rotted in the queue is dropped and LOGGED — never silently,
    or a real event lost to a bug becomes undebuggable. Only the event's ``summary``
    is pushed: the payload is the record, not the notification. ``live_heads`` is passed
    straight through to :func:`stale_reason` — it was fetched live from GitHub by the
    caller BEFORE this lock was taken (:func:`_live_judge_heads`), so a judge verdict
    whose PR has since moved past the commit it judged is caught here too, not just a
    verdict whose run status moved on.

    Returns the events actually delivered (each exactly once — a delivered event is
    popped from the durable queue before we return, so no tick can re-send it).
    """
    orch = orchestrator_wid(store)
    if not orch or not store["queue"]:
        return []
    state, why = address_state(store, statuses, now_epoch)
    if state in UNDELIVERABLE:
        _undeliverable(store, state, orch, why, len(store["queue"]), alarms)
        return []
    if state == ADDR_UNSTAMPED:
        log.warning("inbox: %s", why)
    if statuses.get(orch) != IDLE:
        return []
    # A real, idle window at the address: whatever it was alarming about is over, and the
    # next failure — even the same kind — is news again rather than a de-duped repeat.
    _clear_address_alarm(store)
    runs = runs or []

    sent: list[dict] = []
    while store["queue"] and len(sent) < MAX_DELIVERIES_PER_TICK:
        event = store["queue"][0]
        stale = stale_reason(event, runs, live_heads)
        if stale:
            store["queue"].pop(0)
            log.warning("inbox: dropping stale %s (%s) — %s", event.get("kind"),
                        (event.get("payload") or {}).get("task_id") or "?", stale)
            continue                       # a dropped event doesn't spend the tick's slot
        text = render(event)
        if not text:
            store["queue"].pop(0)          # nothing to say — don't wedge the queue on it
            log.warning("inbox: dropping unrenderable %s event", event.get("kind"))
            continue
        # CMX-223: peer socket first (bypasses the pane's terminal-input-mode risk
        # entirely — CMX-79 doesn't apply), tmux paste as fallback. A handoff whose
        # receipt comes back held/denied/expired is a DROP, not a delivery, even
        # though the socket accepted it — recorded, and HOLD (never drop, same as
        # a tmux refusal): unlike an agent-to-agent room dispatch, this queue's
        # events are merge verdicts the orchestrator must eventually see, so a
        # gate that is transient today is worth retrying on a later tick.
        peer = messenger.send_peer(orch, "chela-inbox", text)
        if peer.handed_off and peer.status in messenger.ADVERSE_RECEIPT_STATUSES:
            log.warning("inbox: delivery of %s to %s was %s; holding it queued",
                        event.get("kind"), orch, peer.status)
            event_log.append(
                "inbox_receipt", f"📭 inbox {event.get('kind')} {peer.status} at {orch}",
                {"kind": event.get("kind"),
                 "task_id": (event.get("payload") or {}).get("task_id"),
                 "status": peer.status}, wid=orch,
            )
            break
        if not (peer.handed_off or messenger.send_tmux(orch, text)):
            # Includes the unsafe-input-mode refusal: HOLD, never drop. The pane will be
            # back at its prose prompt eventually, and the event is still true.
            log.warning("inbox: delivery of %s to %s refused/failed; holding it queued",
                        event.get("kind"), orch)
            break
        store["queue"].pop(0)
        sent.append(event)
        log.info("inbox: delivered %s -> %s", event["kind"], orch)
    return sent


# --- self-heal: re-resolve a renumbered address from the session's identity (CMX-82) ---

def resolve_heal(store: dict, statuses: dict[str, str],
                 now_epoch: str | None = None) -> tuple[str, str] | None:
    """A live window running the orchestrator's session, when its address has ROTTED — or None.

    The CMX-82 fix. The inbox target was the last consumer keyed on a bare ``@N`` with nothing to
    re-resolve it from, so a tmux restart left it dangling until a HUMAN re-ran ``chela watch``.
    Now the orchestrator's recorded session identity (:func:`orchestrator_session`) is re-resolved
    to the window running it TODAY (:func:`chela.sessions.wid_for_session`) — the same wid↔session
    evidence CMX-48/70/77 already trust.

    Attempted ONLY when the address is actually UNDELIVERABLE (dangling / gone): a healthy or
    merely unstamped address is left exactly as registered, and an unregistered/pinned one has no
    identity to heal from. Reads tmux + /proc, so the caller runs this OUTSIDE the store lock;
    :func:`_apply_heal` re-checks under the lock before it trusts the result. ``None`` when there
    is no identity, the address is fine, or the session cannot be found live — never a guess.

    NOTE: this does NOT cover a REBOOT. Healing here re-resolves from the recorded session id,
    and a reboot kills that session outright — so ``wid_for_session`` finds nothing and the
    address dangles until a human runs ``chela watch``. Measured 2026-07-29: the store still
    named session ``50b0b601`` on tmux epoch ``792-…`` with nothing to resolve it to. CMX-194
    shipped the dashboard half (a re-register control on the dangling chip, so the fix is one
    click rather than a shell); the auto-heal half — a name-based resolve, gated unique-or-
    nothing — is deliberately NOT built here and is tracked as **CMX-195**.
    """
    session = orchestrator_session(store)
    if not session:
        return None
    state, _ = address_state(store, statuses, now_epoch)
    if state not in UNDELIVERABLE:
        return None
    try:
        wid = sessions.wid_for_session(session)
    except Exception:
        log.debug("inbox: self-heal resolution failed", exc_info=True)
        return None
    if not wid or wid == orchestrator_wid(store):
        return None
    return session, wid


def _apply_heal(store: dict, heal: tuple[str, str], statuses: dict[str, str],
                now_epoch: str | None, windows: dict[str, str]) -> str | None:
    """Re-point the orchestrator address at the re-resolved window, UNDER the store lock.

    Guarded three ways, because the resolution ran outside the lock and the world moves: the
    recorded identity must still be the one we resolved (a concurrent ``chela watch`` may have
    re-registered a DIFFERENT session), the address must still be undeliverable (that same watch
    may already have fixed it), and the resolved window must be a live claude session right now.
    The healed address is stamped with the CURRENT epoch — it was resolved against the running
    tmux server, so that stamp is honest. Returns the old address on success (the caller
    announces the recovery), else None.
    """
    session, wid = heal
    if orchestrator_session(store) != session:
        return None
    state, _ = address_state(store, statuses, now_epoch)
    if state not in UNDELIVERABLE:
        return None
    if wid not in statuses:
        return None                        # resolved to a window with no claude running: not it
    old = store.get("orchestrator")
    store["orchestrator"] = wid
    store["orchestrator_epoch"] = now_epoch
    store["orchestrator_name"] = windows.get(wid) or store.get("orchestrator_name")
    _clear_address_alarm(store)            # the failure is over — the next one is news again
    return old


def _announce_heal(old: str | None, wid: str, session: str) -> None:
    """A recovered address is LOUD, but as good news and exactly ONCE.

    CMX-77 made the FAILURE shout on every surface, every tick; the recovery is a single durable
    record and a log line — the real signal is that the held queue now flows to ``wid`` on the
    next idle tick. Written outside the store lock (an ``event_log`` append is another file's
    I/O), and ``event_log.append`` never raises, so announcing a recovery can never take the
    inbox down.
    """
    log.warning("inbox: self-healed orchestrator address %s -> %s (session %s)",
                old or "?", wid, session)
    event_log.append(
        "inbox_self_healed",
        f"📥 the decisions inbox re-resolved its orchestrator address: {old or '?'} → {wid} — "
        "same session, renumbered by a tmux restart. The held queue now delivers.",
        {"old": old, "wid": wid, "session": session}, wid=wid, session_id=session)


def _live_judge_heads(runs: list[dict], queue: list[dict]) -> dict[str, str]:
    """``{task_id: live head sha}`` for every judge verdict that could still need delivering.

    ⛔ CMX-197 review: a queued ``run_judge_clean``/``run_judge_cannot_verify`` event is a
    claim about a SPECIFIC commit, and the queue can sit behind a busy orchestrator for a
    while — long enough for a rework agent, a human's own push, or a ``chela reopen`` to
    move the PR's head past the one the judge actually looked at. Reading GitHub is the
    only way to know that happened, so it is done HERE — outside :func:`locked_store`,
    the same way ``runs``/``windows``/``statuses`` are (:func:`tick`'s own rule: slow work
    never happens inside that lock) — and via :func:`chela.dispatcher._read_pr_checks`,
    the SAME live read the merge gate's own new-commit guard uses (``dispatcher.reopen``),
    never the row's own ``pr_head_sha``/``judge_sha`` columns: those are exactly the
    caches whose staleness is the bug this closes.

    Scoped to two sets of runs, so a quiet fleet costs zero extra ``gh`` calls and a run
    whose verdict already delivered costs nothing forever after: runs a judge event is
    ALREADY QUEUED for (``queue``, so a long-parked event keeps getting re-checked every
    tick it sits there), union runs whose ``awaiting_review`` + settled ``judge_state``
    is about to freshly queue ONE this very tick (mirrors :func:`run_events`'s own dedup
    ``mark``, so a first-tick delivery is checked too, not just a re-delivery).
    """
    from chela import dispatcher
    candidates = {
        (e.get("payload") or {}).get("task_id")
        for e in queue
        if e.get("kind") in ("run_judge_clean", "run_judge_cannot_verify")
    }
    for run in runs:
        task_id = run.get("task_id")
        if not task_id or run.get("status") != "awaiting_review":
            continue
        judge_state = run.get("judge_state") or ""
        if judge_state in (judge.J_CLEAN, judge.J_CANNOT_VERIFY):
            candidates.add(task_id)
    candidates.discard(None)
    if not candidates:
        return {}
    runs_by_id = {r.get("task_id"): r for r in runs}
    heads: dict[str, str] = {}
    for task_id in candidates:
        run = runs_by_id.get(task_id)
        if run is None:
            continue
        wf_path = run.get("workflow_path")
        repo_dir = str(Path(wf_path).parent) if wf_path else None
        ci = dispatcher._read_pr_checks(run.get("pr_url"), repo_dir)
        if ci.head_sha:
            heads[task_id] = ci.head_sha
    return heads


def tick(prev: dict[str, str], runs: list[dict] | None = None) -> dict[str, str]:
    """One daemon pass: scan for events, queue them, deliver what the gate allows.

    Returns the current status snapshot, to be fed back in as ``prev`` next tick (the
    caller holds it in memory, exactly like ``notify.check_waiting``'s ``seen`` set).
    """
    if not enabled():
        return {}

    # Slow work FIRST, outside the lock: `claude agents --json` (a heavyweight process)
    # and the runs query. Holding the store lock across them is what let a tick clobber
    # a concurrent `chela watch` — the very bug locked_store() exists to close.
    statuses = status_snapshot()
    if runs is None:
        from chela import dispatcher
        runs = dispatcher.list_runs()
    # The live window table, read ONCE and outside the lock (tmux is slow-ish). It is
    # what attributes an event to an agent — both the window events and, since the Feed's
    # lanes, the run events (see wid_for_window_name).
    windows = discovery.get_windows_by_id()
    # The tmux server that is issuing window ids RIGHT NOW — read once, outside the lock,
    # and handed to every reader of a persisted `@N`. It is what tells an id that still
    # names its window from one that is a number tmux has since given to somebody else.
    now_epoch = epoch.current()
    # If the recorded orchestrator address has ROTTED (a tmux restart renumbered it, or the
    # session exited and came back under a new `@N`), re-resolve it from the session's identity
    # instead of holding the queue until a human re-runs `chela watch` (CMX-82). Resolved OUTSIDE
    # the lock — it reads tmux + /proc — and applied under it, where the address is re-checked.
    pre_store = load()
    heal = resolve_heal(pre_store, statuses, now_epoch)
    # The live GitHub head of every judge verdict that could still need delivering —
    # also read OUTSIDE the lock (`gh` is slow), also before this tick's own new events
    # are computed, since a verdict about to be freshly queued needs the same check as
    # one that has been sitting in the queue for ticks (see `_live_judge_heads`).
    live_heads = _live_judge_heads(runs, pre_store.get("queue", []))
    alarms: list[dict] = []                # raised inside the lock, published outside it

    with locked_store() as store:
        healed_from = _apply_heal(store, heal, statuses, now_epoch, windows) if heal else None
        events = agent_events(prev, statuses, store, runs, windows=windows,
                              now_epoch=now_epoch)
        r_events, store["runs_seen"] = run_events(runs, store.get("runs_seen", {}),
                                                  windows=windows, now_epoch=now_epoch)
        events += r_events

        for event in events:
            # A `silent` event carries no message — it exists only to retire a watch
            # whose window went away because the work SUCCEEDED. Queueing anything for
            # it would be the self-contradiction (run_events already announced the run).
            if not event.get("silent"):
                store["queue"].append(event)
            watch_key = event.get("watch_key") or event.get("wid")
            if event.get("clear_watch") and watch_key:
                # Interest is satisfied — one dispatch, one completion. (A "blocked"
                # event keeps the watch: the agent still owes you the finish.) `watch_key`
                # rather than `wid` because a watch retired for having a DEAD id must not
                # be attributed to whatever holds that id today (see _epoch_lost_event).
                store["watches"].pop(watch_key, None)
            log.info("inbox: %s %s (%s)", "resolved" if event.get("silent") else "queued",
                     event["kind"], event.get("wid") or "run")

        # `runs` and `live_heads` are both fetched above, outside the lock — re-validating
        # against them is a dict/list scan, so the critical section stays as short as it was.
        deliver(store, statuses, runs, now_epoch=now_epoch, alarms=alarms,
               live_heads=live_heads)

    # A self-heal is announced once, OUTSIDE the lock (an event_log append is another file's
    # I/O): the address just recovered from a renumbering, and the held queue — delivered above
    # in the same tick, now that `deliver` sees the healed address — is already on its way.
    if healed_from is not None:
        _announce_heal(healed_from, heal[1], heal[0])
    # The durable record. Written OUTSIDE the store lock — an append is another file's
    # I/O, and locked_store()'s one rule is that nothing slow happens inside it. EVERY
    # event is logged, including the `silent` ones (a watch retired because the work
    # succeeded is a fact worth having): the queue is what the orchestrator is TOLD, the
    # log is what HAPPENED, and conflating the two is what left the inbox with no
    # history to reconcile against. `event_log.append` never raises, so this cannot take
    # the inbox down with it.
    for event in events:
        event_log.from_inbox(event)
    # ...and the same rule for the alarm: an inbox that cannot deliver is the loudest thing
    # this module has to say, and saying it costs a file append and (if configured) an HTTP
    # POST with a ten-second timeout. Neither belongs inside the lock that `chela watch` —
    # the command that FIXES a dangling address — has to take.
    raise_alarms(alarms)
    return statuses
