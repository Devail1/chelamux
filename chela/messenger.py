"""Route messages between agents over tmux.

``send_tmux`` is the low-level primitive: given a tmux window id and text, type
it into that window's Claude Code prompt and press Enter. On top of it,
``send_message``/``broadcast`` resolve an agent's window by name and deliver
there. Delivery is live-only: if the agent has no live window the message is
not delivered (there is no persistent queue).
"""
from __future__ import annotations
import logging
import subprocess
import time

from chela.config import TMUX_SESSION
from chela.discovery import get_window_id, get_all_windows

log = logging.getLogger(__name__)


def send_tmux(window_id: str, text: str) -> bool:
    """Send text to a tmux window. Returns True on success.

    Uses load-buffer + paste-buffer for multi-line text to avoid
    newlines being interpreted as premature Enter presses.
    """
    target = f"{TMUX_SESSION}:{window_id}"
    try:
        if text.startswith("/"):
            # Slash commands: send Escape first to interrupt any in-progress
            # response and return Claude Code to the prompt.
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Escape"],
                check=True, capture_output=True,
            )
            time.sleep(1.0)
        if "\n" in text:
            # Multi-line: use load-buffer + paste-buffer to avoid
            # newlines acting as Enter presses mid-message
            subprocess.run(
                ["tmux", "load-buffer", "-"],
                input=text.encode(), check=True, capture_output=True,
            )
            subprocess.run(
                ["tmux", "paste-buffer", "-t", target],
                check=True, capture_output=True,
            )
            time.sleep(0.5)
            subprocess.run(
                ["tmux", "send-keys", "-t", target, "Enter"],
                check=True, capture_output=True,
            )
        else:
            subprocess.run(
                ["tmux", "send-keys", "-t", target, text, "Enter"],
                check=True, capture_output=True,
            )
        return True
    except subprocess.CalledProcessError as e:
        log.error("tmux send-keys failed for %s: %s", window_id, e.stderr.decode())
        return False


def send_message(from_agent: str, to_agent: str, message: str, priority: str = "normal") -> bool:
    """Send a message to an agent via tmux. Live-only — no fallback queue.

    Returns True if delivered to a live window, False if the agent is offline
    (no live window) or the send failed.
    """
    window_id = get_window_id(to_agent)
    if window_id:
        # Prefix with the sender so the recipient has context on who pinged them.
        formatted = f"[{from_agent}] {message}"
        if send_tmux(window_id, formatted):
            return True
    log.info("%s offline — message not delivered", to_agent)
    return False


def broadcast(from_agent: str, message: str, priority: str = "normal") -> dict[str, bool]:
    """Send a message to every other live agent. Returns {agent: delivered?}."""
    results = {}
    for agent_name in get_all_windows():
        if agent_name == from_agent:
            continue
        results[agent_name] = send_message(from_agent, agent_name, message, priority)
    return results
