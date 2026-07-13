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
import re
from typing import Callable

from chela import config, messenger
from chela.telegram import media
from chela.telegram.format import to_code_block
from chela.telegram.interactive import (
    SELECT_SETTLE_S,
    decode_callback,
    select_keystrokes,
    select_keystrokes_relative,
    split_select_keys,
)
from chela.telegram.panescan import detect_askuserquestion

log = logging.getLogger(__name__)

# A sender delivers text to a tmux window: ``send(window_id, text) -> ok``.
# Production is ``chela.messenger.send_tmux``; tests inject a recording stub.
Sender = Callable[[str, str], bool]

# Bridge-level slash commands: handled HERE (never forwarded to Claude Code),
# and published to Telegram's "/" command menu so they autocomplete. Any other
# ``/command`` falls through to the window's Claude Code prompt via send_tmux.
# ``(name, description)`` pairs — descriptions are what Telegram shows in the menu.
BRIDGE_COMMANDS: list[tuple[str, str]] = [
    ("screenshot", "Snapshot the terminal (with control keys)"),
    ("esc", "Send Escape to interrupt the agent"),
]

# Claude Code slash commands worth surfacing in Telegram's "/" autocomplete menu
# but NOT intercepted by the bridge: no CommandHandler is registered for these, so
# they fall through the catch-all text handler to the window's Claude Code prompt
# (send_tmux) exactly like any other ``/command``. Published alongside
# BRIDGE_COMMANDS so the operator gets autocomplete without the bridge owning them.
PASSTHROUGH_COMMANDS: list[tuple[str, str]] = [
    ("clear", "Clear the agent's conversation (forwarded to Claude Code)"),
]

# Everything published to Telegram's "/" menu: bridge-intercepted + passthrough.
MENU_COMMANDS: list[tuple[str, str]] = BRIDGE_COMMANDS + PASSTHROUGH_COMMANDS

# A leading ``/command``, with the ``@botname`` suffix Telegram appends in GROUPS.
# Anchored, and the command must END the token (``(?=\s|$)``), so a path-like
# ``/home/liav/x`` is not a command and a body ``@`` is never in scope.
_COMMAND_RE = re.compile(r"^/([A-Za-z0-9_]+)(?:@([A-Za-z0-9_]+))?(?=\s|$)")


def resolve_command_for_window(text: str, bot_username: str | None) -> str | None:
    """The text to forward to Claude Code, or None to DROP it.

    In a group, Telegram appends ``@<botname>`` to a command tapped from the "/" menu
    to disambiguate between bots: ``/clear`` is delivered as ``/clear@chelamuxbot``.
    PTB's ``CommandHandler`` strips that natively, so the BRIDGE commands
    (:data:`BRIDGE_COMMANDS`) were fine — but the passthrough path
    (:data:`PASSTHROUGH_COMMANDS`) forwards the raw text, so Claude Code received the
    literal ``/clear@chelamuxbot``, did not recognise its own command, and took it as a
    plain prompt. The session was never cleared. That broke EVERY Claude Code slash
    command tapped from the menu in a group, not just ``/clear``.

    Only the FIRST token is ever rewritten, and only when it is a real ``/command``.
    An ``@`` anywhere in the body is left alone — we pass window ids (``look at @3``),
    handles and emails through this path constantly, and mangling those would be a far
    worse bug than the one being fixed.

    ``/cmd@some_other_bot`` is DROPPED. It is explicitly addressed to a different bot in
    the group, so it was never meant for this agent; forwarding it would type a stray
    command into a Claude session (our bridge sees it at all only because privacy mode
    is off, which it must be for the bridge to relay ordinary text).

    When our own username isn't known yet (``get_me`` failed, or an update raced
    ``post_init``), we strip the suffix and forward rather than drop: forwarding a
    stripped command is the behaviour the operator asked for, whereas silently eating
    their ``/clear`` reproduces the reported bug in a harder-to-see form.
    """
    m = _COMMAND_RE.match(text)
    if not m:
        return text                       # not a command — never touched
    command, addressed_to = m.group(1), m.group(2)
    if addressed_to is None:
        return text                       # bare /command (DM style) — already correct
    if bot_username and addressed_to.lower() != bot_username.lower():
        return None                       # meant for another bot in the group
    return f"/{command}{text[m.end():]}"  # our command: strip the suffix, keep the args

# A terminal snapshot is trimmed to its most recent characters so a single
# ``/screenshot`` reply stays under Telegram's 4096-char per-message limit
# (with headroom for the code-fence markup).
_SNAPSHOT_MAX_CHARS = 3500

# The control-key inline keyboard attached to every ``/screenshot`` reply, so an
# operator can drive the bound terminal from Telegram (ccbot's "with control
# keys" parity). Each entry is ``(label, key_id, tmux_key)``: the button caption,
# the id packed into the callback payload, and the tmux ``send-keys`` key name
# delivered on a tap. Rows render exactly as grouped here.
#
# The arrows are **glyph-only** — the direction is in the glyph, and a worded
# caption ("→ Right") only steals width Telegram then truncates on a narrow
# phone. ``ref`` is the odd one out: it fires no key, it re-captures the pane
# (see :data:`_REFRESH_KEY_ID`), so it carries no tmux key and gets its own
# full-width row — it is the button reached for most, between keypresses.
SCREENSHOT_KEYS: list[list[tuple[str, str, str | None]]] = [
    [("␣ Space", "spc", "Space"), ("↑", "up", "Up"), ("⇥ Tab", "tab", "Tab")],
    [("←", "lt", "Left"), ("↓", "dn", "Down"), ("→", "rt", "Right")],
    [("⎋ Esc", "esc", "Escape"), ("^C", "cc", "C-c"), ("⏎ Enter", "ent", "Enter")],
    [("🔄", "ref", None)],
]

# Callback-data prefix marking a control-key tap from the screenshot keyboard,
# so the single CallbackQueryHandler can tell these taps from any other.
_KEY_CB_PREFIX = "k:"

# The key_id of the 🔄 button: a tap re-captures the pane instead of sending a
# key, so it is deliberately absent from :data:`_KEY_ACTIONS`.
_REFRESH_KEY_ID = "ref"

# key_id → (tmux key name, toast label), flattened from :data:`SCREENSHOT_KEYS`
# so a button and the key it fires can never drift apart. The callback handler
# looks a tapped ``key_id`` up here; the target window is re-resolved from the
# message's topic (never trusted from the payload), so no window id is packed in.
# Keyless buttons (🔄) are excluded — they are handled on their own.
_KEY_ACTIONS: dict[str, tuple[str, str]] = {
    key_id: (tmux_key, label)
    for row in SCREENSHOT_KEYS
    for (label, key_id, tmux_key) in row
    if tmux_key is not None
}


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
    send_key=None,
):
    """Build a ``python-telegram-bot`` Application wired to ``router``.

    ``router`` is any object with ``route(chat_id, thread_id, text)`` and
    ``resolve(chat_id, thread_id)`` methods — :class:`TopicRouter` or
    :class:`RegistryRouter`.

    Handler order matters (PTB runs at most one handler per group): the
    bridge-level commands in :data:`BRIDGE_COMMANDS` are registered FIRST, so
    ``/screenshot`` and ``/esc`` are handled here and never reach Claude Code. A
    catch-all text handler then forwards everything else — including every OTHER
    ``/command`` (``filters.TEXT`` matches them), such as the passthrough
    ``/clear`` in :data:`PASSTHROUGH_COMMANDS` — to the window's Claude Code
    prompt via ``router.route`` → ``send_tmux``. Photo (``filters.PHOTO``) and
    document (``filters.Document.ALL``) handlers are registered just before that
    text catch-all: a media message pasted into a bound topic is downloaded to
    ``CHELA_DIR/documents/`` and its saved path forwarded to the window (so Claude
    Code can ``Read`` it), rather than being silently dropped — see
    :mod:`chela.telegram.media`. On start-up :data:`MENU_COMMANDS` (the bridge
    commands plus the passthrough ones) is published via ``set_my_commands`` so it
    autocompletes in Telegram's "/" menu. PTB is imported here (not at module load) so this module — and the pure
    router — do not require the optional ``[telegram]`` extra.

    ``capture`` / ``send_escape`` / ``send_key`` back the commands: ``/screenshot``
    captures ``capture(window_id, ansi=True)`` (default
    :func:`chela.messenger.capture_pane`) and replies with a PNG rendered by
    :func:`chela.telegram.screenshot.text_to_image` (a monospaced text block if
    Pillow is unavailable), carrying an :class:`~telegram.InlineKeyboardMarkup` of
    control keys (:data:`SCREENSHOT_KEYS`) so an operator can drive the terminal
    from their phone; ``/esc`` fires ``send_escape(window_id)`` (default
    :func:`chela.messenger.send_escape`). Tapping a control key routes through a
    single :class:`~telegram.ext.CallbackQueryHandler` that fires
    ``send_key(window_id, tmux_key)`` (default :func:`chela.messenger.send_key`)
    and refreshes the snapshot in place; its 🔄 key sends nothing and replies with
    a fresh snapshot instead. Every path resolves its target window
    through ``router.resolve`` — the callback re-resolves from the keyboard
    message's topic and never trusts a window id off the wire — so they honour the
    exact same chat/topic gate as a routed message, stay silent outside it, and
    reply into the SAME forum topic. Injectable for testing.

    Taps on an **AskUserQuestion** answer keyboard (the ``qa:`` callbacks the pane
    watcher attaches — see :mod:`chela.telegram.gatewatch`) route to a second
    :class:`~telegram.ext.CallbackQueryHandler`, matched first by a ``^qa:``
    pattern so it never shadows the ``k:`` screenshot keys. A semantic ``qa:<i>``
    tap re-reads the live ``❯`` cursor from the pane and injects the cursor-relative
    Down/Up presses + Enter via ``send_key`` to select and submit option ``i``
    (never a blind ``Down``×i, since the operator may have arrowed the selector);
    the nav-fallback keys drive the selector by hand. Like the control-key handler,
    it re-resolves the window from the message's own topic through ``router.resolve``
    and never trusts a window id off the wire.

    ``on_topic_closed`` (optional) is a callable ``(thread_id) -> None`` invoked on
    a ``StatusUpdate.FORUM_TOPIC_CLOSED`` service message — Slice B's auto-topics
    wires :meth:`chela.telegram.reconcile.TopicClosedHandler.handle` here to
    unbind (never kill) a window when its topic is closed from Telegram. Left
    ``None`` (single-topic / manual bindings), no such handler is registered.
    """
    try:
        from telegram import (
            BotCommand,
            InlineKeyboardButton,
            InlineKeyboardMarkup,
            InputMediaPhoto,
        )
        from telegram.ext import (
            Application,
            CallbackQueryHandler,
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
    send_key = send_key or messenger.send_key

    def _screenshot_keyboard() -> "InlineKeyboardMarkup":
        """The control-key keyboard attached to every ``/screenshot`` reply."""
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(label, callback_data=f"{_KEY_CB_PREFIX}{key_id}")
                    for (label, key_id, _tmux) in row
                ]
                for row in SCREENSHOT_KEYS
            ]
        )

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
        # In a group, a command tapped from the "/" menu arrives as `/clear@ourbot`.
        # Claude Code doesn't know that name, so the suffix must go before we forward
        # (see resolve_command_for_window); a command aimed at ANOTHER bot is dropped.
        text = resolve_command_for_window(msg.text, bot["username"])
        if text is None:
            log.debug("dropping command addressed to another bot: %s", msg.text)
            return
        try:
            router.route(chat_id, thread_id, text)
        except Exception:  # a stuck tmux send must not wedge the update queue
            log.exception("inbound routing failed")

    async def _on_photo(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Download a pasted photo and forward its path to the topic's window.

        The window is resolved from the message's OWN topic through
        ``router.resolve`` (the same chat/topic gate as a text message); the file
        is delivered via :func:`chela.messenger.send_tmux` so Claude Code can
        ``Read`` the image by path. See :func:`chela.telegram.media.receive_photo`.
        """
        msg = update.message
        if msg is None or not getattr(msg, "photo", None):
            return
        chat = update.effective_chat
        chat_id = chat.id if chat else None
        thread_id = getattr(msg, "message_thread_id", None)
        try:
            await media.receive_photo(
                msg, chat_id, thread_id,
                resolve=router.resolve,
                deliver=messenger.send_tmux,
                docs_dir=config.CHELA_DIR / "documents",
            )
        except Exception:  # a media hiccup must not wedge the update queue
            log.exception("inbound photo handling failed")

    async def _on_document(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Download a pasted file and forward its path to the topic's window.

        Same gate/deliver contract as :func:`_on_photo`; rejects a file over
        Telegram's 20 MB bot download cap before fetching. See
        :func:`chela.telegram.media.receive_document`.
        """
        msg = update.message
        if msg is None or not getattr(msg, "document", None):
            return
        chat = update.effective_chat
        chat_id = chat.id if chat else None
        thread_id = getattr(msg, "message_thread_id", None)
        try:
            await media.receive_document(
                msg, chat_id, thread_id,
                resolve=router.resolve,
                deliver=messenger.send_tmux,
                docs_dir=config.CHELA_DIR / "documents",
            )
        except Exception:  # a media hiccup must not wedge the update queue
            log.exception("inbound document handling failed")

    async def _reply_text_snapshot(msg, pane: str) -> None:
        """Fallback when the PNG renderer is unavailable: a monospaced text block.

        Trims to the most recent output so a single reply stays under Telegram's
        4096-char limit, and drops to plain text if the MarkdownV2 code fence is
        rejected. Carries the same control-key keyboard as the PNG reply so the
        keys still drive the terminal when Pillow is absent.
        """
        body = pane.rstrip("\n")[-_SNAPSHOT_MAX_CHARS:]
        keyboard = _screenshot_keyboard()
        try:
            await msg.reply_text(
                to_code_block(body), parse_mode="MarkdownV2", reply_markup=keyboard
            )
        except Exception:  # MarkdownV2 edge case — resend as plain text
            log.debug("MarkdownV2 screenshot rejected; sending plain", exc_info=True)
            await msg.reply_text(body, reply_markup=keyboard)

    async def _reply_screenshot(reply_to, window_id: str) -> None:
        """Capture ``window_id`` and reply to ``reply_to`` with a PNG snapshot.

        Shared by the ``/screenshot`` command and both 🔄 refresh buttons (the
        control keyboard's and AskUserQuestion's): replies into the message's own
        forum topic, carries the control-key keyboard, and degrades to a text
        snapshot when Pillow is absent or rendering fails.
        """
        try:
            pane = capture(window_id, ansi=True)  # -e keeps SGR colour for the PNG
        except Exception:  # a capture hiccup must not wedge the update queue
            log.exception("screenshot capture failed for %s", window_id)
            return
        if not pane.strip():
            await reply_to.reply_text("❌ Couldn't capture the terminal pane.")
            return
        try:
            from chela.telegram.screenshot import text_to_image
        except ImportError:  # Pillow not installed — degrade to a text snapshot
            log.debug("Pillow unavailable; sending screenshot as text")
            await _reply_text_snapshot(reply_to, pane)
            return
        try:
            png = await asyncio.to_thread(text_to_image, pane)
            # reply_photo inherits the message's forum topic, so the PNG lands
            # back in the SAME topic the command arrived on; the control-key
            # keyboard rides along so the terminal can be driven from the reply.
            await reply_to.reply_photo(
                photo=io.BytesIO(png),
                filename="screenshot.png",
                reply_markup=_screenshot_keyboard(),
            )
        except Exception:  # rendering/upload failed — fall back to text
            log.exception("screenshot render failed for %s; sending text", window_id)
            await _reply_text_snapshot(reply_to, pane)

    async def _on_screenshot(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
        msg = update.message
        if msg is None:
            return
        window_id = _window_for(update)
        if window_id is None:  # wrong chat / unbound topic — stay silent
            return
        await _reply_screenshot(msg, window_id)

    async def _on_esc(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
        msg = update.message
        if msg is None:
            return
        window_id = _window_for(update)
        if window_id is None:  # wrong chat / unbound topic — stay silent
            return
        ok = send_escape(window_id)
        await msg.reply_text("⎋ Sent Escape" if ok else "❌ Couldn't send Escape.")

    async def _on_key(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Deliver a tapped control key to the window bound to the keyboard's topic.

        The target window is re-resolved from the callback message's own topic
        via ``router.resolve`` (never trusted from the callback payload), so a tap
        honours the same chat/topic gate as ``/screenshot`` itself. The tap is
        always answered — even when gated out or unknown — so Telegram stops the
        button's spinner. On a successful key press the snapshot is refreshed in
        place (best-effort) so the terminal's reaction is visible. The 🔄 button
        sends no key: it replies with a FRESH snapshot (like the AskUserQuestion
        refresh), because an in-place edit of an unchanged pane is rejected by
        Telegram — a refresh tap must always show something.
        """
        query = update.callback_query
        if query is None:
            return
        data = query.data or ""
        if not data.startswith(_KEY_CB_PREFIX):
            return  # not ours — some other inline keyboard
        key_id = data[len(_KEY_CB_PREFIX):]
        action = _KEY_ACTIONS.get(key_id)
        msg = query.message
        chat = update.effective_chat
        chat_id = chat.id if chat else None
        thread_id = getattr(msg, "message_thread_id", None) if msg else None
        window_id = router.resolve(chat_id, thread_id)
        if window_id is None:  # wrong chat / unbound topic — stay silent
            await query.answer()
            return
        if key_id == _REFRESH_KEY_ID:
            await query.answer("🔄")
            if msg is not None:
                await _reply_screenshot(msg, window_id)
            return
        if action is None:  # bad payload
            await query.answer()
            return
        tmux_key, label = action
        ok = send_key(window_id, tmux_key)
        await query.answer(label if ok else "❌ send failed")
        if not ok or msg is None:
            return
        # Best-effort refresh so the keypress's effect shows without re-running
        # /screenshot — the whole point of driving the terminal from a phone.
        try:
            from chela.telegram.screenshot import text_to_image

            await asyncio.sleep(0.4)  # let the pane repaint after the keystroke
            pane = capture(window_id, ansi=True)
            if not pane.strip():
                return
            png = await asyncio.to_thread(text_to_image, pane)
            await query.edit_message_media(
                media=InputMediaPhoto(io.BytesIO(png)),
                reply_markup=_screenshot_keyboard(),
            )
        except Exception:  # unchanged pane, stale message, or no Pillow — ignore
            log.debug("snapshot refresh after keypress failed", exc_info=True)

    def _select_keys_for(window_id: str, target: int) -> "list[str]":
        """Cursor-relative keystrokes to pick option ``target``, re-reading the pane.

        The selector may have been arrowed since the buttons were posted, so the
        live ``❯`` cursor is re-read (:func:`detect_askuserquestion`) and the move
        is computed relative to it — never a blind ``Down``×i from an assumed
        option 0. Falls back to the blind sequence only when the pane can't be read
        as a single-select selector (so a tap still does something best-effort).
        """
        try:
            uq = detect_askuserquestion(capture(window_id))
        except Exception:  # a capture hiccup must not wedge the tap
            uq = None
        if uq is not None and not uq.multi and uq.cursor >= 0 and target < len(uq.options):
            return select_keystrokes_relative(target, uq.cursor)
        return select_keystrokes(target)

    async def _on_qa(update, _context: "ContextTypes.DEFAULT_TYPE") -> None:
        """Answer an AskUserQuestion tap (semantic option or navigation key).

        Mirrors :func:`_on_key`: the target window is re-resolved from the
        callback message's OWN topic via ``router.resolve`` (never trusted from
        the payload, CMX-8), so a tap honours the same chat/topic gate as the
        prompt it rides on. A semantic ``qa:<i>`` tap re-reads the live ``❯``
        cursor and injects the cursor-relative Down/Up presses + Enter
        (:func:`_select_keys_for`) to select and submit option ``i``; a
        ``qa:nav:<key>`` tap sends one key so the human can drive the selector by
        hand; ``qa:nav:ref`` re-screenshots. Every tap is answered so Telegram
        stops the button's spinner, even when gated out or unrecognised.
        """
        query = update.callback_query
        if query is None:
            return
        action = decode_callback(query.data or "")
        if action is None:
            return  # not ours (or invalid) — some other inline keyboard
        msg = query.message
        chat = update.effective_chat
        chat_id = chat.id if chat else None
        thread_id = getattr(msg, "message_thread_id", None) if msg else None
        window_id = router.resolve(chat_id, thread_id)
        if window_id is None:  # wrong chat / unbound topic — stay silent
            await query.answer()
            return
        kind, payload = action
        if kind == "refresh":
            await query.answer("🔄")
            if msg is not None:
                await _reply_screenshot(msg, window_id)
            return
        if kind == "key":
            tmux_key, label = payload
            ok = send_key(window_id, tmux_key)
            await query.answer(label if ok else "❌ send failed")
            return
        # kind == "select": re-read the live cursor, then inject the keystrokes
        # that move to + submit option i (never a blind Down×i from option 0).
        moves, submit = split_select_keys(_select_keys_for(window_id, payload))
        ok = True
        for key in moves:
            ok = send_key(window_id, key) and ok
        if moves and submit:
            # The selector commits arrow moves on a render tick; an Enter in the
            # same input burst submits the row it was on BEFORE the last move (live
            # CMX-32: tapping 3 answered 2). Let it settle first.
            await asyncio.sleep(SELECT_SETTLE_S)
        for key in submit:
            ok = send_key(window_id, key) and ok
        # The buttons are bare numeric selectors now (the option text lives in the
        # message body, the one surface Telegram doesn't truncate), so the toast
        # echoes the number rather than reading a label back off the keyboard.
        await query.answer(f"✓ Option {payload + 1}" if ok else "❌ send failed")

    # Our own @username, learned from the Bot API at startup (never hardcoded) and
    # cached here — it is needed on EVERY group command, and get_me() is a network
    # call. A dict so the _post_init/_on_message closures share one cell.
    bot: dict[str, str | None] = {"username": None}

    async def _post_init(app) -> None:
        """Cache our @username, and publish the menu commands to Telegram's "/" menu.

        Both bridge-intercepted commands and passthrough Claude Code commands
        (:data:`MENU_COMMANDS`) are published so they autocomplete; only the
        bridge ones have handlers, the rest fall through to send_tmux.
        """
        try:
            bot["username"] = (await app.bot.get_me()).username
            log.info("telegram: bridging as @%s", bot["username"])
        except Exception:  # not fatal: resolve_command_for_window still strips the suffix
            log.warning("could not read the bot's own username", exc_info=True)
        try:
            await app.bot.set_my_commands(
                [BotCommand(name, desc) for name, desc in MENU_COMMANDS]
            )
        except Exception:  # a menu-registration failure must not stop the bridge
            log.warning("could not set Telegram command menu", exc_info=True)

    application = Application.builder().token(token).post_init(_post_init).build()
    # Command handlers FIRST so /screenshot and /esc are intercepted here; the
    # catch-all text handler forwards every other message (and /command) onward.
    application.add_handler(CommandHandler("screenshot", _on_screenshot))
    application.add_handler(CommandHandler("esc", _on_esc))
    # AskUserQuestion taps (``qa:``) are matched FIRST via a pattern so they route
    # to _on_qa; PTB runs one handler per group, and the pattern-less _on_key
    # below picks up the /screenshot ``k:`` taps (and ignores anything else).
    application.add_handler(CallbackQueryHandler(_on_qa, pattern=r"^qa:"))
    application.add_handler(CallbackQueryHandler(_on_key))
    # Media handlers BEFORE the text catch-all (PTB runs one handler per group):
    # a photo/document pasted into a bound topic is downloaded and its path
    # forwarded to the window, instead of being silently dropped.
    application.add_handler(MessageHandler(filters.PHOTO, _on_photo))
    application.add_handler(MessageHandler(filters.Document.ALL, _on_document))
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
