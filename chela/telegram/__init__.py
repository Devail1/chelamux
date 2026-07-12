"""Telegram bridge for chela — remote control of the agent fleet over Telegram.

The outbound path lives here: :class:`~chela.telegram.monitor.TranscriptMonitor`
tails each agent window's Claude Code JSONL and emits parsed
:class:`~chela.telegram.parser.Message` events; :class:`~chela.telegram.relay.
TelegramRelay` renders each event to MarkdownV2 (with a plain-text fallback) and
posts it to a Telegram topic via the direct Bot API (:class:`~chela.telegram.
relay.BotSender`).

The inbound path (Telegram → tmux) lives in :mod:`chela.telegram.inbound`:
:class:`~chela.telegram.inbound.TopicRouter` delivers a bound topic's messages
back to the mapped tmux window via :func:`chela.messenger.send_tmux`, driven by a
``python-telegram-bot`` Application (:func:`~chela.telegram.inbound.
build_application`). Only ``build_application`` imports PTB, so the pure router
stays free of the optional ``[telegram]`` extra.

The bridge is adapted from six-ddc/ccbot (https://github.com/six-ddc/ccbot),
which is MIT-licensed. See the top-level NOTICE file for the upstream
copyright and attribution.
"""
from chela.telegram.format import escape_markdown_v2, to_markdown_v2, to_plain_text
from chela.telegram.inbound import TopicRouter, build_application
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
    "TopicRouter",
    "build_application",
    "escape_markdown_v2",
    "to_markdown_v2",
    "to_plain_text",
]
