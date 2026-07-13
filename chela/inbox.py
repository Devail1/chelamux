"""Decisions inbox — the orchestration loop's missing half.

An orchestrator agent is a Claude Code session: it can only act when a human
messages it, or when a background task it started exits. So an agent FINISHING is
structurally invisible to it — on 2026-07-13 an agent was dispatched, finished, and
nothing told the orchestrator; it polled the pane, and the human became the message
bus ("he's done"). This closes that loop: agent/run events are pushed straight into
the orchestrator's session, so it wakes up and acts.

**Push, gated on idle.** An event is delivered with :func:`chela.messenger.send_tmux`
ONLY when the orchestrator's window is ``idle``. Otherwise it queues (durably) and
goes out on the next idle tick. Two rules make that safe:

* ``idle`` is checked STRICTLY — ``not busy`` is not good enough. A ``waiting``
  session is sitting on a permission/question prompt, and pasting into it would
  ANSWER THE GATE with our notification. Only a session that is genuinely idle at
  its prompt is ever written to.
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

State (watches, the queue, and the run-status marks) lives in one JSON file under
``$CHELA_DIR`` so a daemon restart neither loses a pending event nor re-fires an old
one. Turn the whole thing off with ``CHELA_INBOX_ENABLED=false``.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

from chela import agent_manager, discovery, messenger, transcripts
from chela.config import CHELA_DIR, INBOX_ENABLED

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
SETTLED_RUN_STATES = ("awaiting_review", "done", "failed")

# How long a vanished window's run row gets to settle before we call it a death.
# The window dies a moment BEFORE the row lands: `chela task-finished` flips the run
# to awaiting_review and kills the tmux window, and the daemon can easily sample the
# gone window while the write is still in flight. Deciding on the first sample is what
# made a successful agent get reported as DIED. So the first tick that sees the window
# gone only STAMPS it; the claim is made a tick later, re-reading the run state.
DEATH_CONFIRM_SECONDS = 30


def enabled() -> bool:
    return INBOX_ENABLED


def store_path() -> Path:
    return Path(os.environ.get("CHELA_INBOX_FILE") or (CHELA_DIR / "inbox.json"))


def _empty() -> dict:
    return {"orchestrator": None, "watches": {}, "queue": [], "runs_seen": {}}


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

def orchestrator_wid(store: dict | None = None) -> str | None:
    """The window we may push into: ``$CHELA_ORCHESTRATOR_WID``, else the registered one.

    An env pin always wins (an operator overriding the fleet's wiring). Otherwise it
    is whatever session registered itself by calling ``chela watch`` — i.e. the
    session that actually delegates work. None means "nobody is listening", and the
    inbox stays completely inert: we never fall back to a guess.
    """
    env = (os.environ.get("CHELA_ORCHESTRATOR_WID") or "").strip()
    if env:
        return env
    store = load() if store is None else store
    return store.get("orchestrator")


# --- watches: the orchestrator registers interest when it delegates -------------

def watch(wid: str, note: str = "", *, by: str | None = None) -> dict:
    """Register interest in ``wid``: report when it finishes, blocks, or dies.

    ``by`` is the caller's own window (``$CHELA_WID`` via
    :func:`chela.orchestrator.self_wid`) and registers it as THE orchestrator — the
    session that delegates is the session that gets told. Watching your own window is
    refused: that is the self-notify loop, and it is closed here at the source.
    """
    names = discovery.get_windows_by_id()          # slow-ish (tmux) — do it OUTSIDE the lock
    if wid not in names:
        return {"ok": False, "error": f"no such window: {wid}"}
    with locked_store() as store:                  # ...so a concurrent daemon tick can't
        if by:                                     #    clobber the watch we are writing
            store["orchestrator"] = by
        target = orchestrator_wid(store)
        if target and wid == target:
            return {"ok": False, "error": "refusing to watch the orchestrator's own window"}
        # `since` is the completion evidence line: work the transcript shows AFTER this
        # instant is work this dispatch caused (see agent_events). `name` outlives the
        # window itself and is what links it back to its run row (see run_for_window).
        store["watches"][wid] = {"note": note.strip(), "since": time.time(),
                                 "name": names[wid]}
    return {"ok": True, "wid": wid, "note": note.strip(), "orchestrator": target}


def unwatch(wid: str) -> dict:
    with locked_store() as store:
        existed = store["watches"].pop(wid, None) is not None
    return {"ok": existed, "wid": wid}


def watches() -> dict:
    return load()["watches"]


# --- event generation (pure — no tmux, no send) ---------------------------------

def _line(wid: str, name: str, body: str, note: str = "") -> str:
    """One compact, actionable line. The orchestrator reads it as an instruction."""
    tail = f' — note: "{note}"' if note else ""
    return f"📥 {wid} ({name}) {body}{tail}"


def did_work_since(wid: str, since: float) -> bool:
    """Did this agent's transcript gain an ASSISTANT turn after ``since``?

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
    agent replied". Best-effort — no transcript (or an unreadable one) simply yields
    False, leaving the busy→idle edge as the detector.
    """
    cwd = discovery.get_window_cwd_by_id(wid)
    last = transcripts.last_assistant_activity(cwd)
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
    if run is not None and (run.get("status") in SETTLED_RUN_STATES or run.get("pr_url")):
        return {"kind": "completed_gone", "wid": wid, "clear_watch": True, "silent": True}
    if run is None:
        return {"kind": "gone_unknown", "wid": wid, "clear_watch": True,
                "text": _line(wid, name, "window closed — no run state to confirm the "
                                         "outcome; check before assuming either way.", note)}
    return {"kind": "died", "wid": wid, "clear_watch": True,
            "text": _line(wid, name, "DIED mid-task (window gone) — "
                                     "the work was not finished.", note)}


def agent_events(prev: dict[str, str], cur: dict[str, str], store: dict,
                 runs: list[dict] | None = None) -> list[dict]:
    """Events from agent status transitions, scoped to WATCHED windows only.

    Edge-triggered (mirrors :func:`chela.notify.check_waiting`) so a window that sits
    idle — or sits waiting — across many ticks is announced exactly once.

    Completion is detected TWO ways, because the edge alone is not enough. The
    busy→idle transition is the fast, precise signal when we catch it; but a task that
    starts and finishes BETWEEN two polls is never observed as busy, so it would be
    missed forever. So an idle watched window that has done work since its watch was
    registered (:func:`did_work_since`) is also finished. The evidence path needs no
    baseline at all, which is why it also survives a daemon restart mid-task.

    A window that is GONE is corroborated against ``runs`` (see :func:`_gone_event`)
    rather than being called dead on sight, and only after ``DEATH_CONFIRM_SECONDS``,
    so the run row racing the window's exit cannot produce a false death. This writes
    ``name``/``gone_since`` back onto the watch — the caller persists the store.

    The orchestrator's own window is never a source, so the busy→idle its own reply
    produces (including the reply to one of our own pushes) can never become an event.
    """
    orch = orchestrator_wid(store)
    names = discovery.get_windows_by_id()
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
        store["watches"][wid] = meta
        name = names[wid]
        finished_edge = was == BUSY and now == IDLE
        # The not-missed path: idle now, and the transcript proves it worked for us.
        # Gated on `idle` so an agent still mid-task (busy/waiting) is never called done.
        finished_evidence = now == IDLE and did_work_since(wid, since)
        if finished_edge or finished_evidence:
            out.append({"kind": "finished", "wid": wid, "clear_watch": True,
                        "text": _line(wid, name, "finished the task you dispatched — "
                                                 "verify + commit.", note)})
            continue
        if was is None:
            continue                      # no baseline — the transition below needs one
        if was != WAITING and now == WAITING:
            # Keep the watch: it is blocked, not done — it still owes you the work.
            out.append({"kind": "blocked", "wid": wid, "clear_watch": False,
                        "text": _line(wid, name, "is BLOCKED on a prompt (permission/"
                                                 "question) — answer it.", note)})
    return out


def run_events(runs: list[dict], seen: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
    """Events from the dispatcher runs DB: → ``awaiting_review`` and → ``failed``.

    Edge-triggered on the run's status, against a DURABLE mark: a run parked in
    awaiting_review must announce once, not once per 30s tick, and not again after a
    daemon restart. Runs are delegated work by definition, so these need no watch.
    """
    out: list[dict] = []
    fresh: dict[str, str] = {}
    for run in runs:
        task_id, status = run.get("task_id"), run.get("status")
        if not task_id:
            continue
        fresh[task_id] = status
        if seen.get(task_id) == status:
            continue                      # already announced at this status
        title = run.get("title") or task_id
        if status == "awaiting_review":
            pr = run.get("pr_url")
            out.append({"kind": "run_review", "wid": None, "clear_watch": False,
                        "text": f"📥 run {task_id} ({title}) is awaiting review"
                                f"{' — ' + pr if pr else ' — no PR link'}"})
        elif status == "failed":
            err = (run.get("last_error") or "").splitlines()
            out.append({"kind": "run_failed", "wid": None, "clear_watch": False,
                        "text": f"📥 run {task_id} ({title}) FAILED"
                                f"{' — ' + err[0][:120] if err else ''}"})
    return out, fresh


# --- the daemon tick ------------------------------------------------------------

def status_snapshot() -> dict[str, str]:
    """``{wid: busy|idle|waiting}`` for every live window running claude."""
    return agent_manager.status_by_wid()


def deliver(store: dict, statuses: dict[str, str]) -> list[dict]:
    """Push queued events into the orchestrator — ONLY if its window is ``idle``.

    The gate is a strict equality against ``idle``. ``waiting`` must never be written
    to: that session is sitting on a permission/question prompt, and our paste would
    be consumed as the ANSWER to that prompt. ``busy`` we leave alone by design (never
    interrupt a session mid-thought) — the event just waits for the next idle tick.

    Returns the events actually delivered (each exactly once — a delivered event is
    popped from the durable queue before we return, so no tick can re-send it).
    """
    orch = orchestrator_wid(store)
    if not orch or not store["queue"]:
        return []
    if statuses.get(orch) != IDLE:
        return []

    sent: list[dict] = []
    for event in list(store["queue"])[:MAX_DELIVERIES_PER_TICK]:
        if not messenger.send_tmux(orch, event["text"]):
            log.warning("inbox: delivery to %s failed; leaving it queued", orch)
            break
        store["queue"].remove(event)
        sent.append(event)
        log.info("inbox: delivered %s -> %s", event["kind"], orch)
    return sent


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

    with locked_store() as store:
        events = agent_events(prev, statuses, store, runs)
        r_events, store["runs_seen"] = run_events(runs, store.get("runs_seen", {}))
        events += r_events

        for event in events:
            # A `silent` event carries no message — it exists only to retire a watch
            # whose window went away because the work SUCCEEDED. Queueing anything for
            # it would be the self-contradiction (run_events already announced the run).
            if not event.get("silent"):
                store["queue"].append(event)
            if event.get("clear_watch") and event.get("wid"):
                # Interest is satisfied — one dispatch, one completion. (A "blocked"
                # event keeps the watch: the agent still owes you the finish.)
                store["watches"].pop(event["wid"], None)
            log.info("inbox: %s %s (%s)", "resolved" if event.get("silent") else "queued",
                     event["kind"], event.get("wid") or "run")

        deliver(store, statuses)
    return statuses
