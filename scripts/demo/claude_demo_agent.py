#!/usr/bin/env python3
"""A scripted stand-in for a Claude Code session, for the README/landing demos.

It paints a plausible Claude Code screen into its tmux pane — tool calls, a spinner,
a permission prompt — and publishes a matching native status for the fake
``claude agents --json`` (``fake_claude.py``) to report. Nothing here calls a model,
touches a network or reads anything outside the demo root, so a recording made from
it can show nothing of the machine it ran on.

The file name is load-bearing: chela maps a window to its session with
``pgrep -P <pane_pid> -f claude``, so this script's command line must contain
"claude" for the dashboard to see a session in the pane at all.

    claude_demo_agent.py --scene working|waiting|done|idle --status-dir DIR --cwd DIR
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import signal
import shutil
import sys
import time
import uuid

RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"
ORANGE = "\033[38;5;173m"
GREEN = "\033[38;5;71m"
RED = "\033[38;5;167m"
GREY = "\033[38;5;245m"
BLUE = "\033[38;5;110m"

SPINNER = "·✢✳✶✻✽✻✶✳✢"

# Per-scene script: (title, prompt, [(tool, detail, result)], ending)
SCENES = {
    "working": {
        "status": "busy",
        "title": "Paginate the /users endpoint",
        "prompt": "Add cursor pagination to GET /users and cover it with tests.",
        "steps": [
            ("Read", "src/routes/users.ts", "Read 142 lines"),
            ("Grep", 'pattern: "findMany\\(" in src/', "Found 6 matches"),
            ("Edit", "src/routes/users.ts", "Updated with 38 additions and 9 removals"),
            ("Write", "tests/users.pagination.test.ts", "Wrote 64 lines"),
            ("Bash", "npm test -- users", "Tests: 23 passed, 23 total"),
            ("Edit", "src/db/queries.ts", "Updated with 12 additions and 4 removals"),
            ("Bash", "npm run lint", "0 problems"),
        ],
        "verb": "Paginating",
    },
    "waiting": {
        "status": "waiting",
        "title": "Upgrade the docs site to Astro 5",
        "prompt": "Upgrade the docs site to Astro 5 and fix whatever breaks.",
        "steps": [
            ("Read", "package.json", "Read 48 lines"),
            ("Bash", "npx @astrojs/upgrade --dry-run", "3 packages would be updated"),
            ("Edit", "astro.config.mjs", "Updated with 6 additions and 11 removals"),
        ],
        "ask": "npm install astro@5 @astrojs/mdx@4",
    },
    "done": {
        "status": "idle",
        "title": "Fix the flaky retry test",
        "prompt": "tests/test_retry.py flakes about 1 run in 20 — find out why and fix it.",
        "steps": [
            ("Bash", "pytest tests/test_retry.py -x --count 50", "2 failed, 48 passed"),
            ("Read", "src/worker/retry.py", "Read 97 lines"),
            ("Edit", "src/worker/retry.py", "Updated with 4 additions and 2 removals"),
            ("Bash", "pytest tests/test_retry.py -x --count 200", "200 passed"),
            ("Bash", "gh pr create --fill", "https://github.com/example/worker/pull/12"),
        ],
        "summary": [
            "The backoff jitter read the wall clock, so two",
            "retries could land in the same millisecond. It",
            "now uses the injected clock: 200/200 green, and",
            "PR #12 is open.",
        ],
    },
    "idle": {
        "status": "idle",
        "title": None,
        "prompt": None,
        "steps": [],
    },
}


def publish_status(status_dir: str, cwd: str, status: str, session_id: str) -> None:
    """Write this pid's native status where fake_claude.py will find it."""
    os.makedirs(status_dir, exist_ok=True)
    path = os.path.join(status_dir, f"{os.getpid()}.json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump({"pid": os.getpid(), "cwd": cwd, "status": status, "sessionId": session_id}, fh)
    os.replace(tmp, path)


ANSI = re.compile(r"\033\[[0-9;?]*[A-Za-z]")


def vlen(s: str) -> int:
    return len(ANSI.sub("", s))


def box(color: str, rows: list[str], cols: int, width: int = 54) -> list[str]:
    """A rounded box that never exceeds the pane — a wrapped box edge is what makes a
    scripted pane look fake the moment a phone narrows the window."""
    w = max(20, min(width, cols - 1))
    inner = w - 4
    lines = [f"{color}╭{'─' * (w - 2)}╮{RESET}"]
    for r in rows:
        plain = ANSI.sub("", r)
        if len(plain) > inner:
            r = plain[: inner - 1] + "…"
        lines.append(f"{color}│{RESET} {r}{' ' * (inner - vlen(r))} {color}│{RESET}")
    lines.append(f"{color}╰{'─' * (w - 2)}╯{RESET}")
    return lines


def tool_lines(name: str, detail: str, result: str) -> list[str]:
    return [f"{GREEN}⏺{RESET} {BOLD}{name}{RESET}({detail})", f"  {GREY}⎿  {result}{RESET}", ""]


def screen(spec: dict, scene: str, cols: int, cwd_label: str, steps_done: int) -> list[str]:
    """Every line the pane shows right now, laid out for ``cols`` columns."""
    lines = box(ORANGE, [f"{ORANGE}✻{RESET} {BOLD}Welcome to Claude Code{RESET}", f"{GREY}cwd: {cwd_label}{RESET}"],
                cols, 48) + [""]
    if spec["prompt"]:
        lines += [f"{GREY}>{RESET} {spec['prompt']}", ""]
    steps = spec["steps"]
    shown = [steps[i % len(steps)] for i in range(steps_done)] if steps else []
    for name, detail, result in shown:
        lines += tool_lines(name, detail, result)
    prompt = box(GREY, ["❯"], cols) + [f"  {GREY}? for shortcuts{RESET}"]
    if scene == "waiting":
        lines += box(BLUE, [f"{BOLD}Bash command{RESET}", f"  {spec['ask']}", "",
                            "Do you want to proceed?", f"{BLUE}❯ 1. Yes{RESET}",
                            "  2. Yes, and don't ask again for npm install",
                            "  3. No, and tell Claude what to do differently"], cols)
    elif scene == "done":
        lines += [f"{GREEN}⏺{RESET} {' '.join(spec['summary'])}", ""] + prompt
    elif scene == "idle":
        lines += prompt
    return lines


def paint(lines: list[str], status: str | None = None) -> None:
    """Redraw the whole pane, bottom-anchored like a real session scrolls."""
    cols, rows = shutil.get_terminal_size((80, 24))
    budget = rows - (1 if status is not None else 0)
    keep: list[str] = []
    used = 0
    for line in reversed(lines):
        h = max(1, -(-vlen(line) // cols))
        if used + h > budget:
            break
        keep.insert(0, line)
        used += h
    buf = "\033[H\033[2J" + "\r\n".join(keep)
    if status is not None:
        buf += "\r\n" + status
    sys.stdout.write(buf)
    sys.stdout.flush()


def spinner_line(spec: dict, cols: int, n: int, i: int) -> str:
    # Truncated to the pane's width: a wrapped status line would push the screen.
    verb = f"{spec['verb']}…"
    meta = f"({12 + i * 3}s · ↓ {1.2 + i * 0.4:.1f}k tokens · esc to interrupt)"
    meta = meta[:max(0, cols - len(verb) - 4)]
    return f"{ORANGE}{SPINNER[n % len(SPINNER)]} {verb}{RESET} {GREY}{meta}{RESET}"


def run(scene: str, status_dir: str, cwd: str, cwd_label: str, session_id: str) -> None:
    spec = SCENES[scene]
    publish_status(status_dir, cwd, spec["status"], session_id)
    resized = [True]
    signal.signal(signal.SIGWINCH, lambda *_: resized.__setitem__(0, True))
    sys.stdout.write("\033[?25l")   # a real session hides the terminal cursor too
    if scene == "working":
        # Loop the steps forever with a live spinner between them, so the Wall has
        # something moving in it for the whole recording.
        for i in itertools.count(3):
            for n in range(8):
                cols = shutil.get_terminal_size((80, 24)).columns
                paint(screen(spec, scene, cols, cwd_label, i), spinner_line(spec, cols, n, i))
                time.sleep(0.25)
    steps_done = len(spec["steps"])
    while True:
        if resized[0]:
            resized[0] = False
            cols = shutil.get_terminal_size((80, 24)).columns
            paint(screen(spec, scene, cols, cwd_label, steps_done))
        time.sleep(0.2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scene", choices=sorted(SCENES), required=True)
    ap.add_argument("--status-dir", required=True)
    ap.add_argument("--cwd", required=True)
    ap.add_argument("--cwd-label", default=None, help="what the banner shows for cwd")
    ap.add_argument("--session-id", default=None)
    a = ap.parse_args()
    try:
        run(a.scene, a.status_dir, a.cwd, a.cwd_label or a.cwd, a.session_id or str(uuid.uuid4()))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
