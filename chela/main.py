"""chela CLI entry point.

`chela status` proves tmux-native discovery. `chela run` is the daemon loop
(currently the scheduler tick; the dispatcher and context capture wire in here
as they are ported). `chela schedule ...` manages the scheduled tasks the
daemon fires.
"""
from __future__ import annotations
import argparse
import logging
import time

from chela import discovery, scheduler
from chela.config import TMUX_SESSION, SCHEDULER_POLL_INTERVAL

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
    """Run the daemon loop: poll the scheduler and fire due tasks."""
    log.info("chela daemon starting (session=%s, poll=%ds)", TMUX_SESSION, SCHEDULER_POLL_INTERVAL)
    while True:
        try:
            executed = scheduler.tick()
            if executed:
                log.info("Scheduler executed %d task(s)", executed)
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
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
