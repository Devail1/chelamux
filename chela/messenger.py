"""Route messages between agents over tmux, with a mailbox fallback.

``send_tmux`` is the low-level primitive: given a tmux window id and text, type
it into that window's Claude Code prompt and press Enter. On top of it,
``send_message``/``broadcast`` resolve an agent's window by name and deliver
there; if the agent is offline (no live window) the message is appended to a
per-agent JSONL mailbox under ``CHELA_DIR/mailbox`` so it isn't lost.
"""
from __future__ import annotations
import json
import logging
import subprocess
import time
from datetime import datetime, timezone

from chela.config import TMUX_SESSION, MAILBOX_DIR
from chela.discovery import get_window_id, get_all_windows
from chela.models import AgentMessage

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
    """Send a message to an agent via tmux. Falls back to mailbox if offline.

    Returns True if delivered to a live window, False if it went to the mailbox.
    """
    window_id = get_window_id(to_agent)
    if window_id:
        # Prefix with the sender so the recipient has context on who pinged them.
        formatted = f"[{from_agent}] {message}"
        if send_tmux(window_id, formatted):
            return True
    # Agent offline or send failed: write to mailbox.
    send_to_mailbox(from_agent, to_agent, message, priority)
    return False


def broadcast(from_agent: str, message: str, priority: str = "normal") -> dict[str, bool]:
    """Send a message to every other live agent. Returns {agent: delivered?}."""
    results = {}
    for agent_name in get_all_windows():
        if agent_name == from_agent:
            continue
        results[agent_name] = send_message(from_agent, agent_name, message, priority)
    return results


def send_to_mailbox(from_agent: str, to_agent: str, message: str, priority: str = "normal") -> None:
    """Write a message to an agent's mailbox file (JSONL)."""
    MAILBOX_DIR.mkdir(parents=True, exist_ok=True)
    mailbox_path = MAILBOX_DIR / f"mailbox_{to_agent}.jsonl"
    msg = AgentMessage(
        from_agent=from_agent,
        to_agent=to_agent,
        type="message",
        priority=priority,
        ts=datetime.now(timezone.utc).isoformat(),
        data={"message": message},
    )
    with open(mailbox_path, "a") as f:
        f.write(json.dumps(msg.to_dict()) + "\n")
    log.info("Wrote to %s mailbox", to_agent)


def read_mailbox(agent_name: str) -> list[AgentMessage]:
    """Read and return all messages from an agent's mailbox."""
    mailbox_path = MAILBOX_DIR / f"mailbox_{agent_name}.jsonl"
    if not mailbox_path.exists():
        return []
    messages = []
    with open(mailbox_path) as f:
        for line in f:
            if line.strip():
                messages.append(AgentMessage.from_dict(json.loads(line)))
    return messages


def clear_mailbox(agent_name: str) -> int:
    """Delete an agent's mailbox file. Returns number of messages cleared."""
    mailbox_path = MAILBOX_DIR / f"mailbox_{agent_name}.jsonl"
    if not mailbox_path.exists():
        return 0
    count = len(read_mailbox(agent_name))
    mailbox_path.unlink()
    return count


def get_unread_count(agent_name: str) -> int:
    """Return the number of messages in an agent's mailbox."""
    return len(read_mailbox(agent_name))
