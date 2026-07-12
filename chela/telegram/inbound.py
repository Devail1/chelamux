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

import asyncio
import io
import logging
from typing import Callable

from chela import messenger
from chela.telegram.format import to_code_block

log = logging.getLogger(__name__)

# A sender delivers text to a tmux window: ``send(window_id, text) -> ok``.
# Production is ``chela.messenger.send_tmux``; tests inject a recording stub.
Sender = Callable[[str, str], bool]

# Bridge-level slash commands: handled HERE (never forwarded to Claude Code),
# and published to Telegram's "/" command menu so they autocomplete. Any other
# ``/command`` falls through to the window's Claude Code prompt via send_tmux.
# ``(name, description)`` pairs — descriptions are what Telegram shows in the menu.
BRIDGE_COMMANDS: list[tuple[str, str]] = [
    ("screenshot", "Snapshot the agent's terminal pane"),
    ("esc", "Send Escape to interrupt the agent"),
]

# A terminal snapshot is trimmed to its most recent characters so a single
# ``/screenshot`` reply stays under Telegram's 4096-char per-message limit
# (with headroom for the code-fence markup).
_SNAPSHOT_MAX_CHARS = 3500


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

    def resolve(
        self, chat_id: str | int | None, topic_id: str | int | None
    ) -> str | None:
        """The bound window for a message, or None if it fails the chat/topic gate.

        The gating half of :meth:`route`, shared with the bridge's command
        handlers (``/screenshot``, ``/esc``) so they honour the exact same
        chat/topic boundary before touching a window.
        """
        if chat_id is None or str(chat_id) != self._chat_id:
            log.debug("dropping inbound from chat %s (bound=%s)", chat_id, self._chat_id)
            return None
        if self._topic_id is not None and (
            topic_id is None or str(topic_id) != self._topic_id
        ):
            log.debug("dropping inbound from topic %s (bound=%s)", topic_id, self._topic_id)
            return None
        return self._window_id

    def route(self, chat_id: str | int | None, topic_id: str | int | None, text: str) -> bool:
        """Deliver ``text`` to the bound window if it came from the bound topic.

        Returns True if the message was delivered, False if it was dropped
        (wrong chat/topic, empty text) or the tmux send failed.
        """
        if not text or not text.strip():
            return False
        window_id = self.resolve(chat_id, topic_id)
        if window_id is None:
            return False
        log.info("Telegram → %s: %s", window_id, text.splitlines()[0][:80])
        return self._sender(window_id, text)


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

    def resolve(
        self, chat_id: str | int | None, topic_id: str | int | None
    ) -> str | None:
        """The window bound to ``topic_id``, or None if it fails the chat gate.

        The gating half of :meth:`route`, shared with the bridge's command
        handlers (``/screenshot``, ``/esc``) so they honour the exact same chat
        and per-topic boundary before touching a window.
        """
        if self._chat_id is None or chat_id is None or str(chat_id) != self._chat_id:
            log.debug("dropping inbound from chat %s (bound=%s)", chat_id, self._chat_id)
            return None
        window_id = self._registry.window_for_thread(topic_id)
        if window_id is None:
            log.debug("dropping inbound from unbound topic %s", topic_id)
            return None
        return window_id

    def route(self, chat_id: str | int | None, topic_id: str | int | None, text: str) -> bool:
        """Deliver ``text`` to the window bound to ``topic_id``, else drop it.

        Returns True only if the message was delivered; False if it was dropped
        (no chat bound, wrong chat, unbound topic, empty text) or the send failed.
        """
        if not text or not text.strip():
            return False
        window_id = self.resolve(chat_id, topic_id)
        if window_id is None:
            return False
        log.info("Telegram → %s: %s", window_id, text.splitlines()[0][:80])
        return self._sender(window_id, text)


def build_application(
    token: str,
    router,
    *,
    on_topic_closed=None,
    capture=None,
    send_escape=None,
):
    """Build a ``python-telegram-bot`` Application wired to ``router``.

    ``router`` is any object with ``route(chat_id, thread_id, text)`` and
    ``resolve(chat_id, thread_id)`` methods — :class:`TopicRouter` or
    :class:`RegistryRouter`.

    Handler order matters (PTB runs at most one handler per group): the
    bridge-level commands in :data:`BRIDGE_COMMANDS` are registered FIRST, so
    ``/screenshot`` and ``/esc`` are handled here and never reach Claude Code. A
    catch-all text handler then forwards everything else — including every OTHER
    ``/command`` (``filters.TEXT`` matches them) — to the window's Claude Code
    prompt via ``router.route`` → ``send_tmux``. On start-up the same command set
    is published via ``set_my_commands`` so it autocompletes in Telegram's "/"
    menu. PTB is imported here (not at module load) so this module — and the pure
    router — do not require the optional ``[telegram]`` extra.

    ``capture`` / ``send_escape`` back the two commands: ``/screenshot`` captures
    ``capture(window_id, ansi=True)`` (default :func:`chela.messenger.capture_pane`)
    and replies with a PNG rendered by :func:`chela.telegram.screenshot.text_to_image`
    (a monospaced text block if Pillow is unavailable), while ``/esc`` fires
    ``send_escape(window_id)`` (default :func:`chela.messenger.send_escape`). Both
    resolve the target window through ``router.resolve``, so they honour the exact
    same chat/topic gate as a routed message and stay silent for messages outside
    it — and reply into the SAME forum topic. Injectable for testing.

    ``on_topic_closed`` (optional) is a callable ``(thread_id) -> None`` invoked on
    a ``StatusUpdate.FORUM_TOPIC_CLOSED`` service message — Slice B's auto-topics
    wires :meth:`chela.telegram.reconcile.TopicClosedHandler.handle` here to
    unbind (never kill) a window when its topic is closed from Telegram. Left
    ``None`` (single-topic / manual bindings), no such handler is registered.
    """
    try:
        from telegram import BotCommand
        from telegram.ext import (
            Application,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )
    except ImportError as e:  # pragma: no cover - exercised only without the extra
        raise ImportError(
            "Inbound Telegram routing needs python-telegram-bot. "
            "Install the extra:  uv sync --extra telegram  "
            "(or:  pip install 'chelamux[telegram]')"
        ) from e

    capture = capture or messenger.capture_pane
    send_escape = send_escape or messenger.send_escape

    def _window_for(update) -> "str | None":
        """Resolve the bound window for a command update through the chat gate."""
        msg = update.message
        if msg is None:
            return None
        chat = update.effective_chat
        chat_id = chat.id if chat else None
        thread_id = getattr(msg, "message_thread_id", None)
        return router.resolve(chat_id, thread_id)

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

    async def _reply_text_snapshot(msg, pane: str) -> None:
        """Fallback when the PNG renderer is unavailable: a monospaced text block.

        Trims to the most recent output so a single reply stays under Telegram's
        4096-char limit, and drops to plain text if the MarkdownV2 code fence is
        rejected.
        """
        body = pane.rstrip("\n")[-_SNAPSHOT_MAX_CHARS:]
        try:
            await msg.reply_text(to_code_block(body), parse_mode="MarkdownV2")
        except Exception:  # MarkdownV2 edge case — resend as plain text
            log.debug("MarkdownV2 screenshot rejected; sending plain", exc_info=True)
            await msg.reply_text(body)

    async def _on_screenshot(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
        msg = update.message
        if msg is None:
            return
        window_id = _window_for(update)
        if window_id is None:  # wrong chat / unbound topic — stay silent
            return
        try:
            pane = capture(window_id, ansi=True)  # -e keeps SGR colour for the PNG
        except Exception:  # a capture hiccup must not wedge the update queue
            log.exception("screenshot capture failed for %s", window_id)
            return
        if not pane.strip():
            await msg.reply_text("❌ Couldn't capture the terminal pane.")
            return
        try:
            from chela.telegram.screenshot import text_to_image
        except ImportError:  # Pillow not installed — degrade to a text snapshot
            log.debug("Pillow unavailable; sending screenshot as text")
            await _reply_text_snapshot(msg, pane)
            return
        try:
            png = await asyncio.to_thread(text_to_image, pane)
            # reply_photo inherits the message's forum topic, so the PNG lands
            # back in the SAME topic the command arrived on.
            await msg.reply_photo(photo=io.BytesIO(png), filename="screenshot.png")
        except Exception:  # rendering/upload failed — fall back to text
            log.exception("screenshot render failed for %s; sending text", window_id)
            await _reply_text_snapshot(msg, pane)

    async def _on_esc(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
        msg = update.message
        if msg is None:
            return
        window_id = _window_for(update)
        if window_id is None:  # wrong chat / unbound topic — stay silent
            return
        ok = send_escape(window_id)
        await msg.reply_text("⎋ Sent Escape" if ok else "❌ Couldn't send Escape.")

    async def _post_init(app) -> None:
        """Publish the bridge commands to Telegram's "/" autocomplete menu."""
        try:
            await app.bot.set_my_commands(
                [BotCommand(name, desc) for name, desc in BRIDGE_COMMANDS]
            )
        except Exception:  # a menu-registration failure must not stop the bridge
            log.warning("could not set Telegram command menu", exc_info=True)

    application = Application.builder().token(token).post_init(_post_init).build()
    # Command handlers FIRST so /screenshot and /esc are intercepted here; the
    # catch-all text handler forwards every other message (and /command) onward.
    application.add_handler(CommandHandler("screenshot", _on_screenshot))
    application.add_handler(CommandHandler("esc", _on_esc))
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
