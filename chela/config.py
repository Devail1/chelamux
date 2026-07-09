"""Configuration — environment variables with sensible defaults.

Everything chela needs is discoverable from plain tmux; there is no external
service to point at. State lives under ``~/.chela`` (override with ``CHELA_DIR``).
"""
import os
from pathlib import Path

# Where chela keeps its own state (scheduler.db, dispatcher runs, context cache).
CHELA_DIR = Path(os.environ.get("CHELA_DIR", Path.home() / ".chela"))

# The tmux session chela orchestrates. Each agent lives in its own window of
# this session, and the window name IS the agent's display name. Override with
# CHELA_TMUX_SESSION; defaults to "chela".
TMUX_SESSION = os.environ.get("CHELA_TMUX_SESSION", "chela")

# Window names to hide from discovery everywhere (dashboard, status, the ttyd
# supervisor). For placeholder / keep-alive windows that aren't agents — e.g. a
# pinned "__main__" remain-on-exit window kept so the tmux session survives when
# the last agent window exits; it's noise on the wall, not an agent.
# Comma-separated; default empty so a generic install shows every window.
IGNORE_WINDOWS = {
    w.strip() for w in os.environ.get("CHELA_IGNORE_WINDOWS", "").split(",") if w.strip()
}

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

# Embedded ttyd terminal wall on/off (read by the dashboard and the ttyd
# supervisor in scripts/agent-terminals.sh). The wall — the flagship feature —
# is ON by default, but it serves writable shells, so the dashboard gates it on
# the bind host: a loopback bind (the documented model — fronted by a tailnet /
# SSH tunnel) serves the wall; a non-loopback bind refuses to unless you opt in
# with CHELA_TERMINALS_EXPOSE=true. Set CHELA_TERMINALS_ENABLED=false to turn
# the wall off entirely.
TERMINALS_ENABLED = os.environ.get("CHELA_TERMINALS_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")

# Explicit opt-in to serve the writable terminal wall on a NON-loopback bind
# (e.g. --host 0.0.0.0 or a LAN/tailnet IP). Off by default: a public bind would
# otherwise hand out unauthenticated remote shells (RCE). Loopback binds, fronted
# by a tailnet or SSH tunnel (the recommended setup), never need this.
TERMINALS_EXPOSE = os.environ.get("CHELA_TERMINALS_EXPOSE", "false").strip().lower() not in ("false", "0", "no", "off")

# Collaborative-terminal presence (P3 agent-as-peer). When on, the dashboard
# publishes a Yjs *awareness* frame per running-claude window into that window's
# relay room, so a browser viewing /term/<wid>/?collab=1 sees a "claude" pill
# for the live agent. Purely additive and opaque: it only reaches browsers that
# have opted in with ?collab. On by default with the wall; set CHELA_COLLAB=false
# to stop the publisher. COLLAB_RELAY is the dumb fan-out relay (chela/collab-relay).
COLLAB_PRESENCE = os.environ.get("CHELA_COLLAB", "true").strip().lower() not in ("false", "0", "no", "off")
COLLAB_RELAY = os.environ.get("CHELA_COLLAB_RELAY", "wss://chela-collab-relay.liav-acc.workers.dev").strip().rstrip("/")

# Shared fixed grid (cols x rows) that a ?collab terminal snaps to when 2+ human
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
