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

from chela import dispatcher, inbox, judge, messenger, notify, runtime_truth, stall_alerts

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


def _mixed_queue():
    """A queue whose OLDEST stamped event is neither first nor last, behind an unstamped one,
    and whose NEWEST is far younger than the threshold. ``min``/``max``/``queue[0]``/
    ``queue[-1]`` all pick a DIFFERENT event here, so the alert can only be right if it
    measures the oldest stamped one (min ts)."""
    store = _store()
    store["queue"] = [
        {"kind": "finished", "summary": "x", "payload": {"task_id": "CMX-90"}},   # no ts
        {"kind": "run_rework", "summary": "x", "payload": {"task_id": "CMX-91"},
         "ts": T0 + 600},
        {"kind": "run_judge_clean", "summary": "x", "payload": {"task_id": "CMX-92"},
         "ts": T0},                                                              # oldest
        {"kind": "run_review", "summary": "x", "payload": {"task_id": "CMX-93"},
         "ts": T0 + HELD_S},                                                     # newest
    ]
    return store


def test_oldest_held_is_the_min_ts_not_the_newest_or_first_or_last():
    assert stall_alerts.oldest_held(_mixed_queue())["payload"]["task_id"] == "CMX-92"
    assert stall_alerts.oldest_held(_store()) is None
    unstamped = _store()
    unstamped["queue"] = [{"kind": "finished", "payload": {}}]
    assert stall_alerts.oldest_held(unstamped) is None


def test_held_measures_the_oldest_event_while_the_newest_is_still_young(sent, owner):
    """The newest event is 1s old when the oldest crosses the threshold: an alert measured
    off the newest (or off whichever sits first/last in the queue) stays silent here."""
    store = _mixed_queue()
    _tick(store, T0 + HELD_S - 1, owner)
    assert _held(sent) == []
    _tick(store, T0 + HELD_S + 1, owner)
    assert len(_held(sent)) == 1
    msg = _held(sent)[0]
    assert "4 notice(s) held for 30m" in msg         # n counts every queued event
    assert msg.endswith("Oldest: run_judge_clean CMX-92.")


def test_held_fires_exactly_at_the_threshold(sent, owner):
    store = _store(T0)
    _tick(store, T0 + HELD_S - 1, owner)
    assert _held(sent) == []
    _tick(store, T0 + HELD_S, owner)
    assert len(_held(sent)) == 1


def test_held_threshold_comes_from_config(sent, owner, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_HELD_ALERT_S", "60")
    _tick(_store(T0), T0 + 61, owner)
    assert len(_held(sent)) == 1


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


def test_held_lists_each_status_once_in_the_order_seen(sent, owner):
    store = _store(T0)
    for k, st in enumerate(["busy", "busy", "waiting", "busy", "waiting"]):
        _tick(store, T0 + k, owner, statuses={ORCH: st})
    _tick(store, T0 + HELD_S + 1, owner, statuses={ORCH: "waiting"})
    assert "has been busy/waiting meanwhile" in _held(sent)[0]


def test_a_drain_forgets_the_statuses_the_last_episode_saw(sent, owner):
    store = _store(T0)
    _tick(store, T0 + 1, owner, statuses={ORCH: "busy"})
    store["queue"] = []
    _tick(store, T0 + 2, owner, statuses={ORCH: "busy"})
    assert store[stall_alerts.HELD_KEY] is None
    store["queue"] = _store(T0 + 10)["queue"]
    _tick(store, T0 + 10 + HELD_S + 1, owner, statuses={ORCH: "waiting"})
    assert f"orchestrator {ORCH} has been waiting the whole time" in _held(sent)[0]


def test_held_names_an_absent_or_unregistered_orchestrator(sent, owner):
    store = _store(T0)
    _tick(store, T0 + HELD_S + 1, owner, statuses={"@9": "idle"})
    assert f"orchestrator {ORCH} has been absent the whole time" in _held(sent)[0]

    store = _store(T0)
    store["orchestrator"] = None
    sent.clear()
    _tick(store, T0 + HELD_S + 1, owner, statuses={})
    assert "orchestrator (none) has been unregistered the whole time" in _held(sent)[0]


def test_describe_failure_without_ts_or_target():
    assert stall_alerts.describe_failure({"last_delivery_failure": {"reason": "r"}}, T0) == "r"
    assert stall_alerts.describe_failure({"last_delivery_failure": {"reason": ""}}, T0) is None
    assert stall_alerts.describe_failure({}, T0) is None
    assert (stall_alerts.describe_failure(
        {"last_delivery_failure": {"reason": "r", "target": ORCH, "ts": T0 - 7200}}, T0)
        == f"r at {ORCH}, 2h00m ago")


def test_describe_event_without_a_task():
    assert stall_alerts.describe_event({"kind": "finished", "payload": {}}) == "finished"
    assert stall_alerts.describe_event({}) == "?"


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


def test_clean_unmerged_fires_exactly_at_the_threshold(sent, owner):
    store = inbox._empty()
    runs = [_clean_run()]
    _tick(store, T0, owner, runs=runs)
    _tick(store, T0 + CLEAN_S - 1, owner, runs=runs)
    assert _clean(sent) == []
    _tick(store, T0 + CLEAN_S, owner, runs=runs)
    assert len(_clean(sent)) == 1
    assert "(abc1234)" in _clean(sent)[0] and "for 30m" in _clean(sent)[0]


def test_clean_unmerged_restarts_the_clock_on_a_new_clean_head(sent, owner):
    """Re-judged clean on a NEW head: the PR the human would merge has been clean only since
    that verdict, so the clock restarts — and the push names the new sha."""
    store = inbox._empty()
    _tick(store, T0, owner, runs=[_clean_run()])
    moved = [_clean_run(judge_sha="bbb9999", pr_head_sha="bbb9999")]
    t1 = T0 + CLEAN_S - 10
    _tick(store, t1, owner, runs=moved)
    _tick(store, T0 + CLEAN_S + 1, owner, runs=moved)
    assert _clean(sent) == []
    _tick(store, t1 + CLEAN_S, owner, runs=moved)
    assert len(_clean(sent)) == 1 and "(bbb9999)" in _clean(sent)[0]


def test_clean_unmerged_stays_once_per_run_after_the_head_moves(sent, owner):
    store = inbox._empty()
    _tick(store, T0, owner, runs=[_clean_run()])
    _tick(store, T0 + CLEAN_S, owner, runs=[_clean_run()])
    moved = [_clean_run(judge_sha="bbb9999", pr_head_sha="bbb9999")]
    for k in range(2, 6):
        _tick(store, T0 + k * CLEAN_S, owner, runs=moved)
    assert len(_clean(sent)) == 1


@pytest.mark.parametrize("over", [
    {"judge_state": "survived"}, {"judge_sha": None}, {"judge_sha": None, "pr_head_sha": None},
    {"pr_head_sha": None}, {"status": "reworking"}, {"task_id": None}])
def test_clean_unmerged_needs_a_clean_verdict_on_a_known_current_head(sent, owner, over):
    store = inbox._empty()
    runs = [_clean_run(**over)]
    for k in range(4):
        _tick(store, T0 + k * CLEAN_S, owner, runs=runs)
    assert _clean(sent) == []


def test_clean_unmerged_uses_the_live_head_when_the_cache_is_stale(sent, owner):
    store = inbox._empty()
    runs = [_clean_run(pr_head_sha="0000oldhead")]   # cache lags...
    live = {"CMX-23": "abc1234def"}                  # ...GitHub agrees with the verdict
    _tick(store, T0, owner, runs=runs, live_heads=live)
    _tick(store, T0 + CLEAN_S, owner, runs=runs, live_heads=live)
    assert len(_clean(sent)) == 1


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


def test_tick_records_why_an_idle_orchestrator_was_not_reached(sent, wired):
    calls = wired("idle", messenger.PeerSendResult(False, None))
    inbox.tick({})
    assert len(calls["tmux"]) == 1                       # idle: the paste WAS tried
    assert inbox.load()["last_delivery_failure"]["reason"] == (
        "peer socket unreachable, tmux paste refused")
    assert len(_held(sent)) == 1


class _Clock:
    """``inbox.time`` with a settable ``time()`` — everything else is the real module."""

    def __init__(self, t):
        self.t = t

    def time(self):
        return self.t

    def __getattr__(self, name):
        return getattr(time, name)


@pytest.fixture
def wired_runs(wired, monkeypatch):
    """`wired`, plus a dispatcher that reports ``runs`` and a GitHub whose live PR head is
    ``live`` — so the tick's OWN runs query and live-head read are what feed the alert."""
    clock = _Clock(T0)
    monkeypatch.setattr(inbox, "time", clock)

    def arm(runs, live):
        wired("waiting", messenger.PeerSendResult(True, "sent"))
        monkeypatch.setattr(dispatcher, "list_runs", lambda: runs)
        monkeypatch.setattr(dispatcher, "_read_pr_checks",
                            lambda url, repo: dispatcher.CIStatus(dispatcher.CI_PASSING,
                                                                  head_sha=live))
        return clock
    return arm


def test_tick_feeds_its_own_runs_to_the_clean_unmerged_alert(sent, wired_runs):
    """The daemon wiring, not just the evaluator: a clean run the DISPATCHER reports pushes
    once past the threshold — measured on the tick's own clock — and not before."""
    clock = wired_runs([_clean_run()], live="abc1234def")
    inbox.tick({})                                   # starts the clock at T0
    clock.t = T0 + CLEAN_S - 1
    inbox.tick({})
    assert _clean(sent) == []
    clock.t = T0 + CLEAN_S
    inbox.tick({})
    clock.t = T0 + 3 * CLEAN_S
    inbox.tick({})
    assert len(_clean(sent)) == 1
    assert "CMX-23 has been judge-clean on its current head (abc1234)" in _clean(sent)[0]
    assert "https://github.com/o/r/pull/607" in _clean(sent)[0]
    assert inbox.load()["clean_unmerged"]["CMX-23"]["since"] == T0   # the tick's own clock


def test_tick_measures_the_held_queue_on_its_own_clock(sent, wired_runs):
    """The held age is ``now - oldest ts``, both absolute: a tick that passed any clock but
    its own would fire early (or never). Silent 1s before the threshold, fires at it."""
    clock = wired_runs([], live=None)
    inbox.save(_store(T0, kind="finished"))
    clock.t = T0 + HELD_S - 1
    inbox.tick({})
    assert _held(sent) == []
    clock.t = T0 + HELD_S
    inbox.tick({})
    assert len(_held(sent)) == 1 and "held for 30m" in _held(sent)[0]


def test_tick_feeds_the_live_head_to_the_clean_unmerged_alert(sent, wired_runs):
    """The row's cached head is stale; GitHub says the verdict IS on the current head. Only
    the tick's live read can know that — so the push proves the live heads reach the alert."""
    clock = wired_runs([_clean_run(pr_head_sha="0ld0ld0ld0")], live="abc1234def")
    inbox.tick({})
    clock.t = T0 + CLEAN_S
    inbox.tick({})
    assert len(_clean(sent)) == 1


def test_tick_never_alerts_clean_when_the_live_head_moved_past_the_verdict(sent, wired_runs):
    """The cache says clean-on-head; GitHub says the head moved. The live read must win."""
    clock = wired_runs([_clean_run()], live="fff9999fff")
    for k in range(4):
        clock.t = T0 + k * CLEAN_S
        inbox.tick({})
    assert _clean(sent) == []
    assert inbox.load()["clean_unmerged"] == {}


def test_thresholds_default_to_thirty_minutes(monkeypatch):
    from chela import config
    monkeypatch.delenv("CHELA_INBOX_HELD_ALERT_S", raising=False)
    monkeypatch.delenv("CHELA_CLEAN_UNMERGED_ALERT_S", raising=False)
    assert config.inbox_held_alert_s() == 1800
    assert config.clean_unmerged_alert_s() == 1800


def test_a_windowless_peer_failure_records_why_there_was_no_paste():
    store = _store(T0, kind="finished")
    inbox._deliver_loop(store, [], None, target_desc="pid 4242", event_wid=None,
                        send=lambda text: messenger.PeerSendResult(False, None),
                        tmux_fallback=None)
    failure = store["last_delivery_failure"]
    assert failure["reason"] == "peer socket unreachable (no pane to paste into)"
    assert failure["target"] == "pid 4242" and failure["kind"] == "finished"


def test_held_names_a_windowless_peer_orchestrator(sent, owner):
    store = _store(T0)
    store["orchestrator"] = None
    store["orchestrator_peer"] = {"pid": 4242, "session": "sid", "started": 1.0, "since": 1.0}
    _tick(store, T0 + HELD_S, owner, statuses={})
    [msg] = _held(sent)
    assert "orchestrator pid 4242 has been a windowless peer the whole time" in msg


# --- (3) chela doctor: the fact reads the OLDEST held event ---------------------------------

def _doctor(store, now, monkeypatch, threshold=HELD_S):
    monkeypatch.setattr(inbox, "load", lambda: store)
    monkeypatch.setattr(runtime_truth.time, "time", lambda: now)
    return runtime_truth._inbox_held_report(threshold, runtime_truth._inbox_held_read())


def test_doctor_goes_red_on_the_oldest_held_event_while_the_newest_is_young(monkeypatch):
    [f] = _doctor(_mixed_queue(), T0 + HELD_S + 1, monkeypatch)
    assert f.level == runtime_truth.ERROR
    assert "oldest event run_judge_clean CMX-92 has waited past 30m" in f.title
    assert "Oldest held event: run_judge_clean CMX-92, queued 30m ago; 4 event(s)" in f.detail
    assert f"orchestrator {ORCH}" in f.detail


def test_doctor_is_green_just_under_the_threshold(monkeypatch):
    [f] = _doctor(_mixed_queue(), T0 + HELD_S - 1, monkeypatch)
    assert f.level == runtime_truth.OK and "4 event(s) queued, oldest 29m" in f.title
    [f] = _doctor(_mixed_queue(), T0 + HELD_S, monkeypatch)
    assert f.level == runtime_truth.ERROR


def test_doctor_names_the_last_delivery_failure(monkeypatch):
    store = _mixed_queue()
    store["last_delivery_failure"] = {"ts": T0 + HELD_S - 120, "target": ORCH,
                                      "reason": "peer receipt held"}
    [f] = _doctor(store, T0 + HELD_S + 1, monkeypatch)
    assert f"Last delivery attempt: peer receipt held at {ORCH}, 2m ago." in f.detail


def test_doctor_inbox_held_empty_and_disabled(monkeypatch):
    [f] = _doctor(_store(), T0, monkeypatch)
    assert f.level == runtime_truth.OK and f.title == "decisions inbox: nothing held"
    assert _doctor(_mixed_queue(), T0 + 10 * HELD_S, monkeypatch, threshold=None) == []


def test_a_successful_delivery_clears_the_recorded_failure_before_the_queue_drains(
        sent, wired, monkeypatch):
    """A delivery that succeeds while events are STILL queued must clear the old failure —
    or the held alert would blame a socket that is working. One delivery per tick keeps the
    queue non-empty, so the drain's own reset cannot stand in for this one."""
    wired("busy", messenger.PeerSendResult(False, None))
    store = inbox.load()
    store["queue"] = _store(time.time() - 60, time.time() - 30, kind="finished")["queue"]
    inbox.save(store)
    inbox.tick({})
    assert inbox.load()["last_delivery_failure"] is not None
    monkeypatch.setattr(inbox, "MAX_DELIVERIES_PER_TICK", 1)
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(True, "sent"))
    inbox.tick({})
    after = inbox.load()
    assert len(after["queue"]) == 1
    assert after["last_delivery_failure"] is None
