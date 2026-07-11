"""Telegram bridge for chela — remote control of the agent fleet over Telegram.

The outbound foundation lives here: :class:`~chela.telegram.monitor.
TranscriptMonitor` tails each agent window's Claude Code JSONL and emits parsed
:class:`~chela.telegram.parser.Message` events via a callback. Telegram wiring
(mapping Forum topics to sessions) is not connected yet — this layer produces
message events; a later slice will forward them.

The bridge is adapted from six-ddc/ccbot (https://github.com/six-ddc/ccbot),
which is MIT-licensed. See the top-level NOTICE file for the upstream
copyright and attribution.
"""
from chela.telegram.monitor import TranscriptMonitor
from chela.telegram.parser import Message, parse_entries, parse_line

__all__ = ["TranscriptMonitor", "Message", "parse_entries", "parse_line"]
