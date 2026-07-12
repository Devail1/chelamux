"""Outbound relay + MarkdownV2 formatting — exercised against a stub sender.

No live Telegram: :class:`BotSender` is driven through an injected transport and
:class:`TelegramRelay` through a recording stub sender, so these lock in

  * MarkdownV2 escaping and header rendering of each message content_type;
  * the plain-text fallback when a MarkdownV2 send is rejected;
  * 4096-char splitting and the sendMessage field wiring (chat/topic/parse_mode).
"""
from __future__ import annotations

from chela.telegram.bindings import BindingRegistry
from chela.telegram import format as fmt
from chela.telegram.format import (
    escape_markdown_v2,
    render_markdown,
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
# body markdown rendering (telegramify-markdown)
# --------------------------------------------------------------------------

def test_render_markdown_keeps_fenced_code_block():
    # A fenced block must survive as a real MarkdownV2 code entity — the fence
    # backticks are NOT blind-escaped (that was the old, literal-rendering bug).
    out = render_markdown("```python\nprint(1)\n```")
    assert out.startswith("```")
    assert out.endswith("```")
    assert "\\`" not in out


def test_render_markdown_renders_bold_as_single_asterisk():
    # Claude emits **bold** (CommonMark); MarkdownV2 bold is *single* asterisks.
    assert render_markdown("This is **bold** text") == "This is *bold* text"


def test_to_markdown_v2_body_renders_markdown_not_literally():
    md = to_markdown_v2(Message("assistant", "text", "run `ls` then **stop**"))
    # bold header, then the body with a real code span + bold — no escaped fences.
    assert md == "*🤖*\nrun `ls` then *stop*"


def test_render_markdown_falls_back_to_blind_escape_without_telegramify(monkeypatch):
    # Core install (no [telegram] extra): the module stays importable and the
    # body degrades to the blind character-escape instead of crashing.
    monkeypatch.setattr(fmt, "_telegramify", None)
    assert render_markdown("**bold** a.b") == escape_markdown_v2("**bold** a.b")


def test_render_markdown_falls_back_when_telegramify_raises(monkeypatch):
    class Boom:
        @staticmethod
        def markdownify(_text):
            raise ValueError("malformed")

    monkeypatch.setattr(fmt, "_telegramify", Boom)
    assert render_markdown("a.b") == escape_markdown_v2("a.b")


# --------------------------------------------------------------------------
# markdown table → card-style list (convert_markdown_tables)
# --------------------------------------------------------------------------

_TABLE = (
    "| Name | Age |\n"
    "| --- | --- |\n"
    "| Ann | 30 |\n"
    "| Bob | 25 |"
)


def test_convert_markdown_tables_makes_card_style_pairs():
    out = fmt.convert_markdown_tables(_TABLE)
    # Each row becomes **Header**: value pairs — no raw pipes survive.
    assert "**Name**: Ann" in out
    assert "**Age**: 30" in out
    assert "**Name**: Bob" in out
    assert "|" not in out
    # Rows are separated by a horizontal rule.
    assert "────────────" in out


def test_convert_markdown_tables_fills_missing_cells_with_dash():
    table = "| A | B |\n| --- | --- |\n| x |  |"
    out = fmt.convert_markdown_tables(table)
    assert "**A**: x" in out
    assert "**B**: —" in out


def test_convert_markdown_tables_leaves_table_inside_code_fence_untouched():
    fenced = f"```\n{_TABLE}\n```"
    # A table inside a code block is data, not a table to reformat.
    assert fmt.convert_markdown_tables(fenced) == fenced


def test_convert_markdown_tables_passes_normal_paragraph_through():
    para = "Just a sentence with a | pipe but no table."
    assert fmt.convert_markdown_tables(para) == para


def test_convert_markdown_tables_ignores_pipe_row_without_separator():
    # A pipe row not followed by a --- separator is not a table.
    text = "| not | a | table |\nplain next line"
    assert fmt.convert_markdown_tables(text) == text


def test_render_markdown_renders_table_as_cards_not_pipes():
    # End-to-end through the body renderer: a table becomes bold card labels,
    # never raw pipes, and telegramify turns **Header** into MarkdownV2 *bold*.
    out = render_markdown(_TABLE)
    assert "|" not in out
    assert "Name" in out and "Ann" in out
    assert "*Name*" in out  # ** → * once telegramify renders the bold


def test_render_markdown_table_survives_without_telegramify(monkeypatch):
    # Fallback path: no telegramify still applies the card conversion, then the
    # blind escape — the raw table pipes must NOT survive as an escaped table.
    monkeypatch.setattr(fmt, "_telegramify", None)
    out = render_markdown(_TABLE)
    assert "Name" in out and "Ann" in out
    # The card labels are present (escaped ``\*\*``); no unescaped table pipe row.
    assert "| Name | Age |" not in out


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


def test_bot_sender_json_encodes_reply_markup_on_first_chunk_only():
    tr = _Transport()
    sender = BotSender("tok", "c", None, transport=tr)
    markup = {"inline_keyboard": [[{"text": "main", "callback_data": "qa:0"}]]}

    long_text = "y" * (4096 + 5)  # forces two chunks
    assert sender.send(long_text, "MarkdownV2", reply_markup=markup) is True
    assert len(tr.calls) == 2
    # The keyboard is JSON-encoded and rides on the FIRST message only.
    import json as _json
    assert _json.loads(tr.calls[0][1]["reply_markup"]) == markup
    assert "reply_markup" not in tr.calls[1][1]


def test_bot_sender_omits_reply_markup_when_absent():
    tr = _Transport()
    sender = BotSender("tok", "c", None, transport=tr)
    assert sender.send("plain") is True
    assert "reply_markup" not in tr.calls[0][1]


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
# BotSender.post / .edit — edit-in-place for the interactive prompts
# --------------------------------------------------------------------------

def test_bot_sender_post_returns_message_id_and_single_send():
    tr = _Transport()
    sender = BotSender("tok", "chat42", None, transport=tr)
    markup = {"inline_keyboard": [[{"text": "main", "callback_data": "qa:0"}]]}
    mid = sender.post("Which fruit?", None, "99", reply_markup=markup)
    assert mid == 1  # the transport's scripted message_id
    assert len(tr.calls) == 1
    method, fields = tr.calls[0]
    assert method == "sendMessage"
    assert fields["chat_id"] == "chat42"
    assert fields["message_thread_id"] == "99"
    import json as _json
    assert _json.loads(fields["reply_markup"]) == markup


def test_bot_sender_post_returns_none_on_failure():
    tr = _Transport(ok=False)
    sender = BotSender("tok", "c", None, transport=tr)
    assert sender.post("boom") is None


def test_bot_sender_edit_calls_edit_message_text():
    tr = _Transport()
    sender = BotSender("tok", "chat42", None, transport=tr)
    markup = {"inline_keyboard": [[{"text": "b", "callback_data": "qa:1"}]]}
    assert sender.edit(7, "new text", None, reply_markup=markup) is True
    method, fields = tr.calls[0]
    assert method == "editMessageText"
    assert fields["chat_id"] == "chat42"
    assert fields["message_id"] == 7
    assert fields["text"] == "new text"
    import json as _json
    assert _json.loads(fields["reply_markup"]) == markup


def test_bot_sender_edit_tolerates_not_modified():
    class _NotModified:
        def __call__(self, method, fields):
            return {"ok": False, "description": "Bad Request: message is not modified"}

    sender = BotSender("tok", "c", None, transport=_NotModified())
    assert sender.edit(7, "same") is True  # not-modified counts as success


def test_bot_sender_edit_reports_other_failures():
    tr = _Transport(ok=False)  # generic parse-error failure
    sender = BotSender("tok", "c", None, transport=tr)
    assert sender.edit(7, "x") is False


# --------------------------------------------------------------------------
# TelegramRelay — MarkdownV2 first, plain-text fallback on rejection
# --------------------------------------------------------------------------

class _StubSender:
    """A ``send(text, parse_mode)`` sink that records and can fail MarkdownV2."""

    def __init__(self, fail_markdown=False):
        self.fail_markdown = fail_markdown
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, text: str, parse_mode: str | None, reply_markup=None) -> bool:
        # ``reply_markup`` is accepted for signature compatibility (the transcript
        # relay's ``ask_reply_markup`` seam is now always None — both interactive
        # prompts moved to the pane), but it's not part of the recorded tuple:
        # count/text assertions here stay 2-element.
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


def test_relay_hidden_drops_tool_calls_and_askuserquestion_tool_use():
    # Flag OFF (default): Bash tool_use/tool_result are dropped. The
    # AskUserQuestion *tool_use* is dropped too (Slice A2 — it lands post-answer
    # and the pane watcher already relayed the prompt live); its tool_result stays
    # as the "answered" confirmation. A plain text turn always relays.
    stub = _StubSender()
    relay = TelegramRelay(stub, show_tool_calls=False)

    relay.on_message("@1", Message("assistant", "tool_use", "Bash", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "tool_result", "exit 0", tool_name="Bash"))
    relay.on_message(
        "@1", Message("assistant", "tool_use", "AskUserQuestion", tool_name="AskUserQuestion")
    )
    relay.on_message(
        "@1", Message("assistant", "tool_result", "Apple", tool_name="AskUserQuestion")
    )
    relay.on_message("@1", Message("assistant", "text", "done"))

    # The AskUserQuestion tool_result (confirmation) and the text turn made it.
    assert len(stub.calls) == 2
    assert stub.calls[1][0] == "*🤖*\ndone"


def test_relay_drops_askuserquestion_tool_use_even_when_shown():
    # The double-post guard fires regardless of show_tool_calls: even with the
    # firehose on, the pane watcher owns the AskUserQuestion prompt.
    stub = _StubSender()
    relay = TelegramRelay(stub, show_tool_calls=True)
    relay.on_message(
        "@1", Message("assistant", "tool_use", "AskUserQuestion", tool_name="AskUserQuestion")
    )
    assert stub.calls == []


def test_relay_drops_exit_plan_mode_tool_use_but_keeps_its_result():
    # Slice B2: like AskUserQuestion, the ExitPlanMode tool_use lands post-answer,
    # so it is dropped here (the pane watcher owns the plan prompt live); its
    # tool_result still relays as the "approved / kept planning" confirmation.
    stub = _StubSender()
    relay = TelegramRelay(stub, show_tool_calls=False)
    relay.on_message(
        "@1", Message("assistant", "tool_use", "ExitPlanMode", tool_name="ExitPlanMode")
    )
    relay.on_message(
        "@1", Message("assistant", "tool_result", "approved", tool_name="ExitPlanMode")
    )
    assert len(stub.calls) == 1  # only the tool_result made it


def test_relay_drops_exit_plan_mode_tool_use_even_when_shown():
    # The double-post guard fires regardless of show_tool_calls: even with the
    # firehose on, the pane watcher owns the ExitPlanMode prompt.
    stub = _StubSender()
    relay = TelegramRelay(stub, show_tool_calls=True)
    relay.on_message(
        "@1", Message("assistant", "tool_use", "ExitPlanMode", tool_name="ExitPlanMode")
    )
    assert stub.calls == []


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


def test_registry_relay_hidden_drops_tool_calls_and_askuserquestion_tool_use():
    stub = _ThreadStubSender()
    reg = _registry(("@1", "42"))
    relay = RegistryRelay(stub, reg, show_tool_calls=False)

    relay.on_message("@1", Message("assistant", "tool_use", "Bash", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "tool_result", "exit 0", tool_name="Bash"))
    relay.on_message(
        "@1", Message("assistant", "tool_use", "AskUserQuestion", tool_name="AskUserQuestion")
    )
    relay.on_message(
        "@1", Message("assistant", "tool_result", "Apple", tool_name="AskUserQuestion")
    )
    relay.on_message("@1", Message("assistant", "text", "done"))

    # AskUserQuestion tool_result (confirmation) + text only, both to @1's topic.
    assert len(stub.calls) == 2
    assert all(thread == "42" for _, _, thread in stub.calls)


# --------------------------------------------------------------------------
# AskUserQuestion inline keyboard — attached through the relay's send path
# --------------------------------------------------------------------------

class _MarkupStubSender:
    """A registry-shaped sink that also records the ``reply_markup`` kwarg."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, text, parse_mode, message_thread_id=None, reply_markup=None):
        self.calls.append(
            {"text": text, "parse_mode": parse_mode, "thread": message_thread_id,
             "reply_markup": reply_markup}
        )
        return True


def test_registry_relay_drops_askuserquestion_tool_use_entirely():
    # Slice A2: the AskUserQuestion prompt is surfaced live from the pane (with its
    # answer keyboard) — the post-answer transcript tool_use is dropped here, so
    # the relay neither re-posts the question nor attaches a keyboard.
    stub = _MarkupStubSender()
    relay = RegistryRelay(stub, _registry(("@1", "42")))
    relay.on_message("@1", Message(
        "assistant", "tool_use", "AskUserQuestion", tool_name="AskUserQuestion",
        tool_input={"questions": [{
            "multiSelect": False,
            "options": [{"label": "main"}, {"label": "dev"}],
        }]},
    ))
    assert stub.calls == []


def test_registry_relay_drops_exit_plan_mode_tool_use_entirely():
    # Slice B2: the ExitPlanMode plan approval is surfaced live from the pane (with
    # its approve/keep-planning keyboard) — the post-answer transcript tool_use is
    # dropped here, so the relay neither re-posts the plan nor attaches a keyboard.
    stub = _MarkupStubSender()
    relay = RegistryRelay(stub, _registry(("@1", "42")))
    relay.on_message(
        "@1", Message("assistant", "tool_use", "ExitPlanMode", tool_name="ExitPlanMode"),
    )
    assert stub.calls == []


def test_registry_relay_ordinary_text_has_no_keyboard_kwarg():
    # A plain message must not pass reply_markup at all — the 2-/3-arg senders
    # (and their stubs) keep working unchanged.
    stub = _ThreadStubSender()  # __call__ has no reply_markup param
    relay = RegistryRelay(stub, _registry(("@1", "42")))
    relay.on_message("@1", Message("assistant", "text", "hello"))
    assert len(stub.calls) == 1  # would TypeError if reply_markup were passed


def test_registry_relay_shown_keeps_all_tool_calls():
    stub = _ThreadStubSender()
    relay = RegistryRelay(stub, _registry(("@1", "42")), show_tool_calls=True)

    relay.on_message("@1", Message("assistant", "tool_use", "Bash", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "tool_result", "exit 0", tool_name="Bash"))
    relay.on_message("@1", Message("assistant", "text", "done"))

    assert len(stub.calls) == 3
