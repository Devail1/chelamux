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

# Embedded ttyd terminal wall on/off (read by the dashboard and the ttyd
# supervisor in scripts/agent-terminals.sh). Set false to drop the wall.
TERMINALS_ENABLED = os.environ.get("CHELA_TERMINALS_ENABLED", "true").strip().lower() not in ("false", "0", "no", "off")
