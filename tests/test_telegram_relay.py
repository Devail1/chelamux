"""Outbound relay + MarkdownV2 formatting — exercised against a stub sender.

No live Telegram: :class:`BotSender` is driven through an injected transport and
:class:`TelegramRelay` through a recording stub sender, so these lock in

  * MarkdownV2 escaping and header rendering of each message content_type;
  * the plain-text fallback when a MarkdownV2 send is rejected;
  * 4096-char splitting and the sendMessage field wiring (chat/topic/parse_mode).
"""
from __future__ import annotations

from chela.telegram.format import escape_markdown_v2, to_markdown_v2, to_plain_text
from chela.telegram.parser import Message
from chela.telegram.relay import BotSender, TelegramRelay, split_message


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
