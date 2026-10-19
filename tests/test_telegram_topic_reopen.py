"""CMX-267: a human reopening a closed topic brings its Claude session back.

Fakes for the bot, the spawn and the clock; a real :class:`TopicLedger` on ``tmp_path``.
"""
from __future__ import annotations

import asyncio
import itertools
from types import SimpleNamespace

import pytest

from chela import agent_manager
from chela.telegram.bindings import BindingRegistry
from chela.telegram.reconcile import (
    TopicClosedHandler,
    TopicManager,
    TopicReopenedHandler,
    reconcile_bindings,
)
from chela.topic_ledger import TopicLedger

SID = "0a1b2c3d-1111-2222-3333-444455556666"


@pytest.fixture
def ledger(tmp_path):
    return TopicLedger(tmp_path / "topics.json", clock=itertools.count(1000).__next__)


class _Spawn:
    def __init__(self, ok=True, wid="@12", error=None):
        self.calls: list[tuple] = []
        self._result = SimpleNamespace(ok=ok, wid=wid if ok else None,
                                       name="app" if ok else None, error=error)

    def __call__(self, cwd, **kw):
        self.calls.append((cwd, kw))
        return self._result


def _closed_entry(ledger, thread="42", cwd="/p/app", sid=SID, name="app"):
    ledger.opened(thread, "@3", cwd, name)
    ledger.closed(thread, sid)


def _handler(reg, ledger, spawn, **kw):
    saved: list[int] = []
    pins: list[tuple] = []
    opts = dict(spawn=spawn, session_exists=lambda sid: True, isdir=lambda p: True,
                now_epoch=lambda: "E1", on_change=lambda: saved.append(1),
                pin_session=lambda w, s: pins.append((w, s)))
    opts.update(kw)
    return TopicReopenedHandler(reg, ledger, **opts), saved, pins


# -- on close: the session id is recorded --------------------------------------------

def test_a_reap_records_the_dead_windows_session_id(ledger):
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    ledger.opened("42", "@3", "/p/app")
    api = TopicManager("tok", "777", transport=lambda m, f: {"ok": True, "result": True})
    reconcile_bindings(reg, {}, set(), api, ledger=ledger,
                       session_for={"@3": SID}.get)
    assert ledger.get("42")["closed"] is not None
    assert ledger.get("42")["session_id"] == SID


def test_a_human_close_records_the_windows_session_id(ledger):
    reg = BindingRegistry("777")
    reg.bind("@3", "42")
    ledger.opened("42", "@3", "/p/app")
    asked: list[str] = []
    TopicClosedHandler(reg, ledger=ledger,
                       session_for=lambda w: asked.append(w) or SID).handle(42)
    assert asked == ["@3"]
    assert ledger.get("42")["session_id"] == SID and ledger.get("42")["closed"] == 1001
    # No session_for: still closed, the session recorded as unknown.
    reg.bind("@5", "43")
    TopicClosedHandler(reg, ledger=ledger).handle("43")
    assert ledger.get("43")["session_id"] is None and ledger.get("43")["closed"] is not None


# -- on reopen: resume ---------------------------------------------------------------

def test_reopen_of_a_recorded_thread_resumes_it_bound_and_reopened(ledger):
    reg, spawn = BindingRegistry("777"), _Spawn()
    _closed_entry(ledger)
    h, saved, pins = _handler(reg, ledger, spawn)
    assert h.handle(42) == "Resumed app in /p/app"
    assert spawn.calls == [("/p/app", {"command": f"claude --resume {SID}", "name": "app"})]
    assert reg.thread_for_window("@12") == "42" and reg.epoch_for("@12") == "E1"
    assert ledger.get("42")["closed"] is None and ledger.get("42")["window"] == "@12"
    assert saved == [1] and pins == [("@12", SID)]


def test_reopen_of_an_already_bound_thread_spawns_nothing(ledger):
    reg, spawn = BindingRegistry("777"), _Spawn()
    _closed_entry(ledger)
    reg.bind("@3", "42")
    h, saved, _ = _handler(reg, ledger, spawn)
    assert h.handle("42") is None
    assert h.handle("42") is None          # a duplicate update: still one window at most
    assert spawn.calls == [] and saved == []


def test_a_reopen_chela_made_itself_spawns_nothing_and_consumes_its_mark(ledger):
    reg, spawn = BindingRegistry("777"), _Spawn()
    _closed_entry(ledger)
    reg.bind("@3", "42")                   # chela's reopen binds as it reopens
    marks = {"42"}
    h, _, _ = _handler(reg, ledger, spawn,
                       self_reopened=lambda t: (t in marks, marks.discard(t))[0])
    assert h.handle(42) is None and spawn.calls == []
    assert marks == set()                  # consumed even though the thread was bound
    reg.unbind("@3")
    marks.add("42")
    assert h.handle(42) is None and spawn.calls == []   # unbound, but chela's own reopen
    assert h.handle(42) == "Resumed app in /p/app"      # the human's later reopen resumes
    assert len(spawn.calls) == 1


# -- fallbacks: each one says something ----------------------------------------------

def test_no_ledger_entry_says_so_and_spawns_nothing(ledger):
    spawn = _Spawn()
    h, _, _ = _handler(BindingRegistry("777"), ledger, spawn)
    msg = "chela has no record of this topic's folder, so there is nothing to resume."
    assert h.handle(77) == msg
    ledger.closed("78")                    # a pre-ledger topic: entry, but no cwd
    assert h.handle(78) == msg
    assert spawn.calls == []


def test_a_gone_cwd_says_so_and_spawns_nothing(ledger):
    reg, spawn = BindingRegistry("777"), _Spawn()
    _closed_entry(ledger)
    seen: list[str] = []
    h, _, _ = _handler(reg, ledger, spawn, isdir=lambda p: seen.append(p) or False)
    assert h.handle(42) == "/p/app no longer exists, so there is nothing to resume."
    assert seen == ["/p/app"] and spawn.calls == []
    assert reg.window_for_thread(42) is None


@pytest.mark.parametrize("sid, exists, why", [
    (None, True, "no session was recorded for this topic"),
    ("not a session; rm -rf ~", True, "no session was recorded for this topic"),
    (SID, False, f"session {SID}'s transcript is gone"),
])
def test_a_missing_or_gone_session_starts_a_fresh_claude(ledger, monkeypatch, sid, exists, why):
    monkeypatch.setattr(agent_manager, "DEFAULT_LAUNCH_CMD", "claude --fresh-cmd")
    reg, spawn = BindingRegistry("777"), _Spawn()
    _closed_entry(ledger, sid=sid)
    asked: list[str] = []
    h, saved, pins = _handler(reg, ledger, spawn,
                              session_exists=lambda s: asked.append(s) or exists)
    assert h.handle(42) == f"Started a fresh session as app in /p/app ({why})."
    assert spawn.calls == [("/p/app", {"command": "claude --fresh-cmd", "name": "app"})]
    assert asked == ([SID] if sid == SID else [])
    assert reg.thread_for_window("@12") == "42" and saved == [1] and pins == []


def test_a_session_still_running_elsewhere_is_not_resumed_twice(ledger):
    reg, spawn = BindingRegistry("777"), _Spawn()
    _closed_entry(ledger)
    reg.bind("@7", "99")                   # it has another topic by now
    h, saved, _ = _handler(reg, ledger, spawn, session_live={SID: "@7"}.get)
    assert h.handle(42) == "app is still running in @7; not resuming it twice."
    assert spawn.calls == [] and reg.thread_for_window("@7") == "99" and saved == []
    h, _, _ = _handler(reg, ledger, spawn, session_live=lambda s: None)
    assert h.handle(42) == "Resumed app in /p/app"


def test_a_live_unbound_window_gets_its_topic_back_instead_of_a_second_resume(ledger):
    # A human close leaves the agent running: reopening rebinds it, spawns nothing.
    reg, spawn = BindingRegistry("777"), _Spawn()
    _closed_entry(ledger)
    h, saved, pins = _handler(reg, ledger, spawn, session_live={SID: "@7"}.get)
    assert h.handle(42) == "Rebound app (@7) to this topic; it was still running."
    assert spawn.calls == [] and pins == []
    assert reg.thread_for_window("@7") == "42" and reg.epoch_for("@7") == "E1"
    assert ledger.get("42")["closed"] is None and ledger.get("42")["window"] == "@7"
    assert saved == [1]


def test_a_failed_spawn_says_why_and_binds_nothing(ledger):
    reg = BindingRegistry("777")
    _closed_entry(ledger)
    h, saved, _ = _handler(reg, ledger, _Spawn(ok=False, error="no such directory: /p/app"))
    assert h.handle(42) == "Couldn't start a session in /p/app: no such directory: /p/app"
    assert reg.window_for_thread(42) is None and saved == []
    assert ledger.get("42")["closed"] is not None
    h, _, _ = _handler(reg, ledger, _Spawn(ok=True, wid=None))
    assert h.handle(42) == "Couldn't start a session in /p/app: no window id"


def test_a_failing_pin_does_not_undo_the_resume(ledger):
    reg, spawn = BindingRegistry("777"), _Spawn()
    _closed_entry(ledger)

    def _boom(w, s):
        raise RuntimeError("sessionids unwritable")

    h, _, _ = _handler(reg, ledger, spawn, pin_session=_boom)
    assert h.handle(42) == "Resumed app in /p/app"
    assert reg.thread_for_window("@12") == "42"


# -- TopicManager remembers its own reopens --------------------------------------------

def test_topic_manager_marks_its_own_reopen_once_and_briefly():
    now = [100.0]
    calls: list[str] = []

    def _wire(method, fields):
        calls.append(method)
        if method == "reopenForumTopic" and fields["message_thread_id"] == "9":
            raise RuntimeError("socket died")
        return {"ok": True, "result": True}

    api = TopicManager("tok", "777", transport=_wire, clock=lambda: now[0])
    assert api.reopened_by_chela("42") is False
    api.reopen_topic("42")
    now[0] = 129.0
    assert api.reopened_by_chela(42) is True
    assert api.reopened_by_chela(42) is False          # consumed
    api.reopen_topic("42")
    now[0] = 160.0
    assert api.reopened_by_chela("42") is False        # 31s later: not ours any more
    api.reopen_topic("42")
    api.close_topic("42")
    assert api.reopened_by_chela("42") is False        # a reopen after a close is not ours
    for desc in ("Bad Request: TOPIC_NOT_MODIFIED", "Bad Request: TOPIC_ID_INVALID"):
        api._transport = lambda m, f, d=desc: {"ok": False, "description": d}
        api.reopen_topic("42")             # no service message follows: no mark is left
        assert api.reopened_by_chela("42") is False
    api._transport = _wire
    with pytest.raises(RuntimeError):
        api.reopen_topic("9")
    assert api.reopened_by_chela("9") is True          # marked BEFORE the call


# -- the PTB handler -----------------------------------------------------------------

OPERATOR = 111


class _Bot:
    def __init__(self, fail=False):
        self.sent: list[dict] = []
        self._fail = fail

    async def send_message(self, **kw):
        self.sent.append(kw)
        if self._fail:
            raise RuntimeError("flood control")


def _reopen_cb(on_topic_reopened):
    from chela.telegram.inbound import TopicRouter, build_application

    app = build_application("123:fake-token", TopicRouter("777", "@3", "4", sender=None),
                            allowed_users=(OPERATOR,), on_topic_reopened=on_topic_reopened)
    return next(h.callback for group in app.handlers.values() for h in group
                if getattr(h.callback, "__wrapped__", None).__name__ == "_on_topic_reopened")


def _update(chat=777, is_bot=False, thread=42):
    user = SimpleNamespace(id=OPERATOR, is_bot=is_bot)
    msg = SimpleNamespace(message_thread_id=thread, chat_id=chat, from_user=user)
    return SimpleNamespace(message=msg, effective_chat=SimpleNamespace(id=chat),
                           effective_user=user)


def _fire(cb, update, bot):
    asyncio.run(cb(update, SimpleNamespace(bot=bot)))


def test_the_reopen_line_is_posted_into_that_topic():
    seen: list = []
    bot = _Bot()
    _fire(_reopen_cb(lambda t: seen.append(t) or "Resumed app in /p/app"), _update(), bot)
    assert seen == [42]
    assert bot.sent == [{"chat_id": 777, "text": "Resumed app in /p/app",
                         "message_thread_id": 42}]


@pytest.mark.parametrize("update", [_update(is_bot=True), _update(chat=999)],
                         ids=["chelas-own-reopen", "another-chat"])
def test_a_bot_made_or_foreign_chat_reopen_does_nothing(update):
    seen: list = []
    bot = _Bot()
    _fire(_reopen_cb(lambda t: seen.append(t) or "x"), update, bot)
    assert seen == [] and bot.sent == []


def test_a_crashing_resume_still_answers_and_silence_posts_nothing():
    def _boom(_t):
        raise RuntimeError("tmux gone")

    bot = _Bot()
    _fire(_reopen_cb(_boom), _update(), bot)
    assert [m["text"] for m in bot.sent] == [
        "❌ Couldn't bring this topic's session back — see the chela-telegram log"]
    bot = _Bot()
    _fire(_reopen_cb(lambda t: None), _update(), bot)
    assert bot.sent == []
    bot = _Bot(fail=True)
    _fire(_reopen_cb(lambda t: "Resumed"), _update(), bot)   # a failed post is swallowed
    assert len(bot.sent) == 1


# -- the production wiring -----------------------------------------------------------

def test_the_daemon_resumes_through_the_shared_spawn_path(ledger, monkeypatch, tmp_path):
    from chela import epoch, main, sessionids, sessions, spawn

    reg = BindingRegistry("777")
    api = TopicManager("tok", "777", transport=lambda m, f: {"ok": True})
    h = main._topic_reopened_handler(reg, ledger, api)
    assert h._spawn is spawn.spawn_window
    assert h._session_live is sessions.wid_for_session
    assert h._pin_session is sessionids.set_session_id
    assert h._now_epoch is epoch.current
    assert h._self_reopened == api.reopened_by_chela
    assert h._on_change == reg.save
    monkeypatch.setattr(sessions, "transcript_for_session",
                        lambda s: tmp_path if s == SID else None)
    assert h._session_exists(SID) is True and h._session_exists("x") is False


def test_the_daemon_shares_one_ledger_between_the_loop_and_both_handlers(monkeypatch):
    """Two ledgers on one file would each overwrite the other's writes: the entrypoint
    hands the reconcile loop the SAME instance the close/reopen handlers write."""
    import argparse
    import threading

    import chela.telegram as tg
    from chela import main

    started: list = []

    class _Thread:
        def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
            self.target, self.args = target, args

        def start(self):
            started.append(self)

    built: dict = {}
    monkeypatch.setattr(threading, "Thread", _Thread)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0:test-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-1001")
    monkeypatch.delenv("TELEGRAM_TOPIC_ID", raising=False)
    monkeypatch.setattr(main, "_build_bindings_registry",
                        lambda args, chat: BindingRegistry("-1001"))
    monkeypatch.setattr(tg, "build_application", lambda *a, **kw: built.update(kw) or
                        SimpleNamespace(run_polling=lambda: None))
    main.cmd_telegram(argparse.Namespace(wid=None, bind=None, interval=2, auto_topics=True,
                                         reconcile_interval=15, no_inbound=False))
    closed = built["on_topic_closed"].__self__
    reopened = built["on_topic_reopened"].__self__
    loops = [t for t in started if t.target is main._reconcile_loop]
    assert len(loops) == 1
    assert isinstance(reopened, TopicReopenedHandler)
    assert closed._ledger is reopened._ledger is loops[0].args[4]
    assert closed._session_for is tg.window_session_id


def test_a_reopen_with_no_thread_id_does_nothing_at_all(ledger):
    # A non-forum service message carries no thread: nothing to look up, nothing to say.
    reg, spawn = BindingRegistry("777"), _Spawn()
    ledger.opened("None", "@3", "/p/app")
    ledger.closed("None", SID)            # even a ledger row keyed "None" is never reached
    asked: list = []
    h, _, _ = _handler(reg, ledger, spawn, self_reopened=lambda t: asked.append(t) or False)
    assert h.handle(None) is None
    assert spawn.calls == [] and asked == []


def test_window_session_id_prefers_the_live_session_then_the_pin(monkeypatch):
    from chela import sessionids, sessions
    from chela.telegram.reconcile import window_session_id

    live = {"@3": "live-sid"}
    pins = {"@3": "old-pin", "@4": "pin-sid"}
    monkeypatch.setattr(sessions, "session_of_window", live.get)
    monkeypatch.setattr(sessionids, "session_id_for", pins.get)
    assert window_session_id("@3") == "live-sid"
    assert window_session_id("@4") == "pin-sid"        # dead window: its pin
    assert window_session_id("@5") is None

    def _boom(_wid):
        raise RuntimeError("tmux unreachable")

    monkeypatch.setattr(sessions, "session_of_window", _boom)
    assert window_session_id("@4") is None              # unknown, never a crash
