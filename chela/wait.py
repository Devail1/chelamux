"""``chela wait`` — block until a delegated agent or dispatcher task reaches a state.

Issue #456 (herdr comparison). The decisions inbox (:mod:`chela.inbox`) already PUSHES
agent/run events into the orchestrator's session; this is the complementary PULL: a
synchronous wait an orchestrator (or a script it runs) calls right after delegating
work, instead of a hand-rolled poll loop. Two were measured live killing themselves for
memory on 2026-09-07/09::

    until [ "$(sqlite3 … "select judge_state …")" != "running" ]; do sleep 60; done

**Two of the issue's four asks were already built before this file existed** — this
module does not re-solve them, it reuses them:

* **Session-identity pinning.** The issue's Problem 1 ("the pin is a window id, and
  window ids are reused") is already solved for the orchestrator's OWN address —
  :func:`chela.inbox.orchestrator_session` + :func:`chela.inbox.resolve_heal` pin by the
  claude session id and re-resolve the wid, with the tmux epoch as the falsifier. The
  same epoch machinery protects a *watched* window here: :func:`chela.inbox.agent_events`
  refuses to read a post-restart stranger's status as the watch's outcome — it emits
  :data:`EPOCH_LOST_KIND` instead (``wid=None``, the stale id only in the payload). This
  file's counterweight guard is simply to treat that event as ITS OWN outcome
  (``state="unknown"``), never as a silent "done"/"blocked" for the original wait.
* **Waking on a clean verdict, not only a failure.** The issue's Problem 2 is already
  solved in :func:`chela.inbox.run_events` — ``run_judge_clean`` fires exactly like
  ``run_judge_cannot_verify``/``run_failed`` (CMX-197), and a watched window's
  ``finished`` event carries no verdict of its own. :data:`TASK_DONE_KINDS` and
  :data:`WID_DONE_KINDS` below list every one of those kinds without discriminating
  outcome — this file adds no new push logic, it only reads what already exists.

What is new here: a target can be a window id (``@N``) or a dispatcher task id, resolved
by shape (:func:`resolve_target`); the wait is satisfied by polling the durable event
log (:mod:`chela.event_log`) from its current tip, never tmux directly — "event-driven
off the existing event log", per the issue's suggested shape. A window target that is
not already watched is watched here (mirrors what any dispatch already does via
``chela watch``), which is what stamps the epoch the counterweight guard above relies
on. A task target needs no watch — :func:`chela.inbox.run_events` is driven by the runs
DB directly, independent of any watch.
"""
from __future__ import annotations

import time

from chela import discovery, dispatcher, event_log, inbox

DONE = "done"
BLOCKED = "blocked"
UNTIL_STATES = (DONE, BLOCKED)

# A watched WINDOW's inbox event kinds that mean the delegated work is OVER — success,
# death, or a completion inferred from the window disappearing (chela.inbox.agent_events
# / chela.inbox._gone_event). None of these carry a verdict: a clean finish and a failed
# one both produce "finished".
WID_DONE_KINDS = frozenset({"finished", "died", "completed_gone", "gone_unknown"})
WID_BLOCKED_KINDS = frozenset({"blocked"})

# A dispatcher RUN's event kinds (chela.inbox.run_events) that mean the task reached a
# point the orchestrator must act on. `run_judge_clean` is here deliberately (issue
# #456's "wakes on failure only" complaint) — it is exactly as terminal as `run_failed`.
TASK_DONE_KINDS = frozenset({
    "run_review", "run_judge_clean", "run_judge_cannot_verify",
    "run_judge_blocked_race", "run_needs_human", "run_failed",
})
# A task is BLOCKED when it cannot proceed without a human decision. NOT
# `run_changes_requested` — the dispatcher's own next tick re-spawns that one; it is
# still WORKING, not blocked.
TASK_BLOCKED_KINDS = frozenset({"run_needs_human", "run_judge_blocked_race"})

# ⛔ The counterweight (issue #456's guard). A tmux restart can reissue a watched wid to
# a different session; `inbox.agent_events` already refuses to attribute the stranger's
# status to the old watch and emits THIS kind instead (wid=None on the record itself,
# the stale id only in `payload["wid"]` — see `inbox._epoch_lost_event`). Both wid-poll
# helpers below watch for it and report it as `state="unknown"`, never as a satisfied
# done/blocked for the original wait.
EPOCH_LOST_KIND = "watch_epoch_lost"

POLL_INTERVAL = 0.5

# ⛔ The seam a cadence test observes — NOT `time.sleep` directly. `wait.time` IS the
# stdlib `time` module, so `monkeypatch.setattr(wait.time, "sleep", ...)` replaces
# `time.sleep` PROCESS-WIDE: the recorded durations then include every sleep any other
# thread in that process happened to make, and an assertion about "_poll's cadence"
# silently becomes an assertion about the whole process's. Measured 2026-09-10 — a single
# leaked daemon thread calling `time.sleep(0.001)` turned the floor assertion red with
# `[0.5, 0.5, 0.5, 0.5, 0.5, 0.001, 0.5, 0.5]`, blaming `_poll` for a sleep it never made.
# Patching THIS name observes only this module.
_sleep = time.sleep


def resolve_target(token: str) -> tuple[str, str]:
    """``("wid", "@12")`` for anything that looks like a window id, else ``("task", token)``.

    Mirrors :func:`chela.main._resolve_wid`'s own shape test (bare digits or an ``@``
    prefix) rather than guessing — a dispatcher task id is a hex string and never
    matches it, so there is no real ambiguity between the two target kinds.
    """
    t = (token or "").strip()
    bare = t[1:] if t.startswith("@") else t
    if bare.isdigit():
        return "wid", "@" + bare
    return "task", t


def _error(detail: str) -> dict:
    return {"ok": False, "state": "error", "detail": detail, "event": None}


def _timed_out(until: str) -> dict:
    return {"ok": False, "state": "timeout",
            "detail": f"timed out waiting for {until}", "event": None}


def _satisfied(until: str, event: dict) -> dict:
    return {"ok": True, "state": until, "detail": event.get("summary") or "", "event": event}


def _as_record(inbox_event: dict) -> dict:
    """An inbox-shaped event (``kind``/``summary``/``payload``/``wid``) read as a log
    record, for a state that is ALREADY true right now — so a caller does not have to
    wait for a fresh log line to be appended for something that already happened.
    Never appended to the log itself (that would duplicate the daemon's own write).
    """
    return {"type": inbox_event.get("kind"), "summary": inbox_event.get("summary", ""),
            "payload": inbox_event.get("payload") or {}, "wid": inbox_event.get("wid"),
            "ts": time.time(), "seq": None}


def _poll(types: frozenset, matches, deadline: float | None) -> dict | None:
    """Poll the durable event log, from its CURRENT tip, for the first record matching.

    Starts at ``event_log.tip()`` — never replays the past, so this can never fire on an
    event that predates the wait (a caller wanting an ALREADY-true state checks that
    itself, before calling this — see the task/wid helpers below). Bounded by
    ``deadline`` (wall clock; ``None`` waits forever, like ``chela events --follow``),
    checked on EVERY poll regardless of whether the log had anything new — a target
    that simply never fires still returns on time rather than blocking on
    :func:`chela.event_log.follow`'s own "only yield on new data" gate.

    Reads module-level ``POLL_INTERVAL`` fresh on EVERY iteration rather than freezing it
    as a default argument (issue #478) — a ``def f(..., interval=POLL_INTERVAL)`` default
    is bound once, at import, so a test (or a caller) changing the module attribute later
    would silently have no effect. Reading it live is what makes the constant actually
    configurable, and lets a test assert on the cadence via ``wait.POLL_INTERVAL`` /
    ``time.sleep`` without needing a real multi-second wall-clock wait.
    """
    tip = event_log.tip()
    cursor, boot = tip["seq"], tip["boot_id"]
    while True:
        batch = event_log.read(cursor, after_boot=boot, types=sorted(types))
        boot = batch["boot_id"]
        for ev in batch["events"]:
            if matches(ev):
                return ev
        cursor = batch["next_seq"]
        if deadline is not None and time.time() >= deadline:
            return None
        _sleep(POLL_INTERVAL)


def _find_run(task_id: str) -> dict | None:
    for run in dispatcher.list_runs():
        if run.get("task_id") == task_id:
            return run
    return None


def _current_task_event(run: dict) -> dict | None:
    """What ``inbox.run_events`` would fire for this run RIGHT NOW, as if never seen
    before — reuses the exact classification the inbox already pushes to the
    orchestrator (an empty ``seen`` map means every branch reads as a fresh edge), so a
    wait's idea of done/blocked can never drift from what the push side means by it.
    """
    events, _ = inbox.run_events([run], {})
    return events[0] if events else None


def _wait_task(task_id: str, until: str, deadline: float | None) -> dict:
    run = _find_run(task_id)
    if run is None:
        return _error(f"no dispatcher run found for task {task_id!r}")
    kinds = TASK_DONE_KINDS if until == DONE else TASK_BLOCKED_KINDS
    pre = _current_task_event(run)
    if pre is not None and pre.get("kind") in kinds:
        return _satisfied(until, _as_record(pre))

    def matches(ev: dict) -> bool:
        return ev.get("type") in kinds and (ev.get("payload") or {}).get("task_id") == task_id

    found = _poll(kinds, matches, deadline)
    if found is None:
        return _timed_out(until)
    return _satisfied(until, found)


def _wait_wid(wid: str, until: str, deadline: float | None, *, by: str | None) -> dict:
    live = discovery.get_windows_by_id()
    if wid not in live:
        return _error(f"{wid} is not a live window — nothing to wait on "
                       "(a dispatched task is tracked by its task id, not its window: "
                       "wait on that instead)")
    if wid not in inbox.watches():
        result = inbox.watch(wid, "chela wait", by=by)
        if not result.get("ok"):
            return _error(f"could not watch {wid}: {result.get('error')}")
    if until == BLOCKED and inbox.status_snapshot().get(wid) == inbox.WAITING:
        return _satisfied(until, {
            "type": "blocked", "summary": f"{wid} is already BLOCKED on a prompt",
            "payload": {"wid": wid}, "wid": wid, "ts": time.time(), "seq": None,
        })
    kinds = WID_DONE_KINDS if until == DONE else WID_BLOCKED_KINDS
    watch_types = kinds | {EPOCH_LOST_KIND}

    def matches(ev: dict) -> bool:
        if ev.get("type") == EPOCH_LOST_KIND:
            return (ev.get("payload") or {}).get("wid") == wid
        return ev.get("type") in kinds and ev.get("wid") == wid

    found = _poll(watch_types, matches, deadline)
    if found is None:
        return _timed_out(until)
    if found.get("type") == EPOCH_LOST_KIND:
        return {"ok": False, "state": "unknown",
                "detail": found.get("summary") or
                (f"tmux restarted while waiting on {wid} — the session being watched is "
                 "gone, and that id may now belong to somebody else; outcome unknown"),
                "event": found}
    return _satisfied(until, found)


def wait_for(target: str, until: str = DONE, timeout: float | None = None,
             *, by: str | None = None) -> dict:
    """Block until ``target`` (a wid or a dispatcher task id) reaches ``until``.

    Returns ``{"ok", "state", "detail", "event"}`` — ``state`` is ``"done"``/``"blocked"``
    on success, else one of ``"timeout"``/``"unknown"``/``"error"``. Never raises for an
    ordinary outcome (a bad target, a timeout, a lost race with a tmux restart) — those
    are reported in the result, not as exceptions, so a caller (the CLI, or
    ``chela drive --wait``) decides the exit code once, in one place.

    Issue #479: the ``"unknown"`` branch a wid-watch reaches on :data:`EPOCH_LOST_KIND`
    (see :func:`_wait_wid`) is deliberate, not an unfinished re-resolve. A wid-watch's
    epoch only goes dangling when the whole tmux SERVER has restarted (:mod:`chela.epoch`
    scans, this is not one window dying) — and every real caller (``cmd_wait`` in
    ``chela/main.py`` always passes ``by=orchestrator.self_wid()``) is itself running
    inside a pane of THAT SAME server: the orchestrator's own interactive shell, or a
    script it runs synchronously right after delegating work. When that server dies, the
    waiting process dies with it (its pane's pty closes under it) BEFORE it could ever
    observe the restart it would need to react to — there is no surviving caller left to
    re-resolve a moved target, unlike :func:`chela.inbox.resolve_heal`, which heals the
    orchestrator's OWN address because that is stored state a later, freshly-started
    process reads back, not a live call that must itself outlive the crash.

    This holds even for ``CHELA_RESTORE_RESUME=true`` (v0.10.5), which can relaunch a
    MANUAL row under ``claude --resume <sid>`` at a NEW wid with the SAME session id —
    the one shape where "the target legitimately moved" is real. That relaunch can only
    run AFTER the dead server is noticed and ``chela restore --resume`` is invoked against
    the NEW server, by which point any waiter that was watching from inside the OLD
    server is already gone — so there is no process to hand the new wid to. A waiter that
    runs detached from any tmux pane (backgrounded, outside the crashing server) is not a
    supported shape today — the module docstring above describes this as a synchronous
    call an orchestrator (or a script it runs) makes, not a detached daemon — so it is not
    covered by this analysis.
    """
    if until not in UNTIL_STATES:
        return _error(f"--until must be one of {UNTIL_STATES}, got {until!r}")
    kind, ident = resolve_target(target)
    deadline = (time.time() + timeout) if timeout else None
    if kind == "wid":
        return _wait_wid(ident, until, deadline, by=by)
    return _wait_task(ident, until, deadline)
