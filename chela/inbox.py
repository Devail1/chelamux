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

**An event is a RECORD, not a sentence.** Each event carries a ``kind``, a one-line
``summary`` (what the tmux push renders) and a structured ``payload`` (run id, window,
PR url, task title, timestamps). It used to be a single pre-rendered ``text`` string
built at queue time, which had two consequences, both observed live on 2026-07-13:
the notification was the *entire* TODO item pasted into the orchestrator's window
(``title`` on a run row is the whole ``- [ ]`` line), and nothing downstream could
filter, re-check or re-render it — a string is not a fact. The summary is for the
human/orchestrator; the payload is for the log and the UI that will consume it next.

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

from chela import agent_manager, discovery, event_log, messenger, transcripts
from chela import config
from chela.config import INBOX_ENABLED

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


def _event(kind: str, summary: str, payload: dict, *, wid: str | None = None,
           clear_watch: bool = False, silent: bool = False) -> dict:
    """The queued event record: what happened, in one line, plus the facts behind it.

    ``summary`` is the ONLY thing ever pushed into a session — one line, no newlines.
    ``payload`` is the structured record (run id, wid, PR url, full title, timestamps)
    that a log, a filter, a de-dup or a UI can actually work with, and that
    :func:`stale_reason` re-checks at delivery. Keeping the two apart is what stops a
    notification from being an essay and stops a fact from being un-re-checkable.
    """
    event = {"kind": kind, "summary": " ".join(summary.split()), "payload": payload,
             "wid": wid, "clear_watch": clear_watch, "ts": time.time()}
    if silent:
        event["silent"] = True
    return event


def render(event: dict) -> str:
    """The one line an event renders to. Legacy queues held a pre-rendered ``text``.

    An event queued by an older daemon (before events were records) is still sitting in
    ``inbox.json`` across the upgrade, and must still be deliverable — hence the
    fallback. New events never set ``text``.
    """
    return event.get("summary") or event.get("text") or ""


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


def run_events(runs: list[dict], seen: dict[str, str]) -> tuple[list[dict], dict[str, str]]:
    """Events from the dispatcher runs DB: → ``awaiting_review`` and → ``failed``.

    Edge-triggered on the run's status, against a DURABLE mark: a run parked in
    awaiting_review must announce once, not once per 30s tick, and not again after a
    daemon restart. Runs are delegated work by definition, so these need no watch.

    The event's ``summary`` is one line — a label, the state, the PR, and a *snippet*
    of the title. The run's ``title`` is the whole tracker line (here: a brief with
    landmines and a verify plan), so putting it in the notification pasted the entire
    task body into the orchestrator's window. It lives in the payload instead.
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
        title = run.get("title") or ""
        # The branch is the handle a human recognises ("cmx-38"); the id is the handle
        # the dispatcher does. Prefer the branch, fall back to the id.
        label = run.get("branch_name") or task_id
        snippet = _short_title(title)
        payload = {"task_id": task_id, "run_status": status, "title": title,
                   "branch_name": run.get("branch_name"),
                   "window_name": run.get("window_name"), "pr_url": run.get("pr_url"),
                   "pr_state": run.get("pr_state"), "attempt": run.get("attempt"),
                   "started_at": run.get("started_at"), "ended_at": run.get("ended_at")}
        if status == "awaiting_review":
            pr = run.get("pr_url")
            ref = f"{pr_ref(pr)} — {pr}" if pr else "no PR link"
            out.append(_event("run_review",
                              f"📥 {label} awaiting review — {ref}"
                              f"{' · ' + snippet if snippet else ''}", payload))
        elif status == "failed":
            err = (run.get("last_error") or "").splitlines()
            payload["last_error"] = run.get("last_error")
            out.append(_event("run_failed",
                              f"📥 {label} FAILED{' — ' + err[0][:120] if err else ''}"
                              f"{' · ' + snippet if snippet else ''}", payload))
    return out, fresh


# --- the daemon tick ------------------------------------------------------------

def status_snapshot() -> dict[str, str]:
    """``{wid: busy|idle|waiting}`` for every live window running claude."""
    return agent_manager.status_by_wid()


def stale_reason(event: dict, runs: list[dict]) -> str | None:
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
    """
    kind = event.get("kind")
    if kind not in ("run_review", "run_failed"):
        return None
    task_id = (event.get("payload") or {}).get("task_id")
    if not task_id:
        return None                        # legacy/unstructured event — deliver it
    run = next((r for r in runs if r.get("task_id") == task_id), None)
    if run is None:
        return "run row is gone"
    status, pr_state = run.get("status"), run.get("pr_state")
    if kind == "run_review":
        if status != "awaiting_review":
            return f"run is now {status!r}, not awaiting_review"
        if pr_state in ("merged", "closed"):
            # The row can lag the PR by a reconcile tick: pr_state is refreshed before
            # awaiting_review → done. A merged PR is not awaiting review either way.
            return f"PR is {pr_state}"
    elif kind == "run_failed" and status != "failed":
        return f"run is now {status!r}, not failed"
    return None


def deliver(store: dict, statuses: dict[str, str],
            runs: list[dict] | None = None) -> list[dict]:
    """Push queued events into the orchestrator — ONLY if its window is ``idle``.

    The gate is a strict equality against ``idle``. ``waiting`` must never be written
    to: that session is sitting on a permission/question prompt, and our paste would
    be consumed as the ANSWER to that prompt. ``busy`` we leave alone by design (never
    interrupt a session mid-thought) — the event just waits for the next idle tick.

    Every event is re-validated against the CURRENT runs (:func:`stale_reason`) on its
    way out; one that has rotted in the queue is dropped and LOGGED — never silently,
    or a real event lost to a bug becomes undebuggable. Only the event's ``summary``
    is pushed: the payload is the record, not the notification.

    Returns the events actually delivered (each exactly once — a delivered event is
    popped from the durable queue before we return, so no tick can re-send it).
    """
    orch = orchestrator_wid(store)
    if not orch or not store["queue"]:
        return []
    if statuses.get(orch) != IDLE:
        return []
    runs = runs or []

    sent: list[dict] = []
    while store["queue"] and len(sent) < MAX_DELIVERIES_PER_TICK:
        event = store["queue"][0]
        stale = stale_reason(event, runs)
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
        if not messenger.send_tmux(orch, text):
            log.warning("inbox: delivery to %s failed; leaving it queued", orch)
            break
        store["queue"].pop(0)
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

        # `runs` is the snapshot fetched above, outside the lock — re-validating against
        # it is a list scan, so the critical section stays as short as it was.
        deliver(store, statuses, runs)

    # The durable record. Written OUTSIDE the store lock — an append is another file's
    # I/O, and locked_store()'s one rule is that nothing slow happens inside it. EVERY
    # event is logged, including the `silent` ones (a watch retired because the work
    # succeeded is a fact worth having): the queue is what the orchestrator is TOLD, the
    # log is what HAPPENED, and conflating the two is what left the inbox with no
    # history to reconcile against. `event_log.append` never raises, so this cannot take
    # the inbox down with it.
    for event in events:
        event_log.from_inbox(event)
    return statuses
