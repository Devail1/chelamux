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

from chela.telegram.bindings import BindingRegistry
from chela.telegram.inbound import RegistryRouter, TopicRouter


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
