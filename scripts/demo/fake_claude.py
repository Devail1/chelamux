#!/usr/bin/env python3
"""The demo fleet's ``claude`` binary — only ``claude agents --json`` is implemented.

chela asks ``claude agents --json`` for each live session's native status
(busy / waiting / idle), pid, cwd and session id. In the demo fleet the "sessions" are
``claude_demo_agent.py`` processes, each of which publishes its own status file into
``$CHELA_DEMO_STATUS_DIR``; this script reports every one whose pid is still alive.
Anything else exits 2, so nothing in the demo can reach a real model or account.
"""
from __future__ import annotations

import glob
import json
import os
import sys


def main(argv: list[str]) -> int:
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
