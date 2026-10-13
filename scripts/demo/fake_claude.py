#!/usr/bin/env python3
"""The demo fleet's ``claude`` binary — ``claude agents --json`` and ``claude --resume``.

chela asks ``claude agents --json`` for each live session's native status
(busy / waiting / idle), pid, cwd and session id. In the demo fleet the "sessions" are
``claude_demo_agent.py`` processes, each of which publishes its own status file into
``$CHELA_DEMO_STATUS_DIR``; this script reports every one whose pid is still alive.
``claude --resume <id>`` (what the sidebar's Unarchive sends) re-runs the scripted
session that recorded itself under that id (``claude_demo_agent.record_session``).
Anything else exits 2, so nothing in the demo can reach a real model or account.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys


HERE = os.path.dirname(os.path.abspath(__file__))
SESSION_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


def resume(session_id: str) -> int:
    """Re-run the demo session recorded under ``session_id``, in this process — exec'd, so
    the pane's child stays a process whose command line says "claude"."""
    status_dir = os.environ.get("CHELA_DEMO_STATUS_DIR", "")
    if not status_dir or not SESSION_ID.match(session_id):
        sys.stderr.write("chela demo: nothing to resume\n")
        return 2
    sessions = os.path.join(os.path.dirname(os.path.abspath(status_dir)), "sessions")
    try:
        with open(os.path.join(sessions, f"{session_id}.json"), encoding="utf-8") as fh:
            argv = json.load(fh)["argv"]
    except (OSError, ValueError, KeyError):
        sys.stderr.write(f"chela demo: no recorded session {session_id}\n")
        return 2
    agent = os.path.join(HERE, "claude_demo_agent.py")
    os.execv(sys.executable, [sys.executable, agent, *argv])
    return 2  # not reached


def main(argv: list[str]) -> int:
    if "--resume" in argv:
        i = argv.index("--resume")
        return resume(argv[i + 1] if i + 1 < len(argv) else "")
    if argv[:2] != ["agents", "--json"]:
        sys.stderr.write("chela demo: fake claude implements only `claude agents --json`\n")
        return 2
    status_dir = os.environ.get("CHELA_DEMO_STATUS_DIR", "")
    rows = []
    for path in sorted(glob.glob(os.path.join(status_dir, "*.json"))):
        try:
            with open(path, encoding="utf-8") as fh:
                row = json.load(fh)
            os.kill(int(row["pid"]), 0)
        except (OSError, ValueError, KeyError):
            continue
        rows.append(row)
    json.dump(rows, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
