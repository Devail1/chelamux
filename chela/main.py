"""chela CLI entry point.

`chela status` proves tmux-native discovery. `chela run` is the daemon loop
(scheduler tick + work-item dispatcher). `chela schedule ...` manages scheduled
tasks; `chela dispatch ...` runs the markdown-TODO → worktree → PR dispatcher;
`chela msg`/`broadcast` route messages between live agents over tmux.
`chela dashboard` launches the optional web UI (requires the `dashboard` extra).
"""
from __future__ import annotations
import argparse
import logging
import os
import sys
import time
from pathlib import Path

from chela import agent_manager, discovery, dispatcher, messenger, notify, okf, scheduler
from chela.config import (
    TMUX_SESSION,
    SCHEDULER_POLL_INTERVAL,
    DISPATCH_TICK_INTERVAL,
    DISPATCH_WORKFLOWS,
    NOTIFY_INTERVAL,
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
    if notify.enabled():
        log.info("Needs-input notifications enabled (every %ds)", NOTIFY_INTERVAL)
    last_dispatch_check = 0.0
    last_notify_check = 0.0
    waiting_seen: set[str] = set()

    while True:
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
            if DISPATCH_WORKFLOWS and now - last_dispatch_check >= DISPATCH_TICK_INTERVAL:
                for wf_path in DISPATCH_WORKFLOWS:
                    try:
                        summary = dispatcher.tick(wf_path)
                        if summary["dispatched"] or summary["reconciled_done"] or summary["reconciled_failed"]:
                            log.info("Dispatch %s: %s", wf_path.name, summary)
                    except Exception:
                        log.exception("Dispatch tick failed for %s", wf_path)
                last_dispatch_check = now

            if notify.enabled() and now - last_notify_check >= NOTIFY_INTERVAL:
                try:
                    waiting_seen = notify.check_waiting(waiting_seen)
                except Exception:
                    log.exception("Needs-input check failed")
                last_notify_check = now
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
    elif args.command == "dashboard":
        cmd_dashboard(args)
    elif args.command == "task-finished":
        cmd_task_finished(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
