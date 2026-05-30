"""Send text to an agent's tmux pane.

This is the low-level routing primitive: given a tmux window id and some text,
type it into that window's Claude Code prompt and press Enter. The higher-level
``msg``/broadcast surface (name resolution, mailbox fallback) lands later; the
scheduler and dispatcher only need ``send_tmux``.
"""
from __future__ import annotations
import logging
import subprocess
import time

from chela.config import TMUX_SESSION

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
