"""JSONL transcript parser — turns Claude Code session records into events.

Parses the message records Claude Code writes to its session JSONL and flattens
them into a linear stream of :class:`Message` events (text, thinking, tool_use,
tool_result, user). The load-bearing behaviour is **tool pairing**: a
``tool_use`` block in an assistant message is matched with the ``tool_result``
block that arrives (possibly in a later record, possibly in a later poll cycle)
in the following user message, keyed by ``tool_use_id`` — so a paired
``tool_result`` event carries the originating tool's name.

Adapted from six-ddc/ccbot's ``transcript_parser.py``
(https://github.com/six-ddc/ccbot, MIT). This is the lean, transport-agnostic
core: it emits structured events and does no Telegram/MarkdownV2 formatting.
See the top-level NOTICE file for upstream attribution.
"""
from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

# Placeholder Claude Code writes for an assistant turn that produced no text.
_NO_CONTENT = "(no content)"

# System-injected user text (reminders / bash carriers) that is noise, not a
# real user turn — skipped so the event stream reads as the conversation.
_RE_SYSTEM_TAGS = re.compile(
    r"<(bash-input|bash-stdout|bash-stderr|local-command-caveat|system-reminder"
    r"|command-name|local-command-stdout)"
)


@dataclass
class Message:
    """A single parsed message event ready to relay.

    ``content_type`` is one of ``"text" | "thinking" | "tool_use" |
    "tool_result"``. For ``tool_use``/``tool_result`` events, ``tool_name`` is
    the originating tool (resolved by pairing for results) and ``tool_use_id``
    links the two.
    """

    role: str  # "user" | "assistant"
    content_type: str
    text: str
    tool_name: str | None = None
    tool_use_id: str | None = None
    timestamp: str | None = None
    # The raw ``tool_use`` ``input`` dict (e.g. AskUserQuestion's ``questions``),
    # carried so an interactive relay can build an inline keyboard from the
    # structured prompt instead of scraping the pane. None for non-tool events.
    tool_input: dict | None = None
    # ``(media_type, raw_bytes)`` pairs decoded from a ``tool_result``'s
    # ``image`` content blocks (e.g. a screenshot tool's output). None for a
    # text-only result and for every non-``tool_result`` event — the outbound
    # relay only ever looks here, never at ``text``, for image bytes.
    images: list[tuple[str, bytes]] | None = None


@dataclass
class _Pending:
    """A ``tool_use`` awaiting its ``tool_result``, carried across poll cycles."""

    tool_name: str


def parse_line(line: str) -> dict | None:
    """Parse one JSONL line into a dict, or None if blank / not valid JSON."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _text_blocks(content: list[Any]) -> str:
    """Join the plain-``text`` blocks of a content list."""
    parts = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict) and item.get("type") == "text":
            t = item.get("text", "")
            if t:
                parts.append(t)
    return "\n".join(parts)


def _tool_result_text(content: list | Any) -> str:
    """Extract the text of a ``tool_result`` block's content."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                t = item.get("text", "")
                if t:
                    parts.append(t)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return ""


def _tool_result_images(content: list | Any) -> list[tuple[str, bytes]] | None:
    """Extract the base64 ``image`` blocks of a ``tool_result`` block's content.

    A screenshot (or any image a tool returns — e.g. ``Read`` on a PNG) arrives
    as ``{"type": "image", "source": {"type": "base64", "media_type": ...,
    "data": ...}}`` alongside — or instead of — the ``text`` blocks
    :func:`_tool_result_text` collects. Returns ``(media_type, raw_bytes)``
    pairs, or None when there are none (the common case), so a text-only result
    carries no ``images`` at all. A block that fails to decode is skipped
    rather than aborting the whole result.
    """
    if not isinstance(content, list):
        return None
    images: list[tuple[str, bytes]] = []
    for item in content:
        if not isinstance(item, dict) or item.get("type") != "image":
            continue
        source = item.get("source")
        if not isinstance(source, dict) or source.get("type") != "base64":
            continue
        data = source.get("data", "")
        if not data:
            continue
        try:
            raw = base64.b64decode(data)
        except ValueError:
            continue
        images.append((source.get("media_type", "image/png"), raw))
    return images or None


def parse_entries(
    entries: list[dict],
    pending: dict[str, _Pending] | None = None,
) -> tuple[list[Message], dict[str, _Pending]]:
    """Flatten JSONL records into ``Message`` events, pairing tools.

    ``pending`` carries unmatched ``tool_use`` blocks (keyed by ``tool_use_id``)
    from an earlier call — the monitor threads it across poll cycles so a
    ``tool_use`` read in one cycle still pairs with a ``tool_result`` read in a
    later one. Returns ``(events, remaining_pending)``; unmatched ``tool_use``
    ids stay in ``remaining_pending`` rather than being emitted early.
    """
    out: list[Message] = []
    # Copy so we never mutate the caller's dict.
    pending = dict(pending) if pending else {}

    for data in entries:
        if data.get("type") not in ("user", "assistant"):
            continue
        message = data.get("message")
        if not isinstance(message, dict):
            continue
        ts = data.get("timestamp")
        content = message.get("content", "")
        if not isinstance(content, list):
            content = [{"type": "text", "text": str(content)}] if content else []

        if data["type"] == "assistant":
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    t = block.get("text", "").strip()
                    if t and t != _NO_CONTENT:
                        out.append(Message("assistant", "text", t, timestamp=ts))
                elif btype == "thinking":
                    t = block.get("thinking", "").strip()
                    if t:
                        out.append(Message("assistant", "thinking", t, timestamp=ts))
                elif btype == "tool_use":
                    name = block.get("name", "unknown")
                    tuid = block.get("id") or None
                    if tuid:
                        pending[tuid] = _Pending(tool_name=name)
                    tinput = block.get("input")
                    out.append(
                        Message(
                            "assistant", "tool_use", name,
                            tool_name=name, tool_use_id=tuid, timestamp=ts,
                            tool_input=tinput if isinstance(tinput, dict) else None,
                        )
                    )
        else:  # user
            user_text: list[str] = []
            for block in content:
                if not isinstance(block, dict):
                    if isinstance(block, str) and block.strip():
                        user_text.append(block.strip())
                    continue
                btype = block.get("type", "")
                if btype == "tool_result":
                    tuid = block.get("tool_use_id") or None
                    info = pending.pop(tuid, None) if tuid else None
                    raw_content = block.get("content", "")
                    result_text = _tool_result_text(raw_content).strip()
                    images = _tool_result_images(raw_content)
                    out.append(
                        Message(
                            "assistant", "tool_result", result_text,
                            tool_name=info.tool_name if info else None,
                            tool_use_id=tuid, timestamp=ts,
                            images=images,
                        )
                    )
                elif btype == "text":
                    t = block.get("text", "").strip()
                    if t and not _RE_SYSTEM_TAGS.search(t):
                        user_text.append(t)
            if user_text:
                combined = "\n".join(user_text).strip()
                if combined:
                    out.append(Message("user", "text", combined, timestamp=ts))

    return out, pending
