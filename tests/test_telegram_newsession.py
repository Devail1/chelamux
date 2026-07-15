"""Tests for ``/new`` — the launch-a-session-from-anywhere bridge command.

Two layers, mirroring :mod:`tests.test_telegram_inbound`:

* the **pure browser UI** (:func:`build_browser` / :func:`decode_new_callback` /
  :func:`list_subdirs`) — no ``python-telegram-bot``, no tmux;
* the **wiring** — the real ``_on_new`` / ``_on_new_cb`` callbacks from a live
  ``build_application``, driven with a stub launcher and fake Telegram updates, so
  the chat-only gate and the browse→launch flow are exercised end to end.
"""
from __future__ import annotations

import asyncio

import pytest

from chela.telegram.newsession import (
    NEW_CB_PREFIX,
    build_browser,
    decode_new_callback,
    list_subdirs,
)


# --------------------------------------------------------------------------
# The pure folder-browser UI.
# --------------------------------------------------------------------------

def _all_buttons(rows):
    return [b for row in rows for b in row]


def test_list_subdirs_hides_dotfiles_by_default(tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / "a_file.txt").write_text("x")

    assert list_subdirs(tmp_path) == ["alpha", "beta"]
    assert ".hidden" in list_subdirs(tmp_path, show_hidden=True)


def test_list_subdirs_on_unreadable_path_is_empty(tmp_path):
    # A path that does not exist reads as "no subdirectories", never an exception.
    assert list_subdirs(tmp_path / "nope") == []


def test_browser_offers_a_button_per_subdir(tmp_path):
    for name in ("one", "two", "three"):
        (tmp_path / name).mkdir()

    text, rows, subdirs = build_browser(tmp_path)

    assert subdirs == ["one", "three", "two"]
    cds = [cb for (_label, cb) in _all_buttons(rows)]
    # One n:cd:<idx> per subdir, indexed into the FULL sorted list.
    assert f"{NEW_CB_PREFIX}cd:0" in cds
    assert f"{NEW_CB_PREFIX}cd:2" in cds
    assert str(tmp_path) in text or "~" in text


def test_browser_always_offers_start_here_and_cancel(tmp_path):
    _text, rows, _ = build_browser(tmp_path)
    cds = [cb for (_label, cb) in _all_buttons(rows)]
    assert f"{NEW_CB_PREFIX}go" in cds
    assert f"{NEW_CB_PREFIX}x" in cds


def test_browser_offers_up_unless_at_filesystem_root(tmp_path):
    _text, rows, _ = build_browser(tmp_path)
    assert f"{NEW_CB_PREFIX}up" in [cb for (_l, cb) in _all_buttons(rows)]

    _text, root_rows, _ = build_browser("/")
    assert f"{NEW_CB_PREFIX}up" not in [cb for (_l, cb) in _all_buttons(root_rows)]


def test_browser_paginates_and_indices_stay_global(tmp_path):
    # More subdirs than a page holds → a nav row, and page-2 indices continue past
    # the page boundary (never reset to 0), so a cd tap resolves the right folder.
    for i in range(20):
        (tmp_path / f"d{i:02d}").mkdir()

    _text, rows, subdirs = build_browser(tmp_path, page=1)
    cds = [cb for (_l, cb) in _all_buttons(rows)]
    assert f"{NEW_CB_PREFIX}pg:0" in cds  # a "back a page" control exists
    # Page 1 (0-indexed) starts at DIRS_PER_PAGE, so its first button is not cd:0.
    cd_indices = [int(c.split(":")[2]) for c in cds if c.startswith(f"{NEW_CB_PREFIX}cd:")]
    assert min(cd_indices) >= 8
    assert len(subdirs) == 20


def test_browser_callback_data_stays_within_64_bytes(tmp_path):
    for i in range(30):
        (tmp_path / f"folder-number-{i}").mkdir()
    for page in (0, 1, 2):
        _text, rows, _ = build_browser(tmp_path, page=page)
        for (_label, cb) in _all_buttons(rows):
            assert len(cb.encode()) <= 64


@pytest.mark.parametrize(
    "data,expected",
    [
        ("n:up", ("up", None)),
        ("n:go", ("go", None)),
        ("n:x", ("cancel", None)),
        ("n:noop", ("noop", None)),
        ("n:cd:7", ("cd", 7)),
        ("n:pg:3", ("pg", 3)),
    ],
)
def test_decode_new_callback_round_trips(data, expected):
    assert decode_new_callback(data) == expected


@pytest.mark.parametrize("data", ["k:up", "qa:1", "", "n:", "n:cd:", "n:cd:x", "n:bogus"])
def test_decode_new_callback_rejects_foreign_or_malformed(data):
    assert decode_new_callback(data) is None


# --------------------------------------------------------------------------
# The WIRING: drive the real _on_new / _on_new_cb from a live build_application.
# --------------------------------------------------------------------------

pytest.importorskip("telegram")

CHAT = 777


class _Msg:
    def __init__(self, text, thread_id=None):
        self.text = text
        self.message_thread_id = thread_id
        self.replies: list[tuple[str, object]] = []

    async def reply_text(self, text, reply_markup=None, **_kw):
        self.replies.append((text, reply_markup))


class _CmdUpdate:
    def __init__(self, text, chat_id=CHAT, thread_id=None):
        self.message = _Msg(text, thread_id)
        self.effective_chat = type("Chat", (), {"id": chat_id})()
        self.callback_query = None


class _Query:
    def __init__(self, data, thread_id=None):
        self.data = data
        self.message = _Msg(None, thread_id)
        self.answers: list[str | None] = []
        self.edits: list[tuple[str | None, object]] = []

    async def answer(self, text=None, **_kw):
        self.answers.append(text)

    async def edit_message_text(self, text, reply_markup=None, **_kw):
        self.edits.append((text, reply_markup))

    async def edit_message_reply_markup(self, reply_markup=None, **_kw):
        self.edits.append((None, reply_markup))


class _CbUpdate:
    def __init__(self, data, chat_id=CHAT, thread_id=None):
        self.message = None
        self.callback_query = _Query(data, thread_id)
        self.effective_chat = type("Chat", (), {"id": chat_id})()


class _Ctx:
    """A stand-in for PTB's context — only ``user_data`` is touched by the handlers."""

    def __init__(self):
        self.user_data: dict = {}


def _handlers(launch_session):
    """The real ``_on_new`` (command) and ``_on_new_cb`` (^n: callback) callbacks."""
    from telegram.ext import CallbackQueryHandler, CommandHandler

    from chela.telegram.inbound import TopicRouter, build_application

    app = build_application(
        "123:fake-token",
        # topic_id "4" bound, but /new must ignore the topic and gate on the chat.
        TopicRouter(str(CHAT), "@3", "4"),
        launch_session=launch_session,
    )
    hs = [h for group in app.handlers.values() for h in group]
    on_new = next(
        h.callback for h in hs
        if isinstance(h, CommandHandler) and "new" in h.commands
    )
    on_new_cb = next(
        h.callback for h in hs
        if isinstance(h, CallbackQueryHandler)
        and h.pattern is not None and h.pattern.match("n:go")
    )
    return on_new, on_new_cb


def test_new_from_general_topic_opens_the_browser():
    # The headline: an unbound topic (General reports thread_id=None) is exactly
    # where the normal router would DROP the message — /new must work there.
    on_new, _ = _handlers(lambda _cwd: ("@7", None))
    ctx = _Ctx()
    update = _CmdUpdate("/new", thread_id=None)

    asyncio.run(on_new(update, ctx))

    text, markup = update.message.replies[-1]
    assert markup is not None and markup.inline_keyboard, "a folder browser was offered"
    # The browse state was cached for the follow-up taps.
    assert "new_path" in ctx.user_data


def test_new_from_a_foreign_chat_is_silent():
    launched: list[str] = []
    on_new, _ = _handlers(lambda cwd: launched.append(cwd) or ("@7", None))
    update = _CmdUpdate("/new", chat_id=999)  # not the bound chat

    asyncio.run(on_new(update, _Ctx()))

    assert update.message.replies == []          # stayed silent
    assert launched == []                         # and never launched anything


def test_new_with_a_path_argument_launches_directly():
    launched: list[str] = []
    on_new, _ = _handlers(lambda cwd: launched.append(cwd) or ("@7", None))
    update = _CmdUpdate("/new /tmp/some/project")

    asyncio.run(on_new(update, _Ctx()))

    assert launched == ["/tmp/some/project"]
    reply = update.message.replies[-1][0]
    assert "Started a Claude session" in reply


def test_a_failed_launch_reports_the_error():
    on_new, _ = _handlers(lambda _cwd: (None, "no such directory: /nope"))
    update = _CmdUpdate("/new /nope")

    asyncio.run(on_new(update, _Ctx()))

    assert update.message.replies[-1][0] == "❌ no such directory: /nope"


def test_browser_navigation_then_start_here_launches_the_browsed_path(tmp_path):
    (tmp_path / "repo").mkdir()
    launched: list[str] = []
    on_new, on_new_cb = _handlers(lambda cwd: launched.append(cwd) or ("@7", None))
    ctx = _Ctx()

    # Open the browser rooted at tmp_path (path arg bypasses the projects-dir default
    # AND the browser — so seed the state directly the way _on_new would).
    ctx.user_data["new_path"] = str(tmp_path)
    ctx.user_data["new_dirs"] = ["repo"]

    # Tap the "repo" folder (index 0) → descend into it, keyboard redrawn in place.
    enter = _CbUpdate("n:cd:0")
    asyncio.run(on_new_cb(enter, ctx))
    assert ctx.user_data["new_path"] == str(tmp_path / "repo")
    assert enter.callback_query.edits, "the browser was redrawn in place"

    # Now "Start here" → launch in the descended path, keyboard stripped.
    go = _CbUpdate("n:go")
    asyncio.run(on_new_cb(go, ctx))
    assert launched == [str(tmp_path / "repo")]


def test_cancel_tap_dismisses_the_browser():
    on_new, on_new_cb = _handlers(lambda _cwd: ("@7", None))
    ctx = _Ctx()
    ctx.user_data["new_path"] = "/tmp"

    update = _CbUpdate("n:x")
    asyncio.run(on_new_cb(update, ctx))

    assert update.callback_query.answers == ["Cancelled"]
    # The message was edited to a dismissal (no keyboard).
    assert update.callback_query.edits and update.callback_query.edits[-1][1] is None


def test_callback_from_a_foreign_chat_is_answered_but_inert():
    launched: list[str] = []
    on_new, on_new_cb = _handlers(lambda cwd: launched.append(cwd) or ("@7", None))
    ctx = _Ctx()
    ctx.user_data["new_path"] = "/tmp"

    update = _CbUpdate("n:go", chat_id=999)
    asyncio.run(on_new_cb(update, ctx))

    # The spinner is stopped, but nothing launches from the wrong chat.
    assert update.callback_query.answers == [None]
    assert launched == []
