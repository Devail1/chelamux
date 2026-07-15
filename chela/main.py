"""chela CLI entry point.

`chela status` proves tmux-native discovery. `chela run` is the daemon loop
(scheduler tick + work-item dispatcher). `chela schedule ...` manages scheduled
tasks; `chela dispatch ...` runs the markdown-TODO → worktree → PR dispatcher;
`chela msg`/`broadcast` route messages between live agents over tmux.
`chela telegram` bridges N agent windows and their Telegram topics (outbound
relay of each window's Claude Code output + inbound routing of each topic's
messages back to its window), routed via a persisted thread↔window registry.
`chela dashboard` launches the optional web UI (requires the `dashboard` extra).
"""
from __future__ import annotations
import argparse
import json
import logging
import os
import signal
import sys
import threading
import time
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from chela import (
    agent_manager,
    capabilities,
    config,
    discovery,
    dispatcher,
    doctor,
    epoch,
    event_log,
    hold,
    hooks,
    inbox,
    judge,
    messenger,
    notify,
    okf,
    orchestrator,
    rooms,
    scheduler,
    workflow,
)
from chela.config import (
    BIND_DISPATCHED,
    SCHEDULER_POLL_INTERVAL,
    DISPATCH_WORKFLOWS,
    NOTIFY_INTERVAL,
    SHOW_TOOL_CALLS,
    STATUS_LINE,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("chela")


class GracefulShutdown:
    """SIGINT/SIGTERM → a flag + an interruptible wait. No traceback, prompt exit.

    Python's default SIGINT handler raises KeyboardInterrupt *wherever the interpreter
    happens to be*, so a ``while True: ... time.sleep()`` daemon died with a traceback
    pointing at whatever line the loop was on when the signal landed. That is worse
    than useless: every ``pm2 restart`` left a stack trace fingering an innocent line
    (it blamed the inbox tick, and cost real debugging time chasing a crash-loop that
    was just a restart). Noise that masks real errors is a bug, not a cosmetic issue.

    Installing our own handler means the signal raises NOTHING — it sets an
    :class:`threading.Event`. Two consequences the loops rely on:

    * ``wait()`` replaces ``time.sleep()``, so a signal arriving during the idle gap
      returns IMMEDIATELY instead of sitting out the rest of a 30s nap. PM2 sends
      SIGINT and SIGKILLs after a short grace period, so a daemon that naps through
      its notice gets force-killed; this one exits under its own power.
    * ``stopping`` is checked between phases of a tick, so a signal mid-tick doesn't
      start new work — the loop finishes what it's doing and leaves.
    """

    def __init__(self, name: str):
        self._name = name
        self._event = threading.Event()
        self.signame: str | None = None

    def install(self) -> "GracefulShutdown":
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, self._handle)
        return self

    def _handle(self, signum, _frame) -> None:
        self.signame = signal.Signals(signum).name
        # Re-entrant by design: a second Ctrl-C just re-sets an already-set flag. It
        # must never raise, or we are back to a traceback on the way out the door.
        self._event.set()

    @property
    def stopping(self) -> bool:
        return self._event.is_set()

    def wait(self, seconds: float) -> bool:
        """Sleep, returning True the moment a shutdown signal arrives."""
        return self._event.wait(seconds)

    def log_exit(self) -> None:
        log.info("%s: shutting down (%s)", self._name, self.signame or "stop requested")


def cmd_status(args) -> None:
    """List the agent windows chela can see in the tmux session."""
    session = config.current_session()
    windows = discovery.get_all_windows()
    if not windows:
        print(f"No windows found in tmux session '{session}'.")
        print("Is the session running? Override the session with CHELA_TMUX_SESSION.")
        return
    print(f"Agents in tmux session '{session}':\n")
    for name, wid in sorted(windows.items()):
        cwd = discovery.get_window_cwd(name) or "?"
        print(f"  {name:<24} {wid:<6} {cwd}")


def cmd_run(args) -> None:
    """Run the daemon loop: scheduler tick every pass, dispatcher on its own cadence."""
    log.info("chela daemon starting (session=%s, poll=%ds)",
             config.current_session(), SCHEDULER_POLL_INTERVAL)
    scheduler.init()  # open the WAL scheduler DB + init schema once, before ticking
    # A new epoch for the event log's cursors. `seq` keeps counting (it is the log's
    # identity), but anything that happened while the daemon was down never reached the
    # log — so a reader that remembered a cursor is told the boot changed rather than
    # resuming across a hole it cannot see.
    boot_id = event_log.new_boot()
    event_log.append("daemon_start", f"chela daemon started (session={config.current_session()})",
                     {"session": config.current_session(), "pid": os.getpid()})
    log.info("Event log: %s (boot_id=%s)", event_log.log_path(), boot_id)
    # Say what this daemon can and cannot do — ON *and* OFF, every capability, every
    # start. The old code logged "Dispatcher enabled for N workflow(s)" on the happy path
    # and NOTHING when the dispatcher was off, so a dropped CHELA_DISPATCH_WORKFLOWS took
    # dispatch *and* reconcile down for nine hours with an absent log line as its only
    # tell. Nobody reads an absence. It is also PUBLISHED (~/.chela/daemon.json), so
    # `chela doctor` and the dashboard can check what is really running instead of
    # re-reading the same config that was already wrong.
    caps = capabilities.effective()
    capabilities.announce(caps, log)
    capabilities.publish(caps, boot_id=boot_id)
    # Per-workflow, not global: each WORKFLOW.md carries its own effective poll
    # interval (`polling.interval_ms`, re-read on change), so they no longer share
    # one clock.
    last_dispatch_check: dict[Path, float] = {}
    dispatch_blocked: set[Path] = set()   # workflows whose file currently doesn't parse
    # The queue hold is GLOBAL (one file, honoured by every tick), so this edge-trigger is
    # too: say it once when the queue is held and once when it is released, never every
    # tick. A paused dispatcher is a DISABLED SUBSYSTEM — CMX-53's rule — so it announces
    # itself here, in the startup capability line, in `chela doctor`, and in /api/settings.
    dispatch_held = False
    last_notify_check = 0.0
    waiting_seen: set[str] = set()
    # Held in memory, exactly like `waiting_seen`: it is the PREVIOUS status snapshot
    # the inbox edge-triggers against. Starting empty means a fresh daemon baselines
    # silently on its first tick instead of announcing every already-idle agent. (A
    # completion is NOT lost to that baseline — inbox.did_work_since() detects it from
    # the transcript, with no prior sample needed.)
    inbox_statuses: dict[str, str] = {}
    # Who the inbox is pushing to is RE-READ every tick, never latched at startup: you
    # register the orchestrator at RUNTIME (`chela watch`, right after dispatching), and
    # requiring a daemon restart before the feature ever works is dead on arrival. We
    # only log the TRANSITION (unregistered -> @0), so a 30s loop stays quiet.
    inbox_orch = object()   # sentinel: distinct from every real value, incl. None
    stop = GracefulShutdown("chela daemon").install()

    while not stop.stopping:
        try:
            executed = scheduler.tick()
            if executed:
                log.info("Scheduler executed %d task(s)", executed)

            # Relabel any hand-started/resumed claude window to its cwd basename
            # (the dashboard Start button already names windows; this catches the
            # ones launched directly in tmux). Idempotent — only acts on a mismatch.
            try:
                renamed = agent_manager.reconcile_window_names()
                if renamed:
                    log.info("Reconciled window names: %s", ", ".join(renamed))
            except Exception:
                log.exception("Window-name reconcile failed")

            now = time.time()
            if stop.stopping:
                break          # a signal landed mid-tick: don't start new work
            for wf_path in DISPATCH_WORKFLOWS:
                # The workflow is HOT-RELOADED: `poll_interval` re-reads the file
                # only when it changed on disk (a stat, otherwise), so an edited
                # interval — like every other config key — takes effect on the
                # next tick with no restart. An unparseable file keeps its
                # last-good interval and reconciles on; dispatcher.tick() is what
                # refuses to start new work, and says so.
                try:
                    interval = dispatcher.poll_interval(wf_path)
                    if now - last_dispatch_check.get(wf_path, 0.0) < interval:
                        continue
                    last_dispatch_check[wf_path] = now
                    summary = dispatcher.tick(wf_path)
                    # Edge-triggered, like every other daemon-loop log: a broken
                    # workflow can sit broken for hours, and a 60s drumbeat of the
                    # same line is how an operator learns to ignore the log.
                    if summary.get("blocked") and wf_path not in dispatch_blocked:
                        dispatch_blocked.add(wf_path)
                        if summary.get("refused"):
                            # The workspace fence: NOTHING ran this tick — not the claim,
                            # not the reconcile. Saying "reconciliation continues" here
                            # would be a lie, and a log that lies is how this bug hid.
                            log.error("Dispatch REFUSED for %s — this daemon does NOTHING "
                                      "for it: %s", wf_path.name, summary.get("error"))
                        else:
                            log.error("Dispatch BLOCKED for %s (reconciliation continues on the "
                                      "last known-good config): %s", wf_path.name, summary.get("error"))
                    elif not summary.get("blocked") and wf_path in dispatch_blocked:
                        dispatch_blocked.discard(wf_path)
                        log.info("Dispatch resumed for %s — workflow parses again", wf_path.name)
                    if summary.get("held") and not dispatch_held:
                        dispatch_held = True
                        log.warning(
                            "Work dispatcher HELD — claiming nothing (%s). Reconciliation "
                            "continues. Release with `chela dispatch --resume`.",
                            (summary.get("hold") or {}).get("summary", "queue hold"),
                        )
                    elif not summary.get("held") and dispatch_held:
                        dispatch_held = False
                        log.info("Work dispatcher resumed — the queue hold was released")
                    if summary["dispatched"] or summary["reconciled_done"] or summary["reconciled_failed"]:
                        log.info("Dispatch %s: %s", wf_path.name, summary)
                except Exception:
                    log.exception("Dispatch tick failed for %s", wf_path)

            if notify.enabled() and now - last_notify_check >= NOTIFY_INTERVAL:
                try:
                    waiting_seen = notify.check_waiting(waiting_seen)
                except Exception:
                    log.exception("Needs-input check failed")
                last_notify_check = now

            # Decisions inbox: tell the orchestrator when work it delegated finishes,
            # blocks, or fails — pushed into its session only while it is idle.
            if inbox.enabled():
                try:
                    orch = inbox.orchestrator_wid()
                    if orch != inbox_orch:
                        log.info("Decisions inbox: orchestrator=%s",
                                 orch or "unregistered (inert until `chela watch`)")
                        inbox_orch = orch
                    inbox_statuses = inbox.tick(inbox_statuses)
                except Exception:
                    log.exception("Decisions-inbox tick failed")

            # Agent rooms: a targeted handoff/question/blocker whose recipient was sitting
            # at a gate is PARKED, never pasted (that paste would answer the gate). This is
            # what finally sends it — the moment that window is at its prompt again. Gated
            # on `has_pending()` (one small file read) so the common case costs nothing.
            try:
                if rooms.has_pending():
                    flushed = rooms.flush_pending(inbox_statuses or None)
                    if flushed:
                        log.info("Rooms: delivered %d parked post(s)", len(flushed))
            except Exception:
                log.exception("Room pending-delivery flush failed")
        except Exception:
            log.exception("Error in daemon loop")
        stop.wait(SCHEDULER_POLL_INTERVAL)

    capabilities.clear()   # nothing is providing these any more; don't let doctor say so
    stop.log_exit()


def cmd_schedule_add(args) -> None:
    """Add a scheduled task."""
    if args.every:
        schedule_type, schedule_value = "interval", args.every
    elif args.cron:
        schedule_type, schedule_value = "cron", args.cron
    elif args.once:
        schedule_type, schedule_value = "once", args.once
    else:
        print("Must specify --every, --cron, or --once")
        return
    task_id = scheduler.add_task(args.agent, schedule_type, schedule_value, args.prompt)
    print(f"Added task {task_id}: {args.agent} ({schedule_type} {schedule_value})")


def cmd_schedule_list(args) -> None:
    """List scheduled tasks."""
    tasks = scheduler.list_tasks()
    if not tasks:
        print("No scheduled tasks")
        return
    for t in tasks:
        status = "ON" if t.enabled else "OFF"
        print(f"  [{t.id}] {status}  {t.agent_name:<16} {t.schedule_type} {t.schedule_value:<12} next={t.next_run or 'N/A'}")
        print(f"        prompt: {t.prompt[:80]}")


def cmd_schedule_remove(args) -> None:
    """Remove a scheduled task."""
    if scheduler.remove_task(args.id):
        print(f"Removed task {args.id}")
    else:
        print(f"Task {args.id} not found")


def cmd_msg(args) -> None:
    """Send a message to one live agent over tmux — by window id (``@32``) or name.

    A message must never be lost quietly, so the two failure modes are reported
    apart and both exit NON-ZERO: "the window is gone" and "tmux refused the
    send". The old code collapsed both into a chatty `offline` on stdout with a
    zero exit — and, because it resolved the recipient by *name* only, said it
    about live windows addressed by id. A "busy" agent is NOT a failure mode: it
    is a valid recipient, and Claude Code queues the paste.
    """
    target = messenger.resolve_window(args.agent)
    if target is None:
        live = discovery.get_windows_by_id()
        roster = ", ".join(f"{wid} {name}" for wid, name in sorted(live.items())) or "(none)"
        print(f"{args.agent} is not a live window in tmux session "
              f"'{config.current_session()}' — message NOT delivered.\nlive windows: {roster}",
              file=sys.stderr)
        sys.exit(1)
    # An orchestrator messaging its own window feeds the message straight back to
    # itself. Deliberate refusal — keep it.
    if target == orchestrator.self_wid():
        print(f"{args.agent} resolves to this very window ({target}) — refusing to "
              "message myself (that is a loop). Pass a sibling's @wid.", file=sys.stderr)
        sys.exit(1)
    if messenger.send_message(args.from_agent, target, args.message, args.priority):
        print(f"Sent to {args.agent} ({target})")
        return
    print(f"{args.agent} ({target}) is live but the tmux send FAILED — message not "
          "delivered (see the log for tmux's error).", file=sys.stderr)
    sys.exit(1)


def cmd_broadcast(args) -> None:
    """Send a message to every other live agent. Non-zero if any delivery failed."""
    results = messenger.broadcast(args.from_agent, args.message, args.priority)
    if not results:
        print("No other agents online")
        return
    for agent, delivered in sorted(results.items()):
        print(f"  {agent:<24} {'sent' if delivered else 'FAILED — not delivered'}")
    if not all(results.values()):
        sys.exit(1)


def _resolve_wid(token: str | None) -> str | None:
    """Accept a window id (``@28`` or ``28``) or ``self``; None → self."""
    if token is None or token == "self":
        return orchestrator.self_wid()
    token = token.strip()
    if token.isdigit():
        return "@" + token
    return token


def cmd_watch(args) -> None:
    """Register interest in a window you just delegated work to (the decisions inbox).

    The session that runs this becomes THE orchestrator — the one and only window the
    inbox may push into ($CHELA_WID, or $CHELA_ORCHESTRATOR_WID to pin it). Call it
    right after you dispatch: when that agent finishes, blocks, or dies, the event is
    pushed back into your session the moment you are idle, instead of the completion
    being invisible to you (and a human having to relay it).

    With **no window**, it only (re-)registers you — stamped with the tmux epoch you are
    really in (CMX-77). That is the recovery path after a tmux restart: the addresses in
    ``inbox.json`` were issued by a server that no longer exists, so the inbox refuses to
    push to them (they name other agents now) and holds the queue until a real session says
    "I am here". This is that sentence.
    """
    self_wid = orchestrator.self_wid()
    if not args.wid:
        if not self_wid:
            print("no window id: run this from inside a tmux window (or pass @N to watch it)",
                  file=sys.stderr)
            sys.exit(1)
        result = inbox.register(self_wid)
        if not result["ok"]:
            print(f"register failed: {result['error']}", file=sys.stderr)
            sys.exit(1)
        queued = result["queued"]
        ident = (f", identity {result['session']}" if result.get("session")
                 else ", no session identity (self-heal unavailable — fire a hook first)")
        print(f"registered {self_wid} as the orchestrator (tmux epoch {result['epoch'] or '?'}"
              + ident + ")"
              + (f"; {queued} queued event(s) will be delivered when you are idle"
                 if queued else "; nothing queued"))
        return
    wid = _resolve_wid(args.wid)
    result = inbox.watch(wid, args.note or "", by=self_wid)
    if not result["ok"]:
        print(f"watch failed: {result['error']}", file=sys.stderr)
        sys.exit(1)
    orch = result["orchestrator"] or "(unregistered)"
    note = f' — note: "{result["note"]}"' if result["note"] else ""
    print(f"watching {wid}{note}; events -> {orch} when idle")


def cmd_unwatch(args) -> None:
    """Drop interest in a window (no completion event will be reported for it)."""
    wid = _resolve_wid(args.wid)
    result = inbox.unwatch(wid)
    print(f"unwatched {wid}" if result["ok"] else f"{wid} was not watched")


def cmd_watching(args) -> None:
    """What the inbox is watching, and what is queued for delivery.

    ⛔ And whether the address it would deliver TO is worth anything. A queue behind a dead
    ``@N`` looks identical to a quiet day — that is exactly how five finished PRs went
    unreviewed on 2026-07-14 — so the state of the address is the FIRST thing printed, and a
    rotten one is not something you have to know to go looking for.
    """
    store = inbox.load()
    orch = inbox.orchestrator_wid(store)
    now_epoch = epoch.current()
    state, why = inbox.address_state(store, inbox.status_snapshot(), now_epoch)
    stamp = inbox.orchestrator_epoch(store)
    session = inbox.orchestrator_session(store)
    print(f"orchestrator: {orch or '(unregistered — the inbox is inert)'}"
          + (f"  [{epoch.describe(stamp)}]" if orch else "")
          + (f"  identity: {session}" if session else ""))
    if state in inbox.UNDELIVERABLE:
        print(f"\n⛔ THE INBOX CANNOT DELIVER — the address is {state.upper()}.\n   {why}")
        if session:
            print(f"   ↻ self-heal will re-resolve this from session {session} once it is live "
                  "under a window (CMX-82).")
    elif state == inbox.ADDR_UNSTAMPED:
        print(f"\n! {why}")
    if not inbox.enabled():
        print("inbox: DISABLED (CHELA_INBOX_ENABLED=false)")
    names = discovery.get_windows_by_id()
    watches = store["watches"]
    print(f"\nwatching ({len(watches)}):")
    for wid, meta in sorted(watches.items()):
        note = f' — "{meta.get("note")}"' if meta.get("note") else ""
        gone = epoch.is_dangling(meta.get("epoch"), now_epoch)
        name = "(id reissued by a NEW tmux server)" if gone else names.get(wid, "(gone)")
        print(f"  {wid:<6} {name:<24}{note}")
    queue = store["queue"]
    print(f"\nqueued, awaiting your next idle ({len(queue)}):")
    for event in queue:
        print(f"  {inbox.render(event)}")


# --- agent rooms: the relationship a message finally has ------------------------

def _room_fail(result: dict) -> None:
    """A room refusal is LOUD and non-zero — a dropped message must never look like a send."""
    print(f"room: {result['error']}", file=sys.stderr)
    sys.exit(1)


def cmd_room_create(args) -> None:
    result = rooms.create(args.room)
    if not result["ok"]:
        _room_fail(result)
    print(f"room {result['room']} "
          f"{'created' if result['created'] else 'already exists'}")


def cmd_room_join(args) -> None:
    """Put a window in a room. Defaults to your own — an agent wires itself in."""
    wid = _resolve_wid(args.wid)
    if not wid:
        print("no window id (pass --wid @N)", file=sys.stderr)
        sys.exit(1)
    result = rooms.join(args.room, wid)
    if not result["ok"]:
        _room_fail(result)
    print(f"{result['wid']} ({result['name']}) joined room {result['room']}")


def cmd_room_leave(args) -> None:
    wid = _resolve_wid(args.wid)
    result = rooms.leave(args.room, wid)
    print(f"{result['wid']} left room {result['room']}" if result["ok"]
          else f"{result['wid']} was not in room {result['room']}")


def cmd_room_status(args) -> None:
    """Who is wired to whom, and what is parked at a gate."""
    state = rooms.status(args.room)
    if not state["rooms"]:
        print("no rooms" if not args.room else f"no such room: {args.room}")
        return
    for name, meta in state["rooms"].items():
        print(f"\nroom {name} ({len(meta['members'])} members)")
        for wid, info in meta["members"].items():
            mark = "●" if info["live"] else "○ gone"
            print(f"  {mark} {wid:<6} {info['name']}")
    parked = {wid: q for wid, q in state["pending"].items() if q}
    if parked:
        print("\nparked (recipient is at a gate — never pasted into `waiting`):")
        for wid, queue in sorted(parked.items()):
            for entry in queue:
                print(f"  {wid:<6} {entry['kind']} #{entry['post_seq']} "
                      f"from {entry['from_wid']} in {entry['room']}")


def cmd_room_post(args) -> None:
    """Post to a room — recorded always; injected only if it targets someone and may interrupt.

    A refusal (dead recipient, non-member, a relayed body, self-target) exits NON-ZERO:
    a message that did not arrive must never read like one that did. A loop guard that
    trips is not a refusal — the post IS in the ledger — but it is still said out loud.
    """
    from_wid = _resolve_wid(args.from_wid)
    result = rooms.post(args.room, args.kind, args.message, from_wid=from_wid,
                        targets=args.to, reply_to=args.reply_to)
    if not result["ok"]:
        _room_fail(result)
    where = f" -> {', '.join(result['delivered'])}" if result["delivered"] else ""
    print(f"posted {result['kind']} #{result['seq']} to room {result['room']}{where} "
          f"(chain {result['chain_id']}, hop {result['hop']})")
    for wid in result["deferred"]:
        print(f"  {wid} is at a gate (waiting) — delivery PARKED until it clears "
              f"(never pasted into a prompt)")
    for blocked in result["blocked"]:
        print(f"  {blocked['wid']}: NOT delivered — {blocked['reason']}", file=sys.stderr)
    if result["failed"]:
        print(f"  tmux send FAILED for: {', '.join(result['failed'])} — not delivered",
              file=sys.stderr)
        sys.exit(1)
    if not result["delivered"] and not result["deferred"] and args.to:
        sys.exit(1)          # it was aimed at someone and reached nobody — say so in $?


def cmd_room_recap(args) -> None:
    """The recap a restarted agent is handed at ``SessionStart`` — printed by hand.

    Prints NOTHING for a window in no room, exactly as the hook does: that contract is
    the whole reason the hook is safe to run in front of every session in the fleet.
    """
    wid = _resolve_wid(args.wid)
    if not wid:
        print("no window id (pass --wid @N, or set $CHELA_WID)", file=sys.stderr)
        sys.exit(1)
    text = rooms.recap(wid)
    if text:
        print(text)


def cmd_room_digest(args) -> None:
    """The room's ledger — read straight out of the event log, not a second store."""
    events = rooms.digest(args.room, limit=args.limit)
    if not events:
        print(f"room {args.room}: no events yet")
        return
    for event in events:
        print(_fmt_event(event))


def _fmt_event(event: dict) -> str:
    """One event, one line: the cursor, the clock, the type, the window, the summary."""
    ts = datetime.fromtimestamp(event.get("ts") or 0).strftime("%H:%M:%S")
    wid = event.get("wid") or "-"
    return (f"{event.get('seq'):>6}  {ts}  {event.get('type', '?'):<18} "
            f"{wid:<5} {event.get('summary') or ''}")


def _print_batch(batch: dict, as_json: bool) -> None:
    gap = batch.get("gap")
    if gap:
        # Loud, on stderr, and never mixed into the event stream: a reader that silently
        # resumed across a hole is the failure this whole design exists to prevent.
        print(f"⚠ resume gap: {gap['reason']}", file=sys.stderr)
    for event in batch["events"]:
        print(json.dumps(event, ensure_ascii=False) if as_json else _fmt_event(event),
              flush=True)


def cmd_events(args) -> None:
    """Replay / filter / tail the event log — the durable record of what happened.

    ``--after-seq`` is a cursor; pair it with ``--after-boot`` (the ``boot_id`` you were
    given last time) and a restart or a reset log is reported as a GAP instead of being
    silently resumed across.
    """
    if args.follow:
        try:
            for batch in event_log.follow(args.after_seq, after_boot=args.after_boot,
                                          types=args.types, wid=args.wid):
                _print_batch(batch, args.json)
        except KeyboardInterrupt:
            pass
        return

    batch = event_log.read(args.after_seq, after_boot=args.after_boot,
                           types=args.types, wid=args.wid, limit=args.limit)
    events = batch["events"]
    if args.tail:
        events = events[-args.tail:]
    _print_batch({**batch, "events": events}, args.json)
    if not args.json:
        print(f"\nboot_id={batch['boot_id']} seq={batch['first_seq']}..{batch['last_seq']} "
              f"({len(events)} shown, resume with --after-seq {batch['next_seq']})",
              file=sys.stderr)
        if batch["corrupt_lines"]:
            print(f"note: skipped {batch['corrupt_lines']} unparseable line(s)",
                  file=sys.stderr)


def cmd_events_emit(args) -> None:
    """Append one event from the shell — the programmatic write path, exposed.

    This is how the log is exercised with zero Claude Code involvement (and how N
    concurrent writers are hammered at it in the tests).
    """
    payload = {}
    if args.payload:
        try:
            payload = json.loads(args.payload)
        except json.JSONDecodeError as exc:
            print(f"--payload is not valid JSON: {exc}", file=sys.stderr)
            sys.exit(1)
        if not isinstance(payload, dict):
            print("--payload must be a JSON object", file=sys.stderr)
            sys.exit(1)
    record = event_log.append(args.type, args.summary or "", payload,
                              wid=_resolve_wid(args.wid) if args.wid else None,
                              session_id=args.session_id)
    if record is None:
        print("append failed (see the log)", file=sys.stderr)
        sys.exit(1)
    print(f"seq={record['seq']} boot_id={record['boot_id']} {record['type']}")


def cmd_events_rotate(args) -> None:
    """Retire the current event log to a ``.bak`` and start a fresh boot epoch.

    An OPERATOR step, never a silent unlink at boot: the file is renamed (kept), and the
    ``boot_id`` moves so every reader holding a cursor into the retired log is told about
    the gap rather than resuming into an empty one.
    """
    path = event_log.log_path()
    if not args.yes:
        print(f"This retires {path} to a .bak and starts a fresh boot_id.")
        print("Readers holding a cursor will be told about the gap (they will not resume silently).")
        print("Re-run with --yes to do it.")
        return
    result = event_log.rotate()
    if result["backup"]:
        print(f"retired {path} -> {result['backup']}")
    else:
        print(f"no log at {path} — nothing to retire")
    print(f"fresh boot_id={result['boot_id']} (seq stays monotonic at {result['seq']})")


def cmd_plugin(args) -> None:
    """Render the Claude Code hooks plugin with THIS install's dashboard port baked in.

    The plugin committed to the repo targets chela's default port, and a hook ``url`` is
    a literal — Claude Code does not expand env vars in it. So a dashboard on any other
    port needs its own copy of the manifest, or the hooks post into a closed socket and
    the feature looks simply broken. This is that copy.

    The port comes from the RUNNING dashboard (the one it published at startup), not from
    whatever this process's environment happens to say — the two disagreeing is precisely
    how the manifest ended up pointing at a port nobody served. A disagreement is printed
    here, loudly, instead of being resolved behind the user's back.

    **This file is not the one agents read.** ``/plugin install`` copies it into Claude
    Code's cache, and that copy is what loads at startup — so rendering alone changes
    nothing, and for a day it changed nothing while every check said otherwise. chela
    therefore reads the installed copy back and says, here, whether it is stale. It does
    NOT write into that cache: the cache is Claude Code's, keyed by plugin version and
    recorded in its own bookkeeping, and a reinstall would overwrite anything we put
    there — leaving Claude Code describing a copy it did not install. Detect and instruct.
    """
    live = config.live_dashboard()
    port = args.port or config.live_dashboard_port()
    directory = hooks.render_plugin(Path(args.dir).expanduser(), port=port)
    print(f"plugin rendered at {directory} (posting to 127.0.0.1:{port})")
    if live and live["port"] != port:
        print(f"\n⚠️  the running dashboard is listening on {live['port']}, not {port} — "
              "these hooks will POST into a closed socket (and fail open, silently).")
    elif not live:
        print("\n⚠️  no dashboard is running, so this port is the CONFIGURED one, not an "
              "observed one. Re-run `chela plugin` (or `chela doctor`) once it is up.")
    if config.dashboard_port() != port and not args.port:
        print(f"⚠️  the env says CHELA_DASHBOARD_PORT={config.dashboard_port()} while the "
              f"dashboard actually serves {port}. The env is meant to be the source of "
              "truth — fix the env file and restart it without --port.")
    _report_installed_plugin(directory, port)
    print("\nHooks are read at agent STARTUP — a running agent keeps whatever manifest it "
          "started with, no matter what this command just wrote. Restart an agent for a "
          "change to reach it.")


def _report_installed_plugin(directory: Path, port: int) -> None:
    """What the AGENTS read — rendering is only half the loop. See :func:`cmd_plugin`."""
    copies = hooks.installed_plugins()
    if not copies:
        print("\n⚠️  chela cannot find an INSTALLED copy of the plugin — and the installed "
              "copy is the only one agents ever read. Rendering it does nothing on its "
              "own. Install it, from Claude Code:")
        print(f"\n  /plugin marketplace add {directory}")
        print("  /plugin install chela@chela")
        print(f"\n(or, for one session only: claude --plugin-dir {directory})")
        return
    expected = hooks.hooks_spec(port)
    for copy in copies:
        if copy.hooks is None:
            print(f"\n⚠️  the installed copy at {copy.manifest} cannot be read "
                  f"({copy.error}) — so chela cannot tell you whether the hooks agents "
                  "actually run are the ones it just rendered.")
            continue
        drift = hooks.manifest_drift(copy.hooks, expected)
        if not drift:
            print(f"\n✓ the installed copy agents read agrees with what was just rendered:"
                  f"\n    {copy.manifest} (v{copy.version or '?'})")
            continue
        print(f"\n🔴 STALE INSTALL — agents do NOT read {directory}. They read:")
        print(f"    {copy.manifest}")
        print("  and it disagrees with what was just rendered:")
        for line in drift:
            print(f"    - {line}")
        print("\n  Refresh it from Claude Code (chela will not write into Claude Code's "
              "plugin cache — that copy is Claude Code's to manage, and a reinstall would "
              "overwrite anything chela put there):")
        print("    /plugin uninstall chela@chela")
        print("    /plugin install chela@chela")


def cmd_whoami(args) -> None:
    """Print this agent's own window id (CHELA_WID / derived from tmux)."""
    wid = orchestrator.self_wid()
    if wid:
        print(wid)
    else:
        print("unknown — not in a tmux pane and $CHELA_WID unset", file=sys.stderr)
        sys.exit(1)


def cmd_peek(args) -> None:
    """Filtered status view for one window (status + recap + cwd + health)."""
    wid = _resolve_wid(args.wid)
    if not wid:
        print("no window id (pass @N, or set $CHELA_WID)", file=sys.stderr)
        sys.exit(1)
    result = orchestrator.peek(wid)
    if result is None:
        print(f"{wid} is not a live window", file=sys.stderr)
        sys.exit(1)
    if args.json:
        import json
        print(json.dumps(result, indent=2))
    else:
        print(orchestrator.format_peek(result))


def cmd_read(args) -> None:
    """Distilled read of a sibling's Claude Code transcript."""
    wid = _resolve_wid(args.wid)
    if not wid:
        print("no window id (pass @N, or set $CHELA_WID)", file=sys.stderr)
        sys.exit(1)
    result = orchestrator.read(
        wid, tail=args.tail, query=args.query, all_turns=args.all,
    )
    if not result["ok"]:
        print(result["error"], file=sys.stderr)
        sys.exit(1)
    if args.json:
        import json
        print(json.dumps(result, indent=2))
        return
    header = f"{result['wid']} {result['name']} — {result['mode']} ({result['count']} turns)"
    print(header)
    print("─" * min(len(header), 72))
    if not result["turns"]:
        print("(no matching turns)" if args.query else "(no turns yet)")
    for t in result["turns"]:
        print(t)
        print()


def cmd_drive(args) -> None:
    """Message a sibling window (wid-keyed). Thin alias over the tmux send path."""
    wid = _resolve_wid(args.wid)
    if not wid:
        print("no target window id (pass @N)", file=sys.stderr)
        sys.exit(1)
    if wid not in discovery.get_windows_by_id():
        print(f"{wid} is not a live window", file=sys.stderr)
        sys.exit(1)
    sender = orchestrator.self_wid() or "orchestrator"
    if messenger.send_tmux(wid, f"[{sender}] {args.message}"):
        print(f"Sent to {wid}")
    else:
        print(f"{wid} — send failed", file=sys.stderr)
        sys.exit(1)


def _print_hold(held, prefix: str = "") -> None:
    print(f"{prefix}Dispatch is HELD — no task will be claimed until it is released.")
    print(f"  {held.summary()}")
    print("  Reconciliation keeps running: a merged PR still closes out its run and "
          "frees its slot.")
    print("  Release: chela dispatch --resume")


def cmd_dispatch_hold(args) -> bool:
    """``chela dispatch --pause / --resume / --hold-status``. True if it handled the call.

    The hold is the orchestrator's answer to a race it always loses (see :mod:`chela.hold`):
    it says "claim nothing, I am rewriting the queue" BEFORE it starts writing, so the
    order is decided by intent rather than by whoever is faster. It is GLOBAL — every
    workflow, every dispatcher process — which is why it takes no WORKFLOW.md argument:
    the queue you are rewriting is the one being claimed from, wherever that claim runs.
    """
    if args.resume:
        released = hold.release()
        if released is None:
            print("Dispatch was not held. Nothing to resume.")
        else:
            print(f"Dispatch RESUMED — released a hold {hold.human_duration(released.age())} "
                  f"old ({released.by}{': ' + released.reason if released.reason else ''}).")
            print("The next tick claims the top of the queue as it now stands.")
        return True

    if args.pause:
        try:
            ttl = hold.parse_ttl(args.ttl)
        except ValueError as e:
            print(f"error: --ttl {e}", file=sys.stderr)
            raise SystemExit(2) from None
        try:
            held = hold.take(reason=args.reason or "", ttl_seconds=ttl)
        except OSError as e:
            # A hold the daemon will never see is worse than no hold: the caller would
            # rewrite the queue believing it was protected. Fail loudly, exit non-zero.
            print(f"error: could not take the hold ({e}) — dispatch is NOT paused",
                  file=sys.stderr)
            raise SystemExit(1) from None
        _print_hold(held)
        return True

    if args.hold_status:
        held = hold.read()
        if held is None:
            print("No dispatch hold. Tasks are claimed as normal.")
        elif held.expired():
            print(f"Dispatch hold EXPIRED ({held.summary()}) — the next dispatch tick "
                  "releases it and resumes, loudly.")
        else:
            _print_hold(held)
        return True

    return False


def cmd_dispatch(args) -> None:
    """Run the work-item dispatcher against a WORKFLOW.md."""
    if cmd_dispatch_hold(args):
        return
    if not args.workflow:
        print("error: the path to a WORKFLOW.md is required "
              "(or use --pause / --resume / --hold-status)", file=sys.stderr)
        raise SystemExit(2)
    if args.dry_run:
        plans = dispatcher.dry_run(args.workflow)
        # A dry run that previews a claim while the queue is HELD is telling a half-truth:
        # a live tick right now would claim nothing at all. Say so before the plans.
        held = hold.active()
        if held:
            print(f"NOTE: the queue is HELD ({held.summary()}) — a live tick would claim "
                  "NOTHING. Below is what it would dispatch once released.\n")
        if not plans:
            print("No open tasks")
            return
        for i, p in enumerate(plans):
            if i:
                print()
            print(f"=== task {p['task_id']}: {p['title']}")
            print(f"  worktree: {p['worktree_path']}")
            print(f"  branch:   {p['branch']}")
            print(f"  agent:    {p['agent_cmd']}  ({p['agent_cmd_source']})")
            print("  prompt:")
            for line in p["prompt"].splitlines():
                print(f"    {line}")
        return
    interval = max(5, int(args.interval))
    if args.once:
        summary = dispatcher.tick(args.workflow)
        if summary.get("error"):
            print(f"workflow error — new dispatches blocked until it parses: {summary['error']}")
        print(summary)
        return
    log.info("Dispatcher starting (workflow=%s, interval=%ds)", args.workflow, interval)
    stop = GracefulShutdown("chela dispatcher").install()
    while not stop.stopping:
        try:
            summary = dispatcher.tick(args.workflow)
            if summary["dispatched"] or summary["reconciled_done"] or summary["reconciled_failed"]:
                log.info("Dispatch tick: %s", summary)
        except Exception:
            log.exception("Dispatch tick failed")
        # Re-read on every pass so a `polling.interval_ms` edit re-paces this loop
        # too, without a restart. --interval is the operator's floor and default.
        try:
            interval = max(5, int(dispatcher.poll_interval(args.workflow, default=interval)))
        except Exception:
            log.exception("Could not refresh the poll interval; keeping %ds", interval)
        stop.wait(interval)

    stop.log_exit()


def _filter_runs(runs: list[dict], status: str | Sequence[str] | None) -> list[dict]:
    """Runs in ``status`` — one status, or any of several — or all runs when it is None.

    Reuses the single ``dispatcher.list_runs()`` source of truth — the filter is
    a pure view over it, never a second query."""
    if not status:
        return runs
    wanted = {status} if isinstance(status, str) else set(status)
    return [r for r in runs if r.get("status") in wanted]


def _run_age_str(started_at: str | None, *, now: datetime | None = None) -> str:
    """Coarse human age (``s``/``m``/``h``/``d``) from an ISO ``started_at``.

    ``?`` when the timestamp is missing/unparseable. ``now`` is injectable so the
    formatting is deterministically testable."""
    dt = dispatcher._parse_ts(started_at)
    if dt is None:
        return "?"
    now = now or datetime.now(timezone.utc)
    secs = max(0, int((now - dt).total_seconds()))
    if secs < 60:
        return f"{secs}s"
    mins = secs // 60
    if mins < 60:
        return f"{mins}m"
    hrs = mins // 60
    if hrs < 24:
        return f"{hrs}h"
    return f"{hrs // 24}d"


_CI_CHIPS = {
    dispatcher.CI_PASSING: "ci:green",
    dispatcher.CI_FAILING: "ci:RED",
    dispatcher.CI_PENDING: "ci:pending",
    dispatcher.CI_NONE: "ci:none",
    dispatcher.CI_UNKNOWN: "ci:UNKNOWN",
}


def _ci_chip(r: dict) -> str:
    """What GitHub last said about this run's checks — in WORDS, never a colour alone.

    A run with no recorded state (an old row, a PR the tick has not reached yet) reads
    ``ci:?`` and not ``ci:green``: the whole point of the fact is that not-yet-read is not
    a pass.
    """
    return _CI_CHIPS.get(r.get("pr_checks") or "", "ci:?")


def _format_awaiting_run(r: dict, *, now: datetime | None = None) -> str:
    """One line for the status-filtered view: task id, status, CI, age, PR URL, title."""
    task_id = r.get("task_id") or "-"
    status = r.get("status") or "-"
    age = _run_age_str(r.get("started_at"), now=now)
    pr = r.get("pr_url") or "-"
    title = (r.get("title") or "")[:50]
    return (f"  {task_id}  {status:<16}  {_ci_chip(r):<11}  age={age:<5}  "
            f"{pr:<45}  {title}")


def cmd_dispatch_runs(args) -> None:
    """Show dispatcher runs, optionally filtered by status.

    ``--awaiting`` is the "what is parked in review?" view — every run in
    ``dispatcher.REVIEW_STATUSES``, each with its PR URL + age, so the orchestrator can
    answer on demand without polling the dashboard. It is deliberately all THREE review
    states, not just ``awaiting_review``: a run the reviewer sent back
    (``changes_requested``) and a run the rework loop gave up on (``needs_human``) are
    exactly the ones a "what still needs me?" question is asking about, and a filter that
    showed only the first state would hide the loop it now feeds.
    """
    status = (list(dispatcher.REVIEW_STATUSES) if getattr(args, "awaiting", False)
              else getattr(args, "status", None))
    runs = _filter_runs(dispatcher.list_runs(), status)
    if not runs:
        print(f"No runs in status {status!r}" if status else "No runs")
        return
    if status:
        for r in runs:
            print(_format_awaiting_run(r))
        return
    for r in runs:
        title = (r["title"] or "")[:60]
        print(f"  {r['task_id']}  {r['status']:<16}  attempt={r['attempt']}  {r.get('window_name') or '-':<24}  {title}")


def cmd_knowledge_export(args) -> None:
    """Export the fleet's knowledge as an OKF v0.1 bundle.

    The bundle is LOCAL fleet data — keep it out of version control (the default
    output is ``~/.chela/knowledge``, outside any repo). See docs/OKF.md.
    """
    out = Path(args.out).expanduser() if args.out else None
    summary = okf.export_bundle(out_dir=out, since=args.since)
    print(f"OKF v{summary['okf_version']} bundle → {summary['out']}")
    print(
        f"  agents={summary['agents']}  runs={summary['runs']}  "
        f"schedules={summary['schedules']}  projects={summary['projects']}"
    )
    if summary.get("since"):
        print(f"  (runs filtered since {summary['since']})")


def cmd_install_statusline(args) -> None:
    """Print (or write) the Claude Code statusLine hook that feeds the context bar.

    The hook caches Claude Code's status payload (context %, the 5h/7d rate-limit
    blocks, cost) to ``$CHELA_DIR/context/<window>.json`` — the only place those
    numbers are exposed. Default is to print the snippet; ``--write`` edits the
    settings file and refuses to clobber an existing statusLine without ``--force``.
    """
    import json as _json

    script = Path(__file__).resolve().parent.parent / "scripts" / "cache-statusline.sh"
    snippet = {"type": "command", "command": str(script)}
    settings_path = Path(args.settings).expanduser() if args.settings else Path.home() / ".claude" / "settings.json"

    if not args.write:
        print('Add this "statusLine" key to your Claude Code settings')
        print(f"({settings_path}, or a repo's .claude/settings.json):\n")
        print('  "statusLine": ' + _json.dumps(snippet, indent=2).replace("\n", "\n  "))
        print(f"\nScript: {script}")
        print("Then restart the agents. Already run a statusLine? Keep it — chela falls")
        print("back to a transcript estimate, or point your script at $CHELA_DIR/context/ too.")
        print("\nTo apply automatically:  chela install-statusline --write")
        return

    if not script.exists():
        print(f"Script not found at {script} — run this from a chela checkout.")
        sys.exit(1)
    settings = {}
    if settings_path.exists():
        try:
            settings = _json.loads(settings_path.read_text())
        except (_json.JSONDecodeError, OSError) as e:
            print(f"Could not read/parse {settings_path}: {e}")
            sys.exit(1)
    existing = settings.get("statusLine")
    if existing and not args.force:
        print(f"A statusLine is already configured in {settings_path}:")
        print("  " + _json.dumps(existing))
        print("Refusing to overwrite. Re-run with --force, or wire chela in by hand so both coexist.")
        sys.exit(1)
    settings["statusLine"] = snippet
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(_json.dumps(settings, indent=2) + "\n")
    print(f"Installed chela statusLine into {settings_path}. Restart agents to apply.")


def _outbound_loop(monitor, registry, interval: int, stop) -> None:
    """Poll every bound window's transcript and relay new output, until stopped.

    ``registry.windows()`` is re-read each tick so the polled set follows the
    live bindings (Slice B mutates them without restarting this loop).

    **This loop blocks on the network, for as long as Telegram says.** Every message
    it relays is a synchronous ``sendMessage``, and a 429 makes it *sleep* for the
    advertised ``retry_after`` and re-send (:meth:`chela.telegram.relay.BotSender._call`
    — bounded, but the bound is ~90s per payload). A chatty agent's burst is a backlog
    that walks straight into flood control, so a tick here can run for minutes. That is
    the right trade for a transcript, whose messages keep: they are a *record*, and a
    late record is still the record. It is the wrong trade for a live TUI prompt, which
    is why the pane poll no longer rides this thread — see :func:`_pane_loop`.
    """
    while not stop.is_set():
        try:
            monitor.poll(registry.windows())
        except Exception:
            log.exception("Telegram relay poll failed")
        stop.wait(interval)


def _pane_loop(gate_watcher, registry, interval: int, stop) -> None:
    """Poll every bound window's PANE and relay the live-TUI prompts, until stopped.

    Its own thread, and that is the whole point (CMX-74). A gate exists only while it is
    on the pane — an ``AskUserQuestion`` a human answers in the terminal is on screen for
    *seconds* — and it is the one thing in this bridge that must be read in something
    like real time. Sharing a thread with :func:`_outbound_loop` meant it was read only
    once that loop's flood-controlled sends came back, so the window whose burst had just
    earned the 429 was exactly the window whose pane went unwatched — and the question it
    asked *after* that burst was the message that never arrived. Measured live on
    2026-07-14 on the orchestrator: two gates, 5s and 45s on the pane, both inside a
    flood-control storm, neither ever on the phone.

    So the pump and the watcher are decoupled. A relay stuck in a ``retry_after`` sleep
    now costs the transcript its latency and nothing else; the pane is still captured
    every ``interval``, and a gate still reaches the phone while it is still answerable.
    """
    while not stop.is_set():
        try:
            gate_watcher.poll(registry.windows())
        except Exception:
            log.exception("Telegram pane poll failed")
        stop.wait(interval)


def _reconcile_loop(registry, topic_api, interval: int, stop) -> None:
    """Auto-topics: reconcile the registry against the live fleet, until stopped.

    Each tick diffs live agent windows against the persisted bindings
    (:func:`chela.telegram.reconcile.reconcile_bindings`) — provisioning a topic for a new
    agent window and reaping a dead window's topic — and ``save``s only when
    something actually changed. Runs on its own interval alongside the outbound
    monitor and the inbound PTB app; the first tick fires immediately so a restart
    reconciles before we advertise. Idempotent, so a missed/duplicated tick is
    harmless.

    A DISPATCHER-spawned agent is the exception (CMX-73): it is identified from the
    ``runs`` table (:func:`~chela.telegram.reconcile.dispatched_window_ids` — the row
    owns the wid; the window's name is only a label) and, unless
    ``CHELA_TELEGRAM_BIND_DISPATCHED`` says otherwise, gets a topic **only once it blocks
    on a human** (:func:`~chela.telegram.reconcile.blocked_on_human` — the hook log **or**
    the pane, because a *permission* gate exists only on the pane), so a fleet of
    short-lived workers can't turn the forum into a changelog.
    """
    from chela.discovery import get_window_cwd_by_id
    from chela import telegram as tg

    while not stop.is_set():
        try:
            live, agents = tg.live_agent_windows()
            # The tmux server issuing window ids right now (CMX-77). A binding — and a run
            # row's window_id — only means anything inside the epoch that issued it; read it
            # here and hand it to both, so a restart reaps the stale ones instead of relaying
            # a stranger's pane into a dead agent's topic.
            now_epoch = epoch.current()
            dispatched = set() if BIND_DISPATCHED else tg.dispatched_window_ids(
                live_windows=live, now_epoch=now_epoch)
            if tg.reconcile_bindings(
                registry, live, agents, topic_api,
                cwd_for=get_window_cwd_by_id,
                dispatched=dispatched,
                gate_for=tg.blocked_on_human,
                bind_dispatched=BIND_DISPATCHED,
                now_epoch=now_epoch,
            ):
                registry.save()
                log.info("auto-topics: now bridging %s", ", ".join(registry.windows()) or "(none)")
        except Exception:
            log.exception("Telegram auto-topics reconcile failed")
        stop.wait(interval)


def _build_bindings_registry(args, chat: str):
    """Assemble the thread↔window registry the telegram daemon routes on.

    Sources, applied in order (later wins): the persisted bindings file (default
    ``~/.chela/telegram-bindings.json``), the single-window back-compat seed
    (``--wid @N`` + ``TELEGRAM_TOPIC_ID``), then any ``--bind @N:<thread_id>``
    flags. ``chat`` (from ``TELEGRAM_CHAT_ID``) always overrides any persisted
    chat id so env stays the inbound security boundary.
    """
    from chela.telegram import BindingRegistry

    registry = BindingRegistry.load(chat_id=chat)

    # Back-compat: --wid @N + TELEGRAM_TOPIC_ID seeds a one-entry registry.
    topic = os.environ.get("TELEGRAM_TOPIC_ID")
    if args.wid and topic:
        wid = _resolve_wid(args.wid)
        if wid:
            registry.bind(wid, topic)

    # --bind @N:<thread_id> (repeatable) — manual seeding for testing Slice A
    # before Slice B auto-creates topics.
    for spec in args.bind or []:
        window, sep, thread = spec.partition(":")
        if not sep or not window.strip() or not thread.strip():
            print(f"--bind expects @N:<thread_id>, got {spec!r}", file=sys.stderr)
            sys.exit(1)
        wid = _resolve_wid(window.strip())
        if not wid:
            print(f"--bind: could not resolve window {window!r}", file=sys.stderr)
            sys.exit(1)
        registry.bind(wid, thread.strip())

    return registry


def cmd_telegram(args) -> None:
    """Bridge N agent windows and their Telegram topics — outbound AND inbound.

    Reads TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID from the environment and builds a
    thread↔window BindingRegistry (persisted file + --wid/TELEGRAM_TOPIC_ID
    back-compat seed + --bind flags; see _build_bindings_registry). One process
    then does both halves for every bound window: OUTBOUND polls each window's
    transcript and posts new messages to THAT window's topic via the direct Bot
    API (a background thread), while INBOUND runs a python-telegram-bot
    Application that routes each topic's messages back to its window via
    messenger.send_tmux (the reliable-submit path). Unbound topics/windows are
    dropped/skipped; the bound chat_id is the inbound security boundary.

    With **auto-topics** (the default when neither --wid nor --bind is given, or
    forced with --auto-topics) a reconcile loop populates the registry from the
    live tmux fleet: every agent window gets a Telegram forum topic created for it
    (createForumTopic), a dead window's topic is archived (closeForumTopic), and
    closing a topic from Telegram unbinds its window without killing the agent.
    --wid/--bind stay the manual back-compat path (auto-topics off unless forced).

    Inbound needs the `[telegram]` extra (python-telegram-bot); pass
    --no-inbound to run outbound-only with no PTB dependency.
    """
    import threading

    from functools import partial

    from chela.telegram import (
        BotSender,
        PermissionGateWatcher,
        RegistryRelay,
        StatusRelay,
        TranscriptMonitor,
        default_bindings_path,
    )

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in the environment.", file=sys.stderr)
        sys.exit(1)

    # httpx logs each Bot API request at INFO with the full URL — which carries
    # the bot token — so it would leak the token into pm2 logs. Clamp the PTB/
    # httpx loggers to WARNING before any request goes out.
    for noisy in ("httpx", "telegram"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    # Auto-topics defaults ON unless the manual seed flags (--wid/--bind) are
    # used; --auto-topics forces it back on even alongside a manual seed.
    auto_topics = bool(args.auto_topics) or not (args.wid or args.bind)

    registry = _build_bindings_registry(args, chat)
    if not registry.windows() and not auto_topics:
        print(
            "no bindings — pass --wid @N with TELEGRAM_TOPIC_ID set, --bind @N:<thread_id>, "
            f"persist bindings to {default_bindings_path()}, or use --auto-topics",
            file=sys.stderr,
        )
        sys.exit(1)

    interval = max(1, int(args.interval))
    # One BotSender (fixed chat, no fixed topic); RegistryRelay supplies the
    # per-window message_thread_id looked up from the registry. With
    # CHELA_SHOW_TOOL_CALLS unset (the default) the relay drops the noisy
    # tool_use/tool_result stream, keeping text/thinking/user turns plus the
    # interactive prompts that need a human.
    bot = BotSender(token, chat)
    relay = RegistryRelay(bot.send, registry, show_tool_calls=SHOW_TOOL_CALLS)
    # Pane watcher (Slices C1/A2/B2 + C2): reads each bound window's pane to surface
    # the three live-TUI prompts the transcript can't relay in time — a permission
    # gate, an AskUserQuestion selector and an ExitPlanMode plan approval — each with
    # its answer keyboard. None of them is in the JSONL while it is still pending
    # (every one's tool_use lands only AFTER it is answered), so all three are
    # detected straight from the pane. It observes the SAME message stream the relay
    # does — regardless of SHOW_TOOL_CALLS, since it needs every tool_use/tool_result
    # — and polls in the outbound loop.
    from chela.messenger import capture_pane
    # ``post``/``edit`` let a relay update one message in place as the prompt renders
    # (mid-render partial → settled UI is ONE message, not a double-post), and
    # ``delete`` poofs it once answered so no live keyboard is left behind.
    # The ephemeral status line (CMX-43) rides that same per-tick capture: the live
    # working verb as ONE message that edits in place and poofs when the turn ends,
    # so a phone can tell a thinking agent from a dead one. Its Telegram calls opt
    # OUT of the 429 sleep-and-retry loop (retry_flood=False): they run in this same
    # outbound thread, and stalling every real agent message behind a flood-control
    # wait — to redeliver a decoration that the next poll would refresh anyway — is
    # a trade that only ever goes the wrong way.
    status = None
    if STATUS_LINE:
        status = StatusRelay(
            registry,
            post=partial(bot.post, retry_flood=False),
            edit=partial(bot.edit, retry_flood=False),
            delete=partial(bot.delete, retry_flood=False),
            typing=lambda thread: bot.chat_action("typing", thread),
        )
    # The AskUserQuestion CONTENT authority (CMX-49): the hook payload in the event log,
    # which holds every question, option, description and preview verbatim — where a
    # scrape of a multi-question / preview-bearing selector holds none of them. The pane
    # stays the liveness signal (and the whole content source for a pre-plugin agent that
    # emits no hooks), so this is an enrichment, never a replacement.
    # And the ANSWER channel (CMX-50): while the dashboard holds a gate's
    # `PermissionRequest` hook open, a tap is handed back through it — zero keystrokes,
    # and the only correct way to answer a multi-question / multiSelect picker. With no
    # hook holding the gate (a pre-plugin agent) this reads None and the keyboards fall
    # back to keystroke injection exactly as before.
    # And the PRIMARY surface (CMX-54): those same answer buttons now ride on the pane
    # MIRROR, above its D-pad, on one message — so the human can watch the ❯ cursor and
    # still answer with zero keystrokes. `selected` is the draft book the inbound taps
    # write to, read here so the ☑ ticks on the mirror and on the card cannot disagree.
    from chela.gateanswer import open_gate
    from chela.telegram.gateanswers import DRAFTS
    from chela.telegram.hookgate import pending_gate
    # Every Telegram call this watcher makes opts OUT of the 429 sleep-and-retry loop
    # (retry_flood=False), for the reason the pane poll got its own thread at all
    # (CMX-74): a gate is a live thing that exists for seconds, and the pane loop is
    # SINGLE-THREADED ACROSS WINDOWS. A send that sleeps out a `retry_after` holds the
    # reconcile lock (and the loop) for as long as Telegram says — so the chattiest
    # window's flood control would go on hiding the *next* window's question, which is
    # the bug, wearing a new hat. Nothing is dropped by not sleeping: an undelivered
    # prompt is not recorded as delivered, so the next tick posts it again, on a backoff
    # (`_REPOST_BACKOFF_BASE`) — the retry moved out of the sleep and into the loop.
    gate_watcher = PermissionGateWatcher(
        bot.send,
        registry,
        capture=capture_pane,
        post=partial(bot.post, retry_flood=False),
        edit=partial(bot.edit, retry_flood=False),
        delete=partial(bot.delete, retry_flood=False),
        status=status,
        pending=pending_gate,
        held=open_gate,
        selected=DRAFTS.selected,
    )

    def _on_message(window_id, msg):
        gate_watcher.observe(window_id, msg)
        relay.on_message(window_id, msg)

    monitor = TranscriptMonitor(on_message=_on_message)

    topic_api = None
    reconcile_interval = max(1, int(args.reconcile_interval))
    if auto_topics:
        from chela.telegram import TopicManager
        topic_api = TopicManager(token, chat)

    def _describe() -> str:
        return ", ".join(registry.windows()) or "(auto-topics: awaiting agent windows)"

    if args.no_inbound:
        # Outbound-only (no PTB dependency): the pane watch (and reconcile, if on) in
        # daemon threads, then poll transcripts in the foreground forever.
        stop = threading.Event()
        threading.Thread(
            target=_pane_loop,
            args=(gate_watcher, registry, interval, stop),
            daemon=True,
        ).start()
        if topic_api is not None:
            threading.Thread(
                target=_reconcile_loop,
                args=(registry, topic_api, reconcile_interval, stop),
                daemon=True,
            ).start()
            log.info("auto-topics reconcile every %ds", reconcile_interval)
        log.info("Relaying %s -> Telegram topics every %ds", _describe(), interval)
        try:
            _outbound_loop(monitor, registry, interval, stop)
        finally:
            stop.set()
        return

    try:
        from chela.telegram import RegistryRouter, TopicClosedHandler, build_application
        router = RegistryRouter(registry)
        # Topic-closed → unbind only (never kill the agent), and persist the drop.
        on_topic_closed = (
            TopicClosedHandler(registry, on_change=registry.save).handle
            if auto_topics
            else None
        )
        # The mirror's D-pad (CMX-52): a tap sends its key, then asks the pane watcher to
        # re-draw the mirrored dialog IN PLACE, so the ❯ cursor moves in the chat. The
        # watcher owns that message's id and its de-dup signature, so the re-render must
        # go through it — a second writer would double-post the mirror. Its 📖 toggle
        # (CMX-57) goes through the watcher for the same reason: it re-draws that one
        # message with the gate's full option list instead of the pane.
        application = build_application(
            token, router,
            on_topic_closed=on_topic_closed,
            refresh_mirror=gate_watcher.refresh_mirror,
            toggle_mirror=gate_watcher.toggle_mirror,
        )
    except ImportError as e:
        print(f"{e}\n(or run outbound-only:  chela telegram --no-inbound)", file=sys.stderr)
        sys.exit(1)

    # Outbound polling — the transcript relay and the PANE watch, on SEPARATE threads
    # (CMX-74: the relay sleeps through flood control, and a live gate cannot wait for
    # it) — and the auto-topics reconcile run in daemon threads; PTB owns the main
    # thread (it installs signal handlers, so it must run there).
    stop = threading.Event()
    threading.Thread(
        target=_outbound_loop,
        args=(monitor, registry, interval, stop),
        daemon=True,
    ).start()
    threading.Thread(
        target=_pane_loop,
        args=(gate_watcher, registry, interval, stop),
        daemon=True,
    ).start()
    if topic_api is not None:
        threading.Thread(
            target=_reconcile_loop,
            args=(registry, topic_api, reconcile_interval, stop),
            daemon=True,
        ).start()
        log.info("auto-topics reconcile every %ds", reconcile_interval)
    log.info("Bridging %s <-> Telegram topics (outbound %ds + inbound)", _describe(), interval)
    try:
        application.run_polling()
    finally:
        stop.set()


def cmd_dashboard(args) -> None:
    """Launch the optional web dashboard (requires the 'dashboard' extra).

    Imported lazily so the core CLI never depends on Flask: a plain
    `chelamux` install runs status/run/dispatch/msg without it.
    """
    try:
        from chela.dashboard import app as dashboard_app
    except ImportError as e:
        print("The dashboard is an optional component and needs Flask.")
        print("Install the extra:  uv sync --extra dashboard")
        print("              (or:  pip install 'chelamux[dashboard]')")
        print(f"  import error: {e}")
        sys.exit(1)
    # The env (i.e. $CHELA_DIR/chela.env) is the source of truth for the bind; these
    # flags are a one-off override for a hand-started instance. Either way the dashboard
    # PUBLISHES the port it really bound (config.publish_dashboard_port), so `chela
    # plugin` in another process renders a URL that is actually served.
    if args.host:
        os.environ["CHELA_DASH_HOST"] = args.host
    if args.port:
        os.environ["CHELA_DASHBOARD_PORT"] = str(args.port)
    host, port = config.dashboard_host(), config.dashboard_port()
    log.info("chela dashboard on http://%s:%s (zero auth — keep it loopback/tailnet)", host, port)
    dashboard_app.main()


def cmd_doctor(args) -> None:
    """Report where the config a process is RUNNING with disagrees with the env file.

    Exits 1 on an error-level finding — a drift that is breaking something right now (the
    dashboard on a port the plugin does not know about, say), as against a warning, which
    is a difference that is merely worth knowing about.
    """
    findings = doctor.check()
    for finding in findings:
        print(finding.render())
    errors = [f for f in findings if f.level == doctor.ERROR]
    if errors:
        print(f"\n{len(errors)} problem(s) — see above.")
        sys.exit(1)


def cmd_task_finished(args) -> None:
    """Mark a dispatcher run as awaiting_review and kill its tmux window.

    Invoked by the agent as the last step of its workflow: PR is open, the
    in-branch TODO strike is committed. chela transitions the row to
    awaiting_review (so the dispatcher won't re-dispatch the task) and kills the
    agent's tmux window. The row flips to `done` automatically on the next tick
    after the user merges the PR (which removes the TODO line from the base branch).
    """
    result = dispatcher.mark_awaiting_review(args.task_id)
    if not result.get("ok"):
        print(f"task-finished: {result.get('error', 'unknown error')}")
        sys.exit(1)
    print(f"Task {result['task_id']} awaiting review (pr_url={result.get('pr_url') or 'unknown'})")


def _rework_prospects(workflow_path: str | None) -> list[str]:
    """Will anything ACTUALLY re-spawn this run? Say what is true, not what is intended.

    The first cut of this command printed "The dispatcher re-spawns it in its own worktree
    on the next tick." unconditionally — a promise it never checked. Three ordinary
    conditions make it a lie, and in all three the run parks in ``changes_requested``
    indefinitely: the workflow is not one the daemon ticks, its WORKFLOW.md does not parse,
    or the queue is on hold. The reviewer is the person who can fix all three, and this is
    the moment they are looking.
    """
    lines: list[str] = []
    wf_path = Path(workflow_path).resolve() if workflow_path else None

    if wf_path is None or wf_path not in DISPATCH_WORKFLOWS:
        shown = str(wf_path or "?")
        lines.append(
            f"⚠ {shown} is NOT in CHELA_DISPATCH_WORKFLOWS — the daemon ticks nothing for "
            "it, so NOTHING will re-spawn this run. Add it (and restart the daemon), or "
            f"turn the loop by hand: chela dispatch {shown}"
        )
    else:
        status = workflow.load_workflow_cached(wf_path)
        if status.error:
            lines.append(
                f"⚠ {wf_path} does not parse ({status.error}) — dispatch is BLOCKED for it "
                "and the re-spawn waits until the file is valid again."
            )

    held = hold.active()
    if held:
        lines.append(
            f"⚠ the queue is HELD ({hold.human_duration(held.age())} ago"
            f"{', ' + held.by if held.by else ''}"
            f"{': ' + held.reason if held.reason else ''}) — a hold pauses CLAIMS, and a "
            "rework re-spawn is a claim. It waits until the hold is released "
            "(chela hold --release)."
        )

    if not lines:
        lines.append("The dispatcher re-spawns it in its own worktree on the next tick.")
    return lines


def cmd_review(args) -> None:
    """Record the verdict on a PR under review — the carrier of the rework loop.

    ``--request-changes`` sends the run BACK: the row goes to ``changes_requested`` and the
    next dispatcher tick re-spawns the agent in its ORIGINAL worktree, on its ORIGINAL
    branch, with this verdict in its prompt. ``--approve`` records a pass and changes
    nothing — the run stays ``awaiting_review`` and merging remains a human's call.

    The body is read from a file or from stdin (``--body-file -``) and never from an
    argument: a verdict is long-form markdown — backticks, quotes, newlines, a table of
    defects — and shell-quoting one is how it arrives mangled or truncated.

    ⛔ The run row is the authority on the VERDICT, NOT GitHub. ``gh pr review
    --request-changes`` refuses a PR authored by the calling account, and the whole fleet is
    one account, so GitHub's ``reviewDecision`` can never carry this. The PR comment this
    posts is the projection.

    ⛔ GitHub IS the authority on the CHECKS, and ``--approve`` reads them back from it: an
    approval of a PR whose CI is red (or whose checks could not be read) is REFUSED unless
    ``--force``. A red PR was approved and merged on 2026-07-14 and it broke the base
    branch — nobody had looked at the artifact that decides whether the thing can ship.
    """
    if args.approve == args.request_changes:      # neither, or both
        print("review: pass exactly one of --approve / --request-changes")
        sys.exit(2)

    body = ""
    if args.body_file:
        body = sys.stdin.read() if args.body_file == "-" else Path(args.body_file).read_text()
    if args.request_changes and not body.strip():
        print("review: --request-changes needs a verdict body (--body-file <path>|-)")
        sys.exit(2)

    result = (dispatcher.request_changes(args.run, body) if args.request_changes
              else dispatcher.approve(args.run, body, force=getattr(args, "force", False)))
    if not result.get("ok"):
        print(f"review: {result.get('error', 'unknown error')}")
        sys.exit(1)

    task_id, status = result["task_id"], result["status"]
    if args.request_changes:
        print(f"Run {task_id} ({result.get('branch_name') or '?'}) → changes_requested "
              f"(verdict {result['round']}, rework {result['rework_count']}/{result['max_reworks']})")
        for line in _rework_prospects(result.get("workflow_path")):
            print(f"  {line}")
    else:
        print(f"Run {task_id} ({result.get('branch_name') or '?'}) approved — still "
              f"{status}; merge is yours to make.")
        print(f"  CI: {result.get('ci_note') or result.get('pr_checks')}")
    if result.get("comment_posted"):
        print(f"  PR comment posted: {result.get('pr_url') or ''}")
    elif args.request_changes or body.strip():
        print(f"  ⚠ PR comment NOT posted ({result.get('comment_detail')}) — the run row is "
              "the authority, so the loop still turns, but nothing landed on the PR.")


def cmd_judge(args) -> None:
    """⚖️ Execute the judge's experiments and publish the verdict — the judge agent's last step.

    ⛔ It does NOT take the judge's word for anything. The agent proposes mutations; THIS
    applies them (in the throwaway judge worktree), reads the files back to prove they
    changed, parse-checks them, runs the repo's own ``judge.test_cmd``, restores them, and
    adjudicates. A guard that survives a live, parsing, minimal corruption is a FACT, and it
    is the only thing allowed to send a PR back — through ``request_changes``, the same
    carrier a human reviewer and the CI gate use. Opinions go in ``notes`` and are posted as
    a comment, where they cost nothing.

    ⛔ It never merges and never approves: a clean PR stays ``awaiting_review``.
    """
    result = judge.judge_run(args.run, args.experiments, cleanup=not args.no_cleanup)
    if not result.get("ok") and "task_id" not in result:
        print(f"judge: {result.get('error', 'unknown error')}")
        sys.exit(1)

    state = result.get("state")
    for outcome in result.get("outcomes") or []:
        print(f"  [{outcome['verdict']:8}] {outcome['file']}: {outcome['guard'][:70]}")
        print(f"             {outcome['reason']}")
    if state == judge.J_BLOCKED:
        print(f"⚖️ {result['task_id']}: {result['blocking']} guard(s) SURVIVED corruption — "
              f"the PR was SENT BACK (rework round {result.get('round')}).")
    elif state == judge.J_CANNOT_VERIFY:
        print(f"⚖️ {result['task_id']}: ⚠ CANNOT VERIFY — {result.get('cannot_verify') or result.get('error')}")
        print("   Nothing was blocked and nothing was cleared. This PR is a human's.")
    else:
        print(f"⚖️ {result['task_id']}: every guard held. The run stays awaiting_review — "
              "the judge never merges.")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="chela",
        description="A tiny control plane for a fleet of Claude Code agents on tmux.",
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("status", help="List discovered agent windows")
    sub.add_parser("run", help="Run the daemon loop (scheduler)")

    p_sched = sub.add_parser("schedule", help="Manage scheduled tasks")
    sched_sub = p_sched.add_subparsers(dest="sched_cmd")

    p_add = sched_sub.add_parser("add", help="Add a task")
    p_add.add_argument("agent", help="Target agent (tmux window name)")
    p_add.add_argument("--every", default=None, help="Interval, e.g. 30s/5m/1h/1d")
    p_add.add_argument("--cron", default=None, help="Cron expression, e.g. '0 */8 * * *'")
    p_add.add_argument("--once", default=None, help="ISO timestamp to fire once")
    p_add.add_argument("--prompt", required=True, help="Prompt text to send")

    sched_sub.add_parser("list", help="List tasks")

    p_rm = sched_sub.add_parser("remove", help="Remove a task")
    p_rm.add_argument("id", type=int)

    # msg
    p_msg = sub.add_parser("msg", help="Send a message to an agent")
    p_msg.add_argument("agent", help="Recipient agent — window id (@32) or tmux window name")
    p_msg.add_argument("message", help="Message text")
    p_msg.add_argument("--from", dest="from_agent", default="chela-cli", help="Sender label")
    p_msg.add_argument("--priority", default="normal", help="critical|high|normal|low")

    # broadcast
    p_bc = sub.add_parser("broadcast", help="Send a message to all other live agents")
    p_bc.add_argument("message", help="Message text")
    p_bc.add_argument("--from", dest="from_agent", default="chela-cli", help="Sender label")
    p_bc.add_argument("--priority", default="normal", help="critical|high|normal|low")

    # --- orchestrator toolkit (agent-facing: observe + drive siblings) ---
    sub.add_parser("whoami", help="Print this agent's own window id ($CHELA_WID)")

    p_peek = sub.add_parser(
        "peek", help="Filtered status view of a window (status + recap + cwd + health)")
    p_peek.add_argument("wid", nargs="?", help="Target window id (@N, N, or 'self'); default self")
    p_peek.add_argument("--json", action="store_true", help="Emit the raw peek dict as JSON")

    p_watch = sub.add_parser(
        "watch", help="Report back when a window you delegated to finishes/blocks/dies "
                      "(no window: (re-)register THIS session as the orchestrator)")
    p_watch.add_argument("wid", nargs="?",
                         help="Window you dispatched work to (@N or N). Omit to register "
                              "this session as the orchestrator and nothing else — the "
                              "recovery path after tmux restarts and renumbers the fleet.")
    p_watch.add_argument("--note", help="What you asked it to do (echoed back to you)")

    p_unwatch = sub.add_parser("unwatch", help="Stop watching a window")
    p_unwatch.add_argument("wid", help="Window to stop watching (@N or N)")

    sub.add_parser("watching", help="Show inbox watches + the queued events")

    # rooms — a typed, durable ledger two windows are members of, plus ACTIVE DISPATCH:
    # a targeted handoff/question/blocker is injected into the peer's terminal, and the
    # answer routes back to the asker with no human in the middle.
    p_room = sub.add_parser(
        "room", help="Agent rooms: a shared ledger + active dispatch between agents")
    room_sub = p_room.add_subparsers(dest="room_cmd")

    p_rcreate = room_sub.add_parser("create", help="Create a room")
    p_rcreate.add_argument("room", help="Room id (letters, digits, . _ -)")

    p_rjoin = room_sub.add_parser("join", help="Put a window in a room (default: your own)")
    p_rjoin.add_argument("room")
    p_rjoin.add_argument("--wid", default=None, help="Window to add (@N, N, or 'self')")

    p_rleave = room_sub.add_parser("leave", help="Remove a window from a room")
    p_rleave.add_argument("room")
    p_rleave.add_argument("--wid", default=None, help="Window to remove (@N, N, or 'self')")

    p_rstatus = room_sub.add_parser("status", help="Rooms, their members, and parked deliveries")
    p_rstatus.add_argument("room", nargs="?", default=None)

    p_rpost = room_sub.add_parser(
        "post", help="Post to a room; a targeted handoff/question/blocker WAKES the peer")
    p_rpost.add_argument("room")
    p_rpost.add_argument("message", help="Message body (control chars are stripped)")
    p_rpost.add_argument("--kind", required=True, metavar="K",
                         help=f"One of: {', '.join(rooms.KINDS)} "
                              f"(only {', '.join(sorted(rooms.DISPATCH_KINDS))} may interrupt)")
    p_rpost.add_argument("--to", action="append", metavar="@N",
                         help="Recipient window (repeatable). No --to = recorded, never injected")
    p_rpost.add_argument("--from", dest="from_wid", default=None, metavar="@N",
                         help="Sender window (default: your own, $CHELA_WID)")
    p_rpost.add_argument("--reply-to", type=int, default=None, metavar="SEQ",
                         help="The post you are answering — keeps the chain (and its hop cap)")

    p_rrecap = room_sub.add_parser(
        "recap", help="The bounded room recap a restarted agent is handed at SessionStart")
    p_rrecap.add_argument("--wid", default=None, metavar="@N",
                          help="Window to recap for (default: your own, $CHELA_WID)")

    p_rdigest = room_sub.add_parser("digest", help="The room's ledger (read from the event log)")
    p_rdigest.add_argument("room")
    p_rdigest.add_argument("--limit", type=int, default=50, metavar="N",
                           help="Show the last N events (default: 50)")

    # events — the durable log: replay from a cursor, filter, tail. `emit` is the
    # programmatic append, exposed to the shell (and to N concurrent writers).
    p_ev = sub.add_parser("events", help="Replay / filter / follow the event log")
    p_ev.add_argument("--after-seq", type=int, default=None, metavar="N",
                      help="Only events after this seq (a cursor)")
    p_ev.add_argument("--after-boot", default=None, metavar="ID",
                      help="The boot_id your cursor came from; a change is reported as a gap")
    p_ev.add_argument("--type", dest="types", action="append", metavar="T",
                      help="Only this event type (repeatable)")
    p_ev.add_argument("--wid", default=None, metavar="@N", help="Only events for this window")
    p_ev.add_argument("--limit", type=int, default=None, metavar="N",
                      help="At most N events, oldest-first from the cursor (resumable)")
    p_ev.add_argument("--tail", type=int, default=None, metavar="N",
                      help="Show only the last N of the result")
    p_ev.add_argument("--follow", "-f", action="store_true", help="Tail the log live")
    p_ev.add_argument("--json", action="store_true", help="Emit each event as JSON (one per line)")

    ev_sub = p_ev.add_subparsers(dest="events_cmd")
    p_emit = ev_sub.add_parser("emit", help="Append one event to the log")
    p_emit.add_argument("--type", required=True, help="Event type (mirrors an inbox `kind`)")
    p_emit.add_argument("--summary", default="", help="One line — what a notification renders")
    p_emit.add_argument("--payload", default=None, help="JSON object — the structured record")
    p_emit.add_argument("--wid", default=None, help="Window this event is about (@N or N)")
    p_emit.add_argument("--session-id", default=None, help="Claude Code session id, if known")

    p_rot = ev_sub.add_parser(
        "rotate", help="Retire the log to a .bak and start a fresh boot_id (operator step)")
    p_rot.add_argument("--yes", action="store_true", help="Actually do it (without this: a dry run)")

    # plugin — the Claude Code hooks plugin, with this install's dashboard port in it.
    p_plugin = sub.add_parser(
        "plugin", help="Render the Claude Code hooks plugin (feeds the event log)")
    p_plugin.add_argument("--dir", default=str(config.CHELA_DIR / "plugin"), metavar="PATH",
                          help="Where to write it (default: $CHELA_DIR/plugin)")
    p_plugin.add_argument("--port", type=int, default=None, metavar="N",
                          help="Dashboard port to post to (default: the resolved one)")

    p_read = sub.add_parser(
        "read", help="Distilled read of a sibling's Claude Code transcript")
    p_read.add_argument("wid", nargs="?", help="Target window id (@N, N, or 'self'); default self")
    grp = p_read.add_mutually_exclusive_group()
    grp.add_argument("--tail", type=int, metavar="N",
                     help="Last N conversation turns (default: tail 10)")
    grp.add_argument("--query", metavar="Q", help="Turns whose text contains every term in Q")
    grp.add_argument("--all", action="store_true", help="The whole conversation, uncapped")
    p_read.add_argument("--json", action="store_true", help="Emit the result as JSON")

    p_drive = sub.add_parser("drive", help="Message a sibling window (wid-keyed send)")
    p_drive.add_argument("wid", help="Target window id (@N or N)")
    p_drive.add_argument("message", help="Message text")

    # dispatch
    p_disp = sub.add_parser("dispatch", help="Run the work-item dispatcher")
    p_disp.add_argument(
        "workflow", nargs="?", default=None,
        help="Path to WORKFLOW.md (not needed for --pause/--resume/--hold-status)",
    )
    p_disp.add_argument("--once", action="store_true", help="Run one tick and exit")
    p_disp.add_argument("--interval", type=int, default=60, help="Poll interval in seconds (default 60)")
    p_disp.add_argument(
        "--dry-run", action="store_true",
        help="Print rendered prompt and worktree path for each open task; do not spawn windows or run hooks",
    )
    # The queue hold (chela.hold): claim nothing while the queue is being rewritten.
    # Global (every workflow, every dispatcher process) and persisted to a file, because
    # the daemon that honours it is not the process that takes it.
    p_disp.add_argument(
        "--pause", action="store_true",
        help="HOLD the queue: claim no new task until --resume (reconciliation continues). "
             "Take this BEFORE reordering the tracker.",
    )
    p_disp.add_argument("--resume", action="store_true", help="Release the queue hold")
    p_disp.add_argument(
        "--hold-status", action="store_true", help="Print the current queue hold, if any",
    )
    p_disp.add_argument(
        "--reason", default="", help="Why the queue is held (shown to anyone who looks)",
    )
    p_disp.add_argument(
        "--ttl", default=str(hold.DEFAULT_TTL_SECONDS),
        help=f"How long a --pause lasts before it self-releases, loudly — 900, 30m, 2h "
             f"(default {hold.human_duration(hold.DEFAULT_TTL_SECONDS)}, max "
             f"{hold.human_duration(hold.MAX_TTL_SECONDS)}). A hold can never strand the "
             f"fleet.",
    )

    # dispatch-runs (inspection)
    p_runs = sub.add_parser("dispatch-runs", help="List dispatcher runs")
    p_runs.add_argument(
        "--status", default=None,
        help="Only show runs in this status (e.g. awaiting_review, changes_requested, "
             "needs_human, running, failed, done)",
    )
    p_runs.add_argument(
        "--awaiting", action="store_true",
        help="Every run parked in the review loop — awaiting_review, changes_requested "
             "(sent back) and needs_human (the loop gave up) — with PR URL + age",
    )

    # review — the verdict on a PR under review (the rework loop's carrier)
    p_review = sub.add_parser(
        "review",
        help="Record a verdict on a run's PR: --request-changes sends it back to its agent",
    )
    p_review.add_argument("run", help="Run id, branch name, or window name (e.g. cmx-68)")
    p_review.add_argument(
        "--request-changes", action="store_true",
        help="FAIL the PR: the run goes to changes_requested and the dispatcher re-spawns "
             "its agent in the ORIGINAL worktree/branch with this verdict",
    )
    p_review.add_argument(
        "--approve", action="store_true",
        help="PASS the PR: records the verdict, leaves the run awaiting_review. Merging "
             "stays a human's call — this never merges anything",
    )
    p_review.add_argument(
        "--body-file", default=None, metavar="PATH",
        help="Read the verdict from PATH, or from stdin with '-'. A verdict is long-form "
             "markdown and must never be shell-quoted",
    )
    p_review.add_argument(
        "--force", action="store_true",
        help="Approve a PR whose CI is RED (or whose checks could not be read). Refused "
             "without this: a red PR was approved and merged once, and it broke the base "
             "branch. Say why in the body",
    )

    # judge — the adversarial pass whose BLOCKING verdicts are facts (chela/judge.py)
    p_judge = sub.add_parser(
        "judge",
        help="⚖️ Run the judge's mutation experiments on a PR and publish the verdict",
    )
    judge_sub = p_judge.add_subparsers(dest="judge_cmd")
    p_jrun = judge_sub.add_parser(
        "run",
        help="Apply the proposed mutations, re-run the suite, and publish the verdict. A "
             "guard that survives corruption sends the PR back; everything else is a comment",
    )
    p_jrun.add_argument("run", help="Run id, branch name, or window name (e.g. cmx-75)")
    p_jrun.add_argument(
        "--experiments", required=True, metavar="PATH",
        help="The JSON the judge agent wrote: {\"experiments\": [{guard, file, before, "
             "after, kind}], \"notes\": [...]}. chela runs them; it does not trust them",
    )
    p_jrun.add_argument(
        "--no-cleanup", action="store_true",
        help="Keep the judge worktree and the tmux window (debugging a judge run by hand)",
    )

    # knowledge — export the fleet's knowledge as an OKF bundle (local data; see docs/OKF.md)
    p_know = sub.add_parser("knowledge", help="Export fleet knowledge as an OKF bundle")
    know_sub = p_know.add_subparsers(dest="know_cmd")
    p_kexp = know_sub.add_parser("export", help="Write an OKF v0.1 bundle of runs/schedules/agents/projects")
    p_kexp.add_argument("--out", default=None, help="Output dir (default: ~/.chela/knowledge)")
    p_kexp.add_argument("--since", default=None, help="Only include runs started on/after this ISO date")

    # install-statusline — wire the context-bar producer into Claude Code
    p_sl = sub.add_parser(
        "install-statusline",
        help="Print or install the Claude Code statusLine hook that feeds the context bar",
    )
    p_sl.add_argument("--write", action="store_true", help="Write into the settings file (default: just print the snippet)")
    p_sl.add_argument("--force", action="store_true", help="Overwrite an existing statusLine (with --write)")
    p_sl.add_argument("--settings", default=None, help="settings.json path (default: ~/.claude/settings.json)")

    # telegram — bridge N agent windows and their topics (outbound + inbound)
    p_tg = sub.add_parser(
        "telegram",
        help="Bridge agent windows and their Telegram topics (outbound relay + inbound routing)",
    )
    p_tg.add_argument(
        "--wid",
        help="Single-window back-compat: bind this window (@N or N) to TELEGRAM_TOPIC_ID",
    )
    p_tg.add_argument(
        "--bind", action="append", metavar="@N:THREAD_ID",
        help="Bind a window to a topic thread id (repeatable), e.g. --bind @3:42",
    )
    p_tg.add_argument("--interval", type=int, default=2, help="Outbound poll interval in seconds (default 2)")
    p_tg.add_argument(
        "--auto-topics", action="store_true",
        help="Auto-create/close a Telegram topic per agent window (default on when no --wid/--bind)",
    )
    p_tg.add_argument(
        "--reconcile-interval", type=int, default=15,
        help="Auto-topics reconcile interval in seconds (default 15)",
    )
    p_tg.add_argument(
        "--no-inbound", action="store_true",
        help="Outbound relay only; skip inbound routing (no python-telegram-bot dependency)",
    )

    # dashboard (optional component)
    p_dash = sub.add_parser("dashboard", help="Launch the optional web dashboard (needs the 'dashboard' extra)")
    p_dash.add_argument("--host", default=None,
                        help="Bind host (default: $CHELA_DASH_HOST, else 127.0.0.1)")
    p_dash.add_argument("--port", type=int, default=None,
                        help="One-off override of $CHELA_DASHBOARD_PORT (the env file is "
                             "the source of truth; default 5001)")

    # doctor — is what the fleet is RUNNING with still what the env file says?
    sub.add_parser(
        "doctor",
        help="Check the running config against $CHELA_DIR/chela.env (exits 1 on a break)",
    )

    # task-finished — final step in the dispatcher work-item lifecycle
    p_tf = sub.add_parser(
        "task-finished",
        help="Mark a dispatcher run as awaiting_review and kill its tmux window",
    )
    p_tf.add_argument("task_id")

    args = parser.parse_args()

    if args.command == "status":
        cmd_status(args)
    elif args.command == "run":
        cmd_run(args)
    elif args.command == "schedule":
        if args.sched_cmd == "add":
            cmd_schedule_add(args)
        elif args.sched_cmd == "list":
            cmd_schedule_list(args)
        elif args.sched_cmd == "remove":
            cmd_schedule_remove(args)
        else:
            p_sched.print_help()
    elif args.command == "msg":
        cmd_msg(args)
    elif args.command == "broadcast":
        cmd_broadcast(args)
    elif args.command == "plugin":
        cmd_plugin(args)
    elif args.command == "whoami":
        cmd_whoami(args)
    elif args.command == "peek":
        cmd_peek(args)
    elif args.command == "read":
        cmd_read(args)
    elif args.command == "watch":
        cmd_watch(args)
    elif args.command == "unwatch":
        cmd_unwatch(args)
    elif args.command == "watching":
        cmd_watching(args)
    elif args.command == "room":
        room_cmds = {"create": cmd_room_create, "join": cmd_room_join,
                     "leave": cmd_room_leave, "status": cmd_room_status,
                     "post": cmd_room_post, "digest": cmd_room_digest,
                     "recap": cmd_room_recap}
        if args.room_cmd in room_cmds:
            room_cmds[args.room_cmd](args)
        else:
            p_room.print_help()
    elif args.command == "events":
        if args.events_cmd == "emit":
            cmd_events_emit(args)
        elif args.events_cmd == "rotate":
            cmd_events_rotate(args)
        else:
            cmd_events(args)
    elif args.command == "doctor":
        cmd_doctor(args)
    elif args.command == "drive":
        cmd_drive(args)
    elif args.command == "dispatch":
        cmd_dispatch(args)
    elif args.command == "dispatch-runs":
        cmd_dispatch_runs(args)
    elif args.command == "knowledge":
        if args.know_cmd == "export":
            cmd_knowledge_export(args)
        else:
            p_know.print_help()
    elif args.command == "install-statusline":
        cmd_install_statusline(args)
    elif args.command == "telegram":
        cmd_telegram(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    elif args.command == "task-finished":
        cmd_task_finished(args)
    elif args.command == "review":
        cmd_review(args)
    elif args.command == "judge":
        if args.judge_cmd == "run":
            cmd_judge(args)
        else:
            p_judge.print_help()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
