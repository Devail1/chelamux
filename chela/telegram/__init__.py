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

For the N-agents ↔ N-topics bridge both halves route through a
:class:`~chela.telegram.bindings.BindingRegistry` (a persisted, bidirectional
``thread_id ↔ window_id`` map): :class:`~chela.telegram.inbound.RegistryRouter`
resolves the window per inbound message and :class:`~chela.telegram.relay.
RegistryRelay` posts each window's output to its bound topic. The single-window
:class:`TopicRouter`/:class:`TelegramRelay` remain as the one-binding primitives.

The bridge is adapted from six-ddc/ccbot (https://github.com/six-ddc/ccbot),
which is MIT-licensed. See the top-level NOTICE file for the upstream
copyright and attribution.
"""
from chela.telegram.bindings import BindingRegistry, default_bindings_path
from chela.telegram.format import (
    escape_markdown_v2,
    to_code_block,
    to_markdown_v2,
    to_plain_text,
)
from chela.telegram.inbound import (
    BRIDGE_COMMANDS,
    MENU_COMMANDS,
    PASSTHROUGH_COMMANDS,
    RegistryRouter,
    TopicRouter,
    build_application,
)
from chela.telegram.gatewatch import PermissionGateWatcher, StatusRelay
from chela.telegram.monitor import TranscriptMonitor
from chela.telegram.panescan import (
    AskUQ,
    Gate,
    Status,
    detect_askuserquestion,
    detect_permission_gate,
    detect_status,
)
from chela.telegram.parser import Message, parse_entries, parse_line
from chela.telegram.reconcile import (
    TopicClosedHandler,
    TopicManager,
    live_agent_windows,
    reconcile_bindings,
    topic_name_for,
)
from chela.telegram.relay import BotSender, RegistryRelay, TelegramRelay

__all__ = [
    "TranscriptMonitor",
    "PermissionGateWatcher",
    "StatusRelay",
    "Gate",
    "detect_permission_gate",
    "AskUQ",
    "detect_askuserquestion",
    "Status",
    "detect_status",
    "Message",
    "parse_entries",
    "parse_line",
    "BotSender",
    "TelegramRelay",
    "RegistryRelay",
    "TopicRouter",
    "RegistryRouter",
    "BindingRegistry",
    "default_bindings_path",
    "build_application",
    "BRIDGE_COMMANDS",
    "MENU_COMMANDS",
    "PASSTHROUGH_COMMANDS",
    "escape_markdown_v2",
    "to_code_block",
    "to_markdown_v2",
    "to_plain_text",
    "TopicManager",
    "TopicClosedHandler",
    "reconcile_bindings",
    "live_agent_windows",
    "topic_name_for",
]
