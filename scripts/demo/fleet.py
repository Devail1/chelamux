#!/usr/bin/env python3
"""A throwaway, fully synthetic chela fleet for recording the README/landing demos.

    python scripts/demo/fleet.py up       # build it, print the dashboard URL
    python scripts/demo/fleet.py down     # tear it all down again

⛔ This repo is public, and a recording shows every pixel. Nothing of the operator's
real fleet may reach one, so the demo shares NOTHING with it:

* **its own tmux server** — a ``tmux`` shim first on ``PATH`` pins every call chela,
  ttyd and ``agent-terminals.sh`` make to ``tmux -L chela-demo``; the real server is
  never addressed;
* **its own ``HOME`` and ``CHELA_DIR``** — ``HOME`` is the demo root (``/tmp/demo``,
  or ``$CHELA_DEMO_ROOT``) and ``CHELA_DIR`` is its ``~/.chela``, so the real
  ``~/.chela`` and ``~/.claude`` (transcripts, settings, login) are never read. The
  root is FIXED, not a random temp name: the Work view prints workflow paths verbatim,
  and ``/tmp/demo/api-server/WORKFLOW.md`` reads like a project where
  ``/tmp/chela-demo-ldq9p_6k/…`` reads like debris;
* **a from-scratch environment** — nothing is inherited but ``PATH``, ``LANG`` and
  ``TERM``, so no ``CHELA_*``, ``TMUX``/``TMUX_PANE`` or token leaks in;
* **no real agents** — ``claude`` on the demo ``PATH`` is ``fake_claude.py``, which only
  reports the scripted panes' statuses; ``gh``, ``crontab`` and ``pm2`` are stubs, and
  ``pgrep`` cannot see the host's own chela services.

The panes are ``claude_demo_agent.py`` scenes, one per status the Wall draws:
working, needs you (waiting), done and idle.

It also looks like a HEALTHY install, since Settings is on camera: chela runs from a
git checkout (a copy of the package committed into a fresh repo with a local bare
upstream, so Update reads "up to date" without touching this checkout's git), the
demo's own daemon (``chela run``) is up on the demo ``CHELA_DIR``, and the launcher's
Favorites / Recent are seeded with the demo projects.
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
DEFAULT_ROOT = Path("/tmp/demo")
# Written into every root fleet.py builds; `down` (and a re-`up`) only ever deletes a
# directory that carries it, so a mistyped CHELA_DEMO_ROOT can never rmtree real data.
MARKER = ".chela-demo-fleet"

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


def make_root(root: Path | None = None) -> Path:
    """The demo root: ``$CHELA_DEMO_ROOT``, else ``/tmp/demo`` — never ``$TMPDIR``, since
    a session scratch dir can carry the operator's user name, and a pane shows its path.

    A leftover root from an earlier run (it carries :data:`MARKER`) is wiped and rebuilt;
    anything else already at that path is refused, never deleted."""
    root = Path(root or os.environ.get("CHELA_DEMO_ROOT") or DEFAULT_ROOT)
    if not root.is_absolute():
        sys.exit(f"fleet: the demo root must be absolute, got {root}")
    real_home = Path(os.path.expanduser("~")).resolve()
    if root.resolve() == real_home or root.resolve().is_relative_to(real_home):
        sys.exit(f"fleet: refusing {root} — the demo root may not live in your real HOME")
    if root.exists():
        if not (root / MARKER).is_file():
            sys.exit(f"fleet: {root} exists and is not a demo root fleet.py made — refusing")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / MARKER).write_text("built by scripts/demo/fleet.py; `fleet.py down` removes it\n")
    return root


def demo_env(root: Path, dash_port: int, term_base: int) -> dict[str, str]:
    """The ONLY environment any demo process gets. Built from scratch on purpose."""
    home = root
    bin_dir = root / ".local" / "bin"
    env = {
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '/usr/bin:/bin')}",
        "HOME": str(home),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "TERM": "xterm-256color",
        "CHELA_DIR": str(home / ".chela"),
        "CHELA_TMUX_SESSION": SESSION,
        "CHELA_DASHBOARD_PORT": str(dash_port),
        "CHELA_DASH_HOST": "127.0.0.1",
        "CHELA_TERM_BASE": str(term_base),
        "CHELA_TERM_POLL": "2",
        "CHELA_TERMINALS_ENABLED": "true",
        "CHELA_REMOTE_CONTROL": "false",
        "CHELA_DISPATCH_WORKFLOWS": str(home / "api-server" / "WORKFLOW.md"),
        "CHELA_DEMO_STATUS_DIR": str(home / ".cache" / "chela-demo" / "status"),
        "PYTHON": str(REPO / ".venv" / "bin" / "python"),
        # A COPY of the chela package, first on the import path: the dashboard
        # auto-discovers the WORKFLOW.md beside its own source file and reads that
        # checkout's git branch, and this checkout's path names the operator.
        # make_app() turns the copy into a git checkout of its own.
        "PYTHONPATH": str(app_dir(root)),
        # git reads only the demo HOME's (absent) config — never the operator's.
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "chela demo", "GIT_AUTHOR_EMAIL": "demo@example.com",
        "GIT_COMMITTER_NAME": "chela demo", "GIT_COMMITTER_EMAIL": "demo@example.com",
    }
    return env


def app_dir(root: Path) -> Path:
    return root / ".local" / "share" / "chela"


def write_shims(root: Path) -> None:
    """``tmux`` pinned to the demo socket, the fake ``claude``, and inert stubs."""
    bin_dir = root / ".local" / "bin"
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
    repo = home / "api-server"
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
    # cwd is the demo HOME: `python -c` puts the cwd first on sys.path, so a cwd here would
    # import THIS checkout's chela instead of the demo copy on PYTHONPATH.
    subprocess.run([env["PYTHON"], "-c", code], env=env, cwd=str(home), check=True)


def make_app(env: dict[str, str]) -> None:
    """The chela the demo runs: a copy of the package, committed into a fresh repo whose
    ``main`` tracks a local bare "origin" — so Settings' Update section reads "main · up
    to date" instead of "not a git checkout". Built from scratch rather than as a
    worktree of this checkout, so no ref, remote or path of the real repo is involved."""
    app = Path(env["PYTHONPATH"])
    shutil.copytree(REPO / "chela", app / "chela",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    origin = app.parent / "chela-origin.git"

    def git(*args: str, cwd: Path = app) -> None:
        subprocess.run(["git", *args], env=env, cwd=str(cwd), check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("add", "chela")
    git("commit", "-q", "-m", "chela")
    git("clone", "-q", "--bare", str(app), str(origin), cwd=app.parent)
    git("remote", "add", "origin", str(origin))
    git("fetch", "-q", "origin")
    git("branch", "-q", "--set-upstream-to=origin/main", "main")


def seed_launcher(env: dict[str, str]) -> None:
    """Favorites + Recent for the "+" New session menu, so it offers the demo projects
    rather than "No projects yet". Same shape chela.launcher writes."""
    home = Path(env["HOME"])
    now = time.time()
    names = [name for name, _ in AGENTS]
    store = {
        "favorites": [{"path": str(home / n), "label": n} for n in names[:2]],
        "recent": [{"path": str(home / n), "ts": now - 600 * i} for i, n in enumerate(names[2:])],
    }
    (Path(env["CHELA_DIR"]) / "launcher.json").write_text(json.dumps(store, indent=2))


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
        (home / name).mkdir(parents=True, exist_ok=True)

    # Anchor window first (agent-terminals.sh's own convention), then one per agent.
    tmux(env, "new-session", "-d", "-s", SESSION, "-n", "shell-1", "-x", "200", "-y", "50",
         "-c", str(home))
    tmux(env, "set-option", "-g", "status", "off")
    for name, scene in AGENTS:
        cwd = home / name
        sid = str(uuid.uuid4())
        write_transcript(home, cwd, sid, scene)
        agent = (
            f"{sys.executable} {HERE / 'claude_demo_agent.py'} --scene {scene} "
            f"--status-dir {env['CHELA_DEMO_STATUS_DIR']} --cwd {cwd} "
            f"--cwd-label \"~/{name}\" --session-id {sid}"
        )
        # `; exec sleep` keeps sh as the pane process, so the agent is its CHILD —
        # which is how chela finds a session in a pane (pgrep -P <pane_pid> -f claude).
        tmux(env, "new-window", "-d", "-t", SESSION, "-n", name, "-c", str(cwd),
             f"sh -c '{agent}; exec sleep 1000000'")
    tmux(env, "kill-window", "-t", f"{SESSION}:shell-1")

    make_app(env)
    seed(env)
    seed_launcher(env)
    logs = home / ".cache" / "chela-demo" / "logs"
    logs.mkdir(parents=True)
    procs = {}
    # The demo's OWN daemon, on the demo CHELA_DIR — Settings reads "Daemon: Running"
    # from the daemon.json it publishes. Started before the dashboard so that file
    # exists by the time anything is recorded.
    # It dispatches from an INERT workflow of its own (empty tracker, no runs), never
    # api-server's: a live dispatcher would reconcile the seeded runs against a `gh`
    # stub and claim the TODO cards into failed runs, and the Work view is on camera.
    idle = home / ".cache" / "chela-demo" / "idle"
    idle.mkdir(parents=True)
    (idle / "WORKFLOW.md").write_text(WORKFLOW_MD.replace("api-server", "idle"))
    (idle / "TODO.md").write_text("# TODO\n")
    daemon_env = {**env, "CHELA_DISPATCH_WORKFLOWS": str(idle / "WORKFLOW.md")}
    procs["daemon"] = subprocess.Popen(
        [env["PYTHON"], "-m", "chela.main", "run"], env=daemon_env, cwd=str(idle),
        stdout=open(logs / "daemon.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    procs["terminals"] = subprocess.Popen(
        ["bash", str(REPO / "scripts" / "agent-terminals.sh")], env=env, cwd=str(REPO),
        stdout=open(logs / "terminals.log", "w"), stderr=subprocess.STDOUT, start_new_session=True)
    procs["dashboard"] = subprocess.Popen(
        # cwd is the demo repo too, so nothing resolves relative to this checkout.
        [env["PYTHON"], "-m", "chela.main", "dashboard", "--port", str(dash_port)], env=env,
        cwd=str(home / "api-server"),
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
    if root and (Path(root) / MARKER).is_file():
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
