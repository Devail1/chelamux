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

import json
import logging
import os
import time
from pathlib import Path

from chela import agent_manager, discovery, messenger
from chela.config import CHELA_DIR, INBOX_ENABLED

log = logging.getLogger(__name__)

# Statuses `claude agents --json` reports (see agent_manager.session_status_map).
BUSY, IDLE, WAITING = "busy", "idle", "waiting"

# How many events go out per tick. ONE: a delivery makes the orchestrator busy, and
# a second paste would land mid-thought (or, worse, race its status back to idle).
# The rest of the queue drains on subsequent idle ticks, oldest first.
MAX_DELIVERIES_PER_TICK = 1


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
    store = load()
    if by:
        store["orchestrator"] = by
    target = orchestrator_wid(store)
    if target and wid == target:
        return {"ok": False, "error": "refusing to watch the orchestrator's own window"}
    if wid not in discovery.get_windows_by_id():
        return {"ok": False, "error": f"no such window: {wid}"}
    store["watches"][wid] = {"note": note.strip(), "since": time.time()}
    save(store)
    return {"ok": True, "wid": wid, "note": note.strip(), "orchestrator": target}


def unwatch(wid: str) -> dict:
    store = load()
    existed = store["watches"].pop(wid, None) is not None
    save(store)
    return {"ok": existed, "wid": wid}


def watches() -> dict:
    return load()["watches"]


# --- event generation (pure — no tmux, no send) ---------------------------------

def _line(wid: str, name: str, body: str, note: str = "") -> str:
    """One compact, actionable line. The orchestrator reads it as an instruction."""
    tail = f' — note: "{note}"' if note else ""
    return f"📥 {wid} ({name}) {body}{tail}"


def agent_events(prev: dict[str, str], cur: dict[str, str], store: dict) -> list[dict]:
    """Events from agent status transitions, scoped to WATCHED windows only.

    Edge-triggered (mirrors :func:`chela.notify.check_waiting`): an event fires on the
    transition, so a window that sits idle — or sits waiting — across many ticks is
    announced exactly once. ``prev`` empty (a fresh daemon) baselines silently rather
    than announcing every idle agent at once.

    The orchestrator's own window is never a source, so the busy→idle its own reply
    produces (including the reply to one of our own pushes) can never become an event.
    """
    orch = orchestrator_wid(store)
    names = discovery.get_windows_by_id()
    out: list[dict] = []
    for wid, meta in sorted(store["watches"].items()):
        if wid == orch:
            continue                      # never notify about the orchestrator itself
        note = (meta or {}).get("note", "")
        name = names.get(wid, wid)
        was, now = prev.get(wid), cur.get(wid)
        if was is None:
            continue                      # no baseline yet — nothing to compare against
        if was == BUSY and now == IDLE:
            out.append({"kind": "finished", "wid": wid, "clear_watch": True,
                        "text": _line(wid, name, "finished the task you dispatched — "
                                                 "verify + commit.", note)})
        elif was != WAITING and now == WAITING:
            # Keep the watch: it is blocked, not done — it still owes you the work.
            out.append({"kind": "blocked", "wid": wid, "clear_watch": False,
                        "text": _line(wid, name, "is BLOCKED on a prompt (permission/"
                                                 "question) — answer it.", note)})
        elif was in (BUSY, IDLE, WAITING) and now is None and wid not in names:
            out.append({"kind": "died", "wid": wid, "clear_watch": True,
                        "text": _line(wid, name, "DIED mid-task (window gone) — "
                                                 "the work was not finished.", note)})
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
    store = load()
    statuses = status_snapshot()

    if runs is None:
        from chela import dispatcher
        runs = dispatcher.list_runs()

    events = agent_events(prev, statuses, store)
    r_events, store["runs_seen"] = run_events(runs, store.get("runs_seen", {}))
    events += r_events

    for event in events:
        store["queue"].append(event)
        if event.get("clear_watch") and event.get("wid"):
            # Interest is satisfied — one dispatch, one completion. (A "blocked"
            # event keeps the watch: the agent still owes you the finish.)
            store["watches"].pop(event["wid"], None)
        log.info("inbox: queued %s (%s)", event["kind"], event.get("wid") or "run")

    deliver(store, statuses)
    save(store)
    return statuses
