"""chela dashboard — Flask app + API routes.

ZERO built-in auth, by design. The dashboard binds 127.0.0.1 by default and the
embedded ttyd terminal wall is a *writable shell* — exposing it on an untrusted
network is remote code execution. For remote access, put it behind a tailnet
(`tailscale serve`), an SSH tunnel, or a reverse proxy that adds your own auth.
The tailnet is the trust boundary; there is intentionally no password here.

This is an OPTIONAL component: Flask is an extra (`chelamux[dashboard]`). The
core CLI never imports this module at top level.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from flask import abort, Flask, jsonify, render_template, request, Response

from chela import config
from chela.config import DISPATCH_WORKFLOWS, CHELA_DIR, TMUX_SESSION
from chela import agent_manager, context, discovery, dispatcher, messenger, scheduler, transcripts
from chela.backlog import _BULLET_RE, parse_backlog
from chela.sources import get_source
from chela.sources.markdown import OPEN_RE
from chela.workflow import load_workflow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("dashboard")


app = Flask(
    __name__,
    template_folder=str(Path(__file__).parent / "templates"),
    static_folder=str(Path(__file__).parent / "static"),
)

# {agent: ttyd_port} map written by scripts/agent-terminals.sh on each poll.
TERMINALS_MAP = CHELA_DIR / "agent_terminals.json"


def require_auth(f):
    """No-op decorator — there is no built-in auth (see module docstring).

    Kept as a decorator so every route reads the same and a future deployment
    could reintroduce auth in one place. The security boundary is the network
    (loopback bind + tailnet), not this function.
    """
    return f


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _liveness(claude_running: bool, session_status: str | None) -> tuple[str, str]:
    """Derive (liveness, health_color) from native session state — no heartbeat.

    Liveness comes straight from what `claude agents --json` reports (via
    agent_manager.session_status_map) plus whether a claude process is in the
    pane. This replaces the old firm heartbeat: there is no firm.db to read.

      - "waiting"  → the session is blocked on input (needs attention)
      - "alive"    → claude is running / busy / idle
      - "offline"  → no claude in the pane (a bare shell or dead session)

    health_color is the agent-card dot: green (alive) / yellow (waiting) /
    red (offline).
    """
    if session_status == "waiting":
        return "waiting", "yellow"
    if claude_running or session_status in ("busy", "idle", "waiting"):
        return "alive", "green"
    return "offline", "red"


def _require_terminals() -> None:
    """abort(404) when the embedded terminals feature is disabled.

    Called at the top of every terminal-only endpoint (/api/term/*, spawn,
    kill) so they vanish — not just hide — when CHELA_TERMINALS_ENABLED is
    false.
    """
    if not config.TERMINALS_ENABLED:
        abort(404)


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@app.route("/")
@require_auth
def index():
    return render_template("index.html", terminals_enabled=config.TERMINALS_ENABLED)


# ---------------------------------------------------------------------------
# API: Agents
# ---------------------------------------------------------------------------

@app.route("/api/agents")
@require_auth
def api_agents():
    windows = discovery.get_all_windows()
    tasks = scheduler.list_tasks()

    # Build set of agents with enabled schedules + schedule summary
    scheduled_agents = {t.agent_name for t in tasks if t.enabled}
    # Per-agent: most recent last_run and soonest next_run across enabled tasks
    agent_schedule_summary = {}
    for t in tasks:
        if not t.enabled:
            continue
        prev = agent_schedule_summary.get(t.agent_name, {})
        # Latest last_run
        if t.last_run and (not prev.get("last_run") or t.last_run > prev["last_run"]):
            prev["last_run"] = t.last_run
        # Soonest next_run
        if t.next_run and (not prev.get("next_run") or t.next_run < prev["next_run"]):
            prev["next_run"] = t.next_run
        agent_schedule_summary[t.agent_name] = prev

    # Native busy/idle/waiting from `claude agents --json` (read once, keyed by pid).
    status_map = agent_manager.session_status_map()

    agents = []
    for name, window_id in windows.items():
        transcript = transcripts.agent_transcript_summary(name)

        # Map window -> child claude pid -> session status + cwd. No claude pid
        # means a plain shell (or a dead session): not running, never "thinking".
        cpid = agent_manager.claude_pid(window_id)
        claude_running = cpid is not None
        sess_status = status_map["by_pid"].get(cpid) if cpid is not None else None
        sess_cwd = status_map["cwd_by_pid"].get(cpid) if cpid is not None else None

        liveness, health = _liveness(claude_running, sess_status)

        agents.append({
            "name": name,
            "online": True,
            "window_id": window_id,
            "claude_running": claude_running,
            "thinking": sess_status == "busy",
            "session_status": sess_status,
            "liveness": liveness,
            "health": health,
            "status": sess_status,
            "cwd": sess_cwd,
            "has_schedules": name in scheduled_agents,
            "schedule_last_run": agent_schedule_summary.get(name, {}).get("last_run"),
            "schedule_next_run": agent_schedule_summary.get(name, {}).get("next_run"),
            "recap": transcript["recap"],
            "recap_ts": transcript["recap_ts"],
            "pr": transcript["pr"],
        })

    return jsonify(agents)


@app.route("/api/agents/msg", methods=["POST"])
@require_auth
def api_agents_msg():
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    message = data.get("message", "")
    if not agent or not message:
        return jsonify({"error": "agent and message required"}), 400
    if message.startswith("/"):
        wid = discovery.get_window_id(agent)
        if not wid:
            return jsonify({"error": f"agent {agent} not found"}), 404
        ok = messenger.send_tmux(wid, message)
    else:
        ok = messenger.send_message("dashboard", agent, message)
    return jsonify({"sent": ok, "agent": agent})


# Mobile control bar: whitelisted key / scroll injection via tmux send-keys.
# Keys are delivered at the tmux layer (robust, ttyd/xterm-independent).
_TERM_KEYS = {
    "Up", "Down", "Left", "Right", "Escape", "Tab", "Enter", "BSpace",
    "PageUp", "PageDown", "Home", "End",
    "C-c", "C-d", "C-z", "C-r", "C-l", "C-b", "C-a", "C-e", "C-u", "C-k", "C-w",
}


def _term_target(agent: str) -> str | None:
    """Resolve a terminal handle to a tmux target `<session>:<wid>`.

    The dashboard keys live terminals by stable window id (e.g. `@25`), so most
    calls arrive as a wid — used directly (rename-proof). A plain display name
    is still accepted (resolved via discovery) for compatibility / external
    callers. Returns None if a name can't be resolved to a live window."""
    if agent.startswith("@"):
        return f"{TMUX_SESSION}:{agent}"
    wid = discovery.get_window_id(agent)
    return f"{TMUX_SESSION}:{wid}" if wid else None


def _term_keyargv(target: str, key: str) -> list[str] | None:
    """Resolve a whitelisted key token to a full tmux argv, or None if not allowed."""
    if key == "scroll":               # enter copy-mode (prefix-independent)
        return ["tmux", "copy-mode", "-t", target]
    if key == "scroll-exit":          # leave copy-mode
        return ["tmux", "send-keys", "-t", target, "-X", "cancel"]
    if key in _TERM_KEYS:
        return ["tmux", "send-keys", "-t", target, key]
    return None


@app.route("/api/term/key", methods=["POST"])
@require_auth
def api_term_key():
    _require_terminals()
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    key = data.get("key", "")
    target = _term_target(agent)
    if not target:
        return jsonify({"error": f"agent {agent} not found"}), 404
    argv = _term_keyargv(target, key)
    if argv is None:
        return jsonify({"error": "key not allowed"}), 400
    try:
        subprocess.run(argv, check=True, capture_output=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"sent": key, "agent": agent})


def _terminals_port_map() -> dict:
    """The {agent: ttyd_port} map the terminal wall routes against. File-read
    only; written by scripts/agent-terminals.sh on each ~12s poll. Missing/
    garbled file → empty map (no terminals ready yet)."""
    try:
        return json.loads(TERMINALS_MAP.read_text())
    except Exception:
        return {}


@app.route("/api/term/ready")
@require_auth
def api_term_ready():
    """Cheap readiness probe for a freshly-spawned terminal. The /term/<agent>/
    iframe 404s until agent-terminals.sh assigns a port, so the frontend polls
    this before swapping a placeholder for the real iframe. `ready` = the agent
    is present in the port map with a truthy port. No network call to ttyd —
    just a file read of the same map the wall proxy uses."""
    _require_terminals()
    agent = request.args.get("agent", "")
    port = _terminals_port_map().get(agent)
    return jsonify({"ready": bool(port), "port": port if port else None})


_TERM_PASTE_MAX = 64 * 1024  # reject pastes larger than 64 KB


@app.route("/api/term/paste", methods=["POST"])
@require_auth
def api_term_paste():
    """Inject clipboard text (read browser-side) into an agent's pane.

    Clipboard data lives on the *client* device, so the browser reads it and
    ships the text here; we deliver it at the tmux layer via a dedicated
    buffer + bracketed paste (`-p`) so Claude Code / shells treat it as a
    paste rather than executing it line-by-line. `-d` discards the buffer.
    """
    _require_terminals()
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    text = data.get("text", "")
    if not isinstance(text, str) or not text:
        return jsonify({"error": "empty paste"}), 400
    if len(text.encode("utf-8")) > _TERM_PASTE_MAX:
        return jsonify({"error": "paste too large (max 64 KB)"}), 413
    target = _term_target(agent)
    if not target:
        return jsonify({"error": f"agent {agent} not found"}), 404
    try:
        subprocess.run(
            ["tmux", "load-buffer", "-b", "chela_paste", "-"],
            input=text.encode("utf-8"), check=True, capture_output=True, timeout=5,
        )
        subprocess.run(
            ["tmux", "paste-buffer", "-t", target, "-b", "chela_paste", "-p", "-d"],
            check=True, capture_output=True, timeout=5,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return jsonify({"error": str(e)}), 500
    return jsonify({"pasted": len(text), "agent": agent})


# Clipboard image paste. xterm.js drops non-text clipboard items, so a JS shim
# injected into ttyd's HTML intercepts paste events with an image/* blob and
# POSTs the bytes here. We persist by sha256 — same
# image pasted twice reuses the file — and return the path so the shim can type
# it into the pane as a regular text paste, the same shape Claude Code expects
# for pasted images on a native terminal.
_PASTE_IMAGE_DIR = Path("/tmp/chela-paste-images")
_PASTE_IMAGE_MAX_BYTES = 10 * 1024 * 1024  # 10 MB
_PASTE_IMAGE_TTL_SECONDS = 24 * 3600
_PASTE_IMAGE_MIME_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
}


def _prune_paste_images() -> None:
    """Drop paste-image files older than 24h. Best-effort, never raises."""
    if not _PASTE_IMAGE_DIR.exists():
        return
    cutoff = time.time() - _PASTE_IMAGE_TTL_SECONDS
    for p in _PASTE_IMAGE_DIR.iterdir():
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink()
        except OSError:
            pass


try:
    _PASTE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    _prune_paste_images()
except OSError as _e:
    log.warning("paste-image dir setup failed: %s", _e)


@app.route("/api/term/paste-image", methods=["POST"])
@require_auth
def api_term_paste_image():
    """Accept an image blob pasted from the browser clipboard.

    Multipart form: `agent` (str) + `image` (file). MIME is validated against a
    PNG/JPEG/WebP/GIF allowlist and the byte count is capped at 10 MB. The file
    is written under /tmp by its sha256, and the absolute path is returned so
    the JS shim can type it into the agent's pane via /api/term/paste.
    """
    _require_terminals()
    agent = (request.form.get("agent") or "").strip()
    f = request.files.get("image")
    if not agent or f is None:
        return jsonify({"error": "agent and image required"}), 400
    mime = (f.mimetype or "").lower()
    ext = _PASTE_IMAGE_MIME_EXT.get(mime)
    if not ext:
        return jsonify({"error": f"mime not allowed: {mime}"}), 415
    # Read up to the cap + 1 byte: anything over the cap is rejected without
    # buffering the rest. Werkzeug streams the upload, so this stays bounded.
    data = f.stream.read(_PASTE_IMAGE_MAX_BYTES + 1)
    if not data:
        return jsonify({"error": "empty image"}), 400
    if len(data) > _PASTE_IMAGE_MAX_BYTES:
        return jsonify({"error": "image too large (max 10 MB)"}), 413
    try:
        _PASTE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return jsonify({"error": f"paste dir unavailable: {e}"}), 500
    _prune_paste_images()
    digest = hashlib.sha256(data).hexdigest()
    out = _PASTE_IMAGE_DIR / f"{digest}{ext}"
    if not out.exists():
        try:
            out.write_bytes(data)
        except OSError as e:
            return jsonify({"error": f"write failed: {e}"}), 500
    log.info(
        "paste-image agent=%s sha256=%s size=%d mime=%s path=%s",
        agent, digest, len(data), mime, out,
    )
    return jsonify({"path": str(out), "sha256": digest, "bytes": len(data)})


@app.route("/api/agents/trigger", methods=["POST"])
@require_auth
def api_agents_trigger():
    """Trigger an agent's scheduled cycle immediately."""
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"error": "agent required"}), 400
    tasks = scheduler.list_tasks()
    task = next((t for t in tasks if t.agent_name == agent and t.enabled), None)
    prompt = task.prompt if task else "Please run your work cycle now."
    ok = messenger.send_message("dashboard", agent, prompt)
    return jsonify({"sent": ok, "agent": agent, "prompt_source": "schedule" if task else "default"})


@app.route("/api/agents/broadcast", methods=["POST"])
@require_auth
def api_agents_broadcast():
    data = request.get_json(force=True)
    message = data.get("message", "")
    priority = data.get("priority", "normal")
    if not message:
        return jsonify({"error": "message required"}), 400
    results = messenger.broadcast("dashboard", message, priority)
    return jsonify(results)


# ---------------------------------------------------------------------------
# API: Agent lifecycle
# ---------------------------------------------------------------------------

@app.route("/api/agents/stop", methods=["POST"])
@require_auth
def api_agents_stop():
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"error": "agent required"}), 400
    return jsonify(agent_manager.stop_agent(agent))


@app.route("/api/agents/start", methods=["POST"])
@require_auth
def api_agents_start():
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"error": "agent required"}), 400
    return jsonify(agent_manager.start_agent(agent))


@app.route("/api/agents/restart", methods=["POST"])
@require_auth
def api_agents_restart():
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"error": "agent required"}), 400
    return jsonify(agent_manager.restart_agent(agent))


@app.route("/api/agents/rediscover", methods=["POST"])
@require_auth
def api_agents_rediscover():
    return jsonify(agent_manager.rediscover())


# tmux reserves ':' (window index) and '.' (pane index) in target specs, so a
# window name containing either is unaddressable. Our generated shell-N names
# never hit this, but validate defensively before shelling out.
_WINDOW_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _next_shell_name(existing: set[str]) -> str:
    """Smallest ``shell-N`` (N >= 1) not already a live window name."""
    n = 1
    while f"shell-{n}" in existing:
        n += 1
    return f"shell-{n}"


@app.route("/api/agents/spawn", methods=["POST"])
@require_auth
def api_agents_spawn():
    """Spawn a fresh plain-shell tmux window in the chela session.

    No command is sent — a bare interactive shell (deliberately NOT `claude`).
    The ttyd supervisor (scripts/agent-terminals.sh) discovers the new window
    on its own poll and assigns a ttyd port within ~12s. Until then the new
    pane's /term/<name>/ iframe 404s — known latency.
    """
    _require_terminals()
    existing = set(discovery.get_all_windows())
    name = _next_shell_name(existing)
    if not _WINDOW_NAME_RE.match(name):
        return jsonify({"ok": False, "error": f"invalid window name: {name}"}), 500
    home = str(Path.home())
    try:
        proc = subprocess.run(
            ["tmux", "new-window", "-t", TMUX_SESSION, "-n", name, "-c", home],
            capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "tmux new-window failed").strip()
        return jsonify({"ok": False, "error": err}), 500
    log.info("spawned plain-shell window %s in session %s", name, TMUX_SESSION)
    return jsonify({"ok": True, "name": name})


@app.route("/api/agents/kill", methods=["POST"])
@require_auth
def api_agents_kill():
    """Kill an agent's tmux window (× button on the terminal wall).

    We target the resolved window_id (`@N`) rather than the name so a stale
    display-name collision can't kill the wrong window.
    """
    _require_terminals()
    data = request.get_json(force=True)
    agent = data.get("agent", "")
    if not agent:
        return jsonify({"ok": False, "error": "agent required"}), 400
    # The wall keys panes by stable wid (@N); accept that or a display name.
    windows = discovery.get_all_windows()
    if agent.startswith("@"):
        wid = agent
    else:
        wid = windows.get(agent)
    if not wid:
        return jsonify({"ok": False, "error": f"agent {agent} not found"}), 404
    try:
        # _kill_window builds `<session>:<arg>`; passing the @N window id targets
        # the exact window (kill-window -t <session>:@N is valid tmux).
        dispatcher._kill_window(wid)
    except Exception as e:  # noqa: BLE001 — surface any tmux/exec failure to the caller
        return jsonify({"ok": False, "error": str(e)}), 500
    log.info("killed non-managed window %s (%s)", agent, wid)
    return jsonify({"ok": True, "name": agent})


# ---------------------------------------------------------------------------
# API: Context Usage
# ---------------------------------------------------------------------------

@app.route("/api/agents/context")
@require_auth
def api_agents_context():
    agent_name = request.args.get("agent")
    snapshots = context.get_latest()
    if agent_name:
        snapshots = [s for s in snapshots if s["agent"] == agent_name]

    results = []
    for s in snapshots:
        results.append({
            "name": s["agent"],
            "used": f"{s['used_k']:g}k" if s.get("used_k") else None,
            "total": f"{s['total_k']:g}k" if s.get("total_k") else None,
            "used_pct": s.get("used_pct"),
            "messages_tokens": f"{s['messages_k']:g}k" if s.get("messages_k") else None,
            "messages_pct": s.get("messages_pct"),
            "free": f"{s['free_k']:g}k" if s.get("free_k") else None,
            "free_pct": s.get("free_pct"),
            "model": s.get("model"),
            "cost_usd": round(s["cost_usd"], 2) if s.get("cost_usd") else None,
            "rate_limit_pct": s.get("rate_limit_pct"),
            "rate_limit_resets_at": s.get("rate_limit_resets_at"),
            "weekly_rl_pct": s.get("weekly_rl_pct"),
            "weekly_rl_resets_at": s.get("weekly_rl_resets_at"),
            "session_name": s.get("session_name"),
            "ts": s.get("ts"),
        })
    return jsonify(results)


# ---------------------------------------------------------------------------
# API: Schedules
# ---------------------------------------------------------------------------

@app.route("/api/schedules")
@require_auth
def api_schedules():
    tasks = scheduler.list_tasks()
    return jsonify([
        {
            "id": t.id,
            "agent_name": t.agent_name,
            "schedule_type": t.schedule_type,
            "schedule_value": t.schedule_value,
            "prompt": t.prompt,
            "enabled": t.enabled,
            "last_run": t.last_run,
            "next_run": t.next_run,
        }
        for t in tasks
    ])


@app.route("/api/schedules", methods=["POST"])
@require_auth
def api_schedules_add():
    data = request.get_json(force=True)
    try:
        task_id = scheduler.add_task(
            data["agent_name"],
            data["schedule_type"],
            data["schedule_value"],
            data["prompt"],
        )
        return jsonify({"id": task_id})
    except (KeyError, ValueError) as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/schedules/<int:task_id>", methods=["DELETE"])
@require_auth
def api_schedules_delete(task_id):
    ok = scheduler.remove_task(task_id)
    return jsonify({"deleted": ok})


@app.route("/api/schedules/<int:task_id>", methods=["PATCH"])
@require_auth
def api_schedules_toggle(task_id):
    data = request.get_json(force=True)
    enabled = 1 if data.get("enabled", True) else 0
    db_path = CHELA_DIR / "scheduler.db"
    try:
        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE tasks SET enabled = ? WHERE id = ?", (enabled, task_id))
        conn.commit()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ---------------------------------------------------------------------------
# API: System cron (read-only)
# ---------------------------------------------------------------------------

def _cron_project(command: str) -> str | None:
    """Best-effort friendly label for a cron line — the project it runs in."""
    m = re.search(r"/projects/([^/\s]+)", command)
    if m:
        return m.group(1)
    m = re.search(r"\bcd\s+(\S+)", command)
    if m:
        return m.group(1).rstrip("/").split("/")[-1]
    m = re.search(r"(\S+)\.py", command)
    if m:
        parts = m.group(1).rsplit("/", 2)
        if len(parts) >= 2:
            return parts[-2]
    return None


@app.route("/api/cron")
@require_auth
def api_cron():
    """Read-only view of the user's system crontab, parsed with next-run times.

    The dashboard never edits cron — this is a visibility companion to the
    chela scheduler. Honors CRON_TZ lines: entries below one are evaluated in
    that timezone, earlier entries in system-local time.
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        ZoneInfo = None
    try:
        from croniter import croniter
    except ImportError:
        croniter = None

    try:
        proc = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=5)
    except Exception:
        return jsonify({"ok": False, "jobs": [], "error": "crontab unavailable"})
    if proc.returncode != 0:
        return jsonify({"ok": True, "jobs": []})  # "no crontab for <user>" → empty, not an error

    jobs = []
    section_tz = None  # None → system local
    for raw in proc.stdout.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        env = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$", line)
        if env:
            if env.group(1) == "CRON_TZ":
                section_tz = env.group(2).strip()
            continue
        if line.startswith("@"):
            head, _, command = line.partition(" ")
            expr, command = head, command.strip()
        else:
            parts = line.split(None, 5)
            if len(parts) < 6:
                continue
            expr, command = " ".join(parts[:5]), parts[5]
        next_run = None
        if croniter is not None and croniter.is_valid(expr):
            try:
                base = (datetime.now(ZoneInfo(section_tz)) if section_tz and ZoneInfo
                        else datetime.now().astimezone())
                next_run = croniter(expr, base).get_next(datetime).astimezone(timezone.utc).isoformat()
            except Exception:
                next_run = None
        jobs.append({
            "schedule": expr,
            "command": command,
            "project": _cron_project(command),
            "next_run": next_run,
            "tz": section_tz or "local",
        })
    return jsonify({"ok": True, "jobs": jobs})


# ---------------------------------------------------------------------------
# API: Dispatcher
# ---------------------------------------------------------------------------

def _runs_for_workflow(
    all_runs: list[dict], wf_path: str
) -> tuple[list[dict], list[dict], list[dict]]:
    """Split runs into (active, awaiting_review, recent_completed) for a workflow.

    Match is by resolved-path equality so different env spellings still align
    with the path stored by dispatcher.tick() (which writes str(wf.path) where
    wf.path is already resolved). Returns at most 10 awaiting / completed.
    """
    try:
        target = str(Path(wf_path).expanduser().resolve())
    except OSError:
        target = wf_path
    matching = [r for r in all_runs if r.get("workflow_path") == target]
    active = [r for r in matching if r.get("status") in ("claimed", "running")]
    awaiting = [r for r in matching if r.get("status") == "awaiting_review"][:10]
    recent = [r for r in matching if r.get("status") in ("done", "failed")][:10]
    return active, awaiting, recent


@app.route("/api/dispatcher")
@require_auth
def api_dispatcher():
    """Per-workflow view: open tasks + active runs + recent completed runs."""
    workflows_payload = []
    all_runs = dispatcher.list_runs()

    for wf_path in DISPATCH_WORKFLOWS:
        entry: dict = {
            "path": str(wf_path),
            "exists": wf_path.exists(),
            "project_key": None,
            "open_tasks": [],
            "backlog_items": [],
            "active_runs": [],
            "awaiting_review_runs": [],
            "recent_runs": [],
            "error": None,
        }
        if not wf_path.exists():
            entry["error"] = "workflow file not found"
            workflows_payload.append(entry)
            continue

        active, awaiting, recent = _runs_for_workflow(all_runs, str(wf_path))
        # Hide tasks from Open if they already have an in-flight run, so a
        # single TODO line never shows two cards. The strike on master only
        # lands when the PR merges, so without this filter an awaiting_review
        # task also appears as Open. Failed runs are excluded too; the
        # attempt cap blocks re-dispatch and editing the line mints a new id.
        in_flight_ids = (
            {r.get("task_id") for r in active}
            | {r.get("task_id") for r in awaiting}
            | {r.get("task_id") for r in recent if r.get("status") == "failed"}
        )
        project_key: str | None = None

        try:
            wf = load_workflow(wf_path)
            project_key = wf.project_key
            entry["project_key"] = project_key
            source = get_source(wf)
            open_tasks = source.list_open_tasks()
            entry["open_tasks"] = [
                {
                    "id": t.id,
                    "title": t.title,
                    "file": t.file,
                    "line_number": t.line_number,
                }
                for t in open_tasks
                if t.id not in in_flight_ids
            ]
            backlog_path = (wf.path.parent / "BACKLOG.md").resolve()
            entry["backlog_items"] = [
                {"section": item.section, "text": item.text, "file": str(backlog_path)}
                for item in parse_backlog(backlog_path)
            ]
        except Exception as e:
            entry["error"] = f"{type(e).__name__}: {e}"

        # Stamp project_key onto each run dict — task_number already comes from
        # the row (column added via idempotent migration); pre-migration rows
        # carry task_number=None, which the frontend uses as the signal to fall
        # back to the legacy `dogfood/<sha>` branch_name display. pr_mergeable
        # already rides along via list_runs()'s SELECT *; normalize it to None
        # for pre-migration rows so the frontend can rely on the key existing.
        for r in (*active, *awaiting, *recent):
            r["project_key"] = project_key
            r.setdefault("pr_mergeable", None)
        entry["active_runs"] = active
        entry["awaiting_review_runs"] = awaiting
        entry["recent_runs"] = recent
        workflows_payload.append(entry)

    return jsonify({
        "configured": bool(DISPATCH_WORKFLOWS),
        "workflows": workflows_payload,
    })


def _resolve_dispatch_workflow(wf_path: str) -> Path | None:
    """Match a client-supplied workflow path against ``DISPATCH_WORKFLOWS``.

    Returns the configured ``Path`` on match, ``None`` otherwise. Comparison
    uses fully-resolved paths so different spellings (relative, ``~``, symlink)
    still align with what the daemon registered. Refusing unknown paths keeps
    the endpoint from mutating files in arbitrary repos.
    """
    try:
        target = Path(wf_path).expanduser().resolve()
    except OSError:
        return None
    for wf in DISPATCH_WORKFLOWS:
        if wf == target:
            return wf
    return None


def _insert_into_open_section(todo_text: str, bullet: str) -> str | None:
    """Insert ``bullet`` directly below ``## Open`` in ``TODO.md`` text.

    Matches the layout of recent queue commits (e.g. ``e62fb45``): a blank
    line under the header, then the new bullet, then a blank line, then the
    existing items. Returns ``None`` if no ``## Open`` header is found.
    """
    keep_trailing_nl = todo_text.endswith("\n")
    lines = todo_text.splitlines()
    open_idx: int | None = None
    for i, raw in enumerate(lines):
        if raw.strip() == "## Open":
            open_idx = i
            break
    if open_idx is None:
        return None
    insert_at = open_idx + 1
    if insert_at < len(lines) and lines[insert_at].strip() == "":
        insert_at += 1
    lines[insert_at:insert_at] = [bullet, ""]
    out = "\n".join(lines)
    if keep_trailing_nl:
        out += "\n"
    return out


def _remove_backlog_bullet(backlog_text: str, text: str) -> tuple[str | None, int]:
    """Drop the bullet whose extracted text exactly matches ``text``.

    Returns ``(new_text, match_count)``. ``match_count`` is the number of
    bullet lines that extracted to ``text`` — callers refuse 0 (not found) and
    >1 (ambiguous) without modifying any file.
    """
    keep_trailing_nl = backlog_text.endswith("\n")
    lines = backlog_text.splitlines()
    matches: list[int] = []
    for i, raw in enumerate(lines):
        m = _BULLET_RE.match(raw)
        if not m:
            continue
        if m.group(1).strip() == text:
            matches.append(i)
    if len(matches) != 1:
        return None, len(matches)
    del lines[matches[0]]
    out = "\n".join(lines)
    if keep_trailing_nl:
        out += "\n"
    return out, 1


@app.route("/api/dispatcher/backlog/promote", methods=["POST"])
@require_auth
def api_dispatcher_backlog_promote():
    """Move a backlog bullet into TODO.md's Open section + push to master.

    The dispatcher only picks up TODO lines from master, so a local-only
    commit isn't enough — the push is part of the contract. All failure
    modes (BACKLOG missing, bullet not found / ambiguous, push failure)
    return a JSON error and leave the repo in its pre-call state: we
    capture the pre-call HEAD up front and ``git reset --hard`` to it on
    any post-mutation failure so the call is either fully applied or
    fully rolled back.
    """
    data = request.get_json(force=True) or {}
    wf_path = data.get("workflow_path", "")
    text = (data.get("text", "") or "").strip()
    if not wf_path or not text:
        return jsonify({"ok": False, "error": "workflow_path and text required"}), 400

    wf_resolved = _resolve_dispatch_workflow(wf_path)
    if wf_resolved is None:
        return jsonify({"ok": False, "error": f"unknown workflow: {wf_path}"}), 400

    try:
        wf = load_workflow(wf_resolved)
    except Exception as e:
        return jsonify({"ok": False, "error": f"failed to load workflow: {e}"}), 500
    repo_dir = wf.path.parent
    backlog_path = repo_dir / "BACKLOG.md"
    source = get_source(wf)
    todo_path = source.path

    if not backlog_path.exists():
        return jsonify({"ok": False, "error": f"BACKLOG.md not found at {backlog_path}"}), 404
    if not todo_path.exists():
        return jsonify({"ok": False, "error": f"TODO.md not found at {todo_path}"}), 404

    backlog_text = backlog_path.read_text()
    new_backlog, n = _remove_backlog_bullet(backlog_text, text)
    if n == 0:
        return jsonify({"ok": False, "error": "bullet not found in BACKLOG.md"}), 404
    if n > 1:
        return jsonify({"ok": False, "error": f"bullet text matches {n} lines in BACKLOG.md (ambiguous)"}), 409

    todo_text = todo_path.read_text()
    new_todo = _insert_into_open_section(todo_text, f"- [ ] {text}")
    if new_todo is None:
        return jsonify({"ok": False, "error": "## Open section not found in TODO.md"}), 500

    try:
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=10,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return jsonify({"ok": False, "error": f"git rev-parse failed: {e}"}), 500
    if head.returncode != 0:
        return jsonify({"ok": False, "error": (head.stderr or "git rev-parse HEAD failed").strip()}), 500
    original_sha = head.stdout.strip()

    def _rollback():
        subprocess.run(
            ["git", "reset", "--hard", original_sha],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=15,
        )

    backlog_path.write_text(new_backlog)
    todo_path.write_text(new_todo)

    truncated = text if len(text) <= 50 else text[:47].rstrip() + "..."
    commit_msg = f'backlog: promote "{truncated}" to TODO'

    try:
        add = subprocess.run(
            ["git", "add", backlog_path.name, todo_path.name],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=15,
        )
        if add.returncode != 0:
            _rollback()
            return jsonify({"ok": False, "error": (add.stderr or "git add failed").strip()}), 500
        commit = subprocess.run(
            ["git", "commit", "-m", commit_msg],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=20,
        )
        if commit.returncode != 0:
            _rollback()
            return jsonify({"ok": False, "error": (commit.stderr or commit.stdout or "git commit failed").strip()}), 500
        push = subprocess.run(
            ["git", "push"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
        if push.returncode != 0:
            _rollback()
            err = (push.stderr or push.stdout or "git push failed").strip()
            return jsonify({"ok": False, "error": err}), 502
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        _rollback()
        return jsonify({"ok": False, "error": f"git operation failed: {e}"}), 500

    return jsonify({"ok": True, "commit_msg": commit_msg})
_PR_NUMBER_RE = re.compile(r"/pull/(\d+)(?:[/?#]|$)")


def _best_effort(task_id: str, label: str, argv: list[str], cwd: str, timeout: int) -> None:
    """Run a cleanup command best-effort: log non-zero/errors, never raise."""
    try:
        proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("merge cleanup %s failed for task %s: %s", label, task_id, e)
        return
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or f"{label} failed").strip()
        log.warning("merge cleanup %s failed for task %s: %s", label, task_id, err)


_DONE_RE = re.compile(r"^\s*-\s*\[[xX]\]\s*(.+?)\s*$")


def _pr_mergeable(pr_number: str, repo_dir: str) -> str | None:
    """Return GitHub's mergeable verdict (MERGEABLE / CONFLICTING / UNKNOWN) or None."""
    try:
        proc = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "mergeable", "-q", ".mergeable"],
            cwd=repo_dir, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return (proc.stdout or "").strip() or None


def _struck_titles(worktree_path: str, base_ref: str) -> list[str] | None:
    """Titles this branch struck: `- [ ] X` at the merge-base, `- [x] X` at HEAD.

    Returns the list of titles, or None if the merge-base / file lookups fail.
    """
    def _show(ref: str) -> str | None:
        try:
            proc = subprocess.run(
                ["git", "show", f"{ref}:TODO.md"],
                cwd=worktree_path, capture_output=True, text=True, timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None
        return proc.stdout if proc.returncode == 0 else None

    try:
        mb = subprocess.run(
            ["git", "merge-base", "HEAD", base_ref],
            cwd=worktree_path, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if mb.returncode != 0:
        return None
    merge_base = (mb.stdout or "").strip()
    if not merge_base:
        return None

    base_todo = _show(merge_base)
    head_todo = _show("HEAD")
    if base_todo is None or head_todo is None:
        return None

    base_open = {m.group(1).strip() for ln in base_todo.splitlines()
                 if (m := OPEN_RE.match(ln))}
    head_done = {m.group(1).strip() for ln in head_todo.splitlines()
                 if (m := _DONE_RE.match(ln))}
    return sorted(base_open & head_done)


def _restrike_master_todo(worktree_path: str, titles: list[str]) -> int:
    """In the worktree's TODO.md, flip `- [ ] X` -> `- [x] X` for each title in *titles*.

    Returns the number of lines actually flipped (so the caller can assert it
    matches the expected struck-line count before trusting the resolve).
    """
    todo = Path(worktree_path) / "TODO.md"
    content = todo.read_text()
    trailing_nl = content.endswith("\n")
    want = set(titles)
    out: list[str] = []
    flipped = 0
    for line in content.splitlines():
        m = OPEN_RE.match(line)
        if m and m.group(1).strip() in want:
            out.append(line.replace("[ ]", "[x]", 1))
            flipped += 1
        else:
            out.append(line)
    todo.write_text("\n".join(out) + ("\n" if trailing_nl else ""))
    return flipped


def _auto_resolve_todo_conflict(
    task_id: str, pr_number: str, worktree_path: str, branch_name: str, base_branch: str,
) -> dict:
    """Resolve a TODO.md-ONLY merge conflict in the run's worktree, then push.

    Strict guards — anything outside "only TODO.md, only the expected strike
    lines" aborts the merge and falls back to manual resolution:
      - the conflicted set must be exactly {TODO.md} (never auto-resolve code);
      - the branch must have struck >= 1 line, and master's TODO.md must still
        carry exactly those lines as `- [ ]` (flip count must match).

    Returns {"ok": True} on a clean resolve+push, else {"ok": False, "error": ...}.
    """
    base_ref = f"origin/{base_branch}"

    def _git(argv: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["git", *argv], cwd=worktree_path,
            capture_output=True, text=True, timeout=timeout,
        )

    def _abort_manual(reason: str) -> dict:
        _best_effort(task_id, "merge-abort", ["git", "merge", "--abort"], worktree_path, 15)
        return {"ok": False, "error": reason}

    try:
        _git(["fetch", "origin"], timeout=60)
        # Let it conflict — we inspect the unmerged set rather than trusting exit code.
        _git(["merge", "--no-commit", "--no-ff", base_ref], timeout=60)
        conflicted = _git(["diff", "--name-only", "--diff-filter=U"], timeout=15)
        files = {f for f in conflicted.stdout.split("\n") if f.strip()}
        if files != {"TODO.md"}:
            return _abort_manual(
                f"merge conflict touches files other than TODO.md ({sorted(files)}); "
                "resolve this PR by hand"
            )

        titles = _struck_titles(worktree_path, base_ref)
        if not titles:
            return _abort_manual(
                "could not determine which TODO line this branch struck; resolve by hand"
            )

        # Resolve to master's TODO.md, then re-strike exactly the branch's line(s).
        co = _git(["checkout", base_ref, "--", "TODO.md"], timeout=15)
        if co.returncode != 0:
            return _abort_manual(
                (co.stderr or "git checkout of master TODO.md failed").strip()
            )
        flipped = _restrike_master_todo(worktree_path, titles)
        if flipped != len(titles):
            return _abort_manual(
                f"expected to re-strike {len(titles)} line(s) in master's TODO.md but "
                f"flipped {flipped} (master may have already struck or removed them); "
                "resolve by hand"
            )

        log.info(
            "auto-resolve TODO.md conflict task=%s pr=%s lines=%r",
            task_id, pr_number, titles,
        )

        _git(["add", "TODO.md"], timeout=15)
        commit = _git(["commit", "--no-edit"], timeout=20)
        if commit.returncode != 0:
            return _abort_manual(
                (commit.stderr or commit.stdout or "git commit of resolution failed").strip()
            )
        push = _git(["push", "origin", f"HEAD:{branch_name}"], timeout=60)
        if push.returncode != 0:
            # Already committed locally; nothing to abort. Surface the push error.
            return {"ok": False, "error": (push.stderr or push.stdout or "git push failed").strip()}
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return _abort_manual(f"git operation failed during auto-resolve: {e}")

    # Wait for GitHub to recompute mergeability after the push.
    for _ in range(5):
        if _pr_mergeable(pr_number, worktree_path) == "MERGEABLE":
            break
        time.sleep(2)

    return {"ok": True, "struck": titles}


def _merge_one(row: dict) -> dict:
    """Squash-merge one run's PR, then clean up the local worktree and branch.

    Shared by the single-card merge endpoint and the batch merge-all endpoint.
    Returns a result dict: on success ``{"ok": True, "merge_commit_sha": ...}``;
    on failure ``{"ok": False, "error": ..., "status": <http-code>}`` where
    ``status`` is the HTTP code the single-card endpoint should surface (the
    batch endpoint ignores it and just records the error). Never raises —
    gh/subprocess failures are captured into the error string.

    Squash — NOT rebase: rebase-merge silently drops the post-PR strike commit
    on master, after which the dispatcher would redispatch the already-merged
    task (PR #12 incident).

    Cleanup is done by us, not by `gh pr merge --delete-branch`: gh's
    `--delete-branch` exits non-zero when the local branch is checked out in a
    worktree (the dogfood case), surfacing a noisy "cannot delete branch ...
    used by worktree at ..." error even though the remote merge succeeded.
    Instead we run `gh pr merge --squash` cleanly, then best-effort remove the
    worktree, the local branch, and the remote branch ourselves.
    """
    task_id = row.get("task_id")
    pr_url = row.get("pr_url")
    if not pr_url:
        return {"ok": False, "error": "run has no pr_url", "status": 400}
    m = _PR_NUMBER_RE.search(str(pr_url))
    if not m:
        return {"ok": False, "error": f"could not parse PR number from {pr_url}", "status": 400}
    pr_number = m.group(1)
    wf_path = row.get("workflow_path") or ""
    repo_dir = Path(wf_path).parent if wf_path else None
    if not repo_dir or not repo_dir.is_dir():
        return {"ok": False, "error": f"workflow repo dir not found: {wf_path}", "status": 400}

    # Pre-merge: if GitHub reports CONFLICTING, attempt a strictly-guarded
    # auto-resolve of a TODO.md-ONLY bookkeeping conflict in the run's
    # worktree. Anything beyond TODO.md (or an ambiguous strike) aborts to
    # manual. The batch merge-all path pre-filters to MERGEABLE, so this only
    # fires for a single-card Merge on a conflicting PR.
    if _pr_mergeable(pr_number, str(repo_dir)) == "CONFLICTING":
        worktree_path_pre = row.get("worktree_path")
        branch_name_pre = row.get("branch_name")
        if not worktree_path_pre or not Path(worktree_path_pre).is_dir():
            return {"ok": False, "error": "PR is conflicting but the run's worktree is gone; resolve by hand", "status": 409}
        if not branch_name_pre:
            return {"ok": False, "error": "PR is conflicting but the run has no branch_name; resolve by hand", "status": 409}
        try:
            _wf = load_workflow(Path(wf_path))
            base_branch = _wf.get("workspace", "base_branch", default="master")
        except Exception:
            base_branch = "master"
        resolved = _auto_resolve_todo_conflict(task_id, pr_number, worktree_path_pre, branch_name_pre, base_branch)
        if not resolved.get("ok"):
            return {"ok": False, "error": resolved.get("error", "auto-resolve failed"), "status": 409}

    try:
        merge = subprocess.run(
            ["gh", "pr", "merge", pr_number, "--squash"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return {"ok": False, "error": "gh CLI not found on PATH", "status": 500}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": "gh pr merge timed out", "status": 504}
    if merge.returncode != 0:
        err = (merge.stderr or merge.stdout or "gh pr merge failed").strip()
        return {"ok": False, "error": err, "status": 502}

    merge_sha = None
    try:
        sha_proc = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "mergeCommit", "-q", ".mergeCommit.oid"],
            cwd=str(repo_dir), capture_output=True, text=True, timeout=15,
        )
        if sha_proc.returncode == 0:
            merge_sha = sha_proc.stdout.strip() or None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    repo_cwd = str(repo_dir)
    worktree_path = row.get("worktree_path")
    if worktree_path and Path(worktree_path).exists():
        _best_effort(
            task_id, "worktree-remove",
            ["git", "worktree", "remove", "--force", worktree_path],
            repo_cwd, 30,
        )
    # Read branch_name from the runs row so this works regardless of naming
    # scheme (dogfood/<id>, <project_key>-<N>, etc.).
    branch_name = row.get("branch_name")
    if branch_name:
        _best_effort(
            task_id, "branch-delete",
            ["git", "branch", "-D", branch_name],
            repo_cwd, 15,
        )
        _best_effort(
            task_id, "remote-branch-delete",
            ["git", "push", "origin", "--delete", branch_name],
            repo_cwd, 30,
        )

    return {"ok": True, "merge_commit_sha": merge_sha}


@app.route("/api/dispatcher/runs/<task_id>/merge", methods=["POST"])
@require_auth
def api_dispatcher_run_merge(task_id: str):
    """Squash-merge a single run's PR + clean up. See ``_merge_one`` for detail."""
    row = next((r for r in dispatcher.list_runs() if r.get("task_id") == task_id), None)
    if not row:
        return jsonify({"ok": False, "error": f"run {task_id} not found"}), 404
    result = _merge_one(row)
    status = result.pop("status", 200 if result.get("ok") else 502)
    return jsonify(result), status


@app.route("/api/dispatcher/merge-all", methods=["POST"])
@require_auth
def api_dispatcher_merge_all():
    """Batch squash-merge every awaiting_review run whose PR is MERGEABLE.

    Optional ``{workflow_path}`` filter restricts to one workflow — the Kanban
    passes the active filter unless it's "all". A run is eligible only when
    ``status == 'awaiting_review'`` AND ``pr_state in ('open', None)`` AND
    ``pr_mergeable == 'MERGEABLE'``; anything CONFLICTING / UNKNOWN / non-open
    lands under ``skipped`` and is never merged. Each eligible run goes through
    the shared ``_merge_one`` helper, so each merge gets the same cleanup as the
    single-card button.

    Returns ``{ok, merged: [task_id...], skipped: [{task_id, reason}],
    failed: [{task_id, error}]}``.
    """
    data = request.get_json(silent=True) or {}
    wf_filter = (data.get("workflow_path") or "").strip()
    target: str | None = None
    if wf_filter:
        resolved = _resolve_dispatch_workflow(wf_filter)
        if resolved is None:
            return jsonify({"ok": False, "error": f"unknown workflow: {wf_filter}"}), 400
        target = str(resolved)

    merged: list = []
    skipped: list = []
    failed: list = []
    for row in dispatcher.list_runs():
        if row.get("status") != "awaiting_review":
            continue
        if target is not None and row.get("workflow_path") != target:
            continue
        task_id = row.get("task_id")
        pr_state = row.get("pr_state")
        if pr_state not in ("open", None):
            skipped.append({"task_id": task_id, "reason": f"pr_state={pr_state}"})
            continue
        if row.get("pr_mergeable") != "MERGEABLE":
            skipped.append({"task_id": task_id, "reason": f"mergeable={row.get('pr_mergeable')}"})
            continue
        result = _merge_one(row)
        if result.get("ok"):
            merged.append(task_id)
        else:
            failed.append({"task_id": task_id, "error": result.get("error")})

    return jsonify({"ok": True, "merged": merged, "skipped": skipped, "failed": failed})


def _allowed_source_files() -> set[str]:
    """Resolve every TODO.md / BACKLOG.md path the configured workflows know about.

    The delete endpoint refuses to touch any file outside this set so the
    "source-line" kind can't be coerced into rewriting arbitrary paths.
    """
    allowed: set[str] = set()
    for wf_path in DISPATCH_WORKFLOWS:
        if not wf_path.exists():
            continue
        try:
            wf = load_workflow(wf_path)
            source = get_source(wf)
            allowed.add(str(source.path))
            allowed.add(str((wf.path.parent / "BACKLOG.md").resolve()))
        except Exception:
            continue
    return allowed


def _delete_source_line(file_path: Path, text: str) -> dict:
    """Remove the first bullet whose title equals ``text``. Idempotent."""
    if not file_path.exists():
        return {"ok": True, "deleted": False, "reason": "file missing"}
    content = file_path.read_text()
    trailing_nl = content.endswith("\n")
    lines = content.splitlines()
    new_lines: list[str] = []
    deleted = False
    for line in lines:
        if not deleted:
            m_open = OPEN_RE.match(line)
            m_bullet = _BULLET_RE.match(line)
            if (m_open and m_open.group(1).strip() == text) or \
               (m_bullet and m_bullet.group(1).strip() == text):
                deleted = True
                continue
        new_lines.append(line)
    if not deleted:
        return {"ok": True, "deleted": False, "reason": "no match"}
    new_content = "\n".join(new_lines) + ("\n" if trailing_nl else "")
    file_path.write_text(new_content)
    return {"ok": True, "deleted": True}


@app.route("/api/dispatcher/delete", methods=["POST"])
@require_auth
def api_dispatcher_delete():
    """Delete a Kanban card / Dispatcher row.

    Payload: ``{kind: "run", task_id}`` for runs-table rows;
    ``{kind: "source-line", file, text}`` for Backlog / Open cards backed by a
    markdown bullet. PRs are never touched — done/awaiting_review just drops
    the row; the user closes the PR on GitHub if they want.
    """
    data = request.get_json(force=True) or {}
    kind = data.get("kind")
    if kind == "run":
        task_id = data.get("task_id") or ""
        if not task_id:
            return jsonify({"ok": False, "error": "task_id required"}), 400
        try:
            return jsonify(dispatcher.delete_run(task_id))
        except Exception as e:
            log.exception("delete_run failed for %s", task_id)
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    if kind == "source-line":
        file_arg = data.get("file") or ""
        text = data.get("text") or ""
        if not file_arg or not text:
            return jsonify({"ok": False, "error": "file and text required"}), 400
        try:
            resolved = str(Path(file_arg).expanduser().resolve())
        except OSError:
            return jsonify({"ok": False, "error": "could not resolve file path"}), 400
        if resolved not in _allowed_source_files():
            return jsonify({"ok": False, "error": "file not in any configured workflow"}), 403
        try:
            return jsonify(_delete_source_line(Path(resolved), text))
        except Exception as e:
            log.exception("delete_source_line failed for %s", resolved)
            return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500
    return jsonify({"ok": False, "error": f"unknown kind {kind!r}"}), 400


# ---------------------------------------------------------------------------
# API: Summary (header bar)
# ---------------------------------------------------------------------------

@app.route("/api/summary")
@require_auth
def api_summary():
    windows = discovery.get_all_windows()
    tasks = scheduler.list_tasks()

    # Find soonest next_run per agent
    next_runs = {}
    for t in tasks:
        if t.enabled and t.next_run:
            if t.agent_name not in next_runs or t.next_run < next_runs[t.agent_name]:
                next_runs[t.agent_name] = t.next_run

    return jsonify({
        "agents_online": len(windows),
        "agents_total": len(windows),
        "windows_total": len(windows),
        "schedules_active": sum(1 for t in tasks if t.enabled),
        "schedules_total": len(tasks),
        "next_runs": next_runs,
    })


# ---------------------------------------------------------------------------
# API: Server-Sent Events (reactive UI accelerator)
# ---------------------------------------------------------------------------
#
# Pushes coarse "something changed" deltas so the dashboard reacts within ~1s
# instead of waiting on the per-tab polling timers. This is purely additive:
# every polling timer in app.js stays as a fallback, so if this stream never
# connects or drops, the UI behaves exactly as it did before SSE existed.
#
# The generator holds the previous snapshot in its own local scope, polls the
# same sources the REST endpoints use every ~1s, and yields a named frame only
# when a relevant field changed (window added/removed, run status/pr_state/
# pr_mergeable, or heartbeat status). Payloads stay tiny — the client uses the
# event as a trigger to re-run its existing render/refresh path (which refetches
# the full shape from /api/agents or /api/dispatcher), so no new DOM path is
# introduced. A ': keepalive' comment every ~15s stops idle proxies from
# dropping the connection.

SSE_POLL_INTERVAL = 1.0          # seconds between snapshot diffs
SSE_KEEPALIVE_INTERVAL = 15.0    # seconds between idle keepalive comments


def _sse_windows_snapshot() -> dict:
    try:
        return dict(discovery.get_all_windows())
    except Exception:
        log.exception("SSE: get_all_windows failed")
        return {}


def _sse_runs_snapshot() -> dict:
    try:
        return {
            r.get("task_id"): (r.get("status"), r.get("pr_state"), r.get("pr_mergeable"))
            for r in dispatcher.list_runs()
            if r.get("task_id")
        }
    except Exception:
        log.exception("SSE: list_runs failed")
        return {}


def _sse_terms_snapshot() -> set:
    """Set of agents with a live ttyd port — diffed to push a `term-ready` event
    so a pending pane swaps to its iframe without waiting for the next poll. The
    ~1.5s client poll stays the reliable default; this is a pure accelerator."""
    try:
        return {a for a, p in _terminals_port_map().items() if p}
    except Exception:
        log.exception("SSE: terms snapshot failed")
        return set()


def _sse_stream():
    """Generator yielding SSE frames on relevant state change. Never raises out;
    a disconnected client surfaces as GeneratorExit on the next yield, which
    cleanly tears the loop down."""
    # Prime the baseline from the current state without emitting — the client
    # has already done its initial full fetch on load, so we only push changes
    # from here on.
    prev_windows = _sse_windows_snapshot()
    prev_runs = _sse_runs_snapshot()
    prev_terms = _sse_terms_snapshot()

    # An initial 'hello' lets the client confirm the stream is live (it may
    # optionally lengthen its poll timers; default behavior leaves them as-is).
    yield "event: hello\ndata: {}\n\n"

    last_sent = time.monotonic()
    while True:
        time.sleep(SSE_POLL_INTERVAL)

        cur_windows = _sse_windows_snapshot()
        added = sorted(set(cur_windows) - set(prev_windows))
        removed = sorted(set(prev_windows) - set(cur_windows))
        if added or removed:
            payload = json.dumps({"added": added, "removed": removed})
            yield f"event: windows\ndata: {payload}\n\n"
            last_sent = time.monotonic()
        prev_windows = cur_windows

        cur_runs = _sse_runs_snapshot()
        if cur_runs != prev_runs:
            changed = [
                tid for tid, v in cur_runs.items() if prev_runs.get(tid) != v
            ] + [tid for tid in prev_runs if tid not in cur_runs]
            payload = json.dumps({"changed": len(changed)})
            yield f"event: runs\ndata: {payload}\n\n"
            last_sent = time.monotonic()
        prev_runs = cur_runs

        cur_terms = _sse_terms_snapshot()
        newly_ready = sorted(cur_terms - prev_terms)
        if newly_ready:
            payload = json.dumps({"ready": newly_ready})
            yield f"event: term-ready\ndata: {payload}\n\n"
            last_sent = time.monotonic()
        prev_terms = cur_terms

        now = time.monotonic()
        if now - last_sent >= SSE_KEEPALIVE_INTERVAL:
            yield ": keepalive\n\n"
            last_sent = now


@app.route("/api/events")
@require_auth
def api_events():
    resp = Response(_sse_stream(), mimetype="text/event-stream")
    # no-cache + no proxy buffering so frames reach the browser immediately
    # (a fronting reverse proxy must also stream text/event-stream unbuffered).
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    resp.headers["Connection"] = "keep-alive"
    return resp


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def main():
    # threaded=True is required: the SSE generator at /api/events holds its
    # request thread open for the life of the connection, so a single-threaded
    # dev server would block every other request behind it.
    # debug=False on purpose: the Werkzeug auto-reloader respawns a child
    # process, and the interactive debugger is an RCE vector if the port is
    # ever exposed.
    #
    # Binds 127.0.0.1 by default — ZERO auth (see module docstring); put it
    # behind a tailnet / SSH tunnel for remote access. Override host/port with
    # CHELA_DASH_HOST / CHELA_DASHBOARD_PORT.
    app.run(
        host=os.environ.get("CHELA_DASH_HOST", "127.0.0.1"),
        port=int(os.environ.get("CHELA_DASHBOARD_PORT", "5001")),
        debug=False, threaded=True,
    )


if __name__ == "__main__":
    main()
