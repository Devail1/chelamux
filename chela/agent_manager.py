"""Agent lifecycle management — start, stop, restart claude sessions in tmux."""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import threading
import time
from pathlib import Path

from chela import config
from chela.discovery import get_window_id, get_window_cwd, get_all_windows, get_windows_by_id
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

# Names nobody chose — a placeholder chela handed out (shell-N, the session
# anchor) or one tmux's automatic-rename derived from the running command. A
# window still carrying one of these has no intentional name, so chela is free to
# fill it in from the cwd. ANYTHING ELSE IS A DELIBERATE NAME AND IS NEVER TOUCHED:
# that is what makes a user rename stick (see is_generic_name).
_GENERIC_SHELL_RE = re.compile(r"^shell(-\d+)?$", re.IGNORECASE)
GENERIC_NAMES = {"bash", "zsh", "sh", "fish", "claude", "node", "python", "python3"}


def is_generic_name(name: str) -> bool:
    """True if ``name`` is a placeholder chela may auto-manage, not a chosen name.

    The whole rename story hangs off this predicate. The tmux window name is the
    single source of truth for an agent's display name — wall panes, agent cards,
    nav and the bound Telegram topic all read it — so the auto-namers must FILL IN
    BLANKS AND NEVER OVERRIDE INTENT. A generic name (``shell-3``, or tmux's
    command-follow ``claude``/``bash``) is a blank; anything else was chosen by a
    human and is left alone.

    Deriving intent from the name itself, rather than persisting a set of
    "user-pinned" window ids, is deliberate: a sidecar pin file is a SECOND source
    of truth (the very split-brain this replaces), it has to be pruned as windows
    die, and — decisively — tmux REUSES window ids after a server restart (@0 comes
    back), so a stale pin silently attaches to an unrelated new window. This needs
    no state, survives restarts, and keeps tmux authoritative.
    """
    n = (name or "").strip()
    if not n:
        return True
    return bool(_GENERIC_SHELL_RE.match(n)) or n.lower() in GENERIC_NAMES


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
#
# `claude agents --json` is a heavyweight process (~165 MB resident). The
# dashboard is hit in bursts — multiple browser tabs, each with a 30s refresh, a
# 4s terminals tick, and SSE deltas that trigger /api/agents — and Flask serves
# those concurrently. A plain TTL is not enough on its own: the cache timestamp
# is only stamped once the subprocess RETURNS, so a burst that arrives during the
# ~1s the command runs all miss the cache and each spawn their own (measured 8
# stacked processes, ~1.3 GB transient). So two guards work together:
#   * TTL — a fresh cache satisfies callers with no subprocess at all.
#   * single-flight — a module lock ensures exactly ONE `claude agents --json`
#     runs at a time; concurrent callers block, then find the cache refreshed by
#     the winner and return it instead of spawning their own.
_STATUS_TTL = 2.0
_STATUS_CMD_TIMEOUT = 10.0  # a hung `claude agents --json` must not stack forever
_status_cache: dict = {"ts": 0.0, "by_pid": {}, "by_cwd": {}, "cwd_by_pid": {}}
_status_lock = threading.Lock()


def session_status_map(force: bool = False) -> dict:
    """Maps from `claude agents --json`: by_pid {pid: status},
    by_cwd {cwd: status}, cwd_by_pid {pid: cwd}.

    Cached for :data:`_STATUS_TTL` s and single-flighted: a burst of concurrent
    callers collapses to ONE subprocess. ``force`` skips the TTL fast path (the
    caller needs fresh data) but still coalesces — if the winner refreshed the
    cache while ``force`` waited on the lock, it returns that instead of spawning
    a second command.
    """
    entered = time.time()
    # Fast path: a recent cache satisfies everyone without touching the lock.
    if not force and entered - _status_cache["ts"] < _STATUS_TTL:
        return _status_cache
    with _status_lock:
        # A concurrent caller may have refreshed the cache while we waited for the
        # lock; if it is now newer than when we entered, that result is fresh
        # enough for us too — return it rather than spawn a second subprocess.
        if _status_cache["ts"] >= entered:
            return _status_cache
        _refresh_status_locked()
    return _status_cache


def _refresh_status_locked() -> None:
    """Run `claude agents --json` once and update the cache. Holds ``_status_lock``.

    On any failure the last-good maps are kept (a transient timeout must not blank
    every status pill) and only the timestamp is bumped, so we back off for a TTL
    instead of retry-storming. The completion time — not the caller's entry time —
    is stamped so every caller that entered while the command ran coalesces onto
    this result.
    """
    by_pid, by_cwd, cwd_by_pid = {}, {}, {}
    try:
        r = subprocess.run(
            ["claude", "agents", "--json"],
            capture_output=True, text=True, timeout=_STATUS_CMD_TIMEOUT,
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
            _status_cache.update(
                ts=time.time(), by_pid=by_pid, by_cwd=by_cwd, cwd_by_pid=cwd_by_pid
            )
            return
        log.warning(
            "claude agents --json exited %s; keeping last status cache", r.returncode
        )
    except subprocess.TimeoutExpired:
        log.warning(
            "claude agents --json timed out after %ss; keeping last status cache",
            _STATUS_CMD_TIMEOUT,
        )
    except (ValueError, json.JSONDecodeError) as e:
        log.warning("claude agents --json unparseable (%s); keeping last status cache", e)
    except Exception:
        log.exception("claude agents --json failed; keeping last status cache")
    # Stale-but-safe: preserve the last good maps, just back off for a TTL.
    _status_cache["ts"] = time.time()


def status_by_wid() -> dict[str, str]:
    """``{window_id: busy|idle|waiting}`` for every live window running claude.

    The window-keyed view of :func:`session_status_map` (which is pid-keyed, because
    that is what ``claude agents --json`` reports). Windows with no claude session are
    absent — they have no status, which is NOT the same as being idle. One authority
    for busy/idle/waiting, shared by the decisions inbox and anything else that needs
    it; never add a second source.
    """
    by_pid = session_status_map().get("by_pid", {})
    out: dict[str, str] = {}
    for wid in get_windows_by_id():
        pid = claude_pid(wid)
        status = by_pid.get(pid) if pid is not None else None
        if status:
            out[wid] = status
    return out


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


def lock_window_name(target: str) -> None:
    """Pin a managed window's name against BOTH of tmux's rename mechanisms.

    Two independent tmux features can overwrite a window's name, and locking one
    is not enough:

    * ``allow-rename`` — an application (claude) renaming the window via an OSC
      title escape.
    * ``automatic-rename`` — tmux following ``pane_current_command``, so the name
      flips to ``git`` / ``node`` / ``bash`` the instant claude shells out to a
      subcommand, then back when it returns. That is the dashboard tile "name
      flicker".

    ``rename-window`` / ``new-window -n`` disable ``automatic-rename`` as a side
    effect, so a window WE named is already safe. But a window that reaches us
    already-correctly-named (e.g. hand-started, never renamed by us) keeps the
    global default (``automatic-rename on``) and flickers on every subcommand.
    Setting BOTH options off explicitly makes the lock hold no matter how the
    window got its name. ``target`` is any tmux window ref (id or session:name);
    idempotent and best-effort — a tmux failure is logged, never raised.
    """
    for option in ("allow-rename", "automatic-rename"):
        try:
            subprocess.run(
                ["tmux", "set-window-option", "-t", target, option, "off"],
                capture_output=True, text=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            log.warning("Failed to lock %s on window %s: %s", option, target, e)


def _name_window_to_cwd(window_id: str, start_dir: str) -> str | None:
    """Rename a tmux window to its cwd basename and lock it.

    A window running claude should reflect where it lives instead of a
    stale/persisted name or tmux's automatic-rename clobbering it to "claude".
    Adds a ``-N`` suffix on collision (excluding this window's own current
    name). Returns the applied name, or None if it couldn't be set.

    Same rule as the reconciler: this fills in a blank, it never overrides intent.
    Starting claude in a window a human deliberately named ("billing-fix") keeps
    that name — only a generic ``shell-N``/command-follow name gets replaced.
    """
    base = Path(start_dir).name
    if not base:
        return None
    live = get_all_windows()
    current = next((n for n, wid in live.items() if wid == window_id), None)
    if current is not None and not is_generic_name(current):
        return None
    taken = {n for n, wid in live.items() if wid != window_id}
    name, counter = base, 2
    while name in taken:
        name, counter = f"{base}-{counter}", counter + 1
    target = f"{config.current_session()}:{window_id}"
    try:
        subprocess.run(
            ["tmux", "rename-window", "-t", target, name],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("Failed to rename window %s to %s: %s", window_id, name, e)
        return None
    # Lock against both allow-rename (OSC) and automatic-rename (command follow).
    lock_window_name(target)
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
    the live pane cwd (matches what `claude agents --json` reports).

    It also (re)asserts the name lock — ``allow-rename off`` AND
    ``automatic-rename off`` — on every managed claude window, INCLUDING one
    whose name already matches its cwd. That already-correct case used to be
    skipped before any lock ran, so a hand-started window kept tmux's default
    ``automatic-rename on`` and its tile name flickered to the subcommand name
    (git/node) on every shell-out. The lock is applied only when a window still
    has a rename mechanism live, so steady-state ticks touch tmux for nothing.

    Leaves alone: NEVER_MANAGE windows and panes not running claude.
    Collision-safe -N suffix. Returns a list of "old -> new" rename actions.
    """
    actions: list[str] = []
    try:
        out = subprocess.run(
            ["tmux", "list-windows", "-t", config.current_session(), "-F",
             "#{window_id}\t#{window_name}\t#{pane_current_command}\t"
             "#{pane_current_path}\t#{automatic-rename}\t#{allow-rename}"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("reconcile_window_names: tmux list-windows failed: %s", e)
        return actions

    rows = [tuple(line.split("\t")) for line in out.splitlines() if line.count("\t") == 5]
    live_names = {name: wid for wid, name, *_rest in rows}

    for wid, name, cmd, cwd, auto, allow in rows:
        if name in NEVER_MANAGE or "claude" not in cmd:
            continue
        base = Path(cwd).name
        if not base:
            continue
        target = f"{config.current_session()}:{wid}"
        # Assert the lock on any managed claude window whose name could still be
        # clobbered — an already-correctly-named-but-unlocked window is exactly
        # the flicker case. "0" is tmux's off; anything else (on/unset) needs it.
        if auto != "0" or allow != "0":
            lock_window_name(target)
        # Only ever FILL IN A BLANK. A DELIBERATE name — one a human chose, via the
        # dashboard rename or `tmux rename-window` — is left alone. Without this,
        # every 30s tick renamed it straight back to the cwd basename, so a rename
        # appeared to work and then silently reverted; the tmux name could never be
        # the source of truth it now is.
        #
        # "Deliberate" is read off tmux itself rather than guessed from the string:
        # a LOCKED name (automatic-rename off, `auto == "0"`) was set explicitly —
        # both `rename-window` and our rename endpoint turn that option off — while
        # a window with automatic-rename still ON is merely wearing whatever tmux
        # last derived from its running command. That's what keeps a command-drifted
        # name ("git", "node", any binary at all) auto-correctable without having to
        # enumerate every command name on earth, while still protecting a chosen one.
        # A generic name never counts as deliberate even when locked: `shell-3` is a
        # placeholder we handed out, not a choice.
        if auto == "0" and not is_generic_name(name):
            continue
        if name == base:
            continue
        target_name, counter = base, 2
        while target_name in live_names and live_names[target_name] != wid:
            target_name, counter = f"{base}-{counter}", counter + 1
        if name == target_name:
            continue
        try:
            subprocess.run(["tmux", "rename-window", "-t", target, target_name],
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
