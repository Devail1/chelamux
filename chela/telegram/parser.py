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
from pathlib import Path
from typing import Any

from chela.telegram.media import INBOUND_IMAGE_MARKER

# Placeholder Claude Code writes for an assistant turn that produced no text.
_NO_CONTENT = "(no content)"

# System-injected user text (reminders / bash carriers) that is noise, not a
# real user turn — skipped so the event stream reads as the conversation.
_RE_SYSTEM_TAGS = re.compile(
    r"<(bash-input|bash-stdout|bash-stderr|local-command-caveat|system-reminder"
    r"|command-name|local-command-stdout)"
)

# The Read tool's own note on a downscaled image (e.g. "[Image: original
# 1170x2532, displayed at 924x2000. Multiply coordinates by 1.27 to map to
# original image.]"). Claude Code writes it as a separate ``isMeta`` user record
# next to the image ``tool_result``; it is model-facing guidance, not a user
# turn, so it must never relay as one (CMX-24).
_RE_IMAGE_COORD_NOTE = re.compile(
    r"\[Image: original \d+x\d+, displayed at \d+x\d+\."
    r" Multiply coordinates by [\d.]+ to map to original image\.\]"
)


# The ``isMeta`` record Claude Code writes after a user turn with a pasted image:
# "[Image: source: /tmp/chela-paste-images/<sha>.png]". Never relayed as text (every
# ``isMeta`` record is dropped by its flag, not by this pattern); read ONLY to find
# the image file when its user turn carried no base64 ``image`` block (CMX-36).
_RE_IMAGE_SOURCE = re.compile(r"\[Image: source: (.+?)\]")

# A source-path fallback uploads a file off disk, so it reads only these extensions.
_IMAGE_EXT_MEDIA_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}

# A pasted image whose user turn has no text at all still needs a body to post.
_IMAGE_ONLY_TEXT = "[Image]"


def _is_peer_message(data: dict) -> bool:
    """True for a cross-session peer message (``origin.kind == "peer"``).

    Peer messages are ``isMeta`` too, but they are deliberately relayed so the
    operator sees what other sessions told this one — the ONE ``isMeta`` kind that
    is conversation rather than harness bookkeeping.
    """
    origin = data.get("origin")
    return isinstance(origin, dict) and origin.get("kind") == "peer"


def _image_from_source_path(path: str) -> tuple[str, bytes] | None:
    """``(media_type, bytes)`` of the image file at ``path``, or None.

    Only an existing regular file with an image extension is read — this is the
    fallback for a pasted image whose user turn carried no ``image`` block.
    """
    media_type = _IMAGE_EXT_MEDIA_TYPE.get(Path(path).suffix.lower())
    if media_type is None:
        return None
    try:
        return media_type, Path(path).read_bytes()
    except OSError:  # missing, a directory, unreadable
        return None


def _strip_image_note(text: str) -> str:
    """``text`` with any Read-tool image coordinate note removed, stripped."""
    return _RE_IMAGE_COORD_NOTE.sub("", text).strip()


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
    # The invoked skill identifier (e.g. "superpowers:brainstorming") when
    # ``tool_name == "Skill"``, else None. Kept in ``pending`` past its
    # ``tool_result`` — see the ``sourceToolUseID`` handling in
    # :func:`parse_entries` — since the skill's full body arrives as a LATER,
    # separate synthetic user record, not inside that tool_result.
    skill: str | None = None


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
    # The latest user text event, so a following "[Image: source: …]" meta record
    # can attach its file when that turn carried no image block. Cleared by any
    # assistant record so a stray meta line never lands on an older turn.
    last_user: Message | None = None
    last_user_had_blocks = False

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
            last_user = None
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
                    tinput = block.get("input")
                    if tuid:
                        skill = tinput.get("skill") if isinstance(tinput, dict) else None
                        pending[tuid] = _Pending(
                            tool_name=name,
                            skill=skill if isinstance(skill, str) else None,
                        )
                    out.append(
                        Message(
                            "assistant", "tool_use", name,
                            tool_name=name, tool_use_id=tuid, timestamp=ts,
                            tool_input=tinput if isinstance(tinput, dict) else None,
                        )
                    )
        else:  # user
            # A Skill invocation is followed by a SEPARATE, synthetic ``isMeta``
            # user record — not a tool_result — carrying the skill's ENTIRE body
            # (its whole SKILL.md, sometimes 100K+ chars) as plain "text" content,
            # keyed back to the ``Skill`` tool_use via ``sourceToolUseID`` rather
            # than the usual ``tool_use_id``. Left unhandled, that body relays
            # like any other user turn — precisely what CMX-348 reported (one
            # `update-config` invocation posted 257,276 chars to Telegram). The
            # terminal never shows this raw body either; it only surfaces the
            # skill's name, so mirror that instead of relaying the dump.
            source_id = data.get("sourceToolUseID")
            if source_id and data.get("isMeta"):
                skill_info = pending.pop(source_id, None)
                # A missing pending entry (the originating tool_use fell
                # outside this read window — e.g. the transcript monitor
                # skipped to EOF on a large file, see CMX-348) is treated the
                # SAME as a resolved Skill entry, not relayed: isMeta +
                # sourceToolUseID together already identify this as
                # tool-injected synthetic content, so there is no safe way to
                # let an unresolved one fall through to a raw multi-KB dump.
                # Only a pending entry that resolves to some OTHER tool (a
                # real, non-Skill tool_use) is known-safe to relay normally.
                if skill_info is None or skill_info.tool_name == "Skill":
                    name = skill_info.skill if skill_info is not None else None
                    out.append(
                        Message("user", "text", f"Loaded skill: {name or 'unknown'}", timestamp=ts)
                    )
                    continue
            if data.get("isMeta") and not _is_peer_message(data):
                # Harness bookkeeping, never a user turn (CMX-36): the pasted-image
                # "[Image: source: …]" line, the Read tool's coordinate note,
                # scheduled-task prompts, idle notices, command caveats. Filtered by
                # the FLAG, so a new meta kind can't leak as 👤 either. The one
                # thing read out of it is a pasted image's source path, used only
                # when that image's own turn carried no base64 block.
                if last_user is not None and not last_user_had_blocks:
                    for block in content:
                        t = block.get("text", "") if isinstance(block, dict) else block
                        m = _RE_IMAGE_SOURCE.search(t) if isinstance(t, str) else None
                        image = _image_from_source_path(m.group(1)) if m else None
                        if image is not None:
                            last_user.images = (last_user.images or []) + [image]
                continue
            user_text: list[str] = []
            user_images: list[tuple[str, bytes]] = []
            for block in content:
                if not isinstance(block, dict):
                    if isinstance(block, str) and _strip_image_note(block):
                        user_text.append(_strip_image_note(block))
                    continue
                btype = block.get("type", "")
                if btype == "tool_result":
                    tuid = block.get("tool_use_id") or None
                    info = pending.get(tuid) if tuid else None
                    # A Skill invocation's entry stays in ``pending`` past its
                    # (short) tool_result — its full body arrives as a LATER,
                    # separate ``sourceToolUseID`` record, handled below.
                    if info is not None and info.tool_name != "Skill" and tuid:
                        pending.pop(tuid, None)
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
                    t = _strip_image_note(block.get("text", ""))
                    if t and not _RE_SYSTEM_TAGS.search(t):
                        user_text.append(t)
                elif btype == "image":
                    # An image pasted into the terminal/wall (CMX-36) — relayed
                    # as a photo after this turn's text, like a tool_result's.
                    user_images.extend(_tool_result_images([block]) or [])
            combined = "\n".join(user_text).strip()
            if not combined and not user_images:
                continue
            if INBOUND_IMAGE_MARKER in combined:
                # A photo that ARRIVED from Telegram: it is already in the topic,
                # so it must not echo back into it — and no source-path fallback
                # may re-attach it either.
                out.append(Message("user", "text", combined, timestamp=ts))
                last_user, last_user_had_blocks = None, True
                continue
            msg = Message(
                "user", "text", combined or _IMAGE_ONLY_TEXT,
                timestamp=ts, images=user_images or None,
            )
            out.append(msg)
            last_user, last_user_had_blocks = msg, bool(user_images)

    return out, pending
