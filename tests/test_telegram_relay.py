"""Outbound relay + MarkdownV2 formatting — exercised against a stub sender.

No live Telegram: :class:`BotSender` is driven through an injected transport and
:class:`TelegramRelay` through a recording stub sender, so these lock in

  * MarkdownV2 escaping and header rendering of each message content_type;
  * the plain-text fallback when a MarkdownV2 send is rejected;
  * 4096-char splitting and the sendMessage field wiring (chat/topic/parse_mode).
"""
from __future__ import annotations

from chela.telegram.bindings import BindingRegistry
from chela.telegram.format import (
    escape_markdown_v2,
    to_code_block,
    to_markdown_v2,
    to_plain_text,
)
from chela.telegram.parser import Message
from chela.telegram.relay import (
    INTERACTIVE_TOOL_NAMES,
    BotSender,
    RegistryRelay,
    TelegramRelay,
    split_message,
)


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------

def test_escape_markdown_v2_escapes_every_special_char():
    assert escape_markdown_v2("a_b*c.") == "a\\_b\\*c\\."
    assert escape_markdown_v2("(x)[y]") == "\\(x\\)\\[y\\]"
    assert escape_markdown_v2("plain text") == "plain text"


def test_to_markdown_v2_renders_bold_header_and_escaped_body():
    md = to_markdown_v2(Message("assistant", "text", "done: 1.5 files"))
    # header is bold (unescaped emoji), body escapes the '.' and ':'
    assert md == "*🤖*\ndone: 1\\.5 files"


def test_to_code_block_wraps_and_escapes_only_fence_specials():
    # Inside a code entity only backslash and backtick are special — a '.' or '*'
    # that escape_markdown_v2 would touch must survive verbatim in the snapshot.
    assert to_code_block("plain.text*") == "```\nplain.text*\n```"
    assert to_code_block("a\\b`c") == "```\na\\\\b\\`c\n```"


def test_to_markdown_v2_tool_use_is_header_only():
    md = to_markdown_v2(Message("assistant", "tool_use", "Bash", tool_name="Bash"))
    assert md == "*🔧 Bash*"


def test_to_markdown_v2_tool_result_uses_paired_tool_name():
    md = to_markdown_v2(
        Message("assistant", "tool_result", "exit 0", tool_name="Bash")
    )
    assert md == "*✅ Bash result*\nexit 0"


def test_to_plain_text_has_no_markup():
    assert to_plain_text(Message("assistant", "text", "hi. there")) == "🤖\nhi. there"
    assert to_plain_text(Message("assistant", "tool_use", "Read", tool_name="Read")) == "🔧 Read"


# --------------------------------------------------------------------------
# splitting
# --------------------------------------------------------------------------

def test_split_message_chunks_at_limit():
    assert split_message("abcdef", 2) == ["ab", "cd", "ef"]
    assert split_message("", 4) == [""]
    assert split_message("short", 100) == ["short"]


# --------------------------------------------------------------------------
# BotSender — wiring, over an injected transport (no network)
# --------------------------------------------------------------------------

class _Transport:
    """Records Bot API calls and returns a scripted ok/failure."""

    def __init__(self, ok=True):
        self.ok = ok
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method: str, fields: dict) -> dict:
        self.calls.append((method, dict(fields)))
        if self.ok:
            return {"ok": True, "result": {"message_id": 1}}
        return {"ok": False, "description": "Bad Request: can't parse entities"}


def test_bot_sender_posts_chat_topic_and_parse_mode():
    tr = _Transport()
    sender = BotSender("tok", "chat42", "topic7", transport=tr)

    assert sender.send("hello", "MarkdownV2") is True
    assert len(tr.calls) == 1
    method, fields = tr.calls[0]
    assert method == "sendMessage"
    assert fields == {
        "chat_id": "chat42",
        "text": "hello",
        "message_thread_id": "topic7",
        "parse_mode": "MarkdownV2",
    }


def test_bot_sender_omits_topic_and_parse_mode_when_absent():
    tr = _Transport()
    sender = BotSender("tok", "chat42", None, transport=tr)

    assert sender.send("plain") is True
    _, fields = tr.calls[0]
    assert "message_thread_id" not in fields
    assert "parse_mode" not in fields


def test_bot_sender_splits_long_text_into_multiple_sends():
    tr = _Transport()
    sender = BotSender("tok", "c", None, transport=tr)

    long_text = "x" * (4096 + 10)
    assert sender.send(long_text) is True
    assert len(tr.calls) == 2
    assert len(tr.calls[0][1]["text"]) == 4096
    assert len(tr.calls[1][1]["text"]) == 10


def test_bot_sender_reports_failure_on_rejected_send():
    tr = _Transport(ok=False)
    sender = BotSender("tok", "c", None, transport=tr)
    assert sender.send("boom", "MarkdownV2") is False


def test_bot_sender_per_message_thread_overrides_instance_topic():
    tr = _Transport()
    # No fixed instance topic; the relay supplies message_thread_id per message.
    sender = BotSender("tok", "chat42", None, transport=tr)
    assert sender.send("hi", "MarkdownV2", message_thread_id="99") is True
    _, fields = tr.calls[0]
    assert fields["message_thread_id"] == "99"

    # A per-message thread wins over a configured instance default too.
    tr2 = _Transport()
    sender2 = BotSender("tok", "chat42", "default7", transport=tr2)
    sender2.send("hi", None, message_thread_id="99")
    assert tr2.calls[0][1]["message_thread_id"] == "99"


# --------------------------------------------------------------------------
# TelegramRelay — MarkdownV2 first, plain-text fallback on rejection
# --------------------------------------------------------------------------

class _StubSender:
    """A ``send(text, parse_mode)`` sink that records and can fail MarkdownV2."""

    def __init__(self, fail_markdown=False):
        self.fail_markdown = fail_markdown
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, text: str, parse_mode: str | None) -> bool:
        self.calls.append((text, parse_mode))
        if parse_mode == "MarkdownV2" and self.fail_markdown:
            return False
        return True


def test_relay_sends_markdown_v2_and_does_not_fall_back_on_success():
    stub = _StubSender()
    TelegramRelay(stub).on_message("@1", Message("assistant", "text", "ok. go"))

    assert len(stub.calls) == 1
    text, parse_mode = stub.calls[0]
    assert parse_mode == "MarkdownV2"
    assert text == "*🤖*\nok\\. go"


def test_relay_falls_back_to_plain_text_when_markdown_rejected():
    stub = _StubSender(fail_markdown=True)
    TelegramRelay(stub).on_message("@1", Message("assistant", "text", "ok. go"))

    assert len(stub.calls) == 2
    assert stub.calls[0][1] == "MarkdownV2"          # attempted formatted first
    assert stub.calls[1] == ("🤖\nok. go", None)      # then plain, unescaped


def test_relay_is_a_valid_monitor_on_message_sink():
    """The relay plugs straight into TranscriptMonitor's callback signature."""
    from chela.telegram.monitor import TranscriptMonitor

    stub = _StubSender()
    mon = TranscriptMonitor(on_message=TelegramRelay(stub).on_message, resolver=lambda w: None)
    mon.poll(["@1"])  # no transcript -> no sends, but the wiring type-checks
    assert stub.calls == []


# --------------------------------------------------------------------------
# RegistryRelay — per-window topic via a BindingRegistry, same fallback
# --------------------------------------------------------------------------

class _ThreadStubSender:
    """A ``send(text, parse_mode, message_thread_id)`` sink for the registry relay."""

    def __init__(self, fail_markdown=False):
        self.fail_markdown = fail_markdown
        self.calls: list[tuple[str, str | None, str | int | None]] = []

    def __call__(self, text, parse_mode, message_thread_id=None) -> bool:
        self.calls.append((text, parse_mode, message_thread_id))
        if parse_mode == "MarkdownV2" and self.fail_markdown:
            return False
        return True


def _registry(*bindings):
    reg = BindingRegistry("777")
    for window, thread in bindings:
        reg.bind(window, thread)
    return reg


def test_registry_relay_posts_to_the_windows_bound_topic():
    stub = _ThreadStubSender()
    reg = _registry(("@1", "42"), ("@2", "88"))
    relay = RegistryRelay(stub, reg)
    relay.on_message("@2", Message("assistant", "text", "hello"))

    assert len(stub.calls) == 1
    text, parse_mode, thread = stub.calls[0]
    assert parse_mode == "MarkdownV2"
    assert thread == "88"  # @2's topic, not @1's


def test_registry_relay_skips_unbound_window():
    stub = _ThreadStubSender()
    relay = RegistryRelay(stub, _registry(("@1", "42")))
    relay.on_message("@9", Message("assistant", "text", "orphan"))
    assert stub.calls == []


def test_registry_relay_falls_back_to_plain_text_with_thread_preserved():
    stub = _ThreadStubSender(fail_markdown=True)
    relay = RegistryRelay(stub, _registry(("@1", "42")))
    relay.on_message("@1", Message("assistant", "text", "ok. go"))

    assert len(stub.calls) == 2
    assert stub.calls[0] == ("*🤖*\nok\\. go", "MarkdownV2", "42")
    assert stub.calls[1] == ("🤖\nok. go", None, "42")  # thread kept on retry


# --------------------------------------------------------------------------
# show_tool_calls — hide the tool_use/tool_result firehose by default, but
# NEVER hide interactive prompts (AskUserQuestion / ExitPlanMode) or content
# --------------------------------------------------------------------------

def test_interactive_tool_names_covers_the_prompt_tools():
    assert "AskUserQuestion" in INTERACTIVE_TOOL_NAMES
    assert "ExitPlanMode" in INTERACTIVE_TOOL_NAMES


def test_relay_hidden_drops_tool_calls_but_keeps_prompts_and_text():
    # Flag OFF (default): a Bash tool_use + tool_result are dropped, while an
    # AskUserQuestion tool_use and a plain text turn are relayed.
    stub = _StubSender()
    relay = TelegramRelay(stub, show_tool_calls=False)

    relay.on_message("@1", Message("assistant", "tool_use", "Bash", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "tool_result", "exit 0", tool_name="Bash"))
    relay.on_message(
        "@1", Message("assistant", "tool_use", "AskUserQuestion", tool_name="AskUserQuestion")
    )
    relay.on_message("@1", Message("assistant", "text", "done"))

    # Only the AskUserQuestion prompt and the text turn made it through.
    assert len(stub.calls) == 2
    assert stub.calls[0][0] == "*🔧 AskUserQuestion*"
    assert stub.calls[1][0] == "*🤖*\ndone"


def test_relay_hidden_keeps_exit_plan_mode_prompt():
    stub = _StubSender()
    relay = TelegramRelay(stub, show_tool_calls=False)
    relay.on_message(
        "@1", Message("assistant", "tool_use", "ExitPlanMode", tool_name="ExitPlanMode")
    )
    assert len(stub.calls) == 1


def test_relay_shown_keeps_all_tool_calls():
    # Flag ON: today's behaviour — every event relays.
    stub = _StubSender()
    relay = TelegramRelay(stub, show_tool_calls=True)

    relay.on_message("@1", Message("assistant", "tool_use", "Bash", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "tool_result", "exit 0", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "text", "done"))

    assert len(stub.calls) == 3


def test_relay_default_shows_tool_calls_for_back_compat():
    # No show_tool_calls kwarg -> preserves the pre-flag relay behaviour.
    stub = _StubSender()
    TelegramRelay(stub).on_message(
        "@1", Message("assistant", "tool_use", "Bash", tool_name="Bash")
    )
    assert len(stub.calls) == 1


def test_registry_relay_hidden_drops_tool_calls_but_keeps_prompts():
    stub = _ThreadStubSender()
    reg = _registry(("@1", "42"))
    relay = RegistryRelay(stub, reg, show_tool_calls=False)

    relay.on_message("@1", Message("assistant", "tool_use", "Bash", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "tool_result", "exit 0", tool_name="Bash"))
    relay.on_message(
        "@1", Message("assistant", "tool_use", "AskUserQuestion", tool_name="AskUserQuestion")
    )
    relay.on_message("@1", Message("assistant", "text", "done"))

    assert len(stub.calls) == 2  # prompt + text only, both to @1's topic
    assert all(thread == "42" for _, _, thread in stub.calls)


def test_registry_relay_shown_keeps_all_tool_calls():
    stub = _ThreadStubSender()
    relay = RegistryRelay(stub, _registry(("@1", "42")), show_tool_calls=True)

    relay.on_message("@1", Message("assistant", "tool_use", "Bash", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "tool_result", "exit 0", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "text", "done"))

    assert len(stub.calls) == 3
