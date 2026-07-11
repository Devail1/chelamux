"""Render parsed :class:`~chela.telegram.parser.Message` events for Telegram.

Two renderings per message:

  * :func:`to_markdown_v2` — a Telegram *MarkdownV2* string: a bold, emoji-tagged
    header (role / tool) followed by the escaped body. Every literal character is
    run through :func:`escape_markdown_v2` so user/agent text can never inject or
    break MarkdownV2 entities.
  * :func:`to_plain_text` — the same content with no markup, used as the fallback
    when Telegram rejects the MarkdownV2 send (see :mod:`chela.telegram.relay`).

The MarkdownV2 character-escape (:data:`_MDV2_SPECIAL` / :func:`escape_markdown_v2`)
is ported from six-ddc/ccbot's ``markdown_v2.py`` (https://github.com/six-ddc/ccbot,
MIT). We port only the escape table — not ccbot's ``telegramify_markdown`` /
``mistletoe`` full-markdown pipeline — so this stays dependency-free. See the
top-level NOTICE file for upstream attribution.
"""
from __future__ import annotations

import re

from chela.telegram.parser import Message

# Characters Telegram requires be backslash-escaped in MarkdownV2 plain text.
# Ported verbatim from six-ddc/ccbot's markdown_v2.py (MIT); see NOTICE.
_MDV2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def escape_markdown_v2(text: str) -> str:
    """Backslash-escape every MarkdownV2 special character in ``text``."""
    return _MDV2_SPECIAL.sub(r"\\\1", text)


# Emoji-tagged header for each event, so a topic reads like a conversation.
_ROLE_EMOJI = {"assistant": "🤖", "user": "👤"}


def _header_and_body(msg: Message) -> tuple[str, str]:
    """Split a message into a short display header and its body text.

    Returns ``(header, body)`` as plain (un-escaped) strings. ``body`` may be
    empty (e.g. a ``tool_use`` event, whose payload is just the tool name).
    """
    if msg.content_type == "thinking":
        return "💭 thinking", msg.text
    if msg.content_type == "tool_use":
        return f"🔧 {msg.tool_name or msg.text or 'tool'}", ""
    if msg.content_type == "tool_result":
        return f"✅ {msg.tool_name or 'tool'} result", msg.text
    # plain text turn
    return _ROLE_EMOJI.get(msg.role, "•"), msg.text


def to_plain_text(msg: Message) -> str:
    """Render a message as unformatted text (the MarkdownV2 fallback)."""
    header, body = _header_and_body(msg)
    return f"{header}\n{body}".rstrip() if body else header


def to_markdown_v2(msg: Message) -> str:
    """Render a message as a Telegram MarkdownV2 string (bold header + body)."""
    header, body = _header_and_body(msg)
    out = f"*{escape_markdown_v2(header)}*"
    if body:
        out += f"\n{escape_markdown_v2(body)}"
    return out
