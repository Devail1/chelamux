"""Agent lifecycle management — start, stop, restart claude sessions in tmux."""
from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path

from chela import config
from chela.discovery import get_window_id, get_window_cwd, get_all_windows
from chela.messenger import send_tmux

log = logging.getLogger(__name__)

# Command used to (re)launch an agent from the dashboard Start button. Plain
# `claude` by default — these are long-lived interactive sessions a human is
# watching, so permission prompts are answerable. Override with CHELA_AGENT_CMD.
DEFAULT_LAUNCH_CMD = os.environ.get("CHELA_AGENT_CMD", "claude")

# Windows the reconcile loop must never rename. Empty by default — kept so
# reconcile_window_names() has a single honored exclusion point if a deployment
# ever needs to pin a window's name.
NEVER_MANAGE: set[str] = set()


def liveness(claude_running: bool, session_status: str | None) -> tuple[str, str]:
    """Derive (liveness, health_color) from native session state — no heartbeat.

    discovery only ever lists LIVE windows, so a listed window is never "dead" —
    the label reflects what KIND of live it is:
      - "waiting" → claude blocked on input (needs attention) — yellow
      - "alive"   → claude running / busy / idle — green
      - "live"    → no claude in the pane (a shell / dev server), present — grey

    Shared by the dashboard /api/agents and the agent-facing `chela peek` so both
    read identical status off one data layer.
    """
    if session_status == "waiting":
        return "waiting", "yellow"
    if claude_running or session_status in ("busy", "idle"):
        return "alive", "green"
    return "live", "grey"


def wid_env_prefix(window_id: str) -> str:
    """Shell prefix that exports the agent's own tmux window id as CHELA_WID.

    Injected at every chela-controlled spawn so a running agent knows its own
    identity (``echo $CHELA_WID`` → ``@N``) and can peek/read/drive siblings
    relative to itself — the cmux ``CMUX_SURFACE_ID`` model. Returns a trailing
    ``&&`` so it chains cleanly ahead of the launch command; empty string for a
    falsy id (never emit a broken ``export``).
    """
    if not window_id:
        return ""
    return f"export CHELA_WID={shlex.quote(window_id)} && "


def is_claude_running(window_id: str) -> bool:
    """Check if a claude process is running in the given tmux window."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", f"{config.current_session()}:{window_id}", "-p", "#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode != 0:
            return False
        pane_pid = result.stdout.strip()
        if not pane_pid:
            return False
        check = subprocess.run(
            ["pgrep", "-P", pane_pid, "-f", "claude"],
            capture_output=True, timeout=5,
        )
        return check.returncode == 0
    except (subprocess.TimeoutExpired, Exception):
        return False


# --- Native session activity (busy/idle) via `claude agents --json` ---------
# Claude Code exposes every interactive session with an authoritative
# status ("busy" while generating/running a tool, "idle" at the prompt). One
# command covers all sessions, so we read it once per dashboard poll and cache
# it briefly to coalesce bursts. Sessions are keyed by pid (exact) and cwd
# (fallback); we map a tmux window -> its child claude pid to look up status.
_STATUS_TTL = 2.0
_status_cache: dict = {"ts": 0.0, "by_pid": {}, "by_cwd": {}, "cwd_by_pid": {}}


def session_status_map(force: bool = False) -> dict:
    """Maps from `claude agents --json`: by_pid {pid: status},
    by_cwd {cwd: status}, cwd_by_pid {pid: cwd}."""
    now = time.time()
    if not force and now - _status_cache["ts"] < _STATUS_TTL:
        return _status_cache
    by_pid, by_cwd, cwd_by_pid = {}, {}, {}
    try:
        r = subprocess.run(
            ["claude", "agents", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0:
            for s in json.loads(r.stdout or "[]"):
                st = s.get("status")
                pid, cwd = s.get("pid"), s.get("cwd")
                if pid is not None:
                    by_pid[int(pid)] = st
                    if cwd:
                        cwd_by_pid[int(pid)] = cwd
                if cwd:
                    by_cwd[cwd] = st
    except (subprocess.TimeoutExpired, ValueError, json.JSONDecodeError, Exception):
        pass  # stale-but-safe: keep last cache on any failure
    _status_cache.update(ts=now, by_pid=by_pid, by_cwd=by_cwd, cwd_by_pid=cwd_by_pid)
    return _status_cache


def claude_pid(window_id: str) -> int | None:
    """PID of the claude process in a tmux window (matches `agents --json` pid)."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", f"{config.current_session()}:{window_id}", "-p", "#{pane_pid}"],
            capture_output=True, text=True, timeout=5,
        )
        pane_pid = result.stdout.strip()
        if result.returncode != 0 or not pane_pid:
            return None
        check = subprocess.run(
            ["pgrep", "-P", pane_pid, "-f", "claude"],
            capture_output=True, text=True, timeout=5,
        )
        if check.returncode != 0:
            return None
        pids = [int(p) for p in check.stdout.split()]
        return pids[0] if pids else None
    except (subprocess.TimeoutExpired, ValueError, Exception):
        return None


# Foreground commands that indicate a dev server / long-running process running
# in a pane (best-effort heuristic for the dashboard's window-type icon/filter).
# Matched as a prefix of tmux's #{pane_current_command} (e.g. "python3.11").
_SERVER_COMMANDS = (
    "node", "vite", "next", "webpack", "deno", "bun",
    "npm", "pnpm", "yarn",
    "python", "uv", "uvicorn", "gunicorn", "flask", "hypercorn",
    "rails", "puma", "rackup", "ruby",
    "cargo", "air", "nodemon",
)


def pane_command(window_id: str) -> str:
    """tmux #{pane_current_command} for a window (foreground process), or ""."""
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", f"{config.current_session()}:{window_id}",
             "#{pane_current_command}"],
            capture_output=True, text=True, timeout=5,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def window_type(window_id: str, claude_running: bool | None = None) -> str:
    """Classify a window for the dashboard: 'claude' | 'server' | 'shell'.

    claude wins (an interactive claude session); else a dev-server heuristic on
    the pane's foreground command; else a plain shell. Best-effort — anything
    unknown degrades to 'shell'.
    """
    if claude_running is None:
        claude_running = claude_pid(window_id) is not None
    if claude_running:
        return "claude"
    cmd = pane_command(window_id).lower()
    if cmd and any(cmd.startswith(s) for s in _SERVER_COMMANDS):
        return "server"
    return "shell"


def stop_agent(agent_name: str) -> dict:
    """Stop a claude session by sending /exit. Returns result dict."""
    window_id = get_window_id(agent_name)
    if not window_id:
        return {"ok": False, "agent": agent_name, "detail": "no tmux window found"}

    if not is_claude_running(window_id):
        return {"ok": True, "agent": agent_name, "detail": "already stopped"}

    send_tmux(window_id, "/exit")

    # Poll up to 10s for claude to exit
    for _ in range(20):
        time.sleep(0.5)
        if not is_claude_running(window_id):
            log.info("Stopped agent %s (%s)", agent_name, window_id)
            return {"ok": True, "agent": agent_name, "detail": "stopped"}

    log.warning("Agent %s did not exit within 10s", agent_name)
    return {"ok": False, "agent": agent_name, "detail": "exit timed out after 10s"}


def _resolve_start_dir(agent_name: str) -> str | None:
    """Resolve the directory to launch claude in: the window's live cwd."""
    return get_window_cwd(agent_name)


def _name_window_to_cwd(window_id: str, start_dir: str) -> str | None:
    """Rename a tmux window to its cwd basename and lock it.

    A window running claude should reflect where it lives instead of a
    stale/persisted name or tmux's automatic-rename clobbering it to "claude".
    Adds a ``-N`` suffix on collision (excluding this window's own current
    name). Returns the applied name, or None if it couldn't be set.
    """
    base = Path(start_dir).name
    if not base:
        return None
    taken = {n for n, wid in get_all_windows().items() if wid != window_id}
    name, counter = base, 2
    while name in taken:
        name, counter = f"{base}-{counter}", counter + 1
    target = f"{config.current_session()}:{window_id}"
    try:
        subprocess.run(
            ["tmux", "rename-window", "-t", target, name],
            capture_output=True, text=True, timeout=5,
        )
        # Lock it so tmux automatic-rename doesn't overwrite with the command name.
        subprocess.run(
            ["tmux", "set-window-option", "-t", target, "allow-rename", "off"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("Failed to rename window %s to %s: %s", window_id, name, e)
        return None
    return name


def start_agent(agent_name: str, cmd: str | None = None) -> dict:
    """Start a claude session in the agent's directory."""
    window_id = get_window_id(agent_name)
    if not window_id:
        return {"ok": False, "agent": agent_name, "detail": "no tmux window found"}

    if is_claude_running(window_id):
        return {"ok": False, "agent": agent_name, "detail": "claude already running"}

    start_dir = _resolve_start_dir(agent_name)
    if not start_dir or not Path(start_dir).is_dir():
        return {"ok": False, "agent": agent_name, "detail": f"directory {start_dir} not found"}

    launch = cmd or DEFAULT_LAUNCH_CMD
    # Export CHELA_WID ahead of the launch so both the shell and the claude
    # process it spawns know this window's identity (self-peek / drive siblings).
    send_tmux(window_id, f"cd {start_dir} && {wid_env_prefix(window_id)}{launch}")

    # Name the window after its cwd when we spawn claude in it, so it stops
    # showing a stale/persisted name.
    renamed = _name_window_to_cwd(window_id, start_dir)

    log.info(
        "Started agent %s (%s) from %s%s",
        agent_name, window_id, start_dir, f"; window -> {renamed}" if renamed else "",
    )
    return {"ok": True, "agent": agent_name, "detail": "started"}


def reconcile_window_names() -> list[str]:
    """Rename windows running claude to their cwd basename.

    Periodic counterpart to the start_agent rename: catches claude sessions
    started by hand (not via the dashboard Start button), so an ad-hoc shell
    stops showing its shell-N / stale name once claude is running in it. Uses
    the live pane cwd (matches what `claude agents --json` reports) and locks
    the name with allow-rename off.

    Leaves alone: NEVER_MANAGE windows, panes not running claude, and windows
    whose name already matches their cwd. Collision-safe -N suffix. Returns a
    list of "old -> new" actions.
    """
    actions: list[str] = []
    try:
        out = subprocess.run(
            ["tmux", "list-windows", "-t", config.current_session(), "-F",
             "#{window_id}\t#{window_name}\t#{pane_current_command}\t#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("reconcile_window_names: tmux list-windows failed: %s", e)
        return actions

    rows = [tuple(line.split("\t")) for line in out.splitlines() if line.count("\t") == 3]
    live_names = {name: wid for wid, name, _cmd, _cwd in rows}

    for wid, name, cmd, cwd in rows:
        if name in NEVER_MANAGE or "claude" not in cmd:
            continue
        base = Path(cwd).name
        if not base or name == base:
            continue
        target_name, counter = base, 2
        while target_name in live_names and live_names[target_name] != wid:
            target_name, counter = f"{base}-{counter}", counter + 1
        if name == target_name:
            continue
        target = f"{config.current_session()}:{wid}"
        try:
            subprocess.run(["tmux", "rename-window", "-t", target, target_name],
                           capture_output=True, text=True, timeout=5)
            subprocess.run(["tmux", "set-window-option", "-t", target, "allow-rename", "off"],
                           capture_output=True, text=True, timeout=5)
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.warning("reconcile_window_names: rename %s failed: %s", wid, e)
            continue
        live_names.pop(name, None)
        live_names[target_name] = wid
        actions.append(f"{name} -> {target_name}")
    return actions


def restart_agent(agent_name: str) -> dict:
    """Stop then start an agent."""
    stop_result = stop_agent(agent_name)
    if not stop_result["ok"] and stop_result["detail"] != "already stopped":
        return stop_result
    return start_agent(agent_name)


def rediscover() -> dict[str, str | None]:
    """Re-read live tmux windows. Returns {agent: window_id} for all windows."""
    result = get_all_windows()
    log.info("Rediscovery: %s", result)
    return result
