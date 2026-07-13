"""Render parsed :class:`~chela.telegram.parser.Message` events for Telegram.

Two renderings per message:

  * :func:`to_markdown_v2` — a Telegram *MarkdownV2* string: a bold, emoji-tagged
    header (role / tool) followed by the body rendered via
    :func:`render_markdown`. The header text is blind-escaped
    (:func:`escape_markdown_v2`) so it can never break the entity; the *body* is
    run through ``telegramify-markdown`` so Claude's Markdown (fenced code
    blocks, bold, lists, tables) renders as real Telegram entities instead of
    literal backslash-escaped characters.
  * :func:`to_plain_text` — the same content with no markup, used as the fallback
    when Telegram rejects the MarkdownV2 send (see :mod:`chela.telegram.relay`).

The body renderer wraps ``telegramify-markdown`` (mistletoe-based), ported in
spirit from six-ddc/ccbot's ``markdown_v2.py`` (https://github.com/six-ddc/ccbot,
MIT) — ccbot's approach we had previously declined in order to stay dep-free.
``telegramify-markdown`` ships with the optional ``[telegram]`` extra; when it is
absent (a core install), :func:`render_markdown` degrades to the blind
character-escape so this module stays importable with the stdlib alone. See the
top-level NOTICE file for upstream attribution.
"""
from __future__ import annotations

import logging
import re

from chela.telegram.parser import Message

try:  # ships with the optional [telegram] extra; absent in a core install.
    import telegramify_markdown as _telegramify
except ImportError:  # pragma: no cover - exercised only without the extra
    _telegramify = None

log = logging.getLogger(__name__)

# Characters Telegram requires be backslash-escaped in MarkdownV2 plain text.
# Ported verbatim from six-ddc/ccbot's markdown_v2.py (MIT); see NOTICE.
_MDV2_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")

# A markdown table's separator row: only pipes, colons, dashes and whitespace.
# Ported from six-ddc/ccbot's markdown_v2.py (MIT); see NOTICE.
_TABLE_SEP = re.compile(r"^[\s|:\-]+$")


def escape_markdown_v2(text: str) -> str:
    """Backslash-escape every MarkdownV2 special character in ``text``."""
    return _MDV2_SPECIAL.sub(r"\\\1", text)


def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row into its cells, respecting escaped ``\\|``."""
    content = line.strip().strip("|")
    cells = re.split(r"(?<!\\)\|", content)
    return [cell.strip().replace("\\|", "|") for cell in cells]


def convert_markdown_tables(text: str) -> str:
    """Rewrite markdown tables as card-style ``**Header**: value`` lists.

    Telegram has no table rendering, and ``telegramify-markdown`` passes a table
    through as raw pipes. This turns each table row into a card of
    ``**Header**: value`` pairs separated by a horizontal rule — the same shape
    Claude Code renders tables as in a narrow terminal. Non-table text is
    returned verbatim, and tables inside fenced code blocks are left untouched.

    Ported from six-ddc/ccbot's ``markdown_v2.py`` (MIT); see NOTICE.
    """
    lines = text.split("\n")
    result: list[str] = []
    i = 0
    in_code_block = False

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Track fenced code blocks — never convert a table inside one.
        if stripped.startswith("```"):
            in_code_block = not in_code_block
            result.append(line)
            i += 1
            continue

        if in_code_block:
            result.append(line)
            i += 1
            continue

        # A header row looks like ``| a | b |`` with an inner pipe.
        if (
            stripped.startswith("|")
            and stripped.endswith("|")
            and "|" in stripped[1:-1]
        ):
            # The next line must be a separator (``|---|---|``) for it to count.
            if i + 1 < len(lines):
                sep_line = lines[i + 1].strip()
                if sep_line.startswith("|") and _TABLE_SEP.match(sep_line):
                    headers = _split_table_row(stripped)
                    i += 2  # consume header + separator
                    rows: list[list[str]] = []
                    while i < len(lines):
                        data_line = lines[i].strip()
                        if data_line.startswith("|") and data_line.endswith("|"):
                            rows.append(_split_table_row(data_line))
                            i += 1
                        else:
                            break

                    separator = "────────────"
                    cards: list[str] = []
                    for row in rows:
                        card_lines = [
                            f"**{header}**: {row[j] if j < len(row) and row[j] else '—'}"
                            for j, header in enumerate(headers)
                        ]
                        cards.append("\n".join(card_lines))

                    result.append(f"\n{separator}\n".join(cards))
                    continue

        result.append(line)
        i += 1

    return "\n".join(result)


def render_markdown(text: str) -> str:
    """Render a Markdown ``text`` body as a Telegram MarkdownV2 string.

    Uses ``telegramify-markdown`` so fenced code blocks, bold/italic, lists and
    tables become real Telegram entities rather than the literal, blind-escaped
    characters :func:`escape_markdown_v2` would produce. Markdown tables are
    first rewritten as card-style lists via :func:`convert_markdown_tables`
    (Telegram can't render tables — telegramify would leave raw pipes). Falls
    back to the blind escape when ``telegramify-markdown`` isn't installed (core
    install without the ``[telegram]`` extra) or if it raises on malformed input
    — so a body is always rendered to *some* valid MarkdownV2 and never crashes
    the relay.
    """
    text = convert_markdown_tables(text)
    if _telegramify is None:
        return escape_markdown_v2(text)
    try:
        # markdownify appends a trailing newline; drop it so concatenation with
        # the header stays tight.
        return _telegramify.markdownify(text).rstrip("\n")
    except Exception:  # pragma: no cover - defensive; malformed markdown
        log.debug("telegramify-markdown failed; blind-escaping body", exc_info=True)
        return escape_markdown_v2(text)


def to_code_block(text: str) -> str:
    """Wrap ``text`` in a Telegram MarkdownV2 fenced code block.

    Used by the bridge's ``/screenshot`` command to send a terminal pane as a
    monospaced snapshot. Inside a code entity only ``\\`` and `` ` `` are
    special (the full :func:`escape_markdown_v2` table would leak visible
    backslashes into the snapshot), so escape just those two.
    """
    escaped = text.replace("\\", "\\\\").replace("`", "\\`")
    return f"```\n{escaped}\n```"


# Emoji-tagged header for each event, so a topic reads like a conversation. The
# assistant has no marker: Telegram already shows the message came from the bot,
# so an assistant turn renders as its body alone (see _header_and_body).
_ROLE_EMOJI = {"user": "👤"}


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
    # plain text turn — an assistant turn needs no header (Telegram already marks
    # it as the bot), so its body renders alone; other roles keep a marker.
    if msg.role == "assistant":
        return "", msg.text
    return _ROLE_EMOJI.get(msg.role, "•"), msg.text


def to_plain_text(msg: Message) -> str:
    """Render a message as unformatted text (the MarkdownV2 fallback)."""
    header, body = _header_and_body(msg)
    if not header:                       # headerless (assistant turn) — body alone
        return body.rstrip()
    return f"{header}\n{body}".rstrip() if body else header


def to_markdown_v2(msg: Message) -> str:
    """Render a message as a Telegram MarkdownV2 string (bold header + body).

    The header is blind-escaped and bolded; the body is rendered through
    :func:`render_markdown` so Claude's Markdown displays with real formatting.
    """
    header, body = _header_and_body(msg)
    if not header:                       # headerless (assistant turn) — body alone
        return render_markdown(body) if body else ""
    out = f"*{escape_markdown_v2(header)}*"
    if body:
        out += f"\n{render_markdown(body)}"
    return out
