"""CMX-450: a session restored after a tmux restart takes back ITS OWN topic, by session id.

A restart reissues every window id; the binding (window -> topic) dies with it, and the
restored window used to get a NEW topic while its history stayed in the old one. The topic
ledger now records the session a topic was bound for, and auto-topics reopens that topic
before trying the folder match or creating one. Driven through the real
:class:`TopicManager` over a fake transport, so every Bot API call is pinned.
"""
from __future__ import annotations

import itertools
import sys

import pytest

from chela import main as chela_main
from chela import sidebar_archive
from chela.telegram.bindings import BindingRegistry
from chela.telegram.reconcile import TopicManager, reconcile_bindings
from chela.topic_ledger import TopicLedger
from tests.test_telegram_topic_reuse import CHAT, _thread, _Wire

SID = "11111111-2222-3333-4444-555555555555"
OTHER = "99999999-8888-7777-6666-555555555555"
OLD, NEW = "e1-100", "e2-200"


@pytest.fixture
def ledger(tmp_path):
    return TopicLedger(tmp_path / "topics.json", clock=itertools.count(1000).__next__)


def _tick(reg, ledger, wire, live, cwds, sessions, **kw):
    api = TopicManager("tok", CHAT, transport=wire)
    return reconcile_bindings(reg, live, set(live), api, cwd_for=cwds.get, ledger=ledger,
                              session_for=sessions.get, **kw)


# -- the ledger --------------------------------------------------------------------

def test_ledger_records_the_session_at_bind_and_keeps_it_on_an_unknown_close(ledger):
    ledger.opened("42", "@3", "/p/app", "app", session_id=SID)
    ledger.opened("42", "@3", "/p/app", "renamed")         # a rename: session kept
    assert ledger.get("42")["session_id"] == SID
    ledger.closed("42", None)                              # unknown at close: kept
    assert ledger.get("42")["session_id"] == SID
    ledger.closed("42", OTHER)                             # known at close: it wins
    assert ledger.get("42")["session_id"] == OTHER
    ledger.closed("77")                                    # a pre-ledger topic: recorded as None
    assert ledger.get("77")["session_id"] is None


def test_ledger_for_session_newest_first_and_set_session(ledger):
    ledger.opened("10", "@1", "/p", session_id=SID)
    ledger.opened("11", "@2", "/p", session_id=OTHER)
    ledger.opened("12", "@3", "/p", session_id=SID)
    ledger.opened("14", "@5", "/p")                          # no session recorded
    ledger.closed("15")                                      # closed with an unknown one
    assert ledger.for_session(SID) == ["12", "10"]
    assert ledger.for_session(None) == [] and ledger.for_session("") == []
    ledger.opened("13", "@4", "/p")
    assert ledger.set_session("13", SID) is True and ledger.for_session(SID)[0] == "13"
    assert ledger.set_session("13", OTHER) is False         # already recorded: never moved
    assert ledger.set_session("99", SID) is False           # unknown topic
    assert ledger.set_session("10", "") is False
    assert TopicLedger(ledger._path).for_session(SID) == ["13", "12", "10"]   # persisted


# -- the restart -------------------------------------------------------------------

def _bound_before_restart(ledger):
    """tapparty at @4 with topic 140624, bound under the OLD tmux server."""
    reg = BindingRegistry(CHAT)
    reg.bind("@4", "140624", OLD)
    ledger.opened("140624", "@4", "/p/tapparty", "tapparty", session_id=SID)
    return reg


def test_a_restored_session_gets_its_old_topic_back_and_no_new_one(ledger):
    """Acceptance: after a restart the session resumes at a NEW id (@0); its binding is
    reaped (closed, never deleted, and the stranger now at @4 is never asked for a session),
    and the restored window is bound to its OLD topic. No topic is created."""
    reg, wire = _bound_before_restart(ledger), _Wire()
    live = {"@0": "tapparty", "@4": "someone-else"}
    assert _tick(reg, ledger, wire, live, {"@0": "/p/elsewhere", "@4": "/p/x"},
                 {"@0": SID, "@4": OTHER}, now_epoch=NEW, delete_on_close=True) is True
    assert reg.thread_for_window("@0") == "140624"
    assert wire.calls[:2] == [("closeForumTopic", _thread("140624")),
                              ("reopenForumTopic", _thread("140624"))]
    # Had the reap asked the stranger at @4, the topic would be filed under OTHER and
    # tapparty would have got a fresh one.
    assert ledger.get("140624")["session_id"] == SID
    assert ledger.get("140624")["closed"] is None
    assert reg.topic_name("@0") == "tapparty"
    # The only topic created is the stranger's own: it is a new window.
    assert [f["name"] for m, f in wire.calls if m == "createForumTopic"] == ["someone-else"]
    assert reg.thread_for_window("@4") == "201"


def test_negative_control_without_a_session_match_a_new_topic_is_created(ledger):
    """Today's path: nothing in the ledger for the window's session ⇒ a fresh topic."""
    reg, wire = _bound_before_restart(ledger), _Wire()
    _tick(reg, ledger, wire, {"@0": "tapparty"}, {"@0": "/p/elsewhere"}, {"@0": OTHER},
          now_epoch=NEW)
    assert ("createForumTopic", {"chat_id": CHAT, "name": "tapparty"}) in wire.calls
    assert reg.thread_for_window("@0") == "201"
    assert ledger.get("201")["session_id"] == OTHER          # recorded at create
    # And with no session resolver at all, the same: the fresh-topic path.
    reg2, wire2 = _bound_before_restart(ledger), _Wire()
    api = TopicManager("tok", CHAT, transport=wire2)
    reconcile_bindings(reg2, {"@0": "tapparty"}, {"@0"}, api, cwd_for={}.get, ledger=ledger,
                       now_epoch=NEW)
    assert "createForumTopic" in wire2.methods()


def test_an_open_old_topic_is_rebound_without_a_reap(ledger):
    """The 11-20 shape: the old topic is still OPEN (unbound, never closed)."""
    reg, wire = BindingRegistry(CHAT), _Wire()
    ledger.opened("140624", "@4", "/p/tapparty", "tapparty", session_id=SID)
    assert _tick(reg, ledger, wire, {"@0": "tapparty"}, {"@0": "/p/tapparty"}, {"@0": SID},
                 now_epoch=NEW) is True
    assert wire.calls == [("reopenForumTopic", _thread("140624"))]
    assert reg.thread_for_window("@0") == "140624" and reg.epoch_for("@0") == NEW


def test_a_topic_telegram_already_shows_open_is_rebound(ledger):
    wire = _Wire()
    real = wire.__call__

    class NotModified(_Wire):
        def __call__(self, method, fields):
            if method == "reopenForumTopic":
                self.calls.append((method, dict(fields)))
                return {"ok": False, "description": "Bad Request: TOPIC_NOT_MODIFIED"}
            return real(method, fields)
    wire = NotModified()
    reg = BindingRegistry(CHAT)
    ledger.opened("140624", "@4", "/p/t", session_id=SID)
    _tick(reg, ledger, wire, {"@0": "t"}, {}, {"@0": SID}, now_epoch=NEW)
    assert reg.thread_for_window("@0") == "140624" and "createForumTopic" not in wire.methods()


def test_a_failed_reopen_keeps_the_topic_and_a_new_one_is_created(ledger):
    """A failure may be transient (429, timeout): the session's link is NOT forgotten."""
    reg, wire = BindingRegistry(CHAT), _Wire({"reopenForumTopic"})
    ledger.opened("140624", "@4", "/p/t", session_id=SID)
    _tick(reg, ledger, wire, {"@0": "t"}, {}, {"@0": SID}, now_epoch=NEW)
    assert ledger.get("140624")["session_id"] == SID
    assert reg.thread_for_window("@0") == "201"


def test_after_a_forced_rebind_the_rebound_topic_wins_the_next_restart(ledger):
    """T1 (older) is rebound over T2 (newer): a later restart must come back to T1."""
    reg, wire = BindingRegistry(CHAT), _Wire()
    ledger.opened("100", "@1", "/p/t", session_id=SID)       # T1, created first
    ledger.closed("100")
    reg.bind("@6", "200", NEW)
    ledger.opened("200", "@6", "/p/t", session_id=SID)       # T2, the new one
    _tick(reg, ledger, wire, {"@6": "t"}, {}, {"@6": SID}, now_epoch=NEW,
          rebinds={"@6": "100"}, force_rebinds={"@6"})
    assert reg.thread_for_window("@6") == "100"
    assert ledger.for_session(SID) == ["100", "200"]
    # The restart: @6 dies with the server, the session is back at @0.
    _tick(reg, ledger, wire, {"@0": "t"}, {}, {"@0": SID}, now_epoch="e3-300")
    assert reg.thread_for_window("@0") == "100"


def test_a_forced_rebind_with_an_unreadable_session_still_binds(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@6", "200", NEW)

    def boom(wid):
        raise RuntimeError("no tmux")
    api = TopicManager("tok", CHAT, transport=wire)
    reconcile_bindings(reg, {"@6": "t"}, {"@6"}, api, cwd_for={}.get, ledger=ledger,
                       session_for=boom, now_epoch=NEW, rebinds={"@6": "100"},
                       force_rebinds={"@6"})
    assert reg.thread_for_window("@6") == "100"


def test_a_topic_another_window_holds_is_never_taken(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@9", "140624", NEW)
    ledger.opened("140624", "@9", "/p/t", session_id=SID)
    _tick(reg, ledger, wire, {"@0": "t", "@9": "u"}, {}, {"@0": SID, "@9": SID}, now_epoch=NEW)
    assert reg.thread_for_window("@9") == "140624"
    assert reg.thread_for_window("@0") == "201"


def test_the_folder_match_never_takes_another_restored_sessions_topic(ledger):
    """Two sessions in one folder come back: each takes its OWN topic, whatever the order."""
    reg, wire = BindingRegistry(CHAT), _Wire()
    ledger.opened("500", "@1", "/p/app", session_id=OTHER)
    ledger.closed("500")
    ledger.opened("600", "@2", "/p/app", session_id=SID)
    ledger.closed("600")
    # @0 runs an unknown session (no ledger match): the folder match must skip 500, which
    # @5 (session OTHER) takes back, and 600 (session SID, nobody here) is free for it.
    _tick(reg, ledger, wire, {"@0": "a", "@5": "b"}, {"@0": "/p/app", "@5": "/p/app"},
          {"@0": "33333333-2222-3333-4444-555555555555", "@5": OTHER}, now_epoch=NEW)
    assert reg.thread_for_window("@5") == "500"
    assert reg.thread_for_window("@0") == "600"
    assert "createForumTopic" not in wire.methods()


def test_a_session_lookup_that_raises_falls_back_to_the_folder(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    ledger.opened("500", "@1", "/p/app")
    ledger.closed("500")

    def boom(wid):
        raise RuntimeError("no tmux")
    api = TopicManager("tok", CHAT, transport=wire)
    reconcile_bindings(reg, {"@0": "a"}, {"@0"}, api, cwd_for={"@0": "/p/app"}.get,
                       ledger=ledger, session_for=boom, now_epoch=NEW)
    assert reg.thread_for_window("@0") == "500"


def test_a_live_reap_still_records_the_session_and_deletes_when_asked(ledger):
    """Not a restart (same epoch, the window closed): the setting still deletes, and the
    session is asked for, as before."""
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@3", "42", NEW)
    ledger.opened("42", "@3", "/p/app")
    _tick(reg, ledger, wire, {}, {}, {"@3": SID}, now_epoch=NEW)
    assert wire.calls == [("closeForumTopic", _thread("42"))]
    assert ledger.get("42")["session_id"] == SID
    reg.bind("@3", "43", NEW)
    _tick(reg, ledger, wire, {}, {}, {}, now_epoch=NEW, delete_on_close=True)
    assert wire.calls[-1] == ("deleteForumTopic", _thread("43"))


def test_a_restart_reap_never_asks_the_stranger_for_a_session(ledger):
    reg, wire = _bound_before_restart(ledger), _Wire()
    asked = []

    def session_for(wid):
        asked.append(wid)
        return OTHER
    api = TopicManager("tok", CHAT, transport=wire)
    reconcile_bindings(reg, {"@4": "x"}, set(), api, cwd_for={}.get, ledger=ledger,
                       session_for=session_for, now_epoch=NEW)
    assert asked == []
    assert ledger.get("140624")["session_id"] == SID


def test_a_bound_topic_without_a_session_learns_it_once(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@3", "42", NEW)
    ledger.opened("42", "@3", "/p/app")
    asked = []

    def session_for(wid):
        asked.append(wid)
        return SID
    api = TopicManager("tok", CHAT, transport=wire)
    for _ in range(3):
        reconcile_bindings(reg, {"@3": "app"}, {"@3"}, api, cwd_for={}.get, ledger=ledger,
                           session_for=session_for, now_epoch=NEW)
    assert ledger.get("42")["session_id"] == SID
    assert asked == ["@3"]                                   # edge-triggered
    assert wire.calls == [("editForumTopic", {"chat_id": CHAT, "message_thread_id": "42",
                                              "name": "app"})]


def test_learning_a_session_that_cannot_be_read_is_harmless(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@3", "42", NEW)
    reg.set_topic_name("@3", "app")
    ledger.opened("42", "@3", "/p/app")

    def boom(wid):
        raise RuntimeError("no tmux")
    api = TopicManager("tok", CHAT, transport=wire)
    reconcile_bindings(reg, {"@3": "app"}, {"@3"}, api, cwd_for={}.get, ledger=ledger,
                       session_for=boom, now_epoch=NEW)
    reconcile_bindings(reg, {"@3": "app"}, {"@3"}, api, cwd_for={}.get, ledger=ledger,
                       session_for={}.get, now_epoch=NEW)
    assert ledger.get("42").get("session_id") is None and wire.calls == []


def test_a_sidebar_rebind_records_the_session(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    _tick(reg, ledger, wire, {"@2": "a"}, {}, {"@2": SID}, now_epoch=NEW, rebinds={"@2": "77"})
    assert reg.thread_for_window("@2") == "77"
    assert ledger.get("77")["session_id"] == SID


# -- the one-off: a forced rebind ------------------------------------------------------

def test_a_forced_rebind_moves_a_bound_window_to_its_old_topic(ledger):
    """tapparty's one-off: bound to the NEW topic 159840, moved back to 140624."""
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@6", "159840", NEW)
    ledger.opened("159840", "@6", "/p/t", session_id=SID)
    _tick(reg, ledger, wire, {"@6": "tapparty"}, {}, {"@6": SID}, now_epoch=NEW,
          rebinds={"@6": "140624"}, force_rebinds={"@6"}, delete_on_close=True)
    assert reg.thread_for_window("@6") == "140624"
    assert wire.calls[:2] == [("reopenForumTopic", _thread("140624")),
                              ("closeForumTopic", _thread("159840"))]   # closed, not deleted
    assert ledger.get("159840")["closed"] is not None


def test_an_unforced_rebind_leaves_a_bound_window_alone(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@6", "159840", NEW)
    reg.set_topic_name("@6", "tapparty")
    rebinds = {"@6": "140624"}
    _tick(reg, ledger, wire, {"@6": "tapparty"}, {}, {}, now_epoch=NEW, rebinds=rebinds)
    assert reg.thread_for_window("@6") == "159840" and wire.calls == [] and rebinds == {}


def test_a_forced_rebind_to_the_topic_it_already_has_does_nothing(ledger):
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@6", "140624", NEW)
    reg.set_topic_name("@6", "t")
    _tick(reg, ledger, wire, {"@6": "t"}, {}, {}, now_epoch=NEW,
          rebinds={"@6": "140624"}, force_rebinds={"@6"})
    assert wire.calls == []


def test_a_forced_rebind_whose_topic_is_gone_keeps_the_current_one(ledger, caplog):
    reg, wire = BindingRegistry(CHAT), _Wire({"reopenForumTopic"})
    reg.bind("@6", "159840", NEW)
    reg.set_topic_name("@6", "t")
    with caplog.at_level("WARNING", logger="chela.telegram.reconcile"):
        _tick(reg, ledger, wire, {"@6": "t"}, {}, {}, now_epoch=NEW,
              rebinds={"@6": "140624"}, force_rebinds={"@6"})
    assert reg.thread_for_window("@6") == "159840"
    assert wire.methods() == ["reopenForumTopic"]
    assert "forced rebind of @6 to 140624 dropped: the topic could not be reopened" in caplog.text


def test_a_forced_rebind_to_a_topic_another_window_holds_is_dropped_loudly(ledger, caplog):
    reg, wire = BindingRegistry(CHAT), _Wire()
    reg.bind("@6", "159840", NEW)
    reg.set_topic_name("@6", "t")
    reg.bind("@9", "140624", NEW)
    reg.set_topic_name("@9", "u")
    with caplog.at_level("WARNING", logger="chela.telegram.reconcile"):
        _tick(reg, ledger, wire, {"@6": "t", "@9": "u"}, {}, {}, now_epoch=NEW,
              rebinds={"@6": "140624"}, force_rebinds={"@6"})
    assert reg.thread_for_window("@6") == "159840" and wire.calls == []
    assert "forced rebind of @6 to 140624 dropped: another window holds that topic" in caplog.text


def test_an_unforced_rebind_failure_logs_no_forced_warning(ledger, caplog):
    reg, wire = BindingRegistry(CHAT), _Wire({"reopenForumTopic", "createForumTopic"})
    reg.bind("@9", "140624", NEW)
    reg.set_topic_name("@9", "u")
    with caplog.at_level("WARNING", logger="chela.telegram.reconcile"):
        _tick(reg, ledger, wire, {"@6": "t", "@9": "u"}, {}, {}, now_epoch=NEW,
              rebinds={"@6": "140624"})
        _tick(reg, ledger, wire, {"@6": "t", "@9": "u"}, {}, {}, now_epoch=NEW,
              rebinds={"@6": "500"})
    assert "forced rebind" not in caplog.text


def test_forced_rebinds_are_the_forced_pending_requests(monkeypatch):
    monkeypatch.setattr(sidebar_archive.epoch, "current", lambda: NEW)
    sidebar_archive.request_rebind("@6", "140624", force=True)
    sidebar_archive.request_rebind("@7", "500")
    assert sidebar_archive.pending_rebinds(NEW) == {"@6": "140624", "@7": "500"}
    assert sidebar_archive.forced_rebinds(NEW) == {"@6"}
    assert sidebar_archive.forced_rebinds("e3-300") == set()     # another server: stranger
    assert sidebar_archive.forced_rebinds(NEW, now=10 ** 12) == set()   # expired


@pytest.mark.parametrize("window, thread, ok", [
    ("@6", "140624", True), ("6", "140624", True),
    ("tapparty", "140624", False), ("@", "140624", False), ("@6", "14x", False),
    ("@6", " 140624 ", True),
])
def test_cli_rebind(monkeypatch, capsys, window, thread, ok):
    seen = []
    monkeypatch.setattr(sidebar_archive, "request_rebind",
                        lambda w, t, force=False: seen.append((w, t, force)))
    monkeypatch.setattr(sys, "argv", ["chela", "telegram", "rebind", window, thread])
    if ok:
        chela_main.main()
        assert seen == [("@6", "140624", True)]
        assert "@6 -> topic 140624" in capsys.readouterr().out
    else:
        with pytest.raises(SystemExit) as e:
            chela_main.main()
        assert e.value.code == 1 and seen == []


def test_the_folder_match_skips_a_topic_of_a_session_another_window_runs(ledger):
    """The folder match alone (the session pass found nothing to reopen for this window):
    a topic recorded for a session another window is being placed for is never taken."""
    from chela.telegram.reconcile import _reopen_for_cwd

    reg, wire = BindingRegistry(CHAT), _Wire()
    api = TopicManager("tok", CHAT, transport=wire)
    ledger.opened("500", "@1", "/p/app", session_id=OTHER)
    ledger.closed("500")
    ledger.opened("600", "@2", "/p/app")
    ledger.closed("600")
    assert _reopen_for_cwd(api, ledger, reg, "/p/app", skip_sessions={OTHER}) == "600"
    assert _reopen_for_cwd(api, ledger, reg, "/p/app") == "500"      # control: oldest first
