"""Permission-gate watcher — correlate the transcript's pending tool with the pane.

A tool **permission gate** ("Do you want to proceed?" for a Bash/Edit) is NOT in
the JSONL transcript — only Claude Code's live TUI shows it. Blind-scraping every
window's pane every tick to find one would be wasteful and racy, so this watcher
correlates the two channels instead:

  * **transcript = identity** — observing the parsed :class:`~chela.telegram.
    parser.Message` stream tells us, per window, whether the latest ``tool_use``
    is still *unpaired* (no ``tool_result`` yet) and what it was (tool name +
    input);
  * **pane = liveness** — ONLY for a window with an unpaired ``tool_use`` do we
    :func:`~chela.messenger.capture_pane` and run
    :func:`~chela.telegram.panescan.detect_permission_gate`.

On a NEWLY-detected gate it posts ONE line to the window's bound topic —
``❓ Permission — <tool>: <command/args>`` — with the tool name + args taken from
the transcript's unpaired ``tool_use`` (the two-channel win: the *real* command,
not blind pane text), falling back to the scraped gate region if the transcript
identity is unavailable. The relay is **edge-triggered**: the relayed gate is
tracked per window (by ``tool_use_id``) and cleared when the ``tool_result``
arrives OR the pane stops showing a gate — so a still-open gate is not re-posted
every poll.

This slice is DETECTION + enriched relay only. The ✅ Allow / ❌ Deny answer
keyboard is Slice C2 (it will reuse the ``qa:nav`` Enter/Esc plumbing).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from chela.telegram.panescan import Gate, detect_permission_gate

log = logging.getLogger(__name__)

# A sender posts one plain line to a topic: ``send(text, parse_mode, thread)``.
# The gate line carries shell/path characters, so it is sent as plain text
# (``parse_mode=None``) — no MarkdownV2 escaping to get wrong.
Sender = Callable[..., bool]
# A capture reads a window's visible pane text: ``capture(window_id) -> str``.
Capture = Callable[[str], str]

# Longest command/arg detail we inline before truncating (keeps the line tidy;
# the full command is one Bash approval away in the pane).
_MAX_DETAIL = 300


@dataclass
class _PendingTool:
    """An unpaired ``tool_use`` awaiting its ``tool_result`` — identity for a gate."""

    tool_name: str | None
    tool_input: dict | None


def _tool_detail(tool_name: str | None, tool_input: dict | None) -> str | None:
    """The human-facing arg summary for a tool_use, or None if there's nothing apt.

    Bash → its ``command``; the file tools → their ``file_path`` (whitespace
    collapsed to one line and truncated). Anything else returns None so the caller
    falls back to the scraped gate region.
    """
    if not isinstance(tool_input, dict):
        return None
    if tool_name == "Bash":
        val = tool_input.get("command")
    elif tool_name in ("Edit", "MultiEdit", "Write", "Read"):
        val = tool_input.get("file_path")
    elif tool_name == "NotebookEdit":
        val = tool_input.get("notebook_path")
    else:
        val = None
    if not val:
        return None
    flat = " ".join(str(val).split())
    return flat[: _MAX_DETAIL - 1] + "…" if len(flat) > _MAX_DETAIL else flat


def format_gate_message(info: _PendingTool | None, gate: Gate) -> str:
    """Build the enriched relay line for a detected gate.

    Prefers the transcript identity (``❓ Permission — <tool>: <detail>``); if the
    tool is known but has no apt detail, drops the detail; if no transcript
    identity is available at all, falls back to the scraped gate region text.
    """
    tool = info.tool_name if info else None
    detail = _tool_detail(tool, info.tool_input) if info else None
    if tool and detail:
        return f"❓ Permission — {tool}: {detail}"
    if tool:
        return f"❓ Permission — {tool}"
    text = (gate.text or "").strip()
    return f"❓ Permission\n{text}" if text else "❓ Permission"


class PermissionGateWatcher:
    """Observes the message stream + polls panes to surface blocked-on-permission.

    Wire :meth:`observe` into the monitor's ``on_message`` (alongside the relay)
    so the watcher tracks per-window unpaired ``tool_use``s, and call :meth:`poll`
    once per outbound cycle (after ``monitor.poll``) with the same window-id set —
    it reads only the panes of windows with an unpaired tool and relays a newly
    detected gate exactly once.
    """

    def __init__(
        self,
        sender: Sender,
        registry,
        *,
        capture: Capture,
        detect: Callable[[str], Gate | None] = detect_permission_gate,
    ):
        self._sender = sender
        self._registry = registry
        self._capture = capture
        self._detect = detect
        # window_id -> {tool_use_id: _PendingTool}, insertion-ordered so the most
        # recently added (the likely-blocked tool) is the last key.
        self._pending: dict[str, dict[str, _PendingTool]] = {}
        # window_id -> tool_use_id we've already relayed a gate for (edge trigger).
        self._relayed: dict[str, str] = {}

    def observe(self, window_id: str, msg) -> None:
        """Track ``tool_use``/``tool_result`` pairing for one parsed message.

        Mirrors the transcript parser's own pairing (keyed by ``tool_use_id``) but
        retains the tool ``input`` so the gate relay can name the real command.
        Non-tool events are ignored.
        """
        ct = getattr(msg, "content_type", None)
        uid = getattr(msg, "tool_use_id", None)
        if ct == "tool_use":
            if uid:
                self._pending.setdefault(window_id, {})[uid] = _PendingTool(
                    tool_name=getattr(msg, "tool_name", None),
                    tool_input=getattr(msg, "tool_input", None),
                )
        elif ct == "tool_result":
            pend = self._pending.get(window_id)
            if pend and uid:
                pend.pop(uid, None)

    def forget(self, window_id: str) -> None:
        """Drop all state for a window (e.g. after it closes)."""
        self._pending.pop(window_id, None)
        self._relayed.pop(window_id, None)

    def poll(self, window_ids) -> None:
        """Read the pane of each window with an unpaired tool and relay new gates."""
        for wid in window_ids:
            try:
                self._poll_window(wid)
            except Exception:
                log.exception("permission-gate poll failed for %s", wid)

    # -- internals ---------------------------------------------------------

    def _poll_window(self, window_id: str) -> None:
        pend = self._pending.get(window_id)
        if not pend:
            # No unpaired tool_use → nothing can be blocked; clear any relayed gate
            # (the tool_result has arrived) so a later gate edge-triggers again.
            self._relayed.pop(window_id, None)
            return

        uid = next(reversed(pend))  # latest unpaired tool_use
        info = pend[uid]

        pane = self._capture(window_id)
        gate = self._detect(pane)
        if gate is None:
            # Pane no longer shows a gate — clear so a fresh one relays again.
            self._relayed.pop(window_id, None)
            return

        if self._relayed.get(window_id) == uid:
            return  # already relayed this gate — edge-triggered, not per-poll

        thread = self._registry.thread_for_window(window_id)
        if thread is None:
            log.debug("permission gate on %s but no bound topic; skipping", window_id)
            return

        body = format_gate_message(info, gate)
        self._sender(body, None, thread)
        self._relayed[window_id] = uid
