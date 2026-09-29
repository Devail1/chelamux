#!/usr/bin/env python3
"""A throwaway, fully synthetic chela fleet for recording the README/landing demos.

    python scripts/demo/fleet.py up       # build it, print the dashboard URL
    python scripts/demo/fleet.py down     # tear it all down again

⛔ This repo is public, and a recording shows every pixel. Nothing of the operator's
real fleet may reach one, so the demo shares NOTHING with it:

* **its own tmux server** — a ``tmux`` shim first on ``PATH`` pins every call chela,
  ttyd and ``agent-terminals.sh`` make to ``tmux -L chela-demo``; the real server is
  never addressed;
* **its own ``HOME`` and ``CHELA_DIR``** — both under a fresh temp dir, so the real
  ``~/.chela`` and ``~/.claude`` (transcripts, settings, login) are never read;
* **a from-scratch environment** — nothing is inherited but ``PATH``, ``LANG`` and
  ``TERM``, so no ``CHELA_*``, ``TMUX``/``TMUX_PANE`` or token leaks in;
* **no real agents** — ``claude`` on the demo ``PATH`` is ``fake_claude.py``, which only
  reports the scripted panes' statuses; ``gh``, ``crontab`` and ``pm2`` are stubs, and
  ``pgrep`` cannot see the host's own chela services.

The panes are ``claude_demo_agent.py`` scenes, one per status the Wall draws:
working, needs you (waiting), done and idle.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
TMUX_SOCKET = "chela-demo"
SESSION = "chela"
STATE_FILE = Path(tempfile.gettempdir()) / "chela-demo-fleet.json"

# (window name, scene) — neutral names only; see the module docstring.
AGENTS = [
    ("api-server", "working"),
    ("docs-site", "waiting"),
    ("worker", "done"),
    ("infra", "idle"),
]


def free_port(start: int | None = None) -> int:
    """A free loopback port: ``start`` or the first free one above it, else any."""
    if start is None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]
    port = start
    while True:
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                port += 1


def make_root() -> Path:
    """The demo root. Always under /tmp: a session scratch dir can carry the
    operator's user name in its path, and that path is what a pane would show."""
    base = "/tmp" if os.path.isdir("/tmp") else None
    return Path(tempfile.mkdtemp(prefix="chela-demo-", dir=base))


def demo_env(root: Path, dash_port: int, term_base: int) -> dict[str, str]:
    """The ONLY environment any demo process gets. Built from scratch on purpose."""
    home = root / "demo"
    bin_dir = root / "bin"
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(home),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": "xterm-256color",
        "CHELA_DIR": str(root / "chela"),
        "CHELA_TMUX_SESSION": SESSION,
        "CHELA_DASHBOARD_PORT": str(dash_port),
        "CHELA_DASH_HOST": "127.0.0.1",
        "CHELA_TERM_BASE": str(term_base),
        "CHELA_TERM_POLL": "2",
        "CHELA_TERMINALS_ENABLED": "true",
        "CHELA_REMOTE_CONTROL": "false",
        "CHELA_DISPATCH_WORKFLOWS": str(home / "src" / "api-server" / "WORKFLOW.md"),
        "CHELA_DEMO_STATUS_DIR": str(root / "status"),
        "PYTHON": str(REPO / ".venv" / "bin" / "python"),
        # A COPY of the chela package, first on the import path: the dashboard
        # auto-discovers the WORKFLOW.md beside its own source file and reads that
        # checkout's git branch, and this checkout's path names the operator.
        "PYTHONPATH": str(root / "app"),
    }
    return env


def write_shims(root: Path) -> None:
    """``tmux`` pinned to the demo socket, the fake ``claude``, and inert stubs."""
    bin_dir = root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    real_tmux = shutil.which("tmux")
    if not real_tmux:
        sys.exit("fleet: tmux not found")
    shims = {
        "tmux": f'#!/bin/sh\nexec {real_tmux} -L {TMUX_SOCKET} "$@"\n',
        "claude": f'#!/bin/sh\nexec {sys.executable} {HERE / "fake_claude.py"} "$@"\n',
        "gh": "#!/bin/sh\necho 'chela demo: gh is stubbed' >&2\nexit 1\n",
        "crontab": "#!/bin/sh\nexit 0\n",
        # The host's own chela services are not the demo's: a pgrep for one would
        # find the operator's real bridge/daemon and report it "connected".
        "pgrep": (f'#!/bin/sh\ncase "$*" in *"chela "*) exit 1 ;; esac\n'
                  f'exec {shutil.which("pgrep") or "/usr/bin/pgrep"} "$@"\n'),
        "pm2": "#!/bin/sh\necho '[]'\n",
    }
    for name, body in shims.items():
        p = bin_dir / name
        p.write_text(body)
        p.chmod(0o755)


def _ts(minutes_ago: float) -> str:
    t = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return t.isoformat().replace("+00:00", "Z")


def write_transcript(home: Path, cwd: Path, sid: str, scene: str) -> None:
    """A minimal Claude Code transcript, so the dashboard has a title, a recap and —
    for the ``done`` pane — a reply newer than the last prompt (what ``done`` means)."""
    sys.path.insert(0, str(REPO))
    from chela.transcripts import encode_cwd  # noqa: PLC0415 — needs REPO on sys.path

    sys.path.insert(0, str(HERE))
    from claude_demo_agent import SCENES  # noqa: PLC0415

    spec = SCENES[scene]
    proj = home / ".claude" / "projects" / encode_cwd(str(cwd))
    proj.mkdir(parents=True, exist_ok=True)
    recs: list[dict] = []
    if spec["prompt"]:
        recs.append({"type": "user", "timestamp": _ts(20), "sessionId": sid,
                     "message": {"role": "user", "content": spec["prompt"]}})
        recs.append({"type": "assistant", "timestamp": _ts(19), "sessionId": sid,
                     "message": {"role": "assistant", "content": [{"type": "text", "text": "On it."}]}})
        recs.append({"type": "ai-title", "aiTitle": spec["title"], "sessionId": sid})
    if scene == "done":
        recs.append({"type": "assistant", "timestamp": _ts(2), "sessionId": sid,
                     "message": {"role": "assistant",
                                 "content": [{"type": "text", "text": " ".join(spec["summary"])}]}})
        recs.append({"type": "system", "subtype": "away_summary", "timestamp": _ts(1),
                     "content": "Fixed the flaky retry test (clock race in the jitter); PR #12 is open.",
                     "sessionId": sid})
        recs.append({"type": "pr-link", "prUrl": "https://github.com/example/worker/pull/12",
                     "prNumber": 12, "prRepository": "example/worker", "timestamp": _ts(2)})
    (proj / f"{sid}.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs))


WORKFLOW_MD = """\
---
project_key: CMX

tracker:
  kind: markdown
  path: TODO.md

workspace:
  root: ~/worktrees/api-server
  base_branch: main

concurrency:
  max: 2
---
You are an autonomous coding agent working on one TODO item in api-server.
"""

TODO_MD = """\
# TODO

- [ ] **Rate-limit the public /search endpoint** — 60 req/min per API key.
- [ ] **Add OpenAPI examples for every 4xx response.**
- [ ] **Move session storage from memory to Redis.**
"""


def seed(env: dict[str, str]) -> None:
    """Demo repo + tracker, a few dispatcher runs and schedules — run INSIDE the demo env."""
    home = Path(env["HOME"])
    repo = home / "src" / "api-server"
    (repo / "WORKFLOW.md").write_text(WORKFLOW_MD)
    (repo / "TODO.md").write_text(TODO_MD)
    code = f"""
import sqlite3
from chela import dispatcher, scheduler
wf = {str((repo / 'WORKFLOW.md').resolve())!r}
rows = [
    ("d1a2b3c4e5f6", "Paginate the /users endpoint", "running", "cmx-14", None, {_ts(6)!r}, None),
    ("a7b8c9d0e1f2", "Retry webhooks with exponential backoff", "awaiting_review", "cmx-13",
     "https://github.com/example/api-server/pull/41", {_ts(48)!r}, {_ts(12)!r}),
    ("f3e4d5c6b7a8", "Return 422 instead of 500 on bad JSON", "done", "cmx-12",
     "https://github.com/example/api-server/pull/40", {_ts(190)!r}, {_ts(95)!r}),
    ("b9c8d7e6f5a4", "Drop the legacy v1 auth header", "done", "cmx-11",
     "https://github.com/example/api-server/pull/38", {_ts(400)!r}, {_ts(300)!r}),
]
with dispatcher._db() as conn:
    for n, (tid, title, status, branch, pr, started, ended) in enumerate(rows):
        conn.execute(
            "INSERT OR REPLACE INTO runs (task_id, workflow_path, title, status, window_name, "
            "branch_name, started_at, ended_at, pr_url, pr_state, task_number) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (tid, wf, title, status, branch, branch, started, ended, pr,
             "MERGED" if status == "done" else ("OPEN" if pr else None), 14 - n))
scheduler.init()
scheduler.add_task("worker", "cron", "0 3 * * *", "Run the nightly flaky-test sweep.")
scheduler.add_task("docs-site", "interval", "6h", "Check for broken links and fix them.")
scheduler.add_task("infra", "cron", "0 9 * * 1-5", "Summarise yesterday's error budget.")
"""
    subprocess.run([env["PYTHON"], "-c", code], env=env, cwd=str(REPO), check=True)


def tmux(env: dict[str, str], *args: str) -> str:
    r = subprocess.run(["tmux", *args], env=env, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def up() -> dict:
    if STATE_FILE.exists():
        down()
    root = make_root()
    dash_port = free_port()
    term_base = free_port(6400)
    env = demo_env(root, dash_port, term_base)
    write_shims(root)
    home = Path(env["HOME"])
    (home / ".claude").mkdir(parents=True)
    Path(env["CHELA_DIR"]).mkdir(parents=True)
    Path(env["CHELA_DEMO_STATUS_DIR"]).mkdir(parents=True)
    for name, _ in AGENTS:
        (home / "src" / name).mkdir(parents=True, exist_ok=True)

    # Anchor window first (agent-terminals.sh's own convention), then one per agent.
    tmux(env, "new-session", "-d", "-s", SESSION, "-n", "shell-1", "-x", "200", "-y", "50",
         "-c", str(home))
    tmux(env, "set-option", "-g", "status", "off")
    for name, scene in AGENTS:
        cwd = home / "src" / name
        sid = str(uuid.uuid4())
        write_transcript(home, cwd, sid, scene)
        agent = (
            f"{sys.executable} {HERE / 'claude_demo_agent.py'} --scene {scene} "
            f"--status-dir {env['CHELA_DEMO_STATUS_DIR']} --cwd {cwd} "
            f"--cwd-label \"~/src/{name}\" --session-id {sid}"
        )
        # `; exec sleep` keeps sh as the pane process, so the agent is its CHILD —
        # which is how chela finds a session in a pane (pgrep -P <pane_pid> -f claude).
        tmux(env, "new-window", "-d", "-t", SESSION, "-n", name, "-c", str(cwd),
             f"sh -c '{agent}; exec sleep 1000000'")
    tmux(env, "kill-window", "-t", f"{SESSION}:shell-1")

    shutil.copytree(REPO / "chela", root / "app" / "chela",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    seed(env)
    logs = root / "logs"
    logs.mkdir()
    procs = {}
    procs["terminals"] = subprocess.Popen(
        ["bash", str(REPO / "scripts" / "agent-terminals.sh")], env=env, cwd=str(REPO),
        stdout=open(logs / "terminals.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    procs["dashboard"] = subprocess.Popen(
        # cwd is the demo repo too, so nothing resolves relative to this checkout.
        [env["PYTHON"], "-m", "chela.main", "dashboard", "--port", str(dash_port)], env=env,
        cwd=str(home / "src" / "api-server"),
        stdout=open(logs / "dashboard.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    state = {"root": str(root), "url": f"http://127.0.0.1:{dash_port}/",
             "pids": {k: p.pid for k, p in procs.items()}, "env": env}
    STATE_FILE.write_text(json.dumps(state, indent=2))
    _wait_http(dash_port)
    return state


def _wait_http(port: int, timeout: float = 30) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.3)
    sys.exit(f"fleet: dashboard never came up on {port} (see logs under the demo root)")


def down() -> None:
    if not STATE_FILE.exists():
        return
    state = json.loads(STATE_FILE.read_text())
    for pid in state.get("pids", {}).values():
        try:
            os.killpg(pid, signal.SIGTERM)
        except OSError:
            pass
    env = state.get("env") or {}
    if env:
        subprocess.run(["tmux", "kill-server"], env=env, capture_output=True)
    time.sleep(0.5)
    root = state.get("root", "")
    if root and Path(root).name.startswith("chela-demo-"):
        shutil.rmtree(root, ignore_errors=True)
    STATE_FILE.unlink(missing_ok=True)


def main(argv: list[str]) -> int:
    cmd = argv[0] if argv else "up"
    if cmd == "up":
        state = up()
        print(state["url"])
    elif cmd == "down":
        down()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
