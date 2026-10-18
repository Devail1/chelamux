"""CMX-204: reuse a folder's old topic, opt-in delete-on-close, and the cleanup button.

Driven through the real :class:`TopicManager` over a fake transport, so every Bot API
call is pinned by method AND fields, and no test ever reaches Telegram.
"""
from __future__ import annotations

import itertools

import pytest

from chela import userconfig
from chela import topic_ledger as topics
from chela.telegram.bindings import BindingRegistry
from chela.telegram.reconcile import TopicManager, delete_archived_topics, reconcile_bindings
from chela.topic_ledger import TopicLedger

CHAT = "-100777"


class _Wire:
    """A fake Bot API transport: records every call; ``fail`` methods answer a 400."""

    def __init__(self, fail=()):
        self.calls: list[tuple[str, dict]] = []
        self.fail = set(fail)
        self._ids = itertools.count(201)

    def __call__(self, method, fields):
        self.calls.append((method, dict(fields)))
        if method in self.fail:
            return {"ok": False, "description": "Bad Request: TOPIC_ID_INVALID"}
        if method == "createForumTopic":
            return {"ok": True, "result": {"message_thread_id": next(self._ids)}}
        return {"ok": True, "result": True}

    def methods(self):
        return [m for m, _ in self.calls]


def _thread(tid):
    return {"chat_id": CHAT, "message_thread_id": tid}


@pytest.fixture
def ledger(tmp_path):
    return TopicLedger(tmp_path / "topics.json", clock=itertools.count(1000).__next__)


def _tick(reg, ledger, wire, live, cwds, **kw):
    """One reconcile pass; ``live`` is {wid: window name}, every window an agent."""
    api = TopicManager("tok", CHAT, transport=wire)
    return reconcile_bindings(reg, live, set(live), api, cwd_for=cwds.get, ledger=ledger, **kw)


# -- delete-on-close ---------------------------------------------------------------

def test_delete_topic_sends_delete_forum_topic():
    wire = _Wire()
    assert TopicManager("tok", CHAT, transport=wire).delete_topic("42") is True
    assert wire.calls == [("deleteForumTopic", _thread("42"))]
    assert TopicManager("tok", CHAT, transport=_Wire({"deleteForumTopic"})).delete_topic("42") is False


@pytest.mark.parametrize("delete, expect", [(False, "closeForumTopic"), (True, "deleteForumTopic")])
def test_reap_closes_by_default_and_deletes_only_when_the_setting_is_on(ledger, delete, expect):
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@3", "42")
    ledger.opened("42", "@3", "/p/app")
    assert _tick(reg, ledger, wire, {}, {}, delete_on_close=delete) is True
    assert wire.calls == [(expect, _thread("42"))]
    assert reg.thread_for_window("@3") is None
    # Closed ⇒ remembered for reuse/cleanup; deleted ⇒ nothing left to remember.
    assert ledger.closed_threads() == ([] if delete else ["42"])
    assert (ledger.get("42") is None) is delete


def test_a_failed_delete_falls_back_to_close(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire({"deleteForumTopic"})
    reg.bind("@3", "42")
    _tick(reg, ledger, wire, {}, {}, delete_on_close=True)
    assert wire.calls == [("deleteForumTopic", _thread("42")), ("closeForumTopic", _thread("42"))]
    assert ledger.closed_threads() == ["42"]


# -- reuse a folder's topic --------------------------------------------------------

def test_window_closes_then_a_new_one_in_the_same_cwd_reopens_its_topic(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    _tick(reg, ledger, wire, {"@3": "coder"}, {"@3": "/p/app"})
    assert wire.methods() == ["createForumTopic"]
    _tick(reg, ledger, wire, {}, {})                      # the window dies
    assert ledger.get("201")["cwd"] == "/p/app" and ledger.closed_threads() == ["201"]
    wire.calls.clear()
    _tick(reg, ledger, wire, {"@8": "coder"}, {"@8": "/p/app/"})   # relaunch, same folder
    assert wire.calls == [("reopenForumTopic", _thread("201"))]   # no create, no rename
    assert reg.thread_for_window("@8") == "201"
    assert ledger.closed_threads() == [] and ledger.get("201")["window"] == "@8"


def test_a_renamed_window_gets_its_reopened_topic_renamed(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    _tick(reg, ledger, wire, {"@3": "coder"}, {"@3": "/p/app"})
    _tick(reg, ledger, wire, {}, {})
    wire.calls.clear()
    _tick(reg, ledger, wire, {"@8": "fixer"}, {"@8": "/p/app"})
    assert wire.calls == [("reopenForumTopic", _thread("201")),
                          ("editForumTopic", {**_thread("201"), "name": "fixer"})]
    assert ledger.get("201")["name"] == "fixer"


def test_two_concurrent_windows_in_one_cwd_get_two_topics_both_reused_later(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    _tick(reg, ledger, wire, {"@3": "a", "@4": "b"}, {"@3": "/p/app", "@4": "/p/app"})
    assert wire.methods() == ["createForumTopic", "createForumTopic"]
    _tick(reg, ledger, wire, {}, {})
    wire.calls.clear()
    _tick(reg, ledger, wire, {"@7": "a", "@9": "b"}, {"@7": "/p/app", "@9": "/p/app"})
    # Which window takes which topic follows set order, so a rename may follow; no create.
    assert wire.methods().count("reopenForumTopic") == 2
    assert "createForumTopic" not in wire.methods()
    assert {reg.thread_for_window("@7"), reg.thread_for_window("@9")} == {"201", "202"}


def test_a_failed_reopen_creates_a_new_topic_and_prunes_the_old(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    _tick(reg, ledger, wire, {"@3": "coder"}, {"@3": "/p/app"})
    _tick(reg, ledger, wire, {}, {})
    wire.fail.add("reopenForumTopic")
    wire.calls.clear()
    _tick(reg, ledger, wire, {"@8": "coder"}, {"@8": "/p/app"})
    assert wire.methods() == ["reopenForumTopic", "createForumTopic"]
    assert reg.thread_for_window("@8") == "202"
    assert ledger.get("201") is None


@pytest.mark.parametrize("cwd", ["/p/other", None])
def test_another_cwd_or_none_never_reuses(ledger, cwd):
    reg, wire = BindingRegistry(CHAT), _Wire()
    ledger.opened("201", "@3", None)
    ledger.closed("201")
    ledger.opened("200", "@2", "/p/app")
    ledger.closed("200")
    _tick(reg, ledger, wire, {"@8": "coder"}, {"@8": cwd})
    assert wire.methods() == ["createForumTopic"]


def test_reuse_picks_the_oldest_closed_topic_of_the_folder(tmp_path):
    # Insertion order (200, 300, 100), id order (100, 200, 300) and creation order
    # (300, 100, 200) all differ, so dropping, reversing or re-keying the sort reads wrong.
    clock = itertools.chain([9000, 1000, 5000], itertools.count(10000)).__next__
    led = TopicLedger(tmp_path / "topics.json", clock=clock)
    for tid in ("200", "300", "100"):             # created at 9000, 1000, 5000
        led.opened(tid, "@1", "/p/app")
    for tid in ("100", "200", "300"):
        led.closed(tid)
    assert led.closed_for("/p/app") == ["300", "100", "200"]
    # ... and the reconcile reopens that oldest one, not just lists it first.
    reg, wire = BindingRegistry(CHAT), _Wire()
    _tick(reg, led, wire, {"@8": "coder"}, {"@8": "/p/app"})
    assert wire.calls[0] == ("reopenForumTopic", _thread("300"))
    assert reg.thread_for_window("@8") == "300"


def test_reuse_skips_a_closed_topic_that_is_still_bound(ledger):
    # The ledger and the registry can disagree (a rebind binds a topic the ledger still
    # has as closed). The registry wins: a bound topic is never handed to a second window.
    reg, wire = BindingRegistry(CHAT), _Wire()
    ledger.opened("150", "@4", "/p/app", "a")
    ledger.closed("150")
    reg.bind("@4", "150")
    reg.set_topic_name("@4", "a")
    _tick(reg, ledger, wire, {"@4": "a", "@8": "a"}, {"@4": "/p/other", "@8": "/p/app"})
    assert ("reopenForumTopic", _thread("150")) not in wire.calls
    assert "createForumTopic" in wire.methods()
    assert reg.thread_for_window("@4") == "150" and reg.thread_for_window("@8") == "201"


def test_the_ledger_records_window_cwd_name_and_both_times(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    _tick(reg, ledger, wire, {"@3": "coder"}, {"@3": "/p/app"})
    _tick(reg, ledger, wire, {}, {})
    assert ledger.get("201") == {"created": 1000, "window": "@3", "cwd": "/p/app",
                                 "closed": 1001, "name": "coder"}


def test_a_resumed_archive_marks_its_topic_open(ledger):
    # The rename is refused, so only the rebind itself can mark the topic open.
    reg, wire = BindingRegistry(CHAT), _Wire({"editForumTopic"})
    ledger.closed("55")
    _tick(reg, ledger, wire, {"@8": "coder"}, {"@8": "/p/app"}, rebinds={"@8": "55"})
    assert reg.thread_for_window("@8") == "55" and ledger.closed_threads() == []
    assert (ledger.get("55")["window"], ledger.get("55")["cwd"]) == ("@8", "/p/app")


# -- the ledger survives a restart ---------------------------------------------------

def test_the_ledger_survives_a_bridge_restart(tmp_path):
    path = tmp_path / "topics.json"
    first = TopicLedger(path)
    first.opened("201", "@3", "/p/app", "coder")
    first.closed("201")
    again = TopicLedger(path)
    assert again.closed_for("/p/app") == ["201"]
    assert again.get("201")["name"] == "coder"


@pytest.mark.parametrize("text", ["{not json", "[]", '{"topics": []}', '{"topics": {"9": "x"}}'])
def test_an_unreadable_ledger_reads_empty(tmp_path, text):
    path = tmp_path / "topics.json"
    path.write_text(text)
    assert TopicLedger(path).closed_threads() == []


# -- the cleanup button ------------------------------------------------------------

def test_cleanup_deletes_closed_topics_but_never_bound_ones_or_general(ledger):
    reg = BindingRegistry(CHAT)
    for tid in ("1", "42", "43", "44"):
        ledger.closed(tid)
    ledger.opened("45", "@5", "/p")
    reg.bind("@4", "43")                                     # closed in the ledger, yet bound
    wire = _Wire()
    api = TopicManager("tok", CHAT, transport=wire)
    assert delete_archived_topics(ledger, reg, api) == 2
    assert wire.calls == [("deleteForumTopic", _thread("42")), ("deleteForumTopic", _thread("44"))]
    assert ledger.closed_threads() == ["1", "43"]


def test_a_failed_cleanup_delete_stays_remembered(ledger):
    ledger.closed("42")
    api = TopicManager("tok", CHAT, transport=_Wire({"deleteForumTopic"}))
    assert delete_archived_topics(ledger, BindingRegistry(CHAT), api) == 0
    assert ledger.closed_threads() == ["42"]


# -- production wiring: the loop and the Settings route -------------------------------

@pytest.mark.parametrize("stored, expect", [(True, True), (None, False), ("true", False)])
def test_the_loop_reads_the_delete_setting_each_tick(monkeypatch, stored, expect):
    from tests.test_telegram_reconcile import _run_one_tick
    userconfig.set_(topics.DELETE_ON_CLOSE_KEY, stored)
    try:
        kwargs = _run_one_tick(monkeypatch)["kwargs"]
    finally:
        userconfig.set_(topics.DELETE_ON_CLOSE_KEY, None)
    assert kwargs["delete_on_close"] is expect
    assert isinstance(kwargs["ledger"], TopicLedger)


def test_the_loop_runs_a_requested_cleanup_once(monkeypatch):
    from chela import telegram as tg
    from tests.test_telegram_reconcile import _run_one_tick
    seen = []
    monkeypatch.setattr(tg, "delete_archived_topics", lambda *a: seen.append(a) or 0)
    _run_one_tick(monkeypatch)
    assert seen == []
    userconfig.set_(topics.CLEANUP_KEY, 123.0)
    _run_one_tick(monkeypatch)
    assert len(seen) == 1 and isinstance(seen[0][0], TopicLedger)
    assert userconfig.get(topics.CLEANUP_KEY) is None


def test_a_failed_cleanup_keeps_the_request_and_the_tick_still_reconciles(monkeypatch):
    from chela import telegram as tg
    from tests.test_telegram_reconcile import _run_one_tick

    def _boom(*a):
        raise RuntimeError("network down")
    monkeypatch.setattr(tg, "delete_archived_topics", _boom)
    userconfig.set_(topics.CLEANUP_KEY, 123.0)
    try:
        seen = _run_one_tick(monkeypatch)
        assert userconfig.get(topics.CLEANUP_KEY) == 123.0
        assert "kwargs" in seen                       # reconcile_bindings still ran
    finally:
        userconfig.set_(topics.CLEANUP_KEY, None)


def test_the_ledger_is_written_whole_with_no_tmp_left(tmp_path):
    path = tmp_path / "topics.json"
    TopicLedger(path).closed("42")
    assert TopicLedger(path).closed_threads() == ["42"]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["topics.json"]


def test_a_crash_mid_save_leaves_the_old_ledger_whole(tmp_path, monkeypatch):
    path = tmp_path / "topics.json"
    TopicLedger(path).closed("42")
    before = path.read_text()

    def _crash(*a):
        raise OSError("disk gone")
    monkeypatch.setattr(topics.os, "replace", _crash)
    with pytest.raises(OSError):
        TopicLedger(path).closed("43")
    assert path.read_text() == before


def test_settings_route_stores_the_switch_and_the_cleanup_request(monkeypatch, tmp_path):
    import chela.dashboard.app as app_mod
    monkeypatch.setattr(topics, "default_topics_path", lambda: tmp_path / "t.json")
    led = TopicLedger()
    led.closed("42")
    led.opened("43", "@3", "/p")                  # open: not archived, not counted
    led.closed("44")
    c = app_mod.app.test_client()
    try:
        j = c.get("/api/config").get_json()
        assert (j["telegram_delete_on_close"], j["telegram_archived_topics"]) == (False, 2)
        assert j["telegram_cleanup_pending"] is False
        j = c.post("/api/config", json={"telegram_delete_on_close": True}).get_json()
        assert j["telegram_delete_on_close"] is True
        assert userconfig.get(topics.DELETE_ON_CLOSE_KEY) is True
        assert c.post("/api/config", json={"telegram_delete_on_close": "maybe"}).status_code == 400
        assert c.post("/api/config", json={"telegram_cleanup": "yes"}).get_json()["telegram_cleanup_pending"] is False
        assert c.post("/api/config", json={"telegram_cleanup": True}).get_json()["telegram_cleanup_pending"] is True
        c.post("/api/config", json={"telegram_delete_on_close": False})
        assert userconfig.get(topics.DELETE_ON_CLOSE_KEY) is None
    finally:
        userconfig.set_(topics.DELETE_ON_CLOSE_KEY, None)
        userconfig.set_(topics.CLEANUP_KEY, None)
