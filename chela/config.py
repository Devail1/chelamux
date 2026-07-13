"""Configuration — **the environment is the single source of truth.**

Everything chela needs is discoverable from plain tmux; there is no external service to
point at. State lives under ``~/.chela`` (override with ``CHELA_DIR``).

One file, one authority: ``$CHELA_DIR/chela.env`` (``KEY=value`` lines) is *sourced* —
by :func:`load_env_file` here for anything importing chela, and by ``scripts/run-chela.sh``
for anything PM2 starts. A real environment variable always wins over the file, so an
override stays possible; what is NOT allowed any more is a second *place* config lives.
A PM2 ``env:`` block is exactly that second place, and it drifted: three copies of
``CHELA_TMUX_SESSION`` still said ``ccbot`` a day after the session was renamed.

The dashboard port is the case that proves the rule. It is baked into the Claude Code
hooks plugin as a literal (Claude Code does not expand env vars in a hook ``url``), and
``chela plugin`` renders that manifest from a **different process** than the dashboard —
so a port that only exists inside the dashboard's own process (``--port``) makes every
hook POST into a closed socket, silently. Hence :func:`publish_dashboard_port`: the
dashboard writes down the port it actually bound, and :func:`live_dashboard_port` — what
the plugin renders — reads it back. Nobody has to guess.
"""
import json
import os
import re
import subprocess
import time
from pathlib import Path

# Where chela keeps its own state (scheduler.db, dispatcher runs, context cache).
CHELA_DIR = Path(os.environ.get("CHELA_DIR", Path.home() / ".chela"))


# --- the env file: one place config lives -----------------------------------------

# What a shell will accept as a variable name. Anything else in the file is skipped
# rather than passed to os.environ, which raises on one (a truncated or binary file must
# not take the CLI down before it can tell you the file is broken).
_VALID_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

def env_file_path() -> Path | None:
    """``$CHELA_DIR/chela.env``, or ``$CHELA_ENV_FILE`` when set. ``CHELA_ENV_FILE=""``
    disables the file entirely (what the test suite does — a developer's real
    ``~/.chela/chela.env`` must never leak into a unit test)."""
    raw = os.environ.get("CHELA_ENV_FILE")
    if raw is None:
        return CHELA_DIR / "chela.env"
    raw = raw.strip()
    return Path(raw).expanduser() if raw else None


def parse_env_file(path: Path) -> dict[str, str]:
    """``KEY=value`` lines → a dict. Blank lines and ``#`` comments are skipped, a
    leading ``export`` and one layer of surrounding quotes are stripped — the subset of
    shell syntax ``set -a; . file`` and this parser agree on. Anything unreadable or
    unparseable is skipped, never raised: a typo in a config file must not stop the CLI
    from starting (``chela doctor`` is where it becomes visible)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not _VALID_KEY.fullmatch(key):
            continue        # not a variable name — a stray line, or a corrupt file
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        out[key] = value
    return out


def load_env_file(path: Path | None = None) -> dict[str, str]:
    """Source the env file into ``os.environ``; return what it declared.

    ``setdefault``, not assignment: an explicitly exported variable outranks the file
    (that is how a one-off ``CHELA_TMUX_SESSION=other chela status`` still works, and how
    ``scripts/run-chela.sh`` sourcing the same file first is a no-op rather than a
    conflict). Child processes inherit it, so a tmux-spawned agent sees the same config.
    """
    path = path if path is not None else env_file_path()
    if path is None:
        return {}
    declared = parse_env_file(path)
    for key, value in declared.items():
        os.environ.setdefault(key, value)
    return declared


ENV_FILE: Path | None = env_file_path()
# At import, before anything below reads os.environ — so a plain `chela status` in a bare
# shell is configured identically to the PM2 daemon, with nothing exported by hand.
ENV_FILE_VARS: dict[str, str] = load_env_file(ENV_FILE)

# The tmux session chela orchestrates. Each agent lives in its own window of
# this session, and the window name IS the agent's display name. Override with
# CHELA_TMUX_SESSION; defaults to "chela".
#
# TMUX_SESSION is the import-time value (env override, else "chela") — kept as an
# importable shim for callers that always run with CHELA_TMUX_SESSION set (the
# pm2 daemon/dashboard). New code should prefer current_session() below, which
# also makes a bare `chela peek/read` zero-config for an orchestrator agent.
TMUX_SESSION = os.environ.get("CHELA_TMUX_SESSION", "chela")


def current_session() -> str:
    """The tmux session chela operates on, resolved LAZILY at call time.

    Precedence:
      1. explicit ``$CHELA_TMUX_SESSION`` — an override always wins;
      2. else the caller's OWN pane's session, via ``$TMUX_PANE`` (tmux sets it
         in every pane) — so an orchestrator agent living in whatever session the
         fleet actually uses (e.g. ``myteam``) gets zero-config discovery, mirroring
         how ``orchestrator.self_wid()`` derives the window from the same pane;
      3. else ``"chela"``.

    Resolved per-call (never at import) so it can't cache a stale value, and so a
    process with no tmux pane still imports cleanly. Grouped sessions share one
    window list, so deriving a mirror (``webterm_myteam__*``) lists the same real
    windows as its parent.
    """
    env = os.environ.get("CHELA_TMUX_SESSION")
    if env:
        return env
    pane = os.environ.get("TMUX_PANE")
    if pane:
        try:
            r = subprocess.run(
                ["tmux", "display-message", "-p", "-t", pane, "#{session_name}"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                return r.stdout.strip()
        except (subprocess.TimeoutExpired, FileNotFoundError):
            pass
    return "chela"

# Window names to hide from discovery everywhere (dashboard, status, the ttyd
# supervisor). For placeholder / keep-alive windows that aren't agents — e.g. a
# pinned "__main__" remain-on-exit window kept so the tmux session survives when
# the last agent window exits; it's noise on the wall, not an agent.
# Comma-separated; default empty so a generic install shows every window.
IGNORE_WINDOWS = {
    w.strip() for w in os.environ.get("CHELA_IGNORE_WINDOWS", "").split(",") if w.strip()
}

DEFAULT_DASHBOARD_PORT = 5001


def dashboard_host() -> str:
    return os.environ.get("CHELA_DASH_HOST", "127.0.0.1")


def dashboard_port() -> int:
    """The port the dashboard is CONFIGURED to bind — ``CHELA_DASHBOARD_PORT`` (which the
    env file supplies), else the default.

    This is what the dashboard binds. It is *not* necessarily what it is listening on:
    ``chela dashboard --port`` can override it for a one-off run, and then the flag —
    not the env — is the truth. Anything that has to *reach* the dashboard from another
    process (the hooks plugin, above all) must ask :func:`live_dashboard_port`.

    Resolved per call, never at import: ``--port`` sets the env var after this module is
    imported, and a cached constant would ignore it.
    """
    try:
        return int(os.environ.get("CHELA_DASHBOARD_PORT", DEFAULT_DASHBOARD_PORT))
    except ValueError:
        return DEFAULT_DASHBOARD_PORT


def dashboard_port_file() -> Path:
    """Where the dashboard writes down the port it actually bound."""
    return CHELA_DIR / "dashboard.port"


def publish_dashboard_port(port: int, host: str = "127.0.0.1") -> None:
    """Record the port the dashboard REALLY bound, for every other process to read.

    Best-effort by design: a dashboard that cannot write this file still serves. What it
    loses is the guarantee that ``chela plugin`` renders a reachable URL — which
    ``chela doctor`` then reports, loudly, rather than the feature failing in silence.
    """
    try:
        CHELA_DIR.mkdir(parents=True, exist_ok=True)
        dashboard_port_file().write_text(
            json.dumps({"port": int(port), "host": host, "pid": os.getpid(),
                        "ts": time.time()}) + "\n",
            encoding="utf-8",
        )
    except OSError:
        pass


def clear_dashboard_port() -> None:
    """Drop the published port on a clean shutdown (a crash leaves it; the pid check
    below is what makes a stale file harmless either way)."""
    try:
        dashboard_port_file().unlink()
    except OSError:
        pass


def live_dashboard() -> dict | None:
    """What the running dashboard published, or None if none is running.

    A file whose ``pid`` is gone is stale — the dashboard died — and is treated as no
    dashboard at all, so a crashed instance can't keep pointing hooks at a closed socket.
    """
    try:
        data = json.loads(dashboard_port_file().read_text(encoding="utf-8"))
        port = int(data["port"])
        pid = int(data.get("pid") or 0)
    except (OSError, ValueError, TypeError, KeyError):
        return None
    if pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return None
        except PermissionError:
            pass                     # alive, owned by someone else — still listening
        except OSError:
            return None
    return {"port": port, "host": str(data.get("host") or "127.0.0.1"), "pid": pid}


def live_dashboard_port() -> int:
    """The port the dashboard is ACTUALLY listening on — what a hook must POST to.

    Prefers what the running dashboard published over what the config says, because a
    ``--port`` flag makes those two differ and the *plugin* has to be right regardless.
    Falls back to the configured port when no dashboard is running (rendering a plugin
    before starting one is legitimate). ``chela doctor`` flags the disagreement.
    """
    live = live_dashboard()
    return live["port"] if live else dashboard_port()


# Context-window status-line cache, written by scripts/cache-statusline.sh after
# each assistant turn. The daemon reads these to track per-agent context usage.
CONTEXT_CACHE_DIR = CHELA_DIR / "context"
CACHE_STALE_SECONDS = int(os.environ.get("CHELA_CACHE_STALE_SECONDS", "7200"))  # skip files older than 2h

# Daemon loop intervals (seconds).
SCHEDULER_POLL_INTERVAL = int(os.environ.get("CHELA_SCHEDULER_POLL_INTERVAL", "30"))

# Work-item dispatcher inside the daemon. Colon-separated list of WORKFLOW.md
# paths (~ and $VAR are expanded). Empty = dispatcher off in the daemon; the
# `chela dispatch <workflow>` CLI still works regardless.
DISPATCH_TICK_INTERVAL = int(os.environ.get("CHELA_DISPATCH_TICK_INTERVAL", "60"))
_dispatch_raw = os.environ.get("CHELA_DISPATCH_WORKFLOWS", "")
DISPATCH_WORKFLOWS = [
    Path(os.path.expandvars(os.path.expanduser(p))).resolve()
    for p in _dispatch_raw.split(":") if p.strip()
]

# Needs-input notification: when an agent's pane enters the `waiting` state
# (blocked on a permission prompt or a question), fire a notification once per
# edge. Empty = off. The kind is auto-detected from the URL (ntfy / Telegram /
# generic webhook); override with CHELA_NOTIFY_KIND. CHELA_NOTIFY_INTERVAL is
# how often (seconds) the daemon scans pane states.
NOTIFY_URL = os.environ.get("CHELA_NOTIFY_URL", "").strip()
NOTIFY_KIND = os.environ.get("CHELA_NOTIFY_KIND", "").strip().lower()  # "", ntfy, telegram, webhook
NOTIFY_TITLE = os.environ.get("CHELA_NOTIFY_TITLE", "chela: agent needs input")
NOTIFY_INTERVAL = int(os.environ.get("CHELA_NOTIFY_INTERVAL", "20"))

# Outbound Telegram relay: post every tool_use/tool_result event as its own
# message (🔧 Bash / ✅ Bash result). That's a firehose on a phone, so it is OFF
# by default — the relay then sends only text/thinking/user turns plus the
# interactive prompts that need a human (AskUserQuestion / ExitPlanMode). Set
# CHELA_SHOW_TOOL_CALLS=true for the full stream. Ported from ccbot's
# CCBOT_SHOW_TOOL_CALLS (which defaulted ON).
SHOW_TOOL_CALLS = os.environ.get("CHELA_SHOW_TOOL_CALLS", "false").strip().lower() not in (
    "false", "0", "no", "off",
)

# Embedded ttyd terminal wall on/off (read by the dashboard and the ttyd
# supervisor in scripts/agent-terminals.sh). The wall — the flagship feature —
# is ON by default, but it serves writable shells, so the dashboard gates it on
# the bind host: a loopback bind (the documented model — fronted by a tailnet /
# SSH tunnel) serves the wall; a non-loopback bind refuses to unless you opt in
# with CHELA_TERMINALS_EXPOSE=true. Set CHELA_TERMINALS_ENABLED=false to turn
# the wall off entirely.
TERMINALS_ENABLED = os.environ.get("CHELA_TERMINALS_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")

# Decisions inbox (chela/inbox.py): push agent/run events into the orchestrator's
# session when it is idle, so a finished agent stops being invisible to it. Inert
# until a session registers itself as the orchestrator (`chela watch`), so this
# defaults ON safely; set CHELA_INBOX_ENABLED=false to disable it outright.
INBOX_ENABLED = os.environ.get("CHELA_INBOX_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")

# Explicit opt-in to serve the writable terminal wall on a NON-loopback bind
# (e.g. --host 0.0.0.0 or a LAN/tailnet IP). Off by default: a public bind would
# otherwise hand out unauthenticated remote shells (RCE). Loopback binds, fronted
# by a tailnet or SSH tunnel (the recommended setup), never need this.
TERMINALS_EXPOSE = os.environ.get("CHELA_TERMINALS_EXPOSE", "false").strip().lower() not in ("false", "0", "no", "off")

# Collaborative-terminal presence (P3 agent-as-peer). When on, the dashboard
# publishes a Yjs *awareness* frame per running-claude window into that window's
# relay room, so a browser viewing a *shared* /term/<wid>/ sees a "claude" pill
# for the live agent. Purely additive and opaque: it only reaches browsers that
# have joined the room, i.e. a window the host has shared.
#
# COLLAB_RELAY is the dumb fan-out relay (chela/collab-relay). It defaults to
# EMPTY: presence is OFF until you point CHELA_COLLAB_RELAY at a relay you own, so
# the repo ships no phone-home to anyone's personal infrastructure. Self-hosting
# is a one-liner: `wrangler deploy` in chela/collab-relay/ (free plan), then export
# the printed wss:// URL. With no relay, collab.start() no-ops and the injected
# shim disables presence.js. CHELA_COLLAB=false also stops it.
COLLAB_PRESENCE = os.environ.get("CHELA_COLLAB", "true").strip().lower() not in ("false", "0", "no", "off")
COLLAB_RELAY = os.environ.get("CHELA_COLLAB_RELAY", "").strip().rstrip("/")

# Shared fixed grid (cols x rows) that a shared terminal snaps to when 2+ human
# peers are present (see the adaptive sizing in static/collab/presence.js): while
# collaborating, every viewer sees an identical, complete grid (tmux window-size
# manual) and letterbox-scales it to their viewport; solo, the pane fits the
# viewport dynamically as usual.
TERM_COLS = int(os.environ.get("CHELA_TERM_COLS", "120"))
TERM_ROWS = int(os.environ.get("CHELA_TERM_ROWS", "30"))


def is_loopback_host(host: str) -> bool:
    """True when the dashboard bind host is the local loopback (the safe case
    for serving the writable terminal wall)."""
    return (host or "").strip().lower() in ("127.0.0.1", "::1", "localhost", "")
