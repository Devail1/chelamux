"""Inbound routing (Telegram → tmux) — topic→window against a stub sender.

No live Telegram and no PTB import: :class:`TopicRouter` is driven directly with
``(chat_id, topic_id, text)`` tuples and an injected sender that records every
delivery, so these lock in the routing decision:

  * a message from the bound chat+topic is delivered to the bound window;
  * messages from the wrong chat, the wrong topic, or with empty text are dropped;
  * chat/topic ids compare across int↔str (env config is str, the wire is int);
  * a configured-topic-less router accepts any topic in the bound chat;
  * the sender's own failure/success is propagated as ``route()``'s return.
"""
from __future__ import annotations

import pytest

from chela.telegram.bindings import BindingRegistry
from chela.telegram.inbound import (
    _KEY_ACTIONS,
    _KEY_CB_PREFIX,
    _REFRESH_KEY_ID,
    BRIDGE_COMMANDS,
    MENU_COMMANDS,
    PASSTHROUGH_COMMANDS,
    SCREENSHOT_KEYS,
    RegistryRouter,
    TopicRouter,
    resolve_command_for_window,
)


class _StubSender:
    """Records ``send(window_id, text)`` calls; return value is configurable."""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[str, str]] = []

    def __call__(self, window_id: str, text: str) -> bool:
        self.calls.append((window_id, text))
        return self.ok


def test_routes_bound_chat_and_topic_to_window():
    stub = _StubSender()
    router = TopicRouter("777", "@3", "4", sender=stub)
    # Wire ids arrive as ints; config is str — comparison must bridge the two.
    assert router.route(777, 4, "hello agent") is True
    assert stub.calls == [("@3", "hello agent")]


def test_drops_message_from_wrong_chat():
    stub = _StubSender()
    router = TopicRouter("777", "@3", "4", sender=stub)
    assert router.route(999, 4, "not for us") is False
    assert stub.calls == []


def test_drops_message_from_wrong_topic():
    stub = _StubSender()
    router = TopicRouter("777", "@3", "4", sender=stub)
    assert router.route(777, 9, "other topic") is False
    assert stub.calls == []


def test_drops_general_topic_when_a_topic_is_bound():
    stub = _StubSender()
    router = TopicRouter("777", "@3", "4", sender=stub)
    # General reports thread id None (or 1) — neither matches the bound topic.
    assert router.route(777, None, "in general") is False
    assert router.route(777, 1, "in general") is False
    assert stub.calls == []


def test_drops_empty_and_whitespace_text():
    stub = _StubSender()
    router = TopicRouter("777", "@3", "4", sender=stub)
    assert router.route(777, 4, "") is False
    assert router.route(777, 4, "   \n  ") is False
    assert stub.calls == []


def test_topicless_router_accepts_any_topic_in_bound_chat():
    stub = _StubSender()
    router = TopicRouter("777", "@3", topic_id=None, sender=stub)
    assert router.route(777, None, "general msg") is True
    assert router.route(777, 12, "some topic") is True
    assert router.route(888, 12, "wrong chat") is False
    assert stub.calls == [("@3", "general msg"), ("@3", "some topic")]


def test_empty_string_topic_env_is_treated_as_unbound():
    # os.environ.get returns "" (not None) for an unset-but-present var; the
    # router must treat that the same as no topic, not require thread == "".
    stub = _StubSender()
    router = TopicRouter("777", "@3", "", sender=stub)
    assert router.route(777, 5, "hi") is True
    assert stub.calls == [("@3", "hi")]


def test_send_failure_is_propagated():
    stub = _StubSender(ok=False)
    router = TopicRouter("777", "@3", "4", sender=stub)
    assert router.route(777, 4, "will fail to send") is False
    assert stub.calls == [("@3", "will fail to send")]


def test_none_chat_id_is_dropped():
    stub = _StubSender()
    router = TopicRouter("777", "@3", "4", sender=stub)
    assert router.route(None, 4, "no chat") is False
    assert stub.calls == []


def test_slash_command_is_forwarded_verbatim():
    # send_tmux handles the slash-command Escape path; the router just forwards.
    stub = _StubSender()
    router = TopicRouter("777", "@3", "4", sender=stub)
    assert router.route(777, 4, "/clear") is True
    assert stub.calls == [("@3", "/clear")]


# --------------------------------------------------------------------------
# "/" command menu — what gets published to Telegram's autocomplete
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# Group "/" menu taps — Telegram appends @botname; Claude Code must not see it
# --------------------------------------------------------------------------
#
# Live bug: tapping /clear from the bot's command menu in the forum delivered
# `/clear@chelamuxbot`. The passthrough path forwards text VERBATIM, so Claude Code
# got a string it doesn't recognise and treated it as a plain prompt — the session was
# never cleared. (The bridge commands were fine: PTB's CommandHandler strips the suffix
# natively.) This broke every Claude Code slash command tapped from the menu in a group.

OUR_BOT = "chelamuxbot"


def test_menu_tap_in_a_group_strips_our_bot_suffix():
    assert resolve_command_for_window("/clear@chelamuxbot", OUR_BOT) == "/clear"


def test_bot_suffix_match_is_case_insensitive():
    assert resolve_command_for_window("/clear@ChelamuxBot", OUR_BOT) == "/clear"


def test_command_arguments_survive_the_strip():
    assert resolve_command_for_window("/model@chelamuxbot opus", OUR_BOT) == "/model opus"


def test_a_bare_command_is_unchanged():
    # DM style (Telegram appends nothing) — must pass through byte-identical.
    assert resolve_command_for_window("/clear", OUR_BOT) == "/clear"
    assert resolve_command_for_window("/model opus", OUR_BOT) == "/model opus"


@pytest.mark.parametrize("text", [
    "look at @3 and tell me what it's doing",   # a window id — we use these constantly
    "mail me at a@b.com",
    "ping @liav about the parser",
    "/home/liav/projects/chelamux is the path",  # path-like: not a command at all
    "the file is at src/x.py@v2",
])
def test_an_at_sign_in_the_body_is_never_mangled(text):
    # Only the FIRST token is ever rewritten, and only if it is a real /command.
    # Mangling window ids / handles / emails would be a worse bug than the one fixed.
    assert resolve_command_for_window(text, OUR_BOT) == text


def test_a_command_addressed_to_another_bot_is_dropped():
    # Explicitly aimed at a different bot in the group: forwarding it would type a
    # stray command into a Claude session. None == drop.
    assert resolve_command_for_window("/clear@someotherbot", OUR_BOT) is None
    assert resolve_command_for_window("/start@rando_bot now", OUR_BOT) is None


def test_unknown_own_username_strips_rather_than_drops():
    # get_me() failed or an update raced post_init. Forward the stripped command:
    # silently eating the operator's /clear would reproduce the reported bug in a
    # harder-to-see form.
    assert resolve_command_for_window("/clear@chelamuxbot", None) == "/clear"


def test_compact_menu_tap_in_a_group_strips_our_bot_suffix():
    # /compact is a second passthrough command (CMX-134) — the group @botname
    # suffix must be stripped the same way /clear's is, or Claude Code never
    # recognises its own command and /compact silently degrades to a no-op prompt.
    assert resolve_command_for_window("/compact@chelamuxbot", OUR_BOT) == "/compact"


def test_clear_is_published_to_menu_but_not_bridge_intercepted():
    # /clear autocompletes (it's in the published MENU_COMMANDS) yet is a
    # passthrough — never a bridge command, so no CommandHandler owns it and it
    # falls through to Claude Code (see test_slash_command_is_forwarded_verbatim).
    names = {name for name, _desc in PASSTHROUGH_COMMANDS}
    assert "clear" in names
    bridge_names = {name for name, _desc in BRIDGE_COMMANDS}
    assert "clear" not in bridge_names
    assert MENU_COMMANDS == BRIDGE_COMMANDS + PASSTHROUGH_COMMANDS
    assert ("clear", PASSTHROUGH_COMMANDS[0][1]) in MENU_COMMANDS


def test_compact_is_published_to_menu_but_not_bridge_intercepted():
    # /compact mirrors /clear: a Claude Code slash command, so it autocompletes
    # via the published menu but is never owned by a bridge CommandHandler — it
    # falls through to Claude Code like any other passthrough command.
    names = {name for name, _desc in PASSTHROUGH_COMMANDS}
    assert "compact" in names
    bridge_names = {name for name, _desc in BRIDGE_COMMANDS}
    assert "compact" not in bridge_names
    assert MENU_COMMANDS == BRIDGE_COMMANDS + PASSTHROUGH_COMMANDS
    compact_desc = next(desc for name, desc in PASSTHROUGH_COMMANDS if name == "compact")
    assert ("compact", compact_desc) in MENU_COMMANDS


# --------------------------------------------------------------------------
# RegistryRouter — the multi-topic generalisation, routing via a registry
# --------------------------------------------------------------------------

def _registry(chat="777", *bindings):
    reg = BindingRegistry(chat)
    for window, thread in bindings:
        reg.bind(window, thread)
    return reg


def test_registry_router_routes_each_topic_to_its_window():
    stub = _StubSender()
    reg = _registry("777", ("@3", "42"), ("@7", "88"))
    router = RegistryRouter(reg, sender=stub)
    # Wire ids arrive as ints; the registry compares them as str.
    assert router.route(777, 42, "for three") is True
    assert router.route(777, 88, "for seven") is True
    assert stub.calls == [("@3", "for three"), ("@7", "for seven")]


def test_registry_router_drops_unbound_topic():
    stub = _StubSender()
    router = RegistryRouter(_registry("777", ("@3", "42")), sender=stub)
    assert router.route(777, 999, "no such topic") is False
    assert stub.calls == []


def test_registry_router_drops_general_topic():
    stub = _StubSender()
    router = RegistryRouter(_registry("777", ("@3", "42")), sender=stub)
    # General reports thread id None (or 1); neither is bound.
    assert router.route(777, None, "in general") is False
    assert router.route(777, 1, "in general") is False
    assert stub.calls == []


def test_registry_router_drops_wrong_chat():
    stub = _StubSender()
    router = RegistryRouter(_registry("777", ("@3", "42")), sender=stub)
    assert router.route(999, 42, "wrong chat") is False
    assert stub.calls == []


def test_registry_router_drops_empty_text():
    stub = _StubSender()
    router = RegistryRouter(_registry("777", ("@3", "42")), sender=stub)
    assert router.route(777, 42, "   \n ") is False
    assert stub.calls == []


def test_registry_router_with_no_chat_bound_routes_nothing():
    # Fail-closed: a registry with no chat id must not accept every chat.
    stub = _StubSender()
    router = RegistryRouter(_registry(None, ("@3", "42")), sender=stub)
    assert router.route(None, 42, "no chat bound") is False
    assert router.route(777, 42, "some chat") is False
    assert stub.calls == []


def test_registry_router_propagates_send_failure():
    stub = _StubSender(ok=False)
    router = RegistryRouter(_registry("777", ("@3", "42")), sender=stub)
    assert router.route(777, 42, "will fail") is False
    assert stub.calls == [("@3", "will fail")]


# --------------------------------------------------------------------------
# resolve() — the chat/topic gate the bridge commands (/screenshot, /esc) share
# --------------------------------------------------------------------------

def test_topic_router_resolve_returns_window_for_bound_topic():
    router = TopicRouter("777", "@3", "4")
    # Wire ids arrive as ints; the gate compares them as str.
    assert router.resolve(777, 4) == "@3"


def test_topic_router_resolve_gates_wrong_chat_and_topic():
    router = TopicRouter("777", "@3", "4")
    assert router.resolve(999, 4) is None   # wrong chat
    assert router.resolve(777, 9) is None   # wrong topic
    assert router.resolve(None, 4) is None  # no chat
    assert router.resolve(777, None) is None  # General topic, a topic is bound


def test_topicless_router_resolve_accepts_any_topic_in_bound_chat():
    router = TopicRouter("777", "@3", topic_id=None)
    assert router.resolve(777, None) == "@3"
    assert router.resolve(777, 12) == "@3"
    assert router.resolve(888, 12) is None


def test_registry_router_resolve_returns_window_for_bound_topic():
    router = RegistryRouter(_registry("777", ("@3", "42"), ("@7", "88")))
    assert router.resolve(777, 42) == "@3"
    assert router.resolve(777, 88) == "@7"


def test_registry_router_resolve_gates_wrong_chat_and_unbound_topic():
    router = RegistryRouter(_registry("777", ("@3", "42")))
    assert router.resolve(999, 42) is None   # wrong chat
    assert router.resolve(777, 999) is None  # unbound topic
    assert router.resolve(777, None) is None  # General topic


def test_registry_router_resolve_fails_closed_with_no_chat_bound():
    router = RegistryRouter(_registry(None, ("@3", "42")))
    assert router.resolve(777, 42) is None
    assert router.resolve(None, 42) is None


# --------------------------------------------------------------------------
# /screenshot control-key keyboard — the pure data behind the inline keyboard
# and its callback handler (the PTB glue itself needs the [telegram] extra).
# --------------------------------------------------------------------------

def test_key_actions_flatten_every_key_sending_button_in_the_keyboard():
    # Every (label, key_id, tmux_key) button maps to its (tmux_key, label) action
    # so a button and the key it fires can never drift apart. The keyless 🔄
    # button sends nothing (it re-captures the pane), so it stays out of the map.
    buttons = [b for row in SCREENSHOT_KEYS for b in row]
    assert _KEY_ACTIONS == {
        key_id: (tmux_key, label)
        for (label, key_id, tmux_key) in buttons
        if tmux_key is not None
    }
    assert _REFRESH_KEY_ID not in _KEY_ACTIONS
    # key_ids are unique across the whole keyboard (no button shadows another).
    assert len({key_id for (_l, key_id, _k) in buttons}) == len(buttons)


def test_key_actions_map_to_valid_tmux_key_names():
    # The essentials for driving a terminal: arrows, interrupt, and submit.
    assert _KEY_ACTIONS["cc"][0] == "C-c"
    assert _KEY_ACTIONS["ent"][0] == "Enter"
    assert {_KEY_ACTIONS[k][0] for k in ("up", "dn", "lt", "rt")} == {
        "Up", "Down", "Left", "Right"
    }


def test_arrow_buttons_are_glyph_only():
    # The direction is in the glyph; a worded caption ("→ Right") only steals
    # width, which Telegram then truncates on a narrow phone.
    labels = {
        key_id: label for row in SCREENSHOT_KEYS for (label, key_id, _k) in row
    }
    assert [labels[k] for k in ("up", "dn", "lt", "rt")] == ["↑", "↓", "←", "→"]


def test_keyboard_carries_a_refresh_button():
    # Re-capturing the pane without sending a key is the most-reached-for action
    # between keypresses, so the keyboard must offer it.
    refresh = [
        b for row in SCREENSHOT_KEYS for b in row if b[1] == _REFRESH_KEY_ID
    ]
    assert refresh == [("🔄", _REFRESH_KEY_ID, None)]


def test_callback_data_stays_within_telegram_64_byte_limit():
    # callback_data the keyboard packs is ``k:<key_id>`` — must fit Telegram's cap.
    for row in SCREENSHOT_KEYS:
        for (_label, key_id, _tmux) in row:
            assert len(f"{_KEY_CB_PREFIX}{key_id}".encode()) <= 64


# --------------------------------------------------------------------------
# The WIRING, not just the helper: drive the real _on_message handler.
# --------------------------------------------------------------------------
#
# The helper being correct is not the same as the bridge being correct — the live bug
# was in the forwarding path, and a green unit test on a pure function would not have
# caught it. So build the actual PTB Application, take its text handler, and feed it a
# group message exactly as Telegram delivers one.

pytest.importorskip("telegram")


class _FakeMessage:
    def __init__(self, text, thread_id=4):
        self.text = text
        self.message_thread_id = thread_id
        self.photo = None
        self.document = None
        self.replies: list[str] = []

    async def reply_text(self, text, **_kw):
        self.replies.append(text)


class _FakeUpdate:
    def __init__(self, text, chat_id=777, thread_id=4):
        self.message = _FakeMessage(text, thread_id)
        self.effective_chat = type("Chat", (), {"id": chat_id})()


def _text_handler_and_bot(stub):
    """The real _on_message callback from a real build_application, + its bot cell."""
    from telegram.ext import MessageHandler

    from chela.telegram.inbound import build_application

    app = build_application(
        "123:fake-token", TopicRouter("777", "@3", "4", sender=stub),
    )
    handlers = [h for group in app.handlers.values() for h in group]
    text_handlers = [h for h in handlers if isinstance(h, MessageHandler)]
    on_message = text_handlers[-1].callback           # the TEXT catch-all, added last
    # The username cell lives in build_application's closure (filled by post_init from
    # get_me()); reach it the same way post_init does, without a network call.
    cell = on_message.__closure__[
        on_message.__code__.co_freevars.index("bot")
    ].cell_contents
    return on_message, cell


def _drive(on_message, text, **kw):
    """Feed one message through the real handler; returns the message it replied to."""
    import asyncio
    update = _FakeUpdate(text, **kw)
    asyncio.run(on_message(update, None))
    return update.message


def test_bridge_forwards_a_group_menu_tap_without_the_bot_suffix():
    stub = _StubSender()
    on_message, bot = _text_handler_and_bot(stub)
    bot["username"] = OUR_BOT                          # what post_init's get_me() caches

    _drive(on_message, "/clear@chelamuxbot")           # the live repro, verbatim

    assert stub.calls == [("@3", "/clear")], "Claude Code must receive its own command"


def test_bridge_drops_a_group_command_aimed_at_another_bot():
    stub = _StubSender()
    on_message, bot = _text_handler_and_bot(stub)
    bot["username"] = OUR_BOT

    _drive(on_message, "/clear@someotherbot")

    assert stub.calls == []                            # never typed into the session


def test_bridge_leaves_ordinary_text_alone():
    stub = _StubSender()
    on_message, bot = _text_handler_and_bot(stub)
    bot["username"] = OUR_BOT

    _drive(on_message, "look at @3 and mail me at a@b.com")

    assert stub.calls == [("@3", "look at @3 and mail me at a@b.com")]


# --------------------------------------------------------------------------
# Passthrough commands CONFIRM the send (silence reads as "swallowed" on a phone).
# --------------------------------------------------------------------------

def test_a_passthrough_command_confirms_the_send():
    stub = _StubSender()
    on_message, bot = _text_handler_and_bot(stub)
    bot["username"] = OUR_BOT

    msg = _drive(on_message, "/clear@chelamuxbot")

    assert stub.calls == [("@3", "/clear")]
    assert msg.replies == ["⏎ Sent /clear"]
    # It confirms the SEND, never the effect: "cleared" would be a guess.
    assert "cleared" not in msg.replies[0].lower()


def test_a_failed_passthrough_command_says_so():
    stub = _StubSender(ok=False)                       # tmux send-keys failed
    on_message, bot = _text_handler_and_bot(stub)
    bot["username"] = OUR_BOT

    msg = _drive(on_message, "/clear")

    assert msg.replies == ["❌ Couldn't send /clear."]


def test_a_dropped_command_is_not_confirmed_as_sent():
    stub = _StubSender()
    on_message, bot = _text_handler_and_bot(stub)
    bot["username"] = OUR_BOT

    # Unbound topic (the router binds thread 4) and, separately, the wrong chat.
    unbound = _drive(on_message, "/clear", thread_id=9)
    foreign = _drive(on_message, "/clear", chat_id=999)

    assert stub.calls == []                            # never typed into a session
    assert unbound.replies == [] and foreign.replies == []


def test_a_plain_message_is_never_confirmed():
    stub = _StubSender()
    on_message, bot = _text_handler_and_bot(stub)
    bot["username"] = OUR_BOT

    msg = _drive(on_message, "run the tests please")

    assert stub.calls == [("@3", "run the tests please")]
    assert msg.replies == []                           # no acknowledgement spam


def test_any_claude_code_slash_command_is_confirmed_with_its_own_name():
    stub = _StubSender()
    on_message, bot = _text_handler_and_bot(stub)
    bot["username"] = OUR_BOT

    msg = _drive(on_message, "/model@chelamuxbot opus")

    assert stub.calls == [("@3", "/model opus")]       # args survive
    assert msg.replies == ["⏎ Sent /model"]            # …but the reply names the command


# --------------------------------------------------------------------------
# The 🔄 control key — wired, not just present in the data.
# --------------------------------------------------------------------------

class _FakeCallbackMessage:
    """The keyboard message a control-key tap arrives on."""

    def __init__(self, thread_id=4):
        self.message_thread_id = thread_id
        self.photos: list[bytes] = []
        self.texts: list[str] = []

    async def reply_photo(self, photo, **_kw):
        self.photos.append(photo.read())

    async def reply_text(self, text, **_kw):
        self.texts.append(text)


class _FakeCallbackQuery:
    def __init__(self, data, msg):
        self.data = data
        self.message = msg
        self.answers: list[str | None] = []
        self.edits: list[dict] = []

    async def answer(self, text=None, **_kw):
        self.answers.append(text)

    async def edit_message_media(self, **kw):
        self.edits.append(kw)


class _FakeCallbackUpdate:
    def __init__(self, data, chat_id=777, thread_id=4):
        self.message = None
        self.callback_query = _FakeCallbackQuery(data, _FakeCallbackMessage(thread_id))
        self.effective_chat = type("Chat", (), {"id": chat_id})()


def _key_handler(*, capture, send_key):
    """The real _on_key callback from a real build_application."""
    from telegram.ext import CallbackQueryHandler

    from chela.telegram.inbound import build_application

    app = build_application(
        "123:fake-token",
        TopicRouter("777", "@3", "4", sender=_StubSender()),
        capture=capture,
        send_key=send_key,
    )
    handlers = [h for group in app.handlers.values() for h in group]
    cbs = [h for h in handlers if isinstance(h, CallbackQueryHandler)]
    return cbs[-1].callback  # the pattern-less catch-all: _on_key


def test_refresh_tap_edits_the_screenshot_in_place_and_sends_no_key():
    import asyncio

    sent: list[str] = []
    update = _FakeCallbackUpdate(f"{_KEY_CB_PREFIX}{_REFRESH_KEY_ID}")
    on_key = _key_handler(
        capture=lambda _wid, **_kw: "fresh pane",
        send_key=lambda _wid, key: sent.append(key) or True,
    )

    asyncio.run(on_key(update, None))

    assert sent == [], "🔄 must not type anything into the session"
    query = update.callback_query
    assert query.answers == ["🔄"]
    # The SAME message's photo is edited in place — no fresh message posted.
    assert len(query.edits) == 1
    assert query.message.photos == [] and query.message.texts == []


# ── CMX-50: a gate answered from Telegram sends NOTHING to the pane ──────────

def _qa_handler(*, drafts, send_key):
    """The real _on_qa callback from a real build_application."""
    from telegram.ext import CallbackQueryHandler

    from chela.telegram.inbound import build_application

    app = build_application(
        "123:fake-token",
        TopicRouter("777", "@3", "4", sender=_StubSender()),
        send_key=send_key,
        drafts=drafts,
    )
    handlers = [h for group in app.handlers.values() for h in group]
    cbs = [h for h in handlers if isinstance(h, CallbackQueryHandler)]
    return cbs[0].callback  # the ^qa: handler, registered first


class _FakeAnswerQuery(_FakeCallbackQuery):
    """A tap on an answer button: it may redraw its own keyboard, never a screenshot."""

    def __init__(self, data, msg):
        super().__init__(data, msg)
        self.markups: list = []

    async def edit_message_reply_markup(self, reply_markup=None, **_kw):
        self.markups.append(reply_markup)


class _FakeAnswerUpdate(_FakeCallbackUpdate):
    def __init__(self, data, chat_id=777, thread_id=4):
        super().__init__(data, chat_id, thread_id)
        self.callback_query = _FakeAnswerQuery(data, _FakeCallbackMessage(thread_id))


class _StubDrafts:
    """Stands in for the draft book — records what the tap asked it to do."""

    def __init__(self, tap):
        self.tap = tap
        self.picked: list = []
        self.sent: list = []

    def pick(self, tool_use_id, question_index, option_index):
        self.picked.append((tool_use_id, question_index, option_index))
        return self.tap

    def send(self, tool_use_id, question_index):
        self.sent.append((tool_use_id, question_index))
        return self.tap


def test_an_answer_tap_goes_through_the_hook_and_types_nothing_at_the_terminal():
    """THE point of CMX-50. A tap on a held gate must not put a key anywhere near tmux:
    keystrokes are the substrate that silently answered option 2 for a tap on 3 (CMX-32),
    and for a multi-question / multiSelect picker they cannot express the answer at all."""
    import asyncio

    from chela.telegram.gateanswers import Tap

    keys: list[str] = []
    drafts = _StubDrafts(Tap(True, "✅ Answered", markup={"inline_keyboard": [
        [{"text": "✓ 1", "callback_data": "qa:h:toolu_1:0:0"}]]}, done=True))
    on_qa = _qa_handler(drafts=drafts, send_key=lambda _w, k: keys.append(k) or True)
    update = _FakeAnswerUpdate("qa:h:toolu_1:0:1")

    asyncio.run(on_qa(update, None))

    assert keys == [], "the answer path must not send a keystroke — that is the feature"
    assert drafts.picked == [("toolu_1", 0, 1)]
    assert update.callback_query.answers == ["✅ Answered"]
    assert update.callback_query.markups, "the tapped option is ticked back to the human"


def test_a_send_tap_commits_a_multiselect_question_without_a_keystroke():
    import asyncio

    from chela.telegram.gateanswers import Tap

    keys: list[str] = []
    drafts = _StubDrafts(Tap(True, "✓ 2 selected"))
    on_qa = _qa_handler(drafts=drafts, send_key=lambda _w, k: keys.append(k) or True)

    asyncio.run(on_qa(_FakeAnswerUpdate("qa:hs:toolu_1:2"), None))

    assert keys == []
    assert drafts.sent == [("toolu_1", 2)]


def test_a_refused_tap_says_so_loudly_and_still_sends_no_keystroke():
    """A gate that has resolved must not be re-aimed at whatever is on the pane now."""
    import asyncio

    from chela.telegram.gateanswers import Tap

    keys: list[str] = []
    drafts = _StubDrafts(Tap(False, "⌛ Too late — answer it in the terminal."))
    on_qa = _qa_handler(drafts=drafts, send_key=lambda _w, k: keys.append(k) or True)
    update = _FakeAnswerUpdate("qa:h:toolu_1:0:0")

    asyncio.run(on_qa(update, None))

    assert keys == []
    assert update.callback_query.answers == ["⌛ Too late — answer it in the terminal."]


# ── CMX-52: the mirror's D-pad — a key, then a RE-DRAW of the same message ───

def _mirror_handler(*, send_key, refresh_mirror, toggle_mirror=None):
    """The real _on_mirror callback from a real build_application."""
    from telegram.ext import CallbackQueryHandler

    from chela.telegram.inbound import build_application

    app = build_application(
        "123:fake-token",
        TopicRouter("777", "@3", "4", sender=_StubSender()),
        send_key=send_key,
        refresh_mirror=refresh_mirror,
        toggle_mirror=toggle_mirror,
    )
    handlers = [h for group in app.handlers.values() for h in group]
    cbs = [h for h in handlers if isinstance(h, CallbackQueryHandler)]
    return cbs[1].callback  # ^qa: is first, ^m: second, the pattern-less _on_key last


def test_a_dpad_tap_sends_the_key_then_redraws_the_SAME_message():
    """The whole feature: press ↓, watch the ❯ cursor move IN THE MESSAGE YOU TAPPED.

    The old nav row sent the key and stopped there — nothing on screen changed, so from a
    phone it was indistinguishable from a dead button. The re-draw is what closes the loop.
    """
    import asyncio

    keys: list[str] = []
    redrawn: list[str] = []
    on_mirror = _mirror_handler(
        send_key=lambda _w, k: keys.append(k) or True,
        refresh_mirror=redrawn.append,
    )
    update = _FakeCallbackUpdate("m:dn")

    asyncio.run(on_mirror(update, None))

    assert keys == ["Down"]
    assert redrawn == ["@3"], "the mirror is re-drawn, for the window the TOPIC resolves to"
    assert update.callback_query.answers == ["↓"]
    # A re-draw is an EDIT of the tracked message (the watcher owns it) — never a fresh
    # screenshot posted below, which is what the old 🔄 did and what left the human
    # scrolling between a picture and the buttons meant to move it.
    assert not update.callback_query.message.photos


def test_the_mirror_refresh_types_nothing_and_still_redraws():
    import asyncio

    keys: list[str] = []
    redrawn: list[str] = []
    on_mirror = _mirror_handler(
        send_key=lambda _w, k: keys.append(k) or True,
        refresh_mirror=redrawn.append,
    )
    update = _FakeCallbackUpdate("m:ref")

    asyncio.run(on_mirror(update, None))

    assert keys == [], "🔄 must not type anything into the session"
    assert redrawn == ["@3"]
    assert update.callback_query.answers == ["🔄"]


def test_the_BOOK_tap_types_nothing_and_swaps_the_body_of_the_SAME_message():
    """CMX-57. 📖 is not a key and not an answer — it is a view toggle on the one message.

    It must never reach the terminal: a gate is on screen, and a stray keystroke there
    answers it. And it must not post anything: a second message is exactly the clutter this
    button exists to have replaced.
    """
    import asyncio

    keys: list[str] = []
    redrawn: list[str] = []
    toggled: list[str] = []
    on_mirror = _mirror_handler(
        send_key=lambda _w, k: keys.append(k) or True,
        refresh_mirror=redrawn.append,
        toggle_mirror=toggled.append,
    )
    update = _FakeCallbackUpdate("m:doc")

    asyncio.run(on_mirror(update, None))

    assert keys == [], "📖 must not type anything into a session that is sitting on a gate"
    assert toggled == ["@3"], "the watcher — which owns the message — re-draws it"
    assert redrawn == [], "and it is NOT also refreshed: one re-render, not two"
    assert update.callback_query.answers == ["📖"]
    assert not update.callback_query.message.photos


def test_a_dpad_tap_from_the_wrong_chat_touches_nothing():
    """The window is re-resolved from the message's TOPIC — never trusted off the wire."""
    import asyncio

    keys: list[str] = []
    redrawn: list[str] = []
    on_mirror = _mirror_handler(
        send_key=lambda _w, k: keys.append(k) or True,
        refresh_mirror=redrawn.append,
    )
    update = _FakeCallbackUpdate("m:ent", chat_id=999)   # not the bound chat

    asyncio.run(on_mirror(update, None))

    assert keys == [] and redrawn == []
    assert update.callback_query.answers == [None], "the spinner is still stopped"


def test_a_failed_key_send_does_not_redraw():
    """A key that never landed must not be reported as a moved cursor."""
    import asyncio

    redrawn: list[str] = []
    on_mirror = _mirror_handler(
        send_key=lambda _w, _k: False,
        refresh_mirror=redrawn.append,
    )
    update = _FakeCallbackUpdate("m:up")

    asyncio.run(on_mirror(update, None))

    assert redrawn == []
    assert update.callback_query.answers == ["❌ send failed"]
