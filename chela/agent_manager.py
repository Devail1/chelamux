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
# CMX-179: both knobs below are config-backed (chela/config.py) so an operator can tune
# them without a code change; the module-level names here are what the rest of this file
# and the test suite read, resolved once at import time.
#
# `claude agents --json` cold-starts around 12s and warm-starts 17-18s on the dogfood box
# (measured 2026-07-26: 18.40 / 17.74 / 17.28s over three consecutive runs) — the cost is
# CLI STARTUP, not payload (this box's fleet is 4 entries), so a bigger fleet does not make
# it worse and trimming the query would not help. The old 10.0s timeout was BELOW the
# warm-start floor, so every single call timed out — silently, from 2026-07-14 17:00 to
# 2026-07-26: 17,411 identical "timed out" warnings in the dashboard error log, ~250/hour
# with no gap in 83 consecutive hours, and nobody noticed because nothing but a log line
# said so. Give real headroom above the measured worst case — the default MUST stay >= 45s
# (tests/test_agent_manager_status_cache.py guards this floor directly).
_STATUS_CMD_TIMEOUT = config.STATUS_CMD_TIMEOUT_S
# How long a successful refresh is trusted before :func:`start_background_refresh` asks
# again. This is NOT how long a request blocks — session_status_map() without force=True
# only ever reads the cache; the periodic background thread is what pays the subprocess
# cost, off the request path (see start_background_refresh's docstring).
_STATUS_TTL = config.STATUS_TTL_S
# A single timeout can be a blip (a slow disk, a contended box) and is not worth raising
# one's voice over — but a feed that has been down this long is a REGRESSION, and the
# ordinary per-call WARNING (routine, and easy to lose in a flood of identical lines, which
# is exactly how this went unnoticed for 12 days) is not loud enough. `_refresh_status_locked`
# escalates once, at the ERROR level, the moment an outage crosses this age — and logs the
# recovery too, so both edges of the episode are visible instead of just its interior.
_STATUS_SUSTAINED_FAILURE_S = 120.0
# The per-call WARNING is throttled to at most once per this window while an outage
# continues (plus always once on the FIRST failure of a new episode, and once more on the
# next failure after a recovery) — 17,411 identical lines in 12 days is the flood this
# exists to stop. ⛔ Throttle it, do not silence it to DEBUG (CMX-179 objective 4).
_STATUS_WARN_THROTTLE_S = 300.0
_status_cache: dict = {
    "ts": 0.0, "by_pid": {}, "by_cwd": {}, "cwd_by_pid": {},
    # session_by_pid / started_by_pid: the `sessionId` / `startedAt` the feed reports
    # alongside `status` and `cwd` for the same pid — CMX-184. `started_by_pid` is
    # converted to epoch SECONDS (the feed reports epoch milliseconds) so callers
    # compare like units against /proc-derived timestamps without doing the /1000
    # themselves.
    "session_by_pid": {}, "started_by_pid": {},
    # down_since: wall-clock time of the FIRST failure in the current outage episode, or
    # None while healthy. escalated: whether this episode already fired its one ERROR log
    # (so a long outage does not re-log ERROR on every failed poll).
    "down_since": None, "escalated": False,
    # last_success_ts: wall-clock time of the last SUCCESSFUL refresh, or 0.0 if there has
    # never been one. This is what makes a cold cache (never populated) distinguishable
    # from a healthy-but-quiet one — down_since being None only means "no recorded
    # failure", which a cache that has never been asked also satisfies trivially.
    "last_success_ts": 0.0,
    # last_warning_ts: wall-clock time of the last per-call WARNING actually emitted (for
    # the throttle above).
    "last_warning_ts": 0.0,
}
_status_lock = threading.Lock()


def session_and_start_for_pid(pid: int | None) -> tuple[str | None, float | None]:
    """The ``(sessionId, startedAt)`` the live `claude agents --json` feed last reported
    for ``pid`` — CMX-184, the tier :mod:`chela.sessions` needed and this cache was
    already throwing away.

    A pure read of whatever :func:`start_background_refresh`'s timer (or the last
    ``force=True`` caller) put in the cache — it never itself refreshes the cache or
    spawns the subprocess, so a caller on a tight budget (:func:`chela.sessions.resolve_window`
    runs on the hook path, blocked-agent-in-the-loop) can use it without adding a
    subprocess call of its own. ``startedAt`` is converted to epoch SECONDS to match
    :mod:`chela.sessions`' /proc-derived timestamps.

    ``(None, None)`` for an unknown or absent pid, or a cold cache that has never been
    populated — never a guess.
    """
    if pid is None:
        return None, None
    return (_status_cache["session_by_pid"].get(pid),
            _status_cache["started_by_pid"].get(pid))


def native_status_health() -> dict:
    """Whether `claude agents --json` is currently answering, from this process's cache.

    Read-only view of the cache's own failure bookkeeping — no subprocess call. Reflects
    only what THIS process has observed; a separate process (like a `chela doctor` CLI
    invocation) has its own empty cache and must ask :func:`probe_native_status_feed`
    instead, which asks the command directly.

    ``ok`` requires BOTH "no recorded outage" AND "at least one successful fetch" — a cold
    cache (nothing has ever answered) must never read as healthy just because it has not
    yet recorded a failure either. That gap was the actual CMX-179 bug: an empty status map
    and a genuinely all-idle fleet rendered identically because ``ok`` only checked the
    former condition.
    """
    down_since = _status_cache.get("down_since")
    last_success_ts = _status_cache.get("last_success_ts", 0.0)
    return {
        "ok": down_since is None and last_success_ts > 0.0,
        "down_since": down_since,
        "down_for_s": (time.time() - down_since) if down_since is not None else 0.0,
        "last_fetch_ts": _status_cache["ts"],
        "last_success_ts": last_success_ts,
    }


def probe_native_status_feed() -> tuple[bool, str]:
    """Force a fresh `claude agents --json` call, right now, and report whether it actually
    answered — (ok, detail). Bypasses the TTL fast path so a caller that specifically wants
    to know "is the feed alive RIGHT NOW" (``chela doctor``) is not satisfied by a stale
    healthy cache from before an outage started.
    """
    with _status_lock:
        return _refresh_status_locked()


def start_background_refresh(
    interval: float | None = None, stop_event: threading.Event | None = None
) -> threading.Thread:
    """Keep the status cache warm OFF the request path (CMX-179 objective 2).

    Runs a fresh `claude agents --json` on its own daemon thread every ``interval``
    seconds (:data:`_STATUS_TTL` by default) so an ordinary ``session_status_map()`` call
    almost never pays the up-to-:data:`_STATUS_CMD_TIMEOUT`-second subprocess cost itself —
    it just reads whatever this thread last put in the cache. ``force=True`` callers (the
    dispatcher, ``chela doctor``) are unaffected: they still block for a fresh answer.

    Call once, from the process that actually serves requests (the dashboard's ``main()``)
    — never at import time, and never in tests, which stub the subprocess seam and must
    not have a stray thread calling the real `claude` binary on a timer.
    """
    wait_s = _STATUS_TTL if interval is None else interval
    stop_event = stop_event if stop_event is not None else threading.Event()

    def _loop():
        while not stop_event.is_set():
            try:
                probe_native_status_feed()
            except Exception:
                log.exception("native status background refresh failed")
            stop_event.wait(wait_s)

    t = threading.Thread(target=_loop, name="chela-native-status-refresh", daemon=True)
    t.start()
    return t


def session_status_map(force: bool = False) -> dict:
    """Maps from `claude agents --json`: by_pid {pid: status},
    by_cwd {cwd: status}, cwd_by_pid {pid: cwd}.

    ``by_cwd`` omits any cwd shared by two or more live pids that disagree on
    status (an unresolved/``None`` status counts as disagreement) — a cwd is
    not a session id, and guessing a status for it is the exact honesty gap
    docs/AGENT_IDENTITY.md's slice 1 closes. A cwd held by exactly one pid is
    unaffected.

    Cached for :data:`_STATUS_TTL` s and single-flighted: a burst of concurrent
    callers collapses to ONE subprocess. ``force`` skips the TTL fast path (the
    caller needs fresh data) but still coalesces — if the winner refreshed the
    cache while ``force`` waited on the lock, it returns that instead of spawning
    a second command.

    A non-``force`` caller NEVER blocks on the lock: :func:`start_background_refresh`
    holds it for up to :data:`_STATUS_CMD_TIMEOUT` seconds while refreshing off the
    request path, and a request arriving mid-refresh must not join that wait (the
    brief's request-path ceiling must not grow past what it was before that thread
    existed). It tries the lock; on contention it serves the cache as-is — stale by
    at most one refresh cycle, but never blocking. ``force`` callers (the dispatcher,
    ``chela doctor``) still block for a fresh answer, same as always.
    """
    entered = time.time()
    # Fast path: a recent cache satisfies everyone without touching the lock.
    if not force and entered - _status_cache["ts"] < _STATUS_TTL:
        return _status_cache
    if not force:
        if not _status_lock.acquire(blocking=False):
            return _status_cache
        try:
            if _status_cache["ts"] >= entered:
                return _status_cache
            _refresh_status_locked()
        finally:
            _status_lock.release()
        return _status_cache
    with _status_lock:
        # A concurrent caller may have refreshed the cache while we waited for the
        # lock; if it is now newer than when we entered, that result is fresh
        # enough for us too — return it rather than spawn a second subprocess.
        if _status_cache["ts"] >= entered:
            return _status_cache
        _refresh_status_locked()
    return _status_cache


def _refresh_status_locked() -> tuple[bool, str]:
    """Run `claude agents --json` once and update the cache. Holds ``_status_lock``.

    On any failure the last-good maps are kept (a transient timeout must not blank
    every status pill) and only the timestamp is bumped, so we back off for a TTL
    instead of retry-storming. The completion time — not the caller's entry time —
    is stamped so every caller that entered while the command ran coalesces onto
    this result.

    Returns ``(ok, detail)`` — ``detail`` is a human-readable outcome for callers (like
    :func:`probe_native_status_feed`) that report it further, not just log it.
    """
    by_pid, by_cwd, cwd_by_pid = {}, {}, {}
    session_by_pid, started_by_pid = {}, {}
    cwd_statuses: dict[str, list] = {}
    try:
        r = subprocess.run(
            ["claude", "agents", "--json"],
            capture_output=True, text=True, timeout=_STATUS_CMD_TIMEOUT,
        )
        if r.returncode == 0:
            for s in json.loads(r.stdout or "[]"):
                st = s.get("status")
                pid, cwd = s.get("pid"), s.get("cwd")
                sid, started_ms = s.get("sessionId"), s.get("startedAt")
                if pid is not None:
                    pid = int(pid)
                    by_pid[pid] = st
                    if cwd:
                        cwd_by_pid[pid] = cwd
                    if isinstance(sid, str) and sid:
                        session_by_pid[pid] = sid
                    if isinstance(started_ms, (int, float)):
                        started_by_pid[pid] = started_ms / 1000.0
                if cwd:
                    cwd_statuses.setdefault(cwd, []).append(st)
            # A cwd is not a session id (docs/AGENT_IDENTITY.md) — every live pid
            # sharing one must AGREE before it earns a status, and an unresolved
            # (`None`) status counts as disagreement, same as a real mismatch.
            # Otherwise the last pid processed silently overwrites an earlier
            # one's status: a confidently wrong answer, worse than an omitted key.
            for cwd, statuses in cwd_statuses.items():
                if len(statuses) == 1:
                    by_cwd[cwd] = statuses[0]
                elif len(set(statuses)) == 1 and statuses[0] is not None:
                    by_cwd[cwd] = statuses[0]
            _note_recovery()
            _status_cache.update(
                ts=time.time(), by_pid=by_pid, by_cwd=by_cwd, cwd_by_pid=cwd_by_pid,
                session_by_pid=session_by_pid, started_by_pid=started_by_pid,
            )
            return True, "ok"
        detail = f"exited {r.returncode}"
        _note_failure(f"claude agents --json {detail}; keeping last status cache")
    except subprocess.TimeoutExpired:
        detail = f"timed out after {_STATUS_CMD_TIMEOUT}s"
        _note_failure(f"claude agents --json {detail}; keeping last status cache")
    except (ValueError, json.JSONDecodeError) as e:
        detail = f"unparseable ({e})"
        _note_failure(f"claude agents --json {detail}; keeping last status cache")
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        now = time.time()
        if _should_log_warning(now):
            log.exception("claude agents --json failed; keeping last status cache")
        _note_failure_ts(now)
    # Stale-but-safe: preserve the last good maps, just back off for a TTL.
    _status_cache["ts"] = time.time()
    return False, detail


def _note_recovery() -> None:
    """Called on EVERY successful refresh. Logs (once) if the feed had been down, and
    always advances ``last_success_ts`` — the field :func:`native_status_health` uses to
    tell "never asked" apart from "asked and healthy" (see its docstring)."""
    down_since = _status_cache.get("down_since")
    if down_since is not None:
        log.warning(
            "claude agents --json recovered after %.0fs down", time.time() - down_since
        )
    _status_cache["down_since"] = None
    _status_cache["escalated"] = False
    _status_cache["last_success_ts"] = time.time()


def _should_log_warning(now: float) -> bool:
    """True on the FIRST failure of a new outage episode, or every
    :data:`_STATUS_WARN_THROTTLE_S` while it continues — throttles the per-call WARNING
    (17,411 identical lines over 12 days is exactly the flood this exists to stop) without
    silencing it to DEBUG. A side effect on ``True``: stamps ``last_warning_ts`` so the next
    call measures the window from here, not from the episode's start."""
    is_new_episode = _status_cache.get("down_since") is None
    last_warned = _status_cache.get("last_warning_ts", 0.0)
    if is_new_episode or now - last_warned >= _STATUS_WARN_THROTTLE_S:
        _status_cache["last_warning_ts"] = now
        return True
    return False


def _note_failure(warning: str) -> None:
    """Log the per-call warning (throttled — see :func:`_should_log_warning`), and escalate
    once to ERROR if this outage has crossed :data:`_STATUS_SUSTAINED_FAILURE_S` — see that
    constant's comment for why a per-call WARNING alone was not loud enough to be noticed."""
    now = time.time()
    if _should_log_warning(now):
        log.warning(warning)
    _note_failure_ts(now)


def _note_failure_ts(now: float | None = None) -> None:
    now = time.time() if now is None else now
    if _status_cache.get("down_since") is None:
        _status_cache["down_since"] = now
    down_since = _status_cache["down_since"]
    if not _status_cache.get("escalated") and now - down_since >= _STATUS_SUSTAINED_FAILURE_S:
        log.error(
            "claude agents --json has been failing for %.0fs (down_since=%.0f) — native "
            "busy/idle status is stale fleet-wide, not just for one window",
            now - down_since, down_since,
        )
        _status_cache["escalated"] = True


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
