"""Inbound routing — deliver Telegram topic messages to a tmux window.

This is the inbound half of the bridge (Telegram → tmux); the outbound half
(tmux → Telegram) lives in :mod:`chela.telegram.relay` / :mod:`chela.telegram.
monitor`. A ``python-telegram-bot`` :class:`~telegram.ext.Application` listens on
the bound forum topic and routes each incoming message to the mapped tmux window
via :func:`chela.messenger.send_tmux` — the reliable-submit path (load-buffer +
paste + stranded-chip guard). We never reimplement tmux sending here.

Topic↔window resolution goes through chela's own registry (tmux is the source of
truth via :mod:`chela.discovery`), NOT ccbot's ``session_map.json`` (Decision 1).

Two pure routers share the same ``route(chat_id, thread_id, text)`` contract and
injectable ``sender`` so routing is unit-testable with no live Telegram:

* :class:`TopicRouter` — a single fixed ``(chat_id, topic_id) → window_id``
  binding (the original single-window bridge).
* :class:`RegistryRouter` — the multi-topic generalisation: it resolves the
  window per message from a :class:`~chela.telegram.bindings.BindingRegistry`
  (``window_for_thread``), so one process routes N topics ↔ N windows. This is
  what the ``chela telegram`` daemon uses.

Both still gate on the bound ``chat_id`` (the CMX-8 security boundary) before
delivering via :func:`chela.messenger.send_tmux`. :func:`build_application` is the
thin ``python-telegram-bot`` glue and accepts either router; PTB is imported
lazily inside it so the pure routers (and the test suite) never depend on the
``[telegram]`` extra.

Adapted from six-ddc/ccbot (https://github.com/six-ddc/ccbot), which is
MIT-licensed. See the top-level NOTICE file for the upstream copyright and
attribution.
"""
from __future__ import annotations

import logging
from typing import Callable

from chela import messenger

log = logging.getLogger(__name__)

# A sender delivers text to a tmux window: ``send(window_id, text) -> ok``.
# Production is ``chela.messenger.send_tmux``; tests inject a recording stub.
Sender = Callable[[str, str], bool]


class TopicRouter:
    """Routes an inbound Telegram message to one bound tmux window.

    Binds a single ``(chat_id, topic_id) → window_id``. :meth:`route` validates
    that a message came from the bound chat (and, when a topic is configured, the
    bound topic) before delivering its text to the window via the injected
    ``sender`` — :func:`chela.messenger.send_tmux` in production, a stub in tests.
    Messages from any other chat/topic, and empty messages, are dropped.
    """

    def __init__(
        self,
        chat_id: str | int,
        window_id: str,
        topic_id: str | int | None = None,
        *,
        sender: Sender | None = None,
    ):
        self._chat_id = str(chat_id)
        self._window_id = window_id
        # Telegram's General topic reports message_thread_id as None (or 1); a
        # named topic reports its id. Normalise a configured topic to str so it
        # compares cleanly against the int thread id off the wire.
        self._topic_id = str(topic_id) if topic_id not in (None, "") else None
        self._sender = sender or messenger.send_tmux

    def route(self, chat_id: str | int | None, topic_id: str | int | None, text: str) -> bool:
        """Deliver ``text`` to the bound window if it came from the bound topic.

        Returns True if the message was delivered, False if it was dropped
        (wrong chat/topic, empty text) or the tmux send failed.
        """
        if not text or not text.strip():
            return False
        if chat_id is None or str(chat_id) != self._chat_id:
            log.debug("dropping inbound from chat %s (bound=%s)", chat_id, self._chat_id)
            return False
        if self._topic_id is not None and (
            topic_id is None or str(topic_id) != self._topic_id
        ):
            log.debug("dropping inbound from topic %s (bound=%s)", topic_id, self._topic_id)
            return False
        log.info("Telegram → %s: %s", self._window_id, text.splitlines()[0][:80])
        return self._sender(self._window_id, text)


class RegistryRouter:
    """Routes inbound messages to N windows via a :class:`BindingRegistry`.

    The multi-topic generalisation of :class:`TopicRouter`: instead of one fixed
    binding, each message's ``thread_id`` is resolved to a window through the
    registry (``window_for_thread``). Messages from the wrong chat, from an
    unbound topic (including a forum's General topic, which has no thread id), or
    with empty text are dropped. The registry stays the single source of truth,
    so Slice B can mutate bindings live and this router follows without changes.
    """

    def __init__(self, registry, *, sender: Sender | None = None):
        self._registry = registry
        # The chat gate is the CMX-8 security boundary; a registry with no chat
        # bound routes nothing (fail-closed) rather than accepting every chat.
        self._chat_id = str(registry.chat_id) if registry.chat_id is not None else None
        self._sender = sender or messenger.send_tmux

    def route(self, chat_id: str | int | None, topic_id: str | int | None, text: str) -> bool:
        """Deliver ``text`` to the window bound to ``topic_id``, else drop it.

        Returns True only if the message was delivered; False if it was dropped
        (no chat bound, wrong chat, unbound topic, empty text) or the send failed.
        """
        if not text or not text.strip():
            return False
        if self._chat_id is None or chat_id is None or str(chat_id) != self._chat_id:
            log.debug("dropping inbound from chat %s (bound=%s)", chat_id, self._chat_id)
            return False
        window_id = self._registry.window_for_thread(topic_id)
        if window_id is None:
            log.debug("dropping inbound from unbound topic %s", topic_id)
            return False
        log.info("Telegram → %s: %s", window_id, text.splitlines()[0][:80])
        return self._sender(window_id, text)


def build_application(token: str, router, *, on_topic_closed=None):
    """Build a ``python-telegram-bot`` Application wired to ``router``.

    ``router`` is any object with a ``route(chat_id, thread_id, text)`` method —
    :class:`TopicRouter` or :class:`RegistryRouter`. Registers a single text
    handler that pulls ``(chat_id, thread_id, text)`` off each update and hands it
    to ``router.route``. Slash commands are
    forwarded too (``filters.TEXT`` includes them) so ``/`` commands reach the
    window's Claude Code prompt via ``send_tmux``'s slash-command path. PTB is
    imported here (not at module load) so this module — and the pure router — do
    not require the optional ``[telegram]`` extra.

    ``on_topic_closed`` (optional) is a callable ``(thread_id) -> None`` invoked on
    a ``StatusUpdate.FORUM_TOPIC_CLOSED`` service message — Slice B's auto-topics
    wires :meth:`chela.telegram.reconcile.TopicClosedHandler.handle` here to
    unbind (never kill) a window when its topic is closed from Telegram. Left
    ``None`` (single-topic / manual bindings), no such handler is registered.
    """
    try:
        from telegram.ext import Application, ContextTypes, MessageHandler, filters
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "Inbound Telegram routing needs python-telegram-bot. "
            "Install the extra:  uv sync --extra telegram  "
            "(or:  pip install 'chelamux[telegram]')"
        ) from e

    async def _on_message(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
        msg = update.message
        if msg is None or not msg.text:
            return
        chat = update.effective_chat
        chat_id = chat.id if chat else None
        # message_thread_id is absent outside a forum topic; getattr guards it.
        thread_id = getattr(msg, "message_thread_id", None)
        try:
            router.route(chat_id, thread_id, msg.text)
        except Exception:  # a stuck tmux send must not wedge the update queue
            log.exception("inbound routing failed")

    application = Application.builder().token(token).build()
    application.add_handler(MessageHandler(filters.TEXT, _on_message))

    if on_topic_closed is not None:
        async def _on_topic_closed(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
            msg = update.message
            if msg is None:
                return
            # The closed topic's own thread id rides on the service message.
            thread_id = getattr(msg, "message_thread_id", None)
            try:
                on_topic_closed(thread_id)
            except Exception:  # never let a bad unbind wedge the update queue
                log.exception("topic-closed handling failed")

        application.add_handler(
            MessageHandler(filters.StatusUpdate.FORUM_TOPIC_CLOSED, _on_topic_closed)
        )

    return application
