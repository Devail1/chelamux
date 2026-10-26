"""CMX-117 — a Telegram photo must be SUBMITTED, and nothing may merge into a stranded one.

Live (10-13): a photo+caption landed on the agent's prompt as ``[Image #2]caption`` but
its Enter was swallowed; 3 min later the next Telegram text was typed onto that prompt
and the two went in as ONE turn, echoed back as 👤 "…📎 image:ok i refreshed…". A stub
tty driven through ``send_tmux`` on a private socket showed the bridge's bytes were
right (one bracketed paste, then one ``\\r``) — what was missing is any check that the
submit happened. These pin that check, the no-merge guard, and the echo suppression.
"""
from __future__ import annotations

import asyncio
import base64

import pytest

from chela import event_log, messenger
from chela.telegram import media
from chela.telegram.bindings import BindingRegistry
from chela.telegram.parser import parse_entries
from chela.telegram.relay import RegistryRelay

RULE = "─" * 40


def _pane(input_line: str, *, above: str = RULE) -> str:
    return "\n".join(["● earlier reply", "❯ an old submitted turn", above, input_line, RULE,
                      "  ⏸ plan mode on"])


@pytest.mark.parametrize("line, expected", [
    ("❯ ", ""),
    ("\x1b[39m❯ \x1b[7m \x1b[27m", ""),                          # empty, cursor only
    ("❯ [Image #2]seems like the 3d view is stuck", "[Image #2]seems like the 3d view is stuck"),
    ("\x1b[39m❯ \x1b[2mTry \"fix the bug\"\x1b[0m", ""),         # ghost suggestion
    ("\x1b[39m❯ \x1b[1;2mghost\x1b[0m", ""),                     # faint among other params
    # Captured verbatim 10-21: Claude Code puts a NO-BREAK SPACE (U+00A0) after `❯`, not a
    # space. A ghost read as a draft refused every Telegram message to an idle agent.
    ("\x1b[39m❯\xa0\x1b[2mupdate the handoff\x1b[0m", ""),       # real ghost, NBSP lead
    ("\x1b[39m❯\xa0typed after nbsp", "typed after nbsp"),       # NBSP lead, a real draft
    ("\x1b[39m❯ \x1b[22mreal draft\x1b[0m", "real draft"),       # 22 is NOT faint
    ("❯ Try \"fix the bug\"", 'Try "fix the bug"'),              # no SGR: a typed draft
    ("❯ Press up to edit queued messages", ""),                  # busy, message queued
    ("│ ❯ boxed draft   │", "boxed draft"),
])
def test_input_draft_reads_the_input_line(line, expected):
    assert messenger.input_draft(_pane(line)) == expected


def test_input_draft_skips_the_blank_rows_below_an_inline_tui():
    assert messenger.input_draft(_pane("❯ hi") + "\n" * 30 + "\x1b[0m\n") == "hi"


def test_input_draft_old_box_tui():
    assert messenger.input_draft("╭" + RULE + "╮\n│ ❯ hi │\n╰" + RULE + "╯") == "hi"


@pytest.mark.parametrize("pane", [
    "",
    "Do you want to proceed?\n❯ 1. Yes\n  2. No",                 # a menu, not the prompt
    _pane("❯ hi", above="some reply text"),
])
def test_input_draft_none_without_an_input_line(pane):
    assert messenger.input_draft(pane) is None


class _Tmux:
    """Fakes the pane captures + sends deliver_image makes, recording their order."""

    def __init__(self, monkeypatch, panes, *, sent=True):
        self.panes, self.log = list(panes), []
        def capture(wid, *, ansi=False):
            self.log.append(("capture", wid, ansi))
            p = self.panes.pop(0) if len(self.panes) > 1 else self.panes[0]
            if isinstance(p, BaseException):
                raise p
            return p
        def send(wid, text):
            self.log.append(("send", wid, text))
            return sent
        monkeypatch.setattr(messenger, "capture_pane", capture)
        monkeypatch.setattr(messenger, "send_tmux", send)
        monkeypatch.setattr(messenger, "resend_enter",
                            lambda wid: self.log.append(("enter", wid)) or True)
        monkeypatch.setattr(messenger.time, "sleep", lambda s: self.log.append(("sleep", s)))

    def kinds(self):
        return [e[0] for e in self.log]


EMPTY, CHIP = _pane("❯ "), _pane("❯ [Image #2]caption")


def test_image_is_sent_then_verified_submitted(monkeypatch):
    t = _Tmux(monkeypatch, [EMPTY])
    assert messenger.deliver_image("@3", "cap\n\n📎 image: /d/a.jpg") == "submitted"
    assert t.log == [("capture", "@3", True), ("send", "@3", "cap\n\n📎 image: /d/a.jpg"),
                     ("sleep", 0.5), ("capture", "@3", True)]


def test_swallowed_enter_on_the_chip_is_pressed_once_more(monkeypatch):
    t = _Tmux(monkeypatch, [EMPTY, CHIP, EMPTY])
    assert messenger.deliver_image("@3", "x") == "submitted"
    assert t.kinds() == ["capture", "send", "sleep", "capture", "enter", "sleep", "capture"]


def test_chip_that_never_submits_is_unsubmitted_after_one_retry(monkeypatch):
    t = _Tmux(monkeypatch, [EMPTY, CHIP])
    assert messenger.deliver_image("@3", "x") == "unsubmitted"
    assert t.kinds().count("enter") == 1 and t.kinds().count("sleep") == 7


def test_unconverted_path_is_never_re_entered(monkeypatch):
    t = _Tmux(monkeypatch, [EMPTY, _pane("❯ 📎 image: /d/a.jpg")])
    assert messenger.deliver_image("@3", "x") == "unsubmitted"
    assert "enter" not in t.kinds()


@pytest.mark.parametrize("after", ["", FileNotFoundError("tmux")])
def test_unreadable_prompt_after_send_is_unsubmitted_not_sent(monkeypatch, after):
    _Tmux(monkeypatch, [EMPTY, after])
    assert messenger.deliver_image("@3", "x") == "unsubmitted"


def test_failed_send_is_failed(monkeypatch):
    _Tmux(monkeypatch, [EMPTY], sent=False)
    assert messenger.deliver_image("@3", "x") == "failed"


def test_pending_draft_is_never_appended_to(monkeypatch):
    t = _Tmux(monkeypatch, [CHIP])
    with pytest.raises(messenger.PendingDraft):
        messenger.deliver_prompt("@3", "ok i refreshed and it came back")
    assert "send" not in t.kinds()


@pytest.mark.parametrize("pane", [EMPTY, PermissionError("tmux")])
def test_deliver_prompt_sends_on_empty_or_unreadable_prompt(monkeypatch, pane):
    t = _Tmux(monkeypatch, [pane])
    assert messenger.deliver_prompt("@3", "hello") is True
    assert t.log[-1] == ("send", "@3", "hello")


# -- CMX-344: the submit hook is the authority, the pane only the fallback ----------------

GHOST = _pane("\x1b[39m❯\xa0\x1b[2mcheck the image\x1b[0m")
UNCONVERTED = _pane("❯ 📎 image: /d/a.jpg")


@pytest.fixture
def events(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    return event_log


def _submit_on_send(t, monkeypatch, *, type="hook.user_prompt_submit", wid="@3",
                    prompt=None):
    """The fake send also fires the agent's UserPromptSubmit hook, as Claude Code does."""
    def send(w, text):
        t.log.append(("send", w, text))
        p = text if prompt is None else prompt
        event_log.append(type, f"prompt: {p}", {"prompt": p}, wid=wid)
        return True
    monkeypatch.setattr(messenger, "send_tmux", send)


@pytest.mark.parametrize("pane", [UNCONVERTED, CHIP, "", GHOST])
def test_submit_hook_after_the_send_is_submitted_whatever_the_pane_shows(
        monkeypatch, events, pane):
    t = _Tmux(monkeypatch, [EMPTY, pane])
    _submit_on_send(t, monkeypatch)
    assert messenger.deliver_image("@3", "📎 image: /d/a.jpg") == "submitted"
    assert "enter" not in t.kinds()       # a submitted chip is never pressed again


def test_without_the_hook_the_stale_pane_is_unsubmitted(monkeypatch, events):
    # negative control: the same pane, no hook — the false "unsubmitted" of 10-26
    t = _Tmux(monkeypatch, [EMPTY, UNCONVERTED])
    assert messenger.deliver_image("@3", "x") == "unsubmitted"
    assert t.kinds().count("sleep") == 7      # the six polls, then the last look's wait


@pytest.mark.parametrize("kw", [{"wid": "@4"}, {"type": "hook.stop"}, {"wid": None}])
def test_only_this_windows_submit_hook_counts(monkeypatch, events, kw):
    t = _Tmux(monkeypatch, [EMPTY, UNCONVERTED])
    _submit_on_send(t, monkeypatch, **kw)
    assert messenger.deliver_image("@3", "x") == "unsubmitted"


def test_a_submit_from_before_the_send_does_not_count(monkeypatch, events):
    event_log.append("hook.user_prompt_submit", "prompt: older", {}, wid="@3")
    _Tmux(monkeypatch, [EMPTY, UNCONVERTED])
    assert messenger.deliver_image("@3", "x") == "unsubmitted"


def test_a_hook_landing_after_the_last_pane_read_still_counts(monkeypatch, events):
    t = _Tmux(monkeypatch, [EMPTY, UNCONVERTED])
    polls = []

    def sleep(s):
        t.log.append(("sleep", s))
        polls.append(s)
        if len(polls) == messenger._SUBMIT_POLLS + 1:   # only during the last look's wait
            event_log.append("hook.user_prompt_submit", "", {"prompt": "x"}, wid="@3")
    monkeypatch.setattr(messenger.time, "sleep", sleep)
    assert messenger.deliver_image("@3", "x") == "submitted"
    assert polls == [0.5] * 7


@pytest.mark.parametrize("prompt", [
    "[Image #2]",                                  # bare chip: no marker, no caption
    "a scheduled tick: run the sweep",             # another sender's turn in this window
    "",
])
def test_a_submit_that_does_not_carry_the_delivery_does_not_count(monkeypatch, events,
                                                                  prompt):
    t = _Tmux(monkeypatch, [EMPTY, CHIP])
    _submit_on_send(t, monkeypatch, prompt=prompt)
    assert messenger.deliver_image("@3", "📎 image: /d/a.jpg") == "unsubmitted"
    assert t.kinds().count("enter") == 1         # the stranded chip still got its retry


@pytest.mark.parametrize("text, prompt", [
    ("📎 image: /d/a.jpg", "[Image #2]\n\n📎 image:"),           # path became the chip
    ("📎 image: /d/a.jpg", "📎 image: /d/a.jpg"),                  # path kept verbatim
    ("seems stuck\n\n📎 image: /d/a.jpg", "[Image #2]seems stuck\n\n📎 image:"),
    # the log keeps a 500-char preview: a long caption's marker is cut, its opening is not
    ("c" * 600 + "\n\n📎 image: /d/a.jpg", "[Image #2]" + "c" * 490 + "… [+120 chars]"),
])
def test_a_submit_carrying_the_delivery_counts(monkeypatch, events, text, prompt):
    t = _Tmux(monkeypatch, [EMPTY, CHIP])
    _submit_on_send(t, monkeypatch, prompt=prompt)
    assert messenger.deliver_image("@3", text) == "submitted"
    assert "enter" not in t.kinds()


@pytest.mark.parametrize("prompt, text, expected", [
    ("x 📎 image:", "", True),
    ("x 📎 image", "", False),                    # the marker's colon is part of it
    ("hello world", "hello\nmore", True),         # first line only
    ("hello world", "\n  hello\n", True),        # leading blank lines skipped
    ("anything", "", False),                      # an empty delivery carries nothing
    ("anything", "   \n ", False),
    ("a" * 60, "a" * 60 + "b", True),             # only the first 60 chars must match
    ("a" * 59, "a" * 60, False),
])
def test_carries(prompt, text, expected):
    assert messenger._carries(prompt, text) is expected


def test_the_image_marker_is_the_literal_the_bridge_delivers():
    assert messenger.INBOUND_IMAGE_MARKER == "📎 image:"
    assert media.INBOUND_IMAGE_MARKER is messenger.INBOUND_IMAGE_MARKER


def test_ghost_text_alone_reads_as_submitted_on_the_pane_fallback(monkeypatch, events):
    _Tmux(monkeypatch, [EMPTY, GHOST])
    assert messenger.deliver_image("@3", "x") == "submitted"


@pytest.mark.parametrize("broken", ["tip", "read"])
def test_an_unreadable_event_log_falls_back_to_the_pane(monkeypatch, events, broken):
    def boom(*_a, **_k):
        raise RuntimeError("events.jsonl")
    monkeypatch.setattr(event_log, broken, boom)
    t = _Tmux(monkeypatch, [EMPTY, UNCONVERTED, EMPTY])
    _submit_on_send(t, monkeypatch)
    assert messenger.deliver_image("@3", "x") == "submitted"       # the pane decided
    t = _Tmux(monkeypatch, [EMPTY, UNCONVERTED])
    _submit_on_send(t, monkeypatch)
    assert messenger.deliver_image("@3", "x") == "unsubmitted"     # unknown is not OK


def test_the_cursor_is_taken_before_the_send(monkeypatch, events):
    order = []
    real_tip = event_log.tip
    monkeypatch.setattr(event_log, "tip", lambda: order.append("tip") or real_tip())
    t = _Tmux(monkeypatch, [EMPTY, UNCONVERTED])
    _submit_on_send(t, monkeypatch)
    real_send = messenger.send_tmux
    monkeypatch.setattr(messenger, "send_tmux",
                        lambda w, x: order.append("send") or real_send(w, x))
    assert messenger.deliver_image("@3", "x") == "submitted"
    assert order == ["tip", "send"]


def test_the_submit_event_type_is_the_hooks_own():
    from chela import hooks
    assert hooks.event_type("UserPromptSubmit") == "hook.user_prompt_submit"


# -- the topic is told the truth ------------------------------------------------------

class _Photo:
    width = height = 10
    file_size = 1
    file_unique_id = "PH"

    async def get_file(self, **_kw):
        class _File:
            async def download_to_drive(self, path, **_kw):
                open(path, "wb").write(b"x")
        return _File()


class _Msg:
    def __init__(self, **kw):
        self.photo, self.document = [_Photo()], kw.get("document")
        self.caption, self.replies = "seems stuck", []

    async def reply_text(self, text):
        self.replies.append(text)


def _deliver(result):
    def deliver(_wid, _text):
        if isinstance(result, BaseException):
            raise result
        return result
    return deliver


@pytest.mark.parametrize("result, reply", [
    ("submitted", "📎 Image sent to the agent: 1000_PH.jpg"),
    # CMX-344: unconfirmed is not proven stranded — never a bare "tap Enter"
    ("unsubmitted", "⚠️ Image delivered, but its submit wasn't confirmed: 1000_PH.jpg. Open "
                    "/screenshot — tap Enter only if it is still on the agent's prompt."),
    ("failed", "❌ Couldn't deliver the image to the agent."),
    (True, "❌ Couldn't deliver the image to the agent."),       # a bare bool is no verdict
    (messenger.PendingDraft("x"), media.PENDING_DRAFT_TEXT),
])
def test_photo_reply_matches_the_verified_outcome(tmp_path, result, reply):
    msg = _Msg()
    asyncio.run(media.receive_photo(msg, 1, 2, resolve=lambda c, t: "@3",
                                    deliver=_deliver(result), docs_dir=tmp_path,
                                    clock=lambda: 1000))
    assert msg.replies == [reply]


def test_document_onto_a_pending_draft_is_refused(tmp_path):
    doc = type("Doc", (), {"file_name": "a.pdf", "file_size": 1, "file_unique_id": "D",
                           "get_file": _Photo.get_file})()
    msg = _Msg(document=doc)
    asyncio.run(media.receive_document(msg, 1, 2, resolve=lambda c, t: "@3",
                                       deliver=_deliver(messenger.PendingDraft("x")),
                                       docs_dir=tmp_path, clock=lambda: 1000))
    assert msg.replies == [media.PENDING_DRAFT_TEXT]
    assert "Not sent" in media.PENDING_DRAFT_TEXT


# -- the merged turn is not echoed --------------------------------------------------

def _relayed(texts: list[str]) -> list[str]:
    sent: list[str] = []
    reg = BindingRegistry("777")
    reg.bind("@1", "42")
    relay = RegistryRelay(lambda text, *_a, **_k: sent.append(text) or True, reg,
                          send_photos=lambda *_a, **_k: sent.append("PHOTO") or True)
    content = [{"type": "text", "text": t} for t in texts]
    content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                                "data": base64.b64encode(b"png").decode()}})
    events, _ = parse_entries([{"type": "user", "timestamp": "t",
                                "message": {"role": "user", "content": content}}])
    for m in events:
        relay.on_message("@1", m)
    return sent


@pytest.mark.parametrize("texts", [
    # the 10-13 transcript shape, verbatim: the next message landed after the marker
    ["[Image #2]seems like the 3d view is stuck\n\n📎 image:ok i refreshed and it came back"],
    ["[Image #2]caption\n\n📎 image:"],
    ["[Image #2]caption", "📎 image:ok i refreshed"],               # marker in its own block
])
def test_turn_carrying_the_inbound_marker_is_not_echoed(texts):
    assert _relayed(texts) == []


def test_terminal_pasted_image_without_the_marker_still_relays():
    sent = _relayed(["ok, see this … [Image #47]"])
    assert sent[-1] == "PHOTO" and "see this" in sent[0]


def test_photo_verify_runs_off_the_event_loop(tmp_path):
    import threading
    seen = []

    def deliver(_wid, _text):
        seen.append(threading.current_thread() is threading.main_thread())
        return "submitted"
    asyncio.run(media.receive_photo(_Msg(), 1, 2, resolve=lambda c, t: "@3", deliver=deliver,
                                    docs_dir=tmp_path, clock=lambda: 1000))
    assert seen == [False]
