"""Telegram bridge for chela — remote control of the agent fleet over Telegram.

The outbound path lives here: :class:`~chela.telegram.monitor.TranscriptMonitor`
tails each agent window's Claude Code JSONL and emits parsed
:class:`~chela.telegram.parser.Message` events; :class:`~chela.telegram.relay.
TelegramRelay` renders each event to MarkdownV2 (with a plain-text fallback) and
posts it to a Telegram topic via the direct Bot API (:class:`~chela.telegram.
relay.BotSender`). Inbound (Telegram → tmux) is a later slice.

The bridge is adapted from six-ddc/ccbot (https://github.com/six-ddc/ccbot),
which is MIT-licensed. See the top-level NOTICE file for the upstream
copyright and attribution.
"""
from chela.telegram.format import escape_markdown_v2, to_markdown_v2, to_plain_text
from chela.telegram.monitor import TranscriptMonitor
from chela.telegram.parser import Message, parse_entries, parse_line
from chela.telegram.relay import BotSender, TelegramRelay

__all__ = [
    "TranscriptMonitor",
    "Message",
    "parse_entries",
    "parse_line",
    "BotSender",
    "TelegramRelay",
    "escape_markdown_v2",
    "to_markdown_v2",
    "to_plain_text",
]
