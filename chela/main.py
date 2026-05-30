"""chela CLI entry point.

`chela status` proves tmux-native discovery. `chela run` is the daemon loop
(scheduler tick + work-item dispatcher). `chela schedule ...` manages scheduled
tasks; `chela dispatch ...` runs the markdown-TODO → worktree → PR dispatcher;
`chela msg`/`broadcast` route messages between agents (mailbox fallback).
`chela dashboard` launches the optional web UI (requires the `dashboard` extra).
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
import time

from chela import discovery, dispatcher, messenger, scheduler
from chela.config import (
    TMUX_SESSION,
    SCHEDULER_POLL_INTERVAL,
    DISPATCH_TICK_INTERVAL,
    DISPATCH_WORKFLOWS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("chela")


def cmd_status(args) -> None:
    """List the agent windows chela can see in the tmux session."""
    windows = discovery.get_all_windows()
    if not windows:
        print(f"No windows found in tmux session '{TMUX_SESSION}'.")
        print("Is the session running? Override the session with CHELA_TMUX_SESSION.")
        return
    print(f"Agents in tmux session '{TMUX_SESSION}':\n")
    for name, wid in sorted(windows.items()):
        cwd = discovery.get_window_cwd(name) or "?"
        print(f"  {name:<24} {wid:<6} {cwd}")


def cmd_run(args) -> None:
    """Run the daemon loop: scheduler tick every pass, dispatcher on its own cadence."""
    log.info("chela daemon starting (session=%s, poll=%ds)", TMUX_SESSION, SCHEDULER_POLL_INTERVAL)
    if DISPATCH_WORKFLOWS:
        log.info("Dispatcher enabled for %d workflow(s): %s",
                 len(DISPATCH_WORKFLOWS), ", ".join(str(p) for p in DISPATCH_WORKFLOWS))
    last_dispatch_check = 0.0

    while True:
        try:
            executed = scheduler.tick()
            if executed:
                log.info("Scheduler executed %d task(s)", executed)

            now = time.time()
            if DISPATCH_WORKFLOWS and now - last_dispatch_check >= DISPATCH_TICK_INTERVAL:
                for wf_path in DISPATCH_WORKFLOWS:
                    try:
                        summary = dispatcher.tick(wf_path)
                        if summary["dispatched"] or summary["reconciled_done"] or summary["reconciled_failed"]:
                            log.info("Dispatch %s: %s", wf_path.name, summary)
                    except Exception:
                        log.exception("Dispatch tick failed for %s", wf_path)
                last_dispatch_check = now
        except Exception:
            log.exception("Error in daemon loop")
        time.sleep(SCHEDULER_POLL_INTERVAL)


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
    """Send a message to one agent (mailbox fallback if offline)."""
    delivered = messenger.send_message(args.from_agent, args.agent, args.message, args.priority)
    if delivered:
        print(f"Sent to {args.agent}")
    else:
        print(f"{args.agent} offline — written to mailbox")


def cmd_broadcast(args) -> None:
    """Send a message to every other live agent."""
    results = messenger.broadcast(args.from_agent, args.message, args.priority)
    if not results:
        print("No other agents online")
        return
    for agent, delivered in sorted(results.items()):
        print(f"  {agent:<24} {'sent' if delivered else 'mailbox'}")


def cmd_mailbox(args) -> None:
    """Read or clear an agent's mailbox."""
    if args.clear:
        n = messenger.clear_mailbox(args.agent)
        print(f"Cleared {n} message(s) from {args.agent}'s mailbox")
        return
    msgs = messenger.read_mailbox(args.agent)
    if not msgs:
        print(f"No messages in {args.agent}'s mailbox")
        return
    for m in msgs:
        text = m.data.get("message", "") if isinstance(m.data, dict) else ""
        print(f"  [{m.priority}] from {m.from_agent} @ {m.ts}: {text}")


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
            print("  prompt:")
            for line in p["prompt"].splitlines():
                print(f"    {line}")
        return
    interval = max(5, int(args.interval))
    if args.once:
        summary = dispatcher.tick(args.workflow)
        print(summary)
        return
    log.info("Dispatcher starting (workflow=%s, interval=%ds)", args.workflow, interval)
    while True:
        try:
            summary = dispatcher.tick(args.workflow)
            if summary["dispatched"] or summary["reconciled_done"] or summary["reconciled_failed"]:
                log.info("Dispatch tick: %s", summary)
        except Exception:
            log.exception("Dispatch tick failed")
        time.sleep(interval)


def cmd_dispatch_runs(args) -> None:
    """Show dispatcher runs."""
    runs = dispatcher.list_runs()
    if not runs:
        print("No runs")
        return
    for r in runs:
        title = (r["title"] or "")[:60]
        print(f"  {r['task_id']}  {r['status']:<16}  attempt={r['attempt']}  {r.get('window_name') or '-':<24}  {title}")


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

    # mailbox
    p_mb = sub.add_parser("mailbox", help="Read or clear an agent's mailbox")
    p_mb.add_argument("agent", help="Agent whose mailbox to inspect")
    p_mb.add_argument("--clear", action="store_true", help="Delete the mailbox instead of reading it")

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
    sub.add_parser("dispatch-runs", help="List dispatcher runs")

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
    elif args.command == "mailbox":
        cmd_mailbox(args)
    elif args.command == "dispatch":
        cmd_dispatch(args)
    elif args.command == "dispatch-runs":
        cmd_dispatch_runs(args)
    elif args.command == "dashboard":
        cmd_dashboard(args)
    elif args.command == "task-finished":
        cmd_task_finished(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
