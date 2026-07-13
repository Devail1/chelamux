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
from datetime import datetime, timezone
from pathlib import Path

from chela import (
    agent_manager,
    config,
    discovery,
    dispatcher,
    event_log,
    hooks,
    inbox,
    messenger,
    notify,
    okf,
    orchestrator,
    scheduler,
)
from chela.config import (
    SCHEDULER_POLL_INTERVAL,
    DISPATCH_WORKFLOWS,
    NOTIFY_INTERVAL,
    SHOW_TOOL_CALLS,
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
    if DISPATCH_WORKFLOWS:
        log.info("Dispatcher enabled for %d workflow(s): %s",
                 len(DISPATCH_WORKFLOWS), ", ".join(str(p) for p in DISPATCH_WORKFLOWS))
    if notify.enabled():
        log.info("Needs-input notifications enabled (every %ds)", NOTIFY_INTERVAL)
    # Per-workflow, not global: each WORKFLOW.md carries its own effective poll
    # interval (`polling.interval_ms`, re-read on change), so they no longer share
    # one clock.
    last_dispatch_check: dict[Path, float] = {}
    dispatch_blocked: set[Path] = set()   # workflows whose file currently doesn't parse
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
                        log.error("Dispatch BLOCKED for %s (reconciliation continues on the "
                                  "last known-good config): %s", wf_path.name, summary.get("error"))
                    elif not summary.get("blocked") and wf_path in dispatch_blocked:
                        dispatch_blocked.discard(wf_path)
                        log.info("Dispatch resumed for %s — workflow parses again", wf_path.name)
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
        except Exception:
            log.exception("Error in daemon loop")
        stop.wait(SCHEDULER_POLL_INTERVAL)

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
    """Send a message to one live agent over tmux."""
    delivered = messenger.send_message(args.from_agent, args.agent, args.message, args.priority)
    if delivered:
        print(f"Sent to {args.agent}")
    else:
        print(f"{args.agent} offline — not delivered")


def cmd_broadcast(args) -> None:
    """Send a message to every other live agent."""
    results = messenger.broadcast(args.from_agent, args.message, args.priority)
    if not results:
        print("No other agents online")
        return
    for agent, delivered in sorted(results.items()):
        print(f"  {agent:<24} {'sent' if delivered else 'offline'}")


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
    """
    wid = _resolve_wid(args.wid)
    if not wid:
        print("no window id (pass @N)", file=sys.stderr)
        sys.exit(1)
    result = inbox.watch(wid, args.note or "", by=orchestrator.self_wid())
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
    """What the inbox is watching, and what is queued for delivery."""
    store = inbox.load()
    orch = inbox.orchestrator_wid(store)
    print(f"orchestrator: {orch or '(unregistered — the inbox is inert)'}")
    if not inbox.enabled():
        print("inbox: DISABLED (CHELA_INBOX_ENABLED=false)")
    names = discovery.get_windows_by_id()
    watches = store["watches"]
    print(f"\nwatching ({len(watches)}):")
    for wid, meta in sorted(watches.items()):
        note = f' — "{meta.get("note")}"' if meta.get("note") else ""
        print(f"  {wid:<6} {names.get(wid, '(gone)'):<24}{note}")
    queue = store["queue"]
    print(f"\nqueued, awaiting your next idle ({len(queue)}):")
    for event in queue:
        print(f"  {inbox.render(event)}")


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


def cmd_plugin(args) -> None:
    """Render the Claude Code hooks plugin with THIS install's dashboard port baked in.

    The plugin committed to the repo targets chela's default port, and a hook ``url`` is
    a literal — Claude Code does not expand env vars in it. So a dashboard on any other
    port needs its own copy of the manifest, or the hooks post into a closed socket and
    the feature looks simply broken. This is that copy.
    """
    port = args.port or config.dashboard_port()
    directory = hooks.render_plugin(Path(args.dir).expanduser(), port=port)
    print(f"plugin rendered at {directory} (posting to 127.0.0.1:{port})")
    print("\ninstall it for one session:")
    print(f"  claude --plugin-dir {directory}")
    print("\nor persistently, from Claude Code:")
    print(f"  /plugin marketplace add {directory}")
    print("  /plugin install chela@chela")
    print("\nHooks are read at agent STARTUP — a running agent will not pick them up.")


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


def cmd_dispatch(args) -> None:
    """Run the work-item dispatcher against a WORKFLOW.md."""
    if args.dry_run:
        plans = dispatcher.dry_run(args.workflow)
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


def _filter_runs(runs: list[dict], status: str | None) -> list[dict]:
    """Runs in ``status`` (exact match), or all runs when ``status`` is None.

    Reuses the single ``dispatcher.list_runs()`` source of truth — the filter is
    a pure view over it, never a second query."""
    if not status:
        return runs
    return [r for r in runs if r.get("status") == status]


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


def _format_awaiting_run(r: dict, *, now: datetime | None = None) -> str:
    """One line for the status-filtered view: task id, status, age, PR URL, title."""
    task_id = r.get("task_id") or "-"
    status = r.get("status") or "-"
    age = _run_age_str(r.get("started_at"), now=now)
    pr = r.get("pr_url") or "-"
    title = (r.get("title") or "")[:50]
    return f"  {task_id}  {status:<16}  age={age:<5}  {pr:<45}  {title}"


def cmd_dispatch_runs(args) -> None:
    """Show dispatcher runs, optionally filtered by status.

    ``--awaiting`` is shorthand for ``--status awaiting_review`` — the "what's up
    for review?" view: only runs blocked on a human, each with its PR URL + age,
    so the orchestrator can answer on demand without polling the dashboard.
    """
    status = "awaiting_review" if getattr(args, "awaiting", False) else getattr(args, "status", None)
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


def _outbound_loop(monitor, registry, interval: int, stop, gate_watcher=None) -> None:
    """Poll every bound window's transcript and relay new output, until stopped.

    ``registry.windows()`` is re-read each tick so the polled set follows the
    live bindings (Slice B mutates them without restarting this loop). When a
    ``gate_watcher`` is given, it runs in the SAME tick right after the transcript
    poll (no new thread): the transcript poll has just updated its per-window
    pending-tool state, so the gate watcher reads only the panes of windows whose
    latest ``tool_use`` is still unpaired.
    """
    while not stop.is_set():
        wids = registry.windows()
        try:
            monitor.poll(wids)
        except Exception:
            log.exception("Telegram relay poll failed")
        if gate_watcher is not None:
            try:
                gate_watcher.poll(wids)
            except Exception:
                log.exception("Telegram permission-gate poll failed")
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
    """
    from chela.discovery import get_window_cwd_by_id
    from chela.telegram import live_agent_windows, reconcile_bindings

    while not stop.is_set():
        try:
            live, agents = live_agent_windows()
            if reconcile_bindings(registry, live, agents, topic_api, cwd_for=get_window_cwd_by_id):
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

    from chela.telegram import (
        BotSender,
        PermissionGateWatcher,
        RegistryRelay,
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
    gate_watcher = PermissionGateWatcher(
        bot.send,
        registry,
        capture=capture_pane,
        post=bot.post,
        edit=bot.edit,
        delete=bot.delete,
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
        # Outbound-only (no PTB dependency): reconcile (if on) in a daemon thread,
        # then poll transcripts in the foreground forever.
        stop = threading.Event()
        if topic_api is not None:
            threading.Thread(
                target=_reconcile_loop,
                args=(registry, topic_api, reconcile_interval, stop),
                daemon=True,
            ).start()
            log.info("auto-topics reconcile every %ds", reconcile_interval)
        log.info("Relaying %s -> Telegram topics every %ds", _describe(), interval)
        try:
            _outbound_loop(monitor, registry, interval, stop, gate_watcher)
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
        application = build_application(token, router, on_topic_closed=on_topic_closed)
    except ImportError as e:
        print(f"{e}\n(or run outbound-only:  chela telegram --no-inbound)", file=sys.stderr)
        sys.exit(1)

    # Outbound polling (and auto-topics reconcile) run in daemon threads; PTB owns
    # the main thread (it installs signal handlers, so it must run there).
    stop = threading.Event()
    threading.Thread(
        target=_outbound_loop,
        args=(monitor, registry, interval, stop, gate_watcher),
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
    if args.host:
        os.environ["CHELA_DASH_HOST"] = args.host
    if args.port:
        os.environ["CHELA_DASHBOARD_PORT"] = str(args.port)
    host = os.environ.get("CHELA_DASH_HOST", "127.0.0.1")
    port = os.environ.get("CHELA_DASHBOARD_PORT", "5001")
    log.info("chela dashboard on http://%s:%s (zero auth — keep it loopback/tailnet)", host, port)
    dashboard_app.main()


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
    p_msg.add_argument("agent", help="Recipient agent (tmux window name)")
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
        "watch", help="Report back when a window you delegated to finishes/blocks/dies")
    p_watch.add_argument("wid", help="Window you dispatched work to (@N or N)")
    p_watch.add_argument("--note", help="What you asked it to do (echoed back to you)")

    p_unwatch = sub.add_parser("unwatch", help="Stop watching a window")
    p_unwatch.add_argument("wid", help="Window to stop watching (@N or N)")

    sub.add_parser("watching", help="Show inbox watches + the queued events")

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
    p_disp.add_argument("workflow", help="Path to WORKFLOW.md")
    p_disp.add_argument("--once", action="store_true", help="Run one tick and exit")
    p_disp.add_argument("--interval", type=int, default=60, help="Poll interval in seconds (default 60)")
    p_disp.add_argument(
        "--dry-run", action="store_true",
        help="Print rendered prompt and worktree path for each open task; do not spawn windows or run hooks",
    )

    # dispatch-runs (inspection)
    p_runs = sub.add_parser("dispatch-runs", help="List dispatcher runs")
    p_runs.add_argument(
        "--status", default=None,
        help="Only show runs in this status (e.g. awaiting_review, running, failed, done)",
    )
    p_runs.add_argument(
        "--awaiting", action="store_true",
        help="Shorthand for --status awaiting_review — runs waiting on human review, with PR URL + age",
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
    p_dash.add_argument("--host", default=None, help="Bind host (default 127.0.0.1)")
    p_dash.add_argument("--port", type=int, default=None, help="Bind port (default 5001)")

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
    elif args.command == "events":
        if args.events_cmd == "emit":
            cmd_events_emit(args)
        else:
            cmd_events(args)
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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
