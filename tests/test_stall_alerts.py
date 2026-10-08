"""CMX-26: a held inbox, or a clean PR nobody merges, pushes ONE alert — not silence.

On 2026-10-07 the orchestrator read ``busy`` for 4h25m (a background task in its session),
the idle-gated inbox held nine events — three judge-clean verdicts among them — and nothing
told the human. These pin the alert's contract: past the threshold and not before, once per
held episode, re-armed only by a drain; clean-unmerged once per run and never for a verdict
on a stale head; and only the announcer (the CMX-9 lease) ever sends.

CMX-27 made a ``busy`` orchestrator deliverable over the peer socket, so the tests hold the
queue the ways that STILL hold it: the orchestrator ``waiting`` (an open prompt, refused on
both paths), or a socket that is unreachable / answers with an adverse receipt while busy.
"""
from __future__ import annotations

import time

import pytest

from chela import dispatcher, inbox, judge, messenger, notify, stall_alerts

ORCH = "@6"
T0 = 1_000_000.0
HELD_S = 1800
CLEAN_S = 1800


@pytest.fixture
def sent(monkeypatch):
    out: list[tuple[str, str]] = []
    monkeypatch.setattr(notify, "NOTIFY_URL", "https://ntfy.sh/test-topic")
    monkeypatch.setattr(notify, "send",
                        lambda message, title=None: out.append((title, message)) or True)
    monkeypatch.setenv("CHELA_INBOX_HELD_ALERT_S", str(HELD_S))
    monkeypatch.setenv("CHELA_CLEAN_UNMERGED_ALERT_S", str(CLEAN_S))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    return out


@pytest.fixture
def owner(tmp_path):
    lock = notify.OwnerLock(tmp_path / notify.LOCK_NAME)
    yield lock
    lock.release()


def _store(*event_ts, kind="run_judge_clean"):
    store = inbox._empty()
    store["orchestrator"] = ORCH
    store["queue"] = [{"kind": kind, "summary": "x",
                       "payload": {"task_id": f"CMX-{i}"}, "ts": ts}
                      for i, ts in enumerate(event_ts, start=23)]
    return store


def _tick(store, now, owner, statuses=None, runs=None, live_heads=None):
    alerts = stall_alerts.evaluate(store, statuses or {ORCH: "waiting"}, runs, live_heads, now,
                                   owner=owner)
    stall_alerts.send(alerts)
    return alerts


def _held(sent):
    return [m for t, m in sent if t == stall_alerts.HELD_TITLE]


def _clean(sent):
    return [m for t, m in sent if t == stall_alerts.CLEAN_TITLE]


# --- (1) the held inbox ----------------------------------------------------------------------

def test_held_fires_once_past_the_threshold_and_not_before(sent, owner):
    store = _store(T0)
    _tick(store, T0 + HELD_S - 1, owner)
    assert _held(sent) == []                         # not before

    _tick(store, T0 + HELD_S + 1, owner)
    assert len(_held(sent)) == 1                     # past it
    msg = _held(sent)[0]
    assert "1 notice(s) held for 30m" in msg
    assert f"orchestrator {ORCH} has been waiting the whole time" in msg
    assert "run_judge_clean CMX-23" in msg
    assert "Last delivery attempt" not in msg        # none was recorded: say nothing


def test_held_names_the_last_delivery_failure(sent, owner):
    store = _store(T0)
    store["last_delivery_failure"] = {"ts": T0 + HELD_S - 120, "target": ORCH,
                                      "kind": "run_judge_clean", "reason": "peer receipt held"}
    _tick(store, T0 + HELD_S + 1, owner, statuses={ORCH: "busy"})
    msg = _held(sent)[0]
    assert f"has been busy the whole time. Last delivery attempt: peer receipt held at {ORCH}, 2m ago." in msg


def test_a_drain_forgets_the_last_delivery_failure(sent, owner):
    store = _store(T0)
    store["last_delivery_failure"] = {"ts": T0, "target": ORCH, "reason": "peer receipt held"}
    store["queue"] = []
    _tick(store, T0 + 1, owner)
    assert store["last_delivery_failure"] is None


def test_held_does_not_fire_twice_in_one_episode(sent, owner):
    store = _store(T0, T0 + 10)
    for k in range(1, 6):
        _tick(store, T0 + HELD_S * k, owner)
    # a partial drain is still the same episode
    store["queue"].pop(0)
    _tick(store, T0 + HELD_S * 7, owner)
    assert len(_held(sent)) == 1


def test_held_re_arms_after_the_queue_drains_and_re_forms(sent, owner):
    store = _store(T0)
    _tick(store, T0 + HELD_S + 1, owner)
    assert len(_held(sent)) == 1

    store["queue"] = []                              # drained
    _tick(store, T0 + HELD_S + 2, owner)
    later = T0 + 10 * HELD_S
    store["queue"] = _store(later)["queue"]          # re-formed
    _tick(store, later + 5, owner)
    assert len(_held(sent)) == 1                     # new episode, but not old enough yet
    _tick(store, later + HELD_S + 1, owner)
    assert len(_held(sent)) == 2


def test_held_names_every_status_the_episode_saw(sent, owner):
    store = _store(T0)
    _tick(store, T0 + 1, owner, statuses={ORCH: "busy"})
    _tick(store, T0 + HELD_S + 1, owner, statuses={ORCH: "waiting"})
    assert "has been busy/waiting meanwhile" in _held(sent)[0]


def test_a_non_holder_of_the_lease_never_sends(sent, owner, tmp_path):
    assert owner.acquire()                           # the other process is the announcer
    standby = notify.OwnerLock(tmp_path / notify.LOCK_NAME)
    try:
        store = _store(T0)
        runs = [_clean_run()]
        _tick(store, T0, standby, runs=runs)
        _tick(store, T0 + 10 * HELD_S, standby, runs=runs)
        assert sent == []
        # ...and it latched nothing, so the holder still announces the episode
        _tick(store, T0 + 10 * HELD_S, owner, runs=runs)
        assert len(_held(sent)) == 1
    finally:
        standby.release()


def test_notifications_off_sends_nothing(sent, owner, monkeypatch):
    monkeypatch.setattr(notify, "NOTIFY_URL", "")
    _tick(_store(T0), T0 + 10 * HELD_S, owner)
    assert sent == []


# --- (2) a clean PR, unmerged ----------------------------------------------------------------

def _clean_run(**over):
    return {"task_id": "CMX-23", "status": "awaiting_review", "judge_state": judge.J_CLEAN,
            "judge_sha": "abc1234def", "pr_head_sha": "abc1234def",
            "pr_url": "https://github.com/o/r/pull/607", **over}


def test_clean_unmerged_fires_once_per_run(sent, owner):
    store = inbox._empty()
    runs = [_clean_run()]
    _tick(store, T0, owner, runs=runs)               # starts the clock
    _tick(store, T0 + CLEAN_S - 1, owner, runs=runs)
    assert _clean(sent) == []                        # not before
    _tick(store, T0 + CLEAN_S + 1, owner, runs=runs)
    _tick(store, T0 + 5 * CLEAN_S, owner, runs=runs)
    assert len(_clean(sent)) == 1
    assert "CMX-23" in _clean(sent)[0] and "pull/607" in _clean(sent)[0]

    other = _clean_run(task_id="CMX-24", judge_sha="fff0000", pr_head_sha="fff0000")
    _tick(store, T0 + 5 * CLEAN_S, owner, runs=runs + [other])
    _tick(store, T0 + 7 * CLEAN_S, owner, runs=runs + [other])
    assert len(_clean(sent)) == 2                    # a second run gets its own push


def test_clean_unmerged_ignores_a_verdict_on_a_stale_head(sent, owner):
    store = inbox._empty()
    stale = [_clean_run(pr_head_sha="0000newhead")]
    for k in range(4):
        _tick(store, T0 + k * CLEAN_S, owner, runs=stale)
    assert _clean(sent) == []


def test_clean_unmerged_trusts_the_live_head_over_the_cached_one(sent, owner):
    store = inbox._empty()
    runs = [_clean_run()]                            # cache says current...
    live = {"CMX-23": "0000newhead"}                 # ...GitHub says it moved
    for k in range(4):
        _tick(store, T0 + k * CLEAN_S, owner, runs=runs, live_heads=live)
    assert _clean(sent) == []


def test_clean_unmerged_forgets_a_run_that_left_review(sent, owner):
    store = inbox._empty()
    _tick(store, T0, owner, runs=[_clean_run()])
    _tick(store, T0 + CLEAN_S + 1, owner, runs=[_clean_run(status="done")])
    assert _clean(sent) == []
    assert store[stall_alerts.CLEAN_KEY] == {}


# --- the daemon wiring: inbox.tick really calls it, after CMX-27's deliver ---------------
#
# Window events (`finished`) never go stale, so these isolate the HOLD from the stale-drop.

@pytest.fixture
def wired(sent, owner, tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(notify, "_owner", owner)
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {ORCH: "orchestrator"})
    monkeypatch.setattr(inbox.epoch, "current", lambda: None)
    monkeypatch.setattr(inbox.sessions, "wid_for_session", lambda sid, pane_map=None: None)
    calls = {"peer": [], "tmux": []}

    def arm(status, peer_result):
        monkeypatch.setattr(inbox, "status_snapshot", lambda: {ORCH: status})
        monkeypatch.setattr(inbox.messenger, "send_peer",
                            lambda wid, frm, text: calls["peer"].append(text) or peer_result)
        monkeypatch.setattr(inbox.messenger, "send_tmux",
                            lambda wid, text: calls["tmux"].append(text) or False)
        inbox.save(_store(time.time() - HELD_S - 60, kind="finished"))
        return calls
    return arm


def test_tick_alerts_once_while_the_orchestrator_is_waiting(sent, wired):
    calls = wired("waiting", messenger.PeerSendResult(True, "sent"))
    inbox.tick({})
    inbox.tick({})
    assert calls["peer"] == [] and calls["tmux"] == []   # a prompt is open: nothing sent
    assert len(_held(sent)) == 1
    assert f"orchestrator {ORCH} has been waiting the whole time" in _held(sent)[0]
    assert len(inbox.load()["queue"]) == 1


def test_tick_alerts_when_a_busy_orchestrators_socket_is_unreachable(sent, wired):
    calls = wired("busy", messenger.PeerSendResult(False, None))
    inbox.tick({})
    inbox.tick({})
    assert calls["tmux"] == []                           # never a paste into a busy session
    assert len(_held(sent)) == 1
    assert ("has been busy the whole time. Last delivery attempt: peer socket unreachable "
            f"(no tmux paste while busy) at {ORCH}") in _held(sent)[0]


def test_tick_alerts_when_a_busy_orchestrators_socket_returns_an_adverse_receipt(sent, wired):
    wired("busy", messenger.PeerSendResult(True, "denied"))
    inbox.tick({})
    inbox.tick({})
    assert len(_held(sent)) == 1
    assert f"Last delivery attempt: peer receipt denied at {ORCH}" in _held(sent)[0]


def test_a_tick_that_drains_a_long_held_queue_does_not_alert(sent, wired):
    """Evaluate-AFTER-deliver is load-bearing: the queue this tick delivered was held past
    the threshold, but it is not held any more, so there is nothing to tell the human."""
    calls = wired("busy", messenger.PeerSendResult(True, "sent"))
    inbox.tick({})
    assert len(calls["peer"]) == 1
    assert inbox.load()["queue"] == []
    assert _held(sent) == []
