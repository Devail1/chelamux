"""Outbound relay + MarkdownV2 formatting — exercised against a stub sender.

No live Telegram: :class:`BotSender` is driven through an injected transport and
:class:`TelegramRelay` through a recording stub sender, so these lock in

  * MarkdownV2 escaping and header rendering of each message content_type;
  * the plain-text fallback when a MarkdownV2 send is rejected;
  * 4096-char splitting and the sendMessage field wiring (chat/topic/parse_mode).
"""
from __future__ import annotations

import logging

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
    MAX_LEN,
    BotSender,
    RegistryRelay,
    TelegramRelay,
    _scan,
    _truncate_utf16,
    _utf16_len,
    split_message,
)


def _unclosed(chunk: str) -> list[str]:
    """The MarkdownV2 entities left OPEN at the end of ``chunk``.

    Telegram rejects a message whose entities aren't balanced ("Can't find end of
    Bold entity"), so a chunk is individually valid only when this is empty. The
    scanner is the relay's own, which is the point: it is what decides where an
    entity opens (an escaped ``\\*`` and an asterisk inside `` `code` `` are not
    openers), so the assertion tracks the same parse the splitter balances.
    """
    stack: list[str] = []
    for u in _scan(chunk):
        if u.marker is None:
            continue
        if u.close:
            stack.pop()
        else:
            stack.append(u.marker)
    return stack


# --------------------------------------------------------------------------
# formatting
# --------------------------------------------------------------------------

def test_escape_markdown_v2_escapes_every_special_char():
    assert escape_markdown_v2("a_b*c.") == "a\\_b\\*c\\."
    assert escape_markdown_v2("(x)[y]") == "\\(x\\)\\[y\\]"
    assert escape_markdown_v2("plain text") == "plain text"


def test_to_markdown_v2_assistant_is_headerless_body_only():
    # An assistant plain-text turn has NO header (Telegram marks the bot itself);
    # only the body renders, with the '.' escaped for MarkdownV2.
    md = to_markdown_v2(Message("assistant", "text", "done: 1.5 files"))
    assert md == "done: 1\\.5 files"


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
    assert to_plain_text(Message("assistant", "text", "hi. there")) == "hi. there"
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
    # headerless assistant turn: body with a real code span + bold, no escaped fences.
    assert md == "run `ls` then *stop*"


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

    def __init__(self, fail_markdown=False, fail_all=False):
        self.fail_markdown = fail_markdown
        self.fail_all = fail_all
        self.calls: list[tuple[str, str | None]] = []

    def __call__(self, text: str, parse_mode: str | None, reply_markup=None) -> bool:
        # ``reply_markup`` is accepted for signature compatibility (the transcript
        # relay's ``ask_reply_markup`` seam is now always None — both interactive
        # prompts moved to the pane), but it's not part of the recorded tuple:
        # count/text assertions here stay 2-element.
        self.calls.append((text, parse_mode))
        if self.fail_all:
            return False
        if parse_mode == "MarkdownV2" and self.fail_markdown:
            return False
        return True


def test_relay_sends_markdown_v2_and_does_not_fall_back_on_success():
    stub = _StubSender()
    TelegramRelay(stub).on_message("@1", Message("assistant", "text", "ok. go"))

    assert len(stub.calls) == 1
    text, parse_mode = stub.calls[0]
    assert parse_mode == "MarkdownV2"
    assert text == "ok\\. go"


def test_relay_falls_back_to_plain_text_when_markdown_rejected():
    stub = _StubSender(fail_markdown=True)
    TelegramRelay(stub).on_message("@1", Message("assistant", "text", "ok. go"))

    assert len(stub.calls) == 2
    assert stub.calls[0][1] == "MarkdownV2"          # attempted formatted first
    assert stub.calls[1] == ("ok. go", None)         # then plain, unescaped


def test_relay_logs_permanent_drop_with_window_id_when_both_attempts_fail(caplog):
    stub = _StubSender(fail_all=True)
    with caplog.at_level(logging.DEBUG):
        TelegramRelay(stub).on_message("@1", Message("assistant", "text", "ok. go"))

    assert len(stub.calls) == 2  # MarkdownV2 attempt, then plain-text attempt
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "@1" in errors[0].message
    assert "permanently dropped" in errors[0].message


def test_relay_logs_nothing_extra_when_the_plain_text_fallback_recovers(caplog):
    stub = _StubSender(fail_markdown=True)
    with caplog.at_level(logging.DEBUG):
        TelegramRelay(stub).on_message("@1", Message("assistant", "text", "ok. go"))

    assert not [r for r in caplog.records if r.levelno == logging.ERROR]


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

    def __init__(self, fail_markdown=False, fail_all=False):
        self.fail_markdown = fail_markdown
        self.fail_all = fail_all
        self.calls: list[tuple[str, str | None, str | int | None]] = []

    def __call__(self, text, parse_mode, message_thread_id=None) -> bool:
        self.calls.append((text, parse_mode, message_thread_id))
        if self.fail_all:
            return False
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
    assert stub.calls[0] == ("ok\\. go", "MarkdownV2", "42")
    assert stub.calls[1] == ("ok. go", None, "42")  # thread kept on retry


def test_registry_relay_logs_permanent_drop_with_window_id_when_both_attempts_fail(caplog):
    stub = _ThreadStubSender(fail_all=True)
    relay = RegistryRelay(stub, _registry(("@1", "42")))
    with caplog.at_level(logging.DEBUG):
        relay.on_message("@1", Message("assistant", "text", "ok. go"))

    assert len(stub.calls) == 2
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "@1" in errors[0].message
    assert "permanently dropped" in errors[0].message


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
    assert stub.calls[1][0] == "done"


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


# --------------------------------------------------------------------------
# UTF-16 length: Telegram counts its 4096 limit in UTF-16 code units, so an
# astral character (most emoji) counts as two — split/truncate must agree or an
# emoji-heavy body sails past the limit and gets rejected on the wire.
# --------------------------------------------------------------------------

def test_utf16_len_counts_astral_chars_as_two():
    assert _utf16_len("abc") == 3
    assert _utf16_len("🎉") == 2          # one code point, two UTF-16 units
    assert _utf16_len("a🎉b") == 4


def test_split_message_measures_chunks_in_utf16_units():
    # 3000 emoji = 6000 UTF-16 units > 4096: a code-point split would wrongly keep
    # it whole; a UTF-16 split must produce >1 chunk, each within the limit.
    text = "🎉" * 3000
    chunks = split_message(text)
    assert len(chunks) >= 2
    assert all(_utf16_len(c) <= MAX_LEN for c in chunks)
    assert "".join(chunks) == text        # lossless
    # no surrogate pair was severed — every chunk round-trips through UTF-16.
    for c in chunks:
        c.encode("utf-16-le").decode("utf-16-le")


def test_split_message_never_splits_a_surrogate_pair_at_the_boundary():
    # An odd UTF-16 budget could tempt a split mid-emoji; the accumulator only
    # ever cuts on a whole-character boundary.
    text = "🎉" * 10
    chunks = split_message(text, 3)       # 3 units = one emoji + 1 spare
    assert all(_utf16_len(c) <= 3 for c in chunks)
    assert "".join(chunks) == text


def test_truncate_utf16_caps_at_utf16_units():
    assert _truncate_utf16("hello", 100) == "hello"      # under limit: verbatim
    assert _truncate_utf16("🎉🎉🎉", 4) == "🎉🎉"          # 2 units each → keep 2
    assert _truncate_utf16("🎉🎉🎉", 3) == "🎉"            # odd budget → whole char only
    assert _utf16_len(_truncate_utf16("🎉" * 5000, MAX_LEN)) <= MAX_LEN


# --------------------------------------------------------------------------
# Fenced code blocks: a split landing inside a ``` block closes the fence on the
# chunk and reopens it on the next, so each chunk is valid MarkdownV2 on its own.
# --------------------------------------------------------------------------

def test_split_message_closes_and_reopens_fence_across_a_break():
    body = "```\n" + ("x" * 5000) + "\n```"
    chunks = split_message(body)
    assert len(chunks) >= 2
    # every chunk carries a balanced number of fences (opens == closes)
    for c in chunks:
        assert c.count("```") % 2 == 0
    # the reserved headroom keeps even the fence-decorated chunks under the limit
    assert all(_utf16_len(c) <= MAX_LEN for c in chunks)


def test_split_message_leaves_short_fenced_body_verbatim():
    # A message already under the limit is returned untouched — the fence logic
    # only runs on a real split.
    body = "```py\nprint(1)\n```"
    assert split_message(body) == [body]


# --------------------------------------------------------------------------
# Inline entities across a chunk boundary. THE bug: a >4096 body whose split
# landed inside *bold* or `code` left an unterminated entity, Telegram rejected
# the chunk ("Can't find end of Bold entity at byte offset 4316"), and the relay
# re-sent the WHOLE message unformatted — so a long report arrived as raw '**'
# and '##'. Every chunk must now be valid MarkdownV2 on its own.
# --------------------------------------------------------------------------

def test_split_message_balances_bold_across_a_break():
    # A single bold run straddling the limit: closed on chunk 1, reopened on 2.
    body = "*" + ("b" * 5000) + "*"
    chunks = split_message(body)
    assert len(chunks) == 2
    assert all(_unclosed(c) == [] for c in chunks)
    assert chunks[0].endswith("*") and chunks[1].startswith("*")
    assert all(_utf16_len(c) <= MAX_LEN for c in chunks)


def test_split_message_balances_every_inline_entity_kind():
    for marker in ("*", "_", "__", "~", "||", "`"):
        body = marker + ("z" * 5000) + marker
        chunks = split_message(body)
        assert len(chunks) >= 2, marker
        for c in chunks:
            assert _unclosed(c) == [], (marker, c[:20])
            assert _utf16_len(c) <= MAX_LEN, marker


def test_split_message_prefers_a_blank_line_then_a_newline_then_a_space():
    # A paragraph break can never sit inside an inline entity, so it is the
    # cheapest correct place to cut. Each body is one long run of the given
    # separator; the break must land ON it, not mid-token.
    para = split_message(("word " * 400 + "\n\n") * 6)
    assert all(c.endswith("\n\n") for c in para[:-1])

    lines = split_message(("word " * 400 + "\n") * 6)
    assert all(c.endswith("\n") for c in lines[:-1])

    words = split_message("word " * 2000)          # no newline anywhere
    assert all(c.endswith(" ") for c in words[:-1])
    assert "".join(words) == "word " * 2000        # lossless: nothing was injected


def test_split_message_ignores_escaped_and_code_span_markers():
    # An ESCAPED asterisk is a literal character, and an asterisk inside a code
    # span is not an entity — treating either as an opener would make the
    # splitter inject a closer for an entity that was never open.
    body = ("a\\*b " * 800) + ("`c*d` " * 800)
    chunks = split_message(body)
    assert len(chunks) >= 2
    assert all(_unclosed(c) == [] for c in chunks)
    assert "".join(chunks) == body                 # no closer was ever injected


def test_split_message_keeps_a_long_rendered_report_formatted():
    # The live repro: a nautilus-style long report (headings + bold + inline code)
    # rendered exactly as the relay renders it, then split. Before the fix, one
    # chunk ended inside a bold run and Telegram rejected it.
    src = "\n\n".join(
        f"## {i}. The funding tailwind\n\nThe basis is **zero funding** and "
        f"`rate={i}` on the {i}th leg, which is why it matters."
        for i in range(60)
    )
    body = to_markdown_v2(Message("assistant", "text", src))
    assert _utf16_len(body) > MAX_LEN              # the trigger is length
    chunks = split_message(body)
    assert len(chunks) >= 2
    for c in chunks:
        assert _unclosed(c) == []
        assert _utf16_len(c) <= MAX_LEN


# --------------------------------------------------------------------------
# Per-chunk fallback: a rejected chunk is downgraded ALONE. One bad chunk used
# to strip the formatting from every other chunk of the same message.
# --------------------------------------------------------------------------

class _RejectNth:
    """Rejects the n-th sendMessage that carries a parse_mode; accepts the rest."""

    def __init__(self, n: int):
        self._n = n
        self._formatted = 0
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method: str, fields: dict) -> dict:
        self.calls.append((method, dict(fields)))
        if "parse_mode" in fields:
            self._formatted += 1
            if self._formatted == self._n:
                return {"ok": False, "description": "Bad Request: can't parse entities"}
        return {"ok": True, "result": {"message_id": 1}}


def test_bot_sender_falls_back_per_chunk_not_whole_message():
    tr = _RejectNth(2)                             # the 2nd of 3 chunks is rejected
    sender = BotSender("tok", "c", None, transport=tr)

    assert sender.send("x" * (2 * MAX_LEN + 10), "MarkdownV2") is True
    formatted = [f for _, f in tr.calls if "parse_mode" in f]
    plain = [f for _, f in tr.calls if "parse_mode" not in f]
    # All three chunks were attempted formatted; only the rejected one was
    # re-sent unformatted — the other two stayed MarkdownV2.
    assert len(formatted) == 3
    assert len(plain) == 1
    assert plain[0]["text"] == formatted[1]["text"]


def test_bot_sender_plain_fallback_drops_the_backslash_escapes():
    tr = _RejectNth(1)
    sender = BotSender("tok", "c", None, transport=tr)

    assert sender.send("done: 1\\.5 files", "MarkdownV2") is True
    _, plain = tr.calls[1]
    assert "parse_mode" not in plain
    assert plain["text"] == "done: 1.5 files"      # no literal backslash on screen


# --------------------------------------------------------------------------
# 429 flood control: a rejection carrying retry_after is retried (same payload),
# not dropped — a duplicate on retry beats a lost agent message.
# --------------------------------------------------------------------------

class _ScriptedTransport:
    """Returns queued responses in order; repeats the last one when exhausted."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, method: str, fields: dict) -> dict:
        self.calls.append((method, dict(fields)))
        i = min(len(self.calls) - 1, len(self._responses) - 1)
        return self._responses[i]


_OK = {"ok": True, "result": {"message_id": 1}}
_FLOOD = {"ok": False, "error_code": 429, "parameters": {"retry_after": 2}}


def test_bot_sender_retries_after_a_429_and_succeeds():
    tr = _ScriptedTransport([_FLOOD, _OK])
    slept: list[float] = []
    sender = BotSender("tok", "c", None, transport=tr, sleep=slept.append)

    assert sender.send("hi") is True
    assert len(tr.calls) == 2                 # first 429, retry accepted
    assert slept == [2.0]                     # honored the advertised retry_after
    # the SAME payload was re-sent, not a truncated/altered one
    assert tr.calls[0][1] == tr.calls[1][1]


def test_bot_sender_caps_the_honored_retry_after():
    tr = _ScriptedTransport([{"ok": False, "error_code": 429,
                              "parameters": {"retry_after": 999}}, _OK])
    slept: list[float] = []
    sender = BotSender("tok", "c", None, transport=tr, sleep=slept.append)

    assert sender.send("hi") is True
    assert slept == [30.0]                    # capped at _MAX_RETRY_AFTER


def test_bot_sender_gives_up_after_bounded_retries_on_persistent_429():
    tr = _ScriptedTransport([_FLOOD])         # 429 forever
    slept: list[float] = []
    sender = BotSender("tok", "c", None, transport=tr, sleep=slept.append)

    assert sender.send("hi") is False
    assert len(tr.calls) == 3                  # _MAX_SEND_TRIES attempts total
    assert len(slept) == 2                     # slept between the three attempts


def test_bot_sender_logs_exhausted_flood_control_as_error_not_warning(caplog):
    # A message dropped after flood control never cleared must be findable in
    # the log AS a drop — a generic WARNING is what a routine rejection also
    # gets, so grepping for it can't tell the two apart.
    tr = _ScriptedTransport([_FLOOD])         # 429 forever
    sender = BotSender("tok", "c", None, transport=tr, sleep=lambda _: None)

    with caplog.at_level(logging.DEBUG):
        assert sender.send("hi") is False

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "DROPPED" in errors[0].message
    assert "flood control" in errors[0].message
    # The retry loop's own "flood-controlled; retrying" progress lines still
    # fire at WARNING — only the old, ambiguous final-failure line is gone.
    assert not [r for r in caplog.records
                if r.levelno == logging.WARNING and "sendMessage failed" in r.message]


def test_bot_sender_logs_exhausted_flood_control_after_markdown_downgrade(caplog):
    # send()'s SECOND _log_send_drop call site — reached after a MarkdownV2
    # chunk is rejected and the unformatted re-send ALSO exhausts flood
    # control — must log the same DROPPED/ERROR line as the first call site.
    # Every relay actually calls send() with parse_mode set, so this is the
    # site production traffic reaches; a plain `send("hi")` with no
    # parse_mode (as in the sibling test above) can never drive it.
    tr = _ScriptedTransport([_FLOOD])         # 429 forever, both attempts
    sender = BotSender("tok", "c", None, transport=tr, sleep=lambda _: None)

    with caplog.at_level(logging.DEBUG):
        assert sender.send("hi", "MarkdownV2") is False

    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "DROPPED" in errors[0].message
    assert "flood control" in errors[0].message
    assert not [r for r in caplog.records
                if r.levelno == logging.WARNING and "sendMessage failed" in r.message]


def test_bot_sender_logs_non_flood_rejection_as_warning_not_error(caplog):
    # A real rejection (not flood control) is the routine case — it stays a
    # WARNING, not the flood-control ERROR.
    tr = _ScriptedTransport([{"ok": False, "description": "chat not found"}])
    sender = BotSender("tok", "c", None, transport=tr, sleep=lambda _: None)

    with caplog.at_level(logging.DEBUG):
        assert sender.send("hi") is False

    assert not [r for r in caplog.records if r.levelno == logging.ERROR]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "chat not found" in warnings[0].message


def test_bot_sender_does_not_retry_a_non_429_rejection():
    # A real error (bad MarkdownV2) is terminal for the FORMATTED payload — no
    # sleep, no re-send of the same fields. The one extra call is the chunk's own
    # plain-text fallback (below), which also fails here, so send() reports False
    # and the relay falls through to its whole-message plain-text retry.
    tr = _ScriptedTransport([{"ok": False, "description": "can't parse entities"}])
    slept: list[float] = []
    sender = BotSender("tok", "c", None, transport=tr, sleep=slept.append)

    assert sender.send("hi", "MarkdownV2") is False
    assert len(tr.calls) == 2
    assert "parse_mode" not in tr.calls[1][1]   # the fallback, not a 429 re-send
    assert slept == []


def test_bot_sender_429_retry_covers_post_and_edit_paths():
    tr = _ScriptedTransport([_FLOOD, _OK])
    slept: list[float] = []
    sender = BotSender("tok", "c", None, transport=tr, sleep=slept.append)
    assert sender.post("hi") == 1
    assert len(tr.calls) == 2 and slept == [2.0]

    tr2 = _ScriptedTransport([_FLOOD, {"ok": True}])
    slept2: list[float] = []
    sender2 = BotSender("tok", "c", None, transport=tr2, sleep=slept2.append)
    assert sender2.edit(7, "hi") is True
    assert len(tr2.calls) == 2 and slept2 == [2.0]
