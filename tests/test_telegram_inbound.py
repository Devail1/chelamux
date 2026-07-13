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

def test_key_actions_flatten_every_button_in_the_keyboard():
    # Every (label, key_id, tmux_key) button maps to its (tmux_key, label) action
    # so a button and the key it fires can never drift apart.
    buttons = [b for row in SCREENSHOT_KEYS for b in row]
    assert _KEY_ACTIONS == {
        key_id: (tmux_key, label) for (label, key_id, tmux_key) in buttons
    }
    # key_ids are unique across the whole keyboard (no button shadows another).
    assert len({key_id for (_l, key_id, _k) in buttons}) == len(buttons)


def test_key_actions_map_to_valid_tmux_key_names():
    # The essentials for driving a terminal: arrows, interrupt, and submit.
    assert _KEY_ACTIONS["cc"][0] == "C-c"
    assert _KEY_ACTIONS["ent"][0] == "Enter"
    assert {_KEY_ACTIONS[k][0] for k in ("up", "dn", "lt", "rt")} == {
        "Up", "Down", "Left", "Right"
    }


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


def _drive(on_message, text):
    import asyncio
    asyncio.run(on_message(_FakeUpdate(text), None))


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
