"""CMX-466: a relaunched session keeps the topic it was USING, not an old duplicate.

11-22: glide (one session) had two topics in the ledger — 121037, the one it used until
the update restart, and 121834, a duplicate from 10-29. The reap closed 121037, then the
reopen-by-session took 121834, because :meth:`TopicLedger.for_session` ranked by the
``bound`` stamp, which a topic reopened by folder never got. Two fixes, pinned here:

* the ledger ranks a session's topics by LAST USE (latest of closed / bound / created);
* a gone window whose session is running in a new window hands its topic over directly,
  never closed and reopened — on the same tick, or within a grace period.

Driven through the real :class:`TopicManager` over a fake transport.
"""
from __future__ import annotations

import json

import pytest

from chela.telegram import reconcile
from chela.telegram.bindings import BindingRegistry
from chela.telegram.reconcile import TopicManager, reconcile_bindings
from chela.topic_ledger import TopicLedger
from tests.test_telegram_topic_reuse import CHAT, _thread, _Wire

SID = "54f4e6da-0000-4000-8000-000000000001"
OTHER = "99999999-8888-7777-6666-555555555555"
EP = "e1-100"
A, B = "121834", "121037"      # A: the old duplicate; B: the topic in use until the restart

# Seconds, in the ledger's own clock: 10-29 15:17, 10-29 23:57, 11-20, 11-22 10:31.
OCT29_EARLY, OCT29_LATE, NOV20, NOV22 = 1000.0, 2000.0, 5000.0, 9000.0


def _ledger(tmp_path, b_closed: float | None = NOV22):
    """The 11-22 ledger: B made first and used since (no ``bound`` stamp — it was reopened
    by folder), A made later on 10-29, bound then, closed then, unused since."""
    topics = {
        B: {"created": OCT29_EARLY, "window": "@17", "cwd": "/p/glide", "name": "glide",
            "closed": b_closed, "session_id": SID},
        A: {"created": OCT29_LATE, "window": "@9", "cwd": "/p/glide", "name": "glide-dup",
            "closed": OCT29_LATE + 5, "session_id": SID, "bound": OCT29_LATE + 1},
    }
    path = tmp_path / "topics.json"
    path.write_text(json.dumps({"topics": topics}))
    return TopicLedger(path, clock=lambda: NOV22 + 60)


def _pre_cmx466_for_session(self, session_id):
    """Today's choice before CMX-466, verbatim: ``bound``, then ``created``."""
    hits = [t for t, e in self._topics.items() if e.get("session_id") == session_id]
    return sorted(hits, key=lambda t: (self._topics[t].get("bound") or 0,
                                       self._topics[t].get("created") or 0), reverse=True)


class _Clock:
    def __init__(self, t=0.0):
        self.t = t

    def __call__(self):
        return self.t


def _tick(reg, ledger, wire, live, sessions, *, agents=None, **kw):
    api = TopicManager("tok", CHAT, transport=wire)
    cwds = {w: "/p/glide" for w in live}
    return reconcile_bindings(reg, live, set(live) if agents is None else agents, api,
                              cwd_for=cwds.get, ledger=ledger, session_for=sessions.get,
                              now_epoch=EP, **kw)


def _bound_to_b(name="glide", pin="777", title="Passkey authentication support"):
    reg = BindingRegistry(CHAT)
    reg.bind("@17", B, EP)
    reg.set_topic_name("@17", name)
    reg.set_pinned_title("@17", title)
    reg.set_pinned_message_id("@17", pin)
    return reg


MUTATING = {"closeForumTopic", "reopenForumTopic", "deleteForumTopic", "createForumTopic"}


def _mutations(wire):
    return [c for c in wire.calls if c[0] in MUTATING]


MOVED_B = [("reopenForumTopic", _thread(B))]   # the move's open-check; nothing else


# -- the ledger's ranking ------------------------------------------------------------

def test_the_topic_used_last_ranks_first(tmp_path):
    """Acceptance: A (bound 10-29) and B (used until now) — B first."""
    assert _ledger(tmp_path).for_session(SID) == [B, A]


def test_negative_control_the_old_ranking_picks_the_duplicate(tmp_path):
    """The same rows under the pre-CMX-466 key pick A — the 11-22 bug."""
    led = _ledger(tmp_path)
    assert _pre_cmx466_for_session(led, SID) == [A, B]


@pytest.mark.parametrize("rows, expect", [
    # bound alone decides when nothing was closed
    ({"1": {"created": 1, "bound": 50}, "2": {"created": 2, "bound": 40}}, ["1", "2"]),
    # a later close outranks an earlier bound
    ({"1": {"created": 1, "bound": 50}, "2": {"created": 2, "closed": 60}}, ["2", "1"]),
    # an earlier close loses to a later bound
    ({"1": {"created": 1, "bound": 70}, "2": {"created": 2, "closed": 60}}, ["1", "2"]),
    # created alone counts as use (a topic never bound nor closed)
    ({"1": {"created": 80}, "2": {"created": 2, "closed": 60}}, ["1", "2"]),
    # a tie on last use goes to bound, then to created
    ({"1": {"created": 1, "closed": 60, "bound": 10},
      "2": {"created": 2, "closed": 60, "bound": 20}}, ["2", "1"]),
    ({"1": {"created": 5, "closed": 60}, "2": {"created": 2, "closed": 60}}, ["1", "2"]),
    # missing / None stamps read as 0, never a crash
    ({"1": {"created": None, "closed": None, "bound": None}, "2": {"closed": 3}}, ["2", "1"]),
])
def test_last_use_ranking_table(tmp_path, rows, expect):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"topics": {t: {**e, "session_id": SID} for t, e in rows.items()}}))
    assert TopicLedger(path).for_session(SID) == expect


# -- the reopen picks it -------------------------------------------------------------

def test_a_relaunched_session_with_no_binding_reopens_the_topic_it_used(tmp_path):
    """Acceptance through reconcile: the binding is already gone (closed by the reap), the
    session is back in @76 — it gets B, reopened, and no other topic is touched."""
    led, reg, wire = _ledger(tmp_path), BindingRegistry(CHAT), _Wire()
    assert _tick(reg, led, wire, {"@76": "claude"}, {"@76": SID}) is True
    assert reg.thread_for_window("@76") == B
    assert [c for c in wire.calls if c[0] in MUTATING] == [("reopenForumTopic", _thread(B))]


def test_negative_control_the_old_ranking_rebinds_the_duplicate(tmp_path, monkeypatch):
    led, reg, wire = _ledger(tmp_path), BindingRegistry(CHAT), _Wire()
    monkeypatch.setattr(TopicLedger, "for_session", _pre_cmx466_for_session)
    _tick(reg, led, wire, {"@76": "claude"}, {"@76": SID})
    assert reg.thread_for_window("@76") == A


# -- a relaunch hands the topic over -------------------------------------------------

def test_a_relaunch_seen_on_one_tick_moves_the_topic_without_closing_it(tmp_path):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    grace: dict = {}
    assert _tick(reg, led, wire, {"@76": "claude"}, {"@76": SID}, relaunch_grace=grace) is True
    assert reg.thread_for_window("@76") == B and reg.thread_for_window("@17") is None
    assert reg.epoch_for("@76") == EP
    # name and pin carried over: no rename, and the next title sync edits pin 777
    assert reg.topic_name("@76") == "glide"
    assert reg.pinned_message_id("@76") == "777"
    assert reg.pinned_title("@76") == "Passkey authentication support"
    assert wire.calls == MOVED_B
    entry = led.get(B)
    assert entry["window"] == "@76" and entry["closed"] is None and entry["session_id"] == SID
    assert grace == {}


def test_a_moved_topic_without_a_cached_name_or_pin_carries_none(tmp_path):
    led, wire = _ledger(tmp_path, b_closed=None), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@17", B, EP)
    _tick(reg, led, wire, {"@76": "claude"}, {"@76": SID})
    assert reg.thread_for_window("@76") == B
    assert reg.pinned_message_id("@76") is None and reg.pinned_title("@76") is None
    # no cached name: the rename loop resyncs it once, as for any unknown name
    assert [m for m, _ in wire.calls] == ["reopenForumTopic", "editForumTopic"]


def test_without_a_ledger_cwd_resolver_the_move_records_no_cwd(tmp_path):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    api = TopicManager("tok", CHAT, transport=wire)
    reconcile_bindings(reg, {"@76": "glide"}, {"@76"}, api, ledger=led,
                       session_for={"@76": SID}.get, now_epoch=EP)
    assert reg.thread_for_window("@76") == B and led.get(B)["cwd"] is None


def test_a_gone_window_is_held_through_the_grace_and_then_moved(tmp_path):
    """11-22's timing: @17 died at t=0, @76 ran the session at t=38."""
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    grace, clock = {}, _Clock(0.0)
    assert _tick(reg, led, wire, {}, {}, relaunch_grace=grace, clock=clock) is False
    assert reg.thread_for_window("@17") == B and grace == {"@17": 0.0}
    clock.t = 38.0
    _tick(reg, led, wire, {"@76": "claude"}, {"@76": SID}, relaunch_grace=grace, clock=clock)
    assert reg.thread_for_window("@76") == B
    assert _mutations(wire) == MOVED_B
    assert grace == {}


@pytest.mark.parametrize("elapsed, held", [(89.9, True), (90.0, False), (300.0, False)])
def test_the_grace_runs_out_and_the_topic_closes(tmp_path, elapsed, held):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    grace, clock = {}, _Clock(100.0)
    _tick(reg, led, wire, {}, {}, relaunch_grace=grace, clock=clock)
    clock.t = 100.0 + elapsed
    changed = _tick(reg, led, wire, {}, {}, relaunch_grace=grace, clock=clock)
    if held:
        assert changed is False and reg.thread_for_window("@17") == B and wire.calls == []
        assert grace == {"@17": 100.0}
    else:
        assert changed is True and reg.thread_for_window("@17") is None
        assert wire.calls == [("closeForumTopic", _thread(B))]
        assert grace == {}


def test_the_grace_length_is_a_parameter_defaulting_to_90s(tmp_path):
    assert reconcile.RELAUNCH_GRACE_SECONDS == 90.0
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    grace, clock = {}, _Clock(0.0)
    _tick(reg, led, wire, {}, {}, relaunch_grace=grace, clock=clock,
          relaunch_grace_seconds=5)
    clock.t = 5.0
    _tick(reg, led, wire, {}, {}, relaunch_grace=grace, clock=clock,
          relaunch_grace_seconds=5)
    assert wire.calls == [("closeForumTopic", _thread(B))]


def test_after_the_grace_a_live_close_asks_the_gone_window_and_deletes_when_asked(tmp_path):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    _tick(reg, led, wire, {}, {"@17": OTHER}, delete_on_close=False)
    assert wire.calls == [("closeForumTopic", _thread(B))]
    assert led.get(B)["session_id"] == OTHER           # the gone window's session, as before
    reg.bind("@17", A, EP)
    _tick(reg, led, wire, {}, {}, delete_on_close=True)
    assert wire.calls[-1] == ("deleteForumTopic", _thread(A))


def test_without_a_grace_dict_a_gone_window_closes_at_once(tmp_path):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    assert _tick(reg, led, wire, {}, {}) is True
    assert wire.calls == [("closeForumTopic", _thread(B))]
    assert reg.thread_for_window("@17") is None
    assert led.get(B)["session_id"] == SID             # unknown at close: the record stays


def test_a_stranger_wearing_the_id_gets_no_grace_and_is_never_asked(tmp_path):
    """A dangling id that is live names a stranger: the topic leaves it at once, and is
    closed (never deleted: a restart) when its session is nowhere to be seen."""
    led, wire = _ledger(tmp_path, b_closed=None), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@17", B, "e0-old")
    asked, grace = [], {}

    def session_for(wid):
        asked.append(wid)
        return OTHER
    api = TopicManager("tok", CHAT, transport=wire)
    reconcile_bindings(reg, {"@17": "x"}, set(), api, ledger=led, session_for=session_for,
                       now_epoch=EP, relaunch_grace=grace, delete_on_close=True,
                       clock=_Clock())
    assert wire.calls == [("closeForumTopic", _thread(B))]
    assert asked == [] and grace == {} and reg.thread_for_window("@17") is None
    assert led.get(B)["session_id"] == SID


def test_a_restart_relaunch_moves_the_topic_off_the_stranger(tmp_path):
    """Same tick: the old id is worn by a stranger, the session is back at @0."""
    led, wire = _ledger(tmp_path, b_closed=None), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@17", B, "e0-old")
    _tick(reg, led, wire, {"@17": "x", "@0": "claude"}, {"@17": OTHER, "@0": SID},
          agents={"@0"}, relaunch_grace={})
    assert reg.thread_for_window("@0") == B and reg.thread_for_window("@17") is None
    assert _mutations(wire) == MOVED_B


def test_a_dangling_window_not_live_is_held_by_the_grace(tmp_path):
    led, wire = _ledger(tmp_path, b_closed=None), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@17", B, "e0-old")
    grace = {}
    _tick(reg, led, wire, {}, {}, relaunch_grace=grace, clock=_Clock(3.0))
    assert reg.thread_for_window("@17") == B and grace == {"@17": 3.0} and wire.calls == []


def test_a_gone_window_with_no_recorded_session_closes_at_once_even_with_a_grace(tmp_path):
    led, wire = _ledger(tmp_path), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@3", "42", EP)
    led.opened("42", "@3", "/p/app")                  # no session recorded
    grace = {}
    _tick(reg, led, wire, {}, {}, relaunch_grace=grace, clock=_Clock())
    assert wire.calls == [("closeForumTopic", _thread("42"))] and grace == {}


def test_a_window_running_another_session_does_not_take_the_topic(tmp_path):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    grace = {}
    _tick(reg, led, wire, {"@76": "claude"}, {"@76": OTHER}, relaunch_grace=grace,
          clock=_Clock())
    assert reg.thread_for_window("@17") == B and reg.thread_for_window("@76") != B
    assert grace == {"@17": 0.0}


def test_a_dispatched_window_that_wants_no_topic_does_not_take_it(tmp_path):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    _tick(reg, led, wire, {"@76": "claude"}, {"@76": SID}, dispatched={"@76"},
          relaunch_grace={}, clock=_Clock())
    assert reg.thread_for_window("@76") is None and reg.thread_for_window("@17") == B


def test_an_unreadable_candidate_session_is_no_match_and_no_crash(tmp_path):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()

    def session_for(wid):
        raise RuntimeError("tmux gone")
    api = TopicManager("tok", CHAT, transport=wire)
    reconcile_bindings(reg, {"@76": "claude"}, {"@76"}, api, ledger=led,
                       session_for=session_for, now_epoch=EP)
    # no match: closed as before (the gone window's session unreadable too: record kept)
    assert ("closeForumTopic", _thread(B)) in wire.calls
    assert reg.thread_for_window("@76") != B and led.get(B)["session_id"] == SID


def test_each_candidate_session_is_read_once_per_tick(tmp_path):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    asked = []

    def session_for(wid):
        asked.append(wid)
        return OTHER
    api = TopicManager("tok", CHAT, transport=wire)
    reconcile_bindings(reg, {"@76": "claude"}, {"@76"}, api, ledger=led,
                       session_for=session_for, now_epoch=EP, relaunch_grace={},
                       clock=_Clock())
    assert asked == ["@76"]


def test_a_sidebar_rebind_takes_a_held_topic(tmp_path):
    """The resumed window's session is not readable yet, but the sidebar asked for B:
    the gone window holding B for a relaunch gives it up."""
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    grace = {}
    _tick(reg, led, wire, {}, {}, relaunch_grace=grace, clock=_Clock())
    rebinds = {"@76": B}
    _tick(reg, led, wire, {"@76": "claude"}, {}, relaunch_grace=grace, clock=_Clock(1.0),
          rebinds=rebinds)
    assert reg.thread_for_window("@76") == B and reg.thread_for_window("@17") is None
    assert grace == {} and rebinds == {}
    assert ("closeForumTopic", _thread(B)) not in wire.calls


def test_a_sidebar_rebind_never_takes_a_live_windows_topic(tmp_path):
    led, wire = _ledger(tmp_path, b_closed=None), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@17", B, EP)
    _tick(reg, led, wire, {"@17": "glide", "@76": "claude"}, {"@17": SID},
          relaunch_grace={}, clock=_Clock(), rebinds={"@76": B})
    assert reg.thread_for_window("@17") == B


def test_a_stale_grace_entry_is_dropped(tmp_path):
    led, wire = _ledger(tmp_path, b_closed=None), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@17", B, EP)
    grace = {"@17": 0.0, "@99": 0.0}
    _tick(reg, led, wire, {"@17": "glide"}, {"@17": SID}, relaunch_grace=grace,
          clock=_Clock(1.0))
    assert grace == {}


def test_the_bridge_loop_keeps_one_grace_dict(monkeypatch):
    from tests.test_telegram_reconcile import _run_one_tick
    kwargs = _run_one_tick(monkeypatch)["kwargs"]
    assert kwargs["relaunch_grace"] == {} and isinstance(kwargs["relaunch_grace"], dict)


def test_a_topic_closed_or_deleted_meanwhile_is_not_moved(tmp_path):
    """The move asks Telegram first: a topic it cannot reopen is closed as before, and the
    relaunched window is provisioned like any other."""
    led, reg = _ledger(tmp_path, b_closed=None), _bound_to_b()
    wire = _Wire({"reopenForumTopic"})
    grace = {}
    _tick(reg, led, wire, {"@76": "claude"}, {"@76": SID}, relaunch_grace=grace,
          clock=_Clock())
    assert reg.thread_for_window("@76") not in (None, B)
    assert reg.window_for_thread(B) == "@17" and grace == {"@17": 0.0}


def test_two_gone_windows_of_one_session_hand_over_the_topic_used_last(tmp_path):
    """Orphans of A (old duplicate) and B (in use) both record SID: the relaunched window
    takes B whatever the binding order; A is held, then closed."""
    led, wire = _ledger(tmp_path, b_closed=None), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@9", A, EP)                    # bound FIRST: iterated first
    reg.bind("@17", B, EP)
    grace = {}
    # one tick live: B, in use with no stamp, learns its bound time
    _tick(reg, led, wire, {"@9": "glide", "@17": "glide"}, {"@9": SID, "@17": SID},
          relaunch_grace=grace, clock=_Clock())
    assert led.get(B)["bound"] == NOV22 + 60 and led.get(A)["bound"] == OCT29_LATE + 1
    _tick(reg, led, wire, {"@76": "claude"}, {"@76": SID}, relaunch_grace=grace,
          clock=_Clock())
    assert reg.thread_for_window("@76") == B and reg.thread_for_window("@9") == A


def test_a_pending_sidebar_rebind_wins_over_a_relaunch_match(tmp_path):
    led, reg, wire = _ledger(tmp_path, b_closed=None), _bound_to_b(), _Wire()
    rebinds = {"@76": A}
    _tick(reg, led, wire, {"@76": "claude"}, {"@76": SID}, relaunch_grace={},
          clock=_Clock(), rebinds=rebinds)
    assert reg.thread_for_window("@76") == A and rebinds == {}


def test_a_topic_in_use_with_a_session_but_no_stamp_learns_its_bound_time(tmp_path):
    """B's session came from a close, so it carries no ``bound``: the first tick it is seen
    bound and live stamps it, once, and never moves a complete record."""
    led, wire = _ledger(tmp_path, b_closed=None), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@17", B, EP)
    asked = []

    def session_for(wid):
        asked.append(wid)
        return SID
    api = TopicManager("tok", CHAT, transport=wire)
    for _ in range(3):
        reconcile_bindings(reg, {"@17": "glide"}, {"@17"}, api, ledger=led,
                           session_for=session_for, now_epoch=EP)
    assert led.get(B)["bound"] == NOV22 + 60 and asked == ["@17"]
    assert led.set_session(B, OTHER) is False and led.get(B)["session_id"] == SID
    assert led.set_session(A, OTHER) is False          # A: complete record


def test_a_restart_that_reissues_the_same_id_to_the_session_moves_without_closing(tmp_path):
    """tmux restarted and the restored session came back at the SAME id: the stale binding
    leaves the id first, so the window is a candidate and takes its topic back unclosed."""
    led, wire = _ledger(tmp_path, b_closed=None), _Wire()
    reg = BindingRegistry(CHAT)
    reg.bind("@17", B, "e0-old")
    _tick(reg, led, wire, {"@17": "glide"}, {"@17": SID}, relaunch_grace={}, clock=_Clock())
    assert reg.thread_for_window("@17") == B and reg.epoch_for("@17") == EP
    assert _mutations(wire) == MOVED_B
