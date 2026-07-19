"""`@N` is an ADDRESS, not an identity — every persisted one carries its tmux epoch.

The measured outage (2026-07-14): an OOM killed the tmux server, the fleet came back
RENUMBERED (the orchestrator went ``@0`` → ``@6``), and ``inbox.json`` still read
``{"orchestrator": "@0"}``. Five ``run_review`` notifications queued behind a window that no
longer existed and NONE were delivered — no error, no warning, no log line, ``chela doctor``
green 14/14. A human had to notice that five finished PRs were sitting unreviewed.

These tests pin the two halves of the fix, across every store that persists a window id (the
decisions inbox, the runs DB, the Telegram bindings):

  * an id from a DEAD tmux server is never acted on — not even when something answers to that
    number today, because that something is a different agent (a wrong wid is worse than no
    wid, CMX-48);
  * and being undeliverable is LOUD — a log ERROR, a durable event, a red doctor — because
    the entire cost of the outage was that it was silent.
"""
from __future__ import annotations

import logging

import pytest

from chela import dispatcher, epoch, event_log, inbox
from chela.telegram import reconcile
from chela.telegram.bindings import BindingRegistry

OLD = "786-1784045825"        # the tmux server that was OOM-killed
NEW = "9001-1784099999"       # the one that came back, numbering from @0 again

ORCH = "@1"
AGENT = "@2"


@pytest.fixture(autouse=True)
def event_log_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))


@pytest.fixture(autouse=True)
def no_live_runs(monkeypatch):
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [])


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity", lambda cwd: None)
    monkeypatch.setattr(inbox.discovery, "get_window_cwd_by_id", lambda wid: f"/proj/{wid}")
    return tmp_path / "inbox.json"


@pytest.fixture
def sends(monkeypatch):
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(inbox.messenger, "send_tmux",
                        lambda wid, text: (calls.append((wid, text)), True)[1])
    return calls


def _tmux(monkeypatch, *, now, windows):
    """The tmux server that is running RIGHT NOW, and the windows it is serving."""
    monkeypatch.setattr(epoch, "current", lambda: now)
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: dict(windows))


def _statuses(monkeypatch, mapping):
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: dict(mapping))


def _runs(n=1):
    """The finished PRs the orchestrator never heard about — real rows, so the delivery-time
    re-validation (:func:`inbox.stale_reason`) sees claims that are still TRUE."""
    return [{"task_id": f"T{i}", "title": "a task", "status": "awaiting_review",
             "branch_name": f"cmx-7{i}", "window_name": f"cmx-7{i}", "pr_state": "open",
             "pr_url": f"https://github.com/x/y/pull/{i}"} for i in range(n)]


SESSION = "0a1b2c3d-4e5f-6789-abcd-ef0123456789"   # the orchestrator's stable claude identity


def _registered_under_the_old_server(queued=1, *, session=None):
    """The store as the OOM left it: an address from a server that is now dead, and a queue.

    ``session`` is the orchestrator's recorded identity (CMX-82). None reproduces a store
    written before CMX-82 — nothing to self-heal from; a value lets the renumbered address
    re-resolve itself.
    """
    inbox.save({
        "orchestrator": ORCH, "orchestrator_epoch": OLD, "orchestrator_name": "orchestrator",
        "orchestrator_session": session,
        "watches": {}, "queue": [
            inbox._event("run_review", f"📥 cmx-7{i} awaiting review — PR #{i}",
                         {"task_id": f"T{i}"})
            for i in range(queued)
        ],
        # already announced — the events are IN the queue; this run of the tick is about
        # whether they can ever get OUT of it.
        "runs_seen": {f"T{i}": "awaiting_review" for i in range(queued)},
    })


def _kinds() -> list[str]:
    return [e["type"] for e in event_log.read()["events"]]


# --- the primitive --------------------------------------------------------------------

def test_only_a_stamp_that_contradicts_a_KNOWN_epoch_is_dangling():
    assert epoch.is_dangling(OLD, NEW) is True
    assert epoch.is_dangling(OLD, OLD) is False
    # Unknown is not stale. An unstamped id (a store written before this existed) and an
    # unreadable tmux are both "cannot say" — and a check that turns "cannot say" into an
    # accusation cries wolf on every legacy file and every machine with no tmux.
    assert epoch.is_dangling(None, NEW) is False
    assert epoch.is_dangling(OLD, None) is False
    assert epoch.is_dangling(None, None) is False


# --- the inbox: the address the outage rotted ------------------------------------------

def test_a_queue_addressed_to_a_dead_server_is_NOT_delivered_to_whoever_holds_that_id_now(
        store, sends, monkeypatch, caplog):
    """The outage, and the disaster it was one step away from.

    tmux restarted and reissued `@1` — to an AGENT. The orchestrator's five review
    notifications must not be pasted into that agent's prompt (it would act on them), and the
    silence must end: an ERROR, and a durable event anyone can see.
    """
    _registered_under_the_old_server(queued=5)
    _tmux(monkeypatch, now=NEW, windows={ORCH: "cmx-88-worker", "@6": "orchestrator"})
    _statuses(monkeypatch, {ORCH: inbox.IDLE, "@6": inbox.IDLE})    # @1 is idle — and NOT ours

    with caplog.at_level(logging.ERROR, logger="chela.inbox"):
        inbox.tick({}, runs=_runs(5))

    assert sends == [], "a review queue was pasted into the window that INHERITED the id"
    assert len(inbox.load()["queue"]) == 5, "the events must survive to be delivered later"
    assert "UNDELIVERABLE" in caplog.text and "dangling" in caplog.text
    assert "inbox_undeliverable" in _kinds(), "the Feed/audit trail never heard about it"


def test_the_undeliverable_alarm_is_loud_but_not_a_flood(store, sends, monkeypatch):
    """It must shout, and it must not become wallpaper: one row per dead address, not per
    tick (a 30s tick would put 2,880 identical rows a day in the Feed, and a queue nobody
    reads is the same silence with more steps)."""
    _registered_under_the_old_server()
    _tmux(monkeypatch, now=NEW, windows={"@6": "orchestrator"})
    _statuses(monkeypatch, {"@6": inbox.IDLE})

    for _ in range(5):
        inbox.tick({}, runs=_runs())

    assert _kinds().count("inbox_undeliverable") == 1


def test_the_alarm_does_not_re_fire_when_the_SAME_address_flips_between_gone_and_dangling(
        store, sends, monkeypatch):
    """CMX-110: `gone` and `dangling` are two READINGS of the same dead address, not two
    failures. A churning fleet (worker windows spawning/dying, a status map that hiccups
    empty for a tick) can flip which reading `address_state` returns for `@1` from one tick
    to the next even though nothing about the address actually changed — it is still `@1`,
    still unreachable. The old key (`f"{state}:{wid}"`) changed on every flip and re-armed the
    alarm each time, which is exactly the burst of alternating "is gone" / "is dangling"
    phone pushes observed live for one days-old address. De-duping on the address alone must
    survive the flap: one durable event for the whole outage, regardless of how many times the
    classification of WHY flips underneath it.
    """
    _registered_under_the_old_server()

    # Tick 1: epoch mismatch -> dangling.
    _tmux(monkeypatch, now=NEW, windows={"@6": "orchestrator"})
    _statuses(monkeypatch, {"@6": inbox.IDLE})
    inbox.tick({}, runs=_runs())
    first = [e for e in event_log.read()["events"] if e["type"] == "inbox_undeliverable"]
    assert first[0]["payload"]["state"] == "dangling"

    # Tick 2: epoch now reads as matching (a momentary/flaky read), and the orchestrator's
    # own address is simply absent from the status map -> gone. Still `@1`; still dead.
    _tmux(monkeypatch, now=OLD, windows={AGENT: "cmx-9"})
    _statuses(monkeypatch, {AGENT: inbox.BUSY})
    inbox.tick({}, runs=_runs())

    # Tick 3: flips back to dangling.
    _tmux(monkeypatch, now=NEW, windows={"@6": "orchestrator"})
    _statuses(monkeypatch, {"@6": inbox.IDLE})
    inbox.tick({}, runs=_runs())

    assert sends == []
    assert _kinds().count("inbox_undeliverable") == 1, \
        "the flap between gone and dangling re-armed the alarm for the same dead address"


def test_re_registering_after_the_restart_drains_the_queue_that_piled_up(
        store, sends, monkeypatch):
    """The recovery path, and the reason refusing to deliver is safe: nothing is thrown away.

    `chela watch` (with no window — or any dispatch, which registers as a side effect) stamps
    a NEW address, in the epoch that is really running. The queue that the dead address was
    holding goes out on the next idle tick.
    """
    _registered_under_the_old_server(queued=2)
    _tmux(monkeypatch, now=NEW, windows={"@6": "orchestrator", AGENT: "cmx-9"})
    _statuses(monkeypatch, {"@6": inbox.IDLE})
    inbox.tick({}, runs=_runs(2))
    assert sends == []

    assert inbox.register("@6")["queued"] == 2       # ...the orchestrator says "I am here"

    inbox.tick({}, runs=_runs(2))
    assert [wid for wid, _ in sends] == ["@6"]       # one per idle tick, oldest first
    inbox.tick({}, runs=_runs(2))
    assert [wid for wid, _ in sends] == ["@6", "@6"]
    assert inbox.load()["queue"] == []


# --- CMX-82: the recovery no longer waits on a human ------------------------------------

def _heals_to(monkeypatch, wid, session=SESSION):
    """The sessions layer says: `session` is running under `wid` right now."""
    monkeypatch.setattr(inbox.sessions, "wid_for_session",
                        lambda sid, pane_map=None: wid if sid == session else None)


def test_a_renumbered_address_SELF_HEALS_from_the_session_identity(store, sends, monkeypatch):
    """The 4th face of the address-as-a-key bug, fixed like the other three.

    CMX-77 made the renumbered address LOUD but the recovery still waited on a human to re-run
    `chela watch`. Now the inbox re-resolves its OWN address from the orchestrator's session
    identity — the same wid↔session evidence CMX-48/70/77 trust — to the window running it
    today (`@6`), re-points itself, and the held queue goes out. No human, nothing lost, and
    NOT one byte into `@1`, the stranger that inherited the dead number.
    """
    _registered_under_the_old_server(queued=2, session=SESSION)
    _tmux(monkeypatch, now=NEW, windows={ORCH: "cmx-88-worker", "@6": "orchestrator"})
    _statuses(monkeypatch, {ORCH: inbox.IDLE, "@6": inbox.IDLE})   # @1 is a live STRANGER
    _heals_to(monkeypatch, "@6")

    inbox.tick({}, runs=_runs(2))

    healed = inbox.load()
    assert healed["orchestrator"] == "@6", "the address must re-point at the window running the session"
    assert healed["orchestrator_epoch"] == NEW, "re-stamped in the epoch that resolved it"
    assert [wid for wid, _ in sends] == ["@6"], "the held queue delivers — to the right window"
    assert all(wid == "@6" for wid, _ in sends), "nothing pasted into the id's new owner"
    assert "inbox_self_healed" in _kinds(), "the recovery is a durable record"
    assert "inbox_undeliverable" not in _kinds(), "a healed address never alarms"


def test_self_heal_delivers_the_whole_backlog_across_ticks(store, sends, monkeypatch):
    """Once healed the address is stamped in the current epoch, so it stays OK: the rest of the
    queue drains one-per-idle-tick, exactly as it would for an address that never rotted."""
    _registered_under_the_old_server(queued=2, session=SESSION)
    _tmux(monkeypatch, now=NEW, windows={"@6": "orchestrator"})
    _statuses(monkeypatch, {"@6": inbox.IDLE})
    _heals_to(monkeypatch, "@6")

    inbox.tick({}, runs=_runs(2))
    inbox.tick({}, runs=_runs(2))

    assert [wid for wid, _ in sends] == ["@6", "@6"]
    assert inbox.load()["queue"] == []
    assert _kinds().count("inbox_self_healed") == 1, "recovery announced once, not per tick"


def test_no_identity_recorded_stays_loud_and_never_guesses(store, sends, monkeypatch, caplog):
    """A store written before CMX-82 (or an env pin) has no identity to re-resolve from. The
    CMX-77 behaviour is preserved EXACTLY — loud, held, waiting for `chela watch` — and the
    resolver is never even consulted, so a live window is never guessed into the address."""
    _registered_under_the_old_server(queued=1, session=None)
    _tmux(monkeypatch, now=NEW, windows={"@6": "orchestrator"})
    _statuses(monkeypatch, {"@6": inbox.IDLE})
    monkeypatch.setattr(inbox.sessions, "wid_for_session",
                        lambda sid, pane_map=None: pytest.fail("resolved without a recorded identity"))

    with caplog.at_level(logging.ERROR, logger="chela.inbox"):
        inbox.tick({}, runs=_runs())

    assert sends == []
    assert inbox.load()["orchestrator"] == ORCH, "the address is left dangling, not guessed"
    assert "inbox_undeliverable" in _kinds() and "inbox_self_healed" not in _kinds()


def test_self_heal_refuses_when_the_session_is_not_live_anywhere(store, sends, monkeypatch, caplog):
    """The identity is recorded but the session is not running under any window (it truly
    exited). No guess: the address stays dangling-and-loud, precisely as CMX-77 left it."""
    _registered_under_the_old_server(queued=1, session=SESSION)
    _tmux(monkeypatch, now=NEW, windows={"@6": "some-agent"})
    _statuses(monkeypatch, {"@6": inbox.IDLE})
    _heals_to(monkeypatch, None)                     # the resolver finds nothing live

    with caplog.at_level(logging.ERROR, logger="chela.inbox"):
        inbox.tick({}, runs=_runs())

    assert sends == []
    assert inbox.load()["orchestrator"] == ORCH
    assert "inbox_self_healed" not in _kinds()
    assert "inbox_undeliverable" in _kinds()


def test_self_heal_also_recovers_a_GONE_address_under_the_same_server(store, sends, monkeypatch):
    """No renumbering needed: the orchestrator's session exited `@1` and was resumed into `@6`
    on the SAME tmux server. `@1` is simply gone; the identity still resolves, so the inbox
    re-points and delivers instead of holding the queue for a human."""
    _registered_under_the_old_server(queued=1, session=SESSION)
    _tmux(monkeypatch, now=OLD, windows={"@6": "orchestrator", AGENT: "cmx-9"})  # same epoch
    _statuses(monkeypatch, {"@6": inbox.IDLE})
    _heals_to(monkeypatch, "@6")

    inbox.tick({}, runs=_runs())

    assert inbox.load()["orchestrator"] == "@6"
    assert [wid for wid, _ in sends] == ["@6"]
    assert "inbox_self_healed" in _kinds()


def test_registering_records_the_session_identity(store, monkeypatch):
    """The identity self-heal needs is captured at registration — read off the window's own
    session (chela.sessions), never guessed. No session resolvable → recorded as None, and
    self-heal is simply unavailable, never wrong."""
    monkeypatch.setattr(epoch, "current", lambda: NEW)
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {ORCH: "orchestrator"})
    monkeypatch.setattr(inbox.sessions, "session_of_window",
                        lambda wid, pane_map=None: SESSION if wid == ORCH else None)

    result = inbox.register(ORCH)

    assert result["session"] == SESSION
    assert inbox.orchestrator_session(inbox.load()) == SESSION


def test_apply_heal_re_checks_under_the_lock_before_it_trusts_the_resolution(store, monkeypatch):
    """The heal is resolved OUTSIDE the store lock (it reads tmux + /proc), so the world can move
    before `_apply_heal` runs. It re-checks three things under the lock: the recorded identity is
    still the one resolved, the address is still undeliverable, and the resolved window is a live
    claude — any of them failing (a concurrent `chela watch`, a dead pane) means the heal is stale
    and is refused, never a guess."""
    monkeypatch.setattr(epoch, "current", lambda: NEW)
    windows, statuses = {"@6": "orchestrator"}, {"@6": inbox.IDLE}

    # identity changed under us (a `chela watch` re-registered a different session) → refuse
    changed = {**inbox._empty(), "orchestrator": ORCH, "orchestrator_epoch": OLD,
               "orchestrator_session": "other-sid"}
    assert inbox._apply_heal(changed, (SESSION, "@6"), statuses, NEW, windows) is None
    assert changed["orchestrator"] == ORCH

    # the address is already healthy again → nothing to heal → refuse
    ok = {**inbox._empty(), "orchestrator": "@6", "orchestrator_epoch": NEW,
          "orchestrator_session": SESSION}
    assert inbox._apply_heal(ok, (SESSION, "@6"), statuses, NEW, windows) is None

    # the resolved window has no claude running in it → refuse (don't guess into a dead pane)
    nolive = {**inbox._empty(), "orchestrator": ORCH, "orchestrator_epoch": OLD,
              "orchestrator_session": SESSION}
    assert inbox._apply_heal(nolive, (SESSION, "@9"), statuses, NEW, windows) is None
    assert nolive["orchestrator"] == ORCH

    # dangling + identity matches + window live → heal, and re-stamp in the current epoch
    good = {**inbox._empty(), "orchestrator": ORCH, "orchestrator_epoch": OLD,
            "orchestrator_session": SESSION, "orchestrator_name": "orchestrator"}
    assert inbox._apply_heal(good, (SESSION, "@6"), statuses, NEW, windows) == ORCH
    assert good["orchestrator"] == "@6" and good["orchestrator_epoch"] == NEW


def test_an_address_whose_window_is_simply_GONE_is_loud_too(store, sends, monkeypatch, caplog):
    """The same tmux server, and the orchestrator's session just exited. The queue is behind
    an address that cannot take it — which is the outage's shape without the renumbering, and
    it used to look exactly like `busy`: `statuses.get(orch) != IDLE`, wait for the next tick,
    forever."""
    inbox.save({"orchestrator": ORCH, "orchestrator_epoch": OLD, "watches": {}, "queue": [
        inbox._event("run_review", "📥 cmx-70 awaiting review", {"task_id": "T0"})],
        "runs_seen": {"T0": "awaiting_review"}})
    _tmux(monkeypatch, now=OLD, windows={AGENT: "cmx-9"})
    _statuses(monkeypatch, {AGENT: inbox.BUSY})      # the orchestrator is not there at all

    with caplog.at_level(logging.ERROR, logger="chela.inbox"):
        inbox.tick({}, runs=_runs())

    assert sends == []
    assert "UNDELIVERABLE" in caplog.text and "gone" in caplog.text
    assert "inbox_undeliverable" in _kinds()


def test_an_unstamped_address_still_delivers(store, sends, monkeypatch):
    """The upgrade must not take the feature down. A store written before CMX-77 has no epoch
    to check, and "cannot verify" is not "wrong" — it is delivered (and reported, so it gets
    stamped). Refusing here would break every live install on upgrade."""
    inbox.save({"orchestrator": ORCH, "watches": {}, "queue": [
        inbox._event("run_review", "📥 cmx-70 awaiting review", {"task_id": "T0"})],
        "runs_seen": {"T0": "awaiting_review"}})
    _tmux(monkeypatch, now=NEW, windows={ORCH: "orchestrator"})
    _statuses(monkeypatch, {ORCH: inbox.IDLE})

    inbox.tick({}, runs=_runs())

    assert [wid for wid, _ in sends] == [ORCH]


def test_a_watch_on_a_reissued_id_is_retired_not_believed(store, sends, monkeypatch):
    """A watch is an `@N` too, and a status read against a reissued one is a lie.

    The agent that was being watched died with the server. The window wearing its number now
    is a STRANGER, and it is idle — so the busy→idle logic would report "your agent finished
    the task you dispatched; verify + commit" about somebody else's work, which the
    orchestrator would then act on. Instead the watch is retired and the truth is told: the
    outcome is unknown, go look at the run.
    """
    inbox.save({"orchestrator": ORCH, "orchestrator_epoch": NEW, "watches": {
        AGENT: {"note": "fix the parser", "since": 0, "name": "cmx-9", "epoch": OLD}},
        "queue": [], "runs_seen": {}})
    _tmux(monkeypatch, now=NEW, windows={ORCH: "orchestrator", AGENT: "someone-else"})
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})       # a busy→idle edge on the STRANGER

    assert inbox.watches() == {}, "the dead watch must not survive to lie again"
    assert len(sends) == 1
    _, text = sends[0]
    assert "tmux SERVER restarted" in text and "UNKNOWN" in text
    assert "finished" not in text
    # ...and it is not filed under the agent that inherited the id: an unattributed event is
    # visibly ownerless, a MISattributed one is invisibly false (CMX-48).
    assert inbox.load()["queue"] == []
    assert next(e for e in event_log.read()["events"]
                if e["type"] == "watch_epoch_lost")["wid"] is None


# --- the runs DB: the id a run_review is addressed to -----------------------------------

def test_a_run_row_from_a_dead_server_does_not_attribute_its_events_to_a_live_agent():
    run = {"task_id": "T9", "window_id": "@3", "window_epoch": OLD, "window_name": "cmx-9"}
    live = {"@3": "someone-else"}                   # tmux gave @3 to a different window

    assert inbox.run_wid(run, live, now_epoch=NEW) is None, \
        "a dead run's PR review would have been filed under a LIVE agent's lane"
    # Same row, same server it was spawned under: the recorded id is exactly what it is for.
    assert inbox.run_wid(run, live, now_epoch=OLD) == "@3"
    # An unstamped row (pre-CMX-77) keeps the old behaviour — unverifiable, not wrong.
    assert inbox.run_wid({**run, "window_epoch": None}, live, now_epoch=NEW) == "@3"


def test_the_runs_table_records_the_epoch_beside_the_window_id(tmp_path):
    import sqlite3

    conn = dispatcher.ensure_schema(sqlite3.connect(tmp_path / "runs.db"))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    assert {"window_id", "window_epoch"} <= columns, \
        "an id with no epoch beside it is a number, not a window"


# --- the Telegram bindings: a topic is a window id too -----------------------------------

class _StubTopics:
    def __init__(self):
        self.created, self.closed = [], []

    def create_topic(self, name):
        self.created.append(name)
        return str(100 + len(self.created))

    def rename_topic(self, thread, name):
        return True

    def close_topic(self, thread):
        self.closed.append(thread)
        return True


def test_a_binding_that_outlived_its_tmux_server_is_reaped_even_though_its_id_is_live():
    """Otherwise the bridge relays the pane of whatever agent INHERITED `@2` into the topic a
    human opened for the one that died — and routes their replies back into that stranger."""
    reg = BindingRegistry("777")
    reg.bind(AGENT, "42", OLD)
    reg.set_topic_name(AGENT, "cmx-9")
    api = _StubTopics()

    changed = reconcile.reconcile_bindings(
        reg, {AGENT: "someone-else"}, {AGENT}, api, now_epoch=NEW)

    assert changed
    assert api.closed == ["42"], "the dead agent's topic must be archived"
    assert reg.thread_for_window(AGENT) != "42"
    # ...and the live window is re-provisioned as what it is: a NEW agent, with a NEW topic.
    assert api.created == ["someone-else"]
    assert reg.epoch_for(AGENT) == NEW


def test_a_binding_made_now_is_stamped_and_a_legacy_one_is_adopted():
    reg = BindingRegistry("777")
    reconcile.reconcile_bindings(reg, {AGENT: "cmx-9"}, {AGENT}, _StubTopics(), now_epoch=NEW)
    assert reg.epoch_for(AGENT) == NEW

    legacy = BindingRegistry("777")
    legacy.bind("@5", "9", None)                    # a file written before CMX-77
    assert reconcile.reconcile_bindings(
        legacy, {"@5": "cmx-4"}, {"@5"}, _StubTopics(), now_epoch=NEW) is True
    assert legacy.epoch_for("@5") == NEW, \
        "an unstamped binding whose window is live is adopted — and verifiable from now on"


def test_a_dead_servers_run_row_cannot_disown_a_humans_window():
    """`dispatched_window_ids` honours an in-flight row's id unconditionally — so after a
    restart, a `running` row whose agent died with the server would claim whatever window
    inherited its number, and the human sitting in it would silently lose their topic."""
    runs = [{"task_id": "T1", "status": "running", "window_id": "@3",
             "window_epoch": OLD, "window_name": "cmx-9"}]
    live = {"@3": "orchestrator"}

    assert reconcile.dispatched_window_ids(runs, live_windows=live, now_epoch=NEW) == set()
    assert reconcile.dispatched_window_ids(runs, live_windows=live, now_epoch=OLD) == {"@3"}
