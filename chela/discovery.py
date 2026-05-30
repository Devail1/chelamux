"""Window discovery — tmux is the single source of truth.

Each agent runs in a tmux window of the configured session; the window name is
the agent's display name. No external state file, no daemon to coordinate with —
tmux never lies about what is live right now.
"""
from __future__ import annotations
import logging
import subprocess

from chela.config import TMUX_SESSION

log = logging.getLogger(__name__)


def _get_live_windows() -> dict[str, str]:
    """Live windows of the chela session as ``{window_id: window_name}``."""
    try:
        result = subprocess.run(
            ["tmux", "list-windows", "-t", TMUX_SESSION,
             "-F", "#{window_id}\t#{window_name}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            out: dict[str, str] = {}
            for line in result.stdout.splitlines():
                wid, _, name = line.partition("\t")
                wid, name = wid.strip(), name.strip()
                if wid and name:
                    out[wid] = name
            return out
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("Failed to query tmux for live windows (session=%s)", TMUX_SESSION)
    return {}


def get_all_windows() -> dict[str, str]:
    """``{display_name: window_id}`` for every live window in the session."""
    return {name: wid for wid, name in _get_live_windows().items()}


def get_window_id(agent_name: str) -> str | None:
    """tmux window id for an agent, by display name (its window name)."""
    return get_all_windows().get(agent_name)


def get_window_cwd(agent_name: str) -> str | None:
    """Working directory of an agent's window, read straight from its tmux pane."""
    wid = get_window_id(agent_name)
    if not wid:
        return None
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-p", "-t", wid, "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return result.stdout.strip() or None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        log.warning("Failed to read pane cwd for %s", agent_name)
    return None
