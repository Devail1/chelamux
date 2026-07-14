"""Decisions inbox — push agent/run events into the orchestrator, gated on idle.

The loop this closes: an orchestrator agent is a Claude Code session, so an agent
FINISHING is structurally invisible to it (on 2026-07-13 the human had to be the
message bus). These lock in the contract that makes pushing into a live session safe:

  * the idle gate — NEVER write to a ``busy`` session, and above all never to a
    ``waiting`` one (it is sitting on a permission prompt; our paste would ANSWER it);
  * the edge trigger — one event per transition, not one per 30s tick;
  * watch-scoping — only work the orchestrator DELEGATED can produce an event, so the
    busy→idle that ends literally every agent turn can't spam it;
  * anti-self-notify — the orchestrator's own window is never an event source, so a
    delivery (which makes it busy, then idle) cannot feed itself.

Pure: tmux/`claude agents --json`/send are all stubbed, so no live session is touched.
"""
from __future__ import annotations

import threading
import time

import pytest

from chela import dispatcher, event_log, inbox

ORCH = "@1"      # the orchestrator's own window
AGENT = "@2"     # a window it delegated work to


@pytest.fixture(autouse=True)
def no_live_runs(monkeypatch):
    """Default: the dispatcher has no runs.

    ``tick(prev)`` with no ``runs=`` falls back to ``dispatcher.list_runs()``, which
    reads the REAL runs DB under ``~/.chela``. Without this the suite was green only on
    an idle machine: a live run parked in ``awaiting_review`` synthesized an extra event
    into the tmp store and every count assertion here went off by one. The ``runs=``
    parameter is the seam — a test that wants run events passes them in explicitly.
    """
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [])


@pytest.fixture(autouse=True)
def event_log_file(tmp_path, monkeypatch):
    """Never the real ``~/.chela/events.jsonl``.

    ``tick()`` appends every event it generates to the durable log. Autouse for the same
    reason ``no_live_runs`` is: a test that reaches live state under ``~/.chela`` is only
    green by luck — here it would also POLLUTE the fleet's real audit trail with
    synthetic events, which is worse than a flaky assertion.
    """
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))


@pytest.fixture
def store_file(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    return tmp_path / "inbox.json"


@pytest.fixture
def sends(monkeypatch):
    """Capture every send_tmux — the ONLY way an event reaches a live session."""
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(inbox.messenger, "send_tmux",
                        lambda wid, text: (calls.append((wid, text)), True)[1])
    return calls


@pytest.fixture(autouse=True)
def no_transcript_evidence(monkeypatch):
    """Default: the transcript shows no work (so status transitions are the only signal).

    Autouse so no test can reach the real tmux/transcripts of the LIVE fleet, and so the
    evidence path is opt-in per test rather than silently on.
    """
    monkeypatch.setattr(inbox.discovery, "get_window_cwd_by_id", lambda wid: f"/proj/{wid}")
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity", lambda cwd: None)


@pytest.fixture
def windows(monkeypatch):
    live = {ORCH: "orchestrator", AGENT: "chelamux"}
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: dict(live))
    return live


def _statuses(monkeypatch, mapping):
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: dict(mapping))


def _registered(note="fix the parser"):
    """An orchestrator that has delegated to AGENT and is watching it."""
    inbox.watch(AGENT, note, by=ORCH)


def _run(status, **over):
    """A dispatcher run row for AGENT's window (the dispatcher names it after the branch)."""
    return {"task_id": "T9", "title": "fix the parser", "status": status,
            "window_name": "cmx-9", "pr_url": None, **over}


def _decide_immediately(monkeypatch):
    """Collapse the settle window — the race itself is covered by its own test below."""
    monkeypatch.setattr(inbox, "DEATH_CONFIRM_SECONDS", 0)


# --- the edge trigger ----------------------------------------------------------

def test_busy_to_idle_on_a_watched_window_fires_once(store_file, windows, sends, monkeypatch):
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    # The agent was busy; now it is idle → the task it was dispatched finished.
    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    assert len(sends) == 1
    wid, text = sends[0]
    assert wid == ORCH                                  # ...into the ORCHESTRATOR
    assert text.startswith("📥 @2 (chelamux) finished")  # one compact, actionable line
    assert 'note: "fix the parser"' in text
    assert "\n" not in text

    # A window that simply STAYS idle across further ticks is not re-announced: the
    # trigger is the transition, and the watch was cleared when it fired.
    inbox.tick(prev)
    inbox.tick(prev)
    assert len(sends) == 1
    assert inbox.watches() == {}                        # one dispatch, one completion


def test_a_watched_window_going_waiting_is_reported_but_keeps_its_watch(
        store_file, windows, sends, monkeypatch):
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.WAITING})

    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    assert "is BLOCKED on a prompt" in sends[0][1]
    # It is blocked, not done — it still owes the orchestrator the work, so the watch
    # stays and its eventual finish is still reported.
    assert AGENT in inbox.watches()
    inbox.tick(prev)                                    # still waiting → not re-sent
    assert len(sends) == 1


def test_a_watched_window_that_dies_mid_task_is_reported(store_file, sends, monkeypatch):
    # The real alarm — DO NOT let the fix for the false positive below silence it. The
    # window is gone while its run is still `running` with no PR: the work IS unfinished.
    _decide_immediately(monkeypatch)
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator", AGENT: "cmx-9"})
    _registered()
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {ORCH: "orchestrator"})
    _statuses(monkeypatch, {ORCH: inbox.IDLE})

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY}, runs=[_run("running")])

    assert "DIED mid-task" in sends[0][1]
    assert inbox.watches() == {}


def test_a_fresh_daemon_baselines_silently(store_file, windows, sends, monkeypatch):
    # No previous snapshot (daemon just started): every agent looks "new". Announcing
    # them all would spam the orchestrator on every restart, so an absent baseline
    # produces nothing — we only ever report a TRANSITION we actually observed.
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    inbox.tick({})

    assert sends == []


def test_an_unwatched_window_finishing_is_never_reported(store_file, windows, sends, monkeypatch):
    # THE noise landmine: every agent turn ends busy→idle. Only work the orchestrator
    # actually delegated (i.e. registered a watch for) may produce an event.
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    inbox.load()  # no watches registered

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    assert sends == []


# --- the idle gate (the landmine that would answer a permission prompt) ---------

def test_no_push_while_the_orchestrator_is_busy_then_delivered_on_next_idle(
        store_file, windows, sends, monkeypatch):
    _registered()
    # Agent finished, but the orchestrator is mid-thought — never interrupt it.
    _statuses(monkeypatch, {ORCH: inbox.BUSY, AGENT: inbox.IDLE})
    prev = inbox.tick({ORCH: inbox.BUSY, AGENT: inbox.BUSY})

    assert sends == []
    assert len(inbox.load()["queue"]) == 1        # queued DURABLY, not dropped

    # It stays queued across ticks while the orchestrator is still busy...
    inbox.tick(prev)
    assert sends == []

    # ...and goes out the moment it is idle.
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    inbox.tick(prev)
    assert len(sends) == 1
    assert inbox.load()["queue"] == []            # delivered exactly once...
    inbox.tick(prev)
    assert len(sends) == 1                        # ...and never re-delivered


def test_never_push_into_a_waiting_orchestrator(store_file, windows, sends, monkeypatch):
    # THE dangerous one. A `waiting` session sits on a permission/question prompt:
    # pasting our notification would be consumed as the ANSWER to that gate. `not
    # busy` is NOT good enough — the gate is a strict equality against `idle`.
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.WAITING, AGENT: inbox.IDLE})

    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    assert sends == []                            # NOT delivered into the prompt
    assert len(inbox.load()["queue"]) == 1        # held until it is genuinely idle

    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    inbox.tick(prev)
    assert len(sends) == 1


def test_queue_drains_one_event_per_idle_tick(store_file, windows, sends, monkeypatch):
    # A delivery makes the orchestrator busy; a second paste in the same tick would
    # land mid-thought. So the queue drains oldest-first, one per idle tick.
    _registered()
    store = inbox.load()
    store["queue"] = [{"kind": "run_review", "wid": None, "text": "📥 one"},
                      {"kind": "run_failed", "wid": None, "text": "📥 two"}]
    inbox.save(store)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    assert [t for _, t in sends] == ["📥 one"]
    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    assert [t for _, t in sends] == ["📥 one", "📥 two"]


# --- anti-self-notify: the loop must not be able to run away -------------------

def test_the_orchestrator_is_never_an_event_source(store_file, windows, sends, monkeypatch):
    # Delivering a push makes the orchestrator busy and then idle — the very
    # transition that fires "finished". It must never feed itself. Two guards: its own
    # window is excluded from the scan, and it cannot be watched (below). Force the
    # watch into the store directly to prove the SCAN excludes it even so.
    store = inbox.load()
    store["orchestrator"] = ORCH
    store["watches"] = {ORCH: {"note": "", "since": 0}}
    inbox.save(store)
    _statuses(monkeypatch, {ORCH: inbox.IDLE})

    inbox.tick({ORCH: inbox.BUSY})                # busy -> idle on the ORCHESTRATOR

    assert sends == []
    assert inbox.load()["queue"] == []


def test_watching_your_own_window_is_refused(store_file, windows):
    result = inbox.watch(ORCH, "recursion, please", by=ORCH)
    assert result["ok"] is False
    assert "own window" in result["error"]
    assert inbox.watches() == {}


def test_watch_rejects_an_unknown_window(store_file, windows):
    assert inbox.watch("@99", "", by=ORCH)["ok"] is False


# --- explicit identity + off switch --------------------------------------------

def test_nothing_is_pushed_when_no_orchestrator_is_registered(
        store_file, windows, sends, monkeypatch):
    # Identity is EXPLICIT: with nobody registered the inbox is inert. It must be
    # impossible for an event to land in a random agent's session by default.
    store = inbox.load()
    store["watches"] = {AGENT: {"note": "", "since": 0}}   # watched, but no orchestrator
    inbox.save(store)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    inbox.tick({AGENT: inbox.BUSY})

    assert sends == []
    assert len(inbox.load()["queue"]) == 1     # event recorded, but delivered to nobody


def test_env_pin_overrides_the_registered_orchestrator(store_file, monkeypatch):
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setenv("CHELA_ORCHESTRATOR_WID", "@7")
    assert inbox.orchestrator_wid() == "@7"


def test_disabled_flag_makes_the_tick_a_no_op(store_file, windows, sends, monkeypatch):
    _registered()
    monkeypatch.setattr(inbox, "INBOX_ENABLED", False)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    assert inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY}) == {}
    assert sends == []
    assert inbox.load()["queue"] == []


# --- dispatcher runs (delegated by definition — no watch needed) ---------------

def test_run_awaiting_review_and_failure_fire_once_each(store_file, windows, sends, monkeypatch):
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    runs = [{"task_id": "T1", "title": "add parser", "status": "awaiting_review",
             "pr_url": "https://github.com/x/y/pull/3"},
            {"task_id": "T2", "title": "flaky test", "status": "failed",
             "last_error": "pytest exploded\nstack..."}]

    inbox.tick({ORCH: inbox.IDLE}, runs=runs)      # queues both, delivers the first
    inbox.tick({ORCH: inbox.IDLE}, runs=runs)      # same runs → no NEW events
    inbox.tick({ORCH: inbox.IDLE}, runs=runs)

    texts = [t for _, t in sends]
    assert len(texts) == 2                          # exactly one event per run, ever
    assert "awaiting review" in texts[0] and "pull/3" in texts[0]
    assert "FAILED" in texts[1] and "pytest exploded" in texts[1]
    assert "\n" not in texts[1]                     # one line, not a stack dump


def test_run_marks_are_durable_across_a_daemon_restart(store_file, windows, sends, monkeypatch):
    # The mark lives in the store, not in memory: a restarted daemon must not
    # re-announce every run parked in awaiting_review.
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    runs = [{"task_id": "T1", "title": "add parser", "status": "awaiting_review"}]

    inbox.tick({}, runs=runs)
    assert len(sends) == 1

    inbox.tick({}, runs=runs)                       # "restart": empty prev snapshot
    assert len(sends) == 1


def test_a_run_that_changes_status_fires_again(store_file, windows, sends, monkeypatch):
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[{"task_id": "T1", "title": "x", "status": "awaiting_review"}])
    inbox.tick({}, runs=[{"task_id": "T1", "title": "x", "status": "failed"}])

    assert len(sends) == 2
    assert "FAILED" in sends[1][1]


# --- attribution: a run event belongs to the agent that produced it -------------
#
# The Feed groups the log into per-agent LANES, and a lane is only as good as the `wid`
# on a row. Measured on the real log: every `run_review` row carried `wid: null` while
# naming its window (`window_name: "cmx-57"`) in its own payload — attributable, and
# simply never attributed.
#
# The id therefore comes from the RUN ROW, which recorded it at spawn (CMX-62). The
# live-tmux lookup that came first (CMX-60) never fired for the events it was written
# for: a dispatched agent finishes by calling `chela task-finished`, which KILLS ITS OWN
# WINDOW, and only *then* does the run reconcile to awaiting_review and the event get
# queued — so by lookup time the window is always already gone. The lookup survives as
# the fallback (an old row has no recorded id; a hand-driven window may still be alive).
# ⛔ Neither path guesses: a wrong `wid` files an agent's work under a different agent,
# which is strictly worse than an event with no owner at all (CMX-48).

def test_a_run_event_is_attributed_to_its_agents_window(store_file, sends, monkeypatch):
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator", AGENT: "cmx-9"})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_run("awaiting_review", pr_url="https://x/pull/9")])

    logged = event_log.read()["events"]
    review = [e for e in logged if e["type"] == "run_review"]
    assert len(review) == 1
    assert review[0]["wid"] == AGENT              # the lane it belongs in
    assert review[0]["payload"]["window_name"] == "cmx-9"


def test_an_unresolvable_run_event_is_left_ownerless_rather_than_guessed(
        store_file, sends, monkeypatch):
    # The window is gone (or was never ours). There is nothing to resolve against, so the
    # event goes to chela's own lane — it does NOT get pinned on whoever is standing near.
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator", AGENT: "some-other-agent"})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_run("failed", last_error="boom")])

    logged = event_log.read()["events"]
    failed = [e for e in logged if e["type"] == "run_failed"]
    assert len(failed) == 1
    assert failed[0]["wid"] is None


def test_an_ambiguous_window_name_resolves_to_nothing():
    # Two live windows share the name (a retry racing its predecessor's exit). Picking
    # one would be a coin flip that misattributes half the time — refuse instead.
    windows = {"@2": "cmx-9", "@7": "cmx-9"}
    assert inbox.wid_for_window_name("cmx-9", windows) is None
    assert inbox.wid_for_window_name("cmx-9", {"@2": "cmx-9"}) == "@2"
    assert inbox.wid_for_window_name(None, {"@2": "cmx-9"}) is None


def test_a_run_event_is_attributed_even_though_its_window_is_already_gone(
        store_file, sends, monkeypatch):
    """THE BUG (measured live at seq 3070, right after CMX-60 shipped). The agent killed
    its own window on `task-finished`, so the live table no longer holds it — and the run
    row's recorded id is the only thing left that knows whose work this was."""
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator"})     # the agent's window is REAPED
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_run("awaiting_review", window_id=AGENT,
                              pr_url="https://x/pull/9")])

    review = [e for e in event_log.read()["events"] if e["type"] == "run_review"]
    assert len(review) == 1
    assert review[0]["wid"] == AGENT                        # its agent's lane, not chela's
    assert review[0]["payload"]["window_id"] == AGENT


def test_the_recorded_id_beats_a_live_window_that_reused_the_name():
    # A retry's window (or a human's) can be sitting on the same name. The row is the
    # authority precisely so a recycled name cannot re-file a finished agent's work.
    run = {"task_id": "T9", "window_name": "cmx-9", "window_id": "@2"}
    assert inbox.run_wid(run, {"@7": "cmx-9"}) == "@2"


def test_a_run_with_no_recorded_id_falls_back_to_the_live_name_lookup():
    # A row that predates CMX-62 has no window_id. If its window is still up and the name
    # is unambiguous, the lookup still attributes it.
    run = {"task_id": "T9", "window_name": "cmx-9"}
    assert inbox.run_wid(run, {"@2": "cmx-9"}) == "@2"
    # ...and if it is not, the event stays honestly ownerless. No branch, no worktree, no
    # "closest" window: an unattributed event is visibly ownerless, a misattributed one is
    # invisibly false.
    assert inbox.run_wid(run, {}) is None
    assert inbox.run_wid(run, {"@2": "cmx-9", "@7": "cmx-9"}) is None
    assert inbox.run_wid({"task_id": "T9"}, {"@2": "cmx-9"}) is None


def test_a_junk_recorded_id_is_refused_not_trusted():
    # window_id must be a tmux @id. Anything else (a name that leaked in, an empty string)
    # is not an id, and keying a lane on it would invent an agent that never existed.
    assert inbox.run_wid({"window_name": "cmx-9", "window_id": ""}, {"@2": "cmx-9"}) == "@2"
    assert inbox.run_wid({"window_name": "cmx-9", "window_id": "cmx-9"}, {}) is None


# --- BUG 2 (live): a task shorter than the poll interval was missed ENTIRELY -----
#
# The daemon samples every 30s and detected completion as a busy→idle EDGE. A watched
# window that went idle→busy→idle BETWEEN two polls is sampled `idle, idle`: `busy` is
# never observed, so there is no transition and the completion is dropped FOREVER.
# Live, a ~15s delegation produced nothing at all. Quick delegations are exactly what
# this feature exists to catch, so completion cannot depend on catching it mid-flight.
# These fail on the edge-only code.

def test_a_task_that_starts_and_finishes_between_two_polls_is_still_reported(
        store_file, windows, sends, monkeypatch):
    _registered()
    # Both samples see idle: the entire busy period fell between them. The transcript is
    # the evidence — the agent wrote an assistant turn AFTER the watch was registered.
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity",
                        lambda cwd: watched_since + 5)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})   # never saw it busy

    assert len(sends) == 1
    assert "finished the task you dispatched" in sends[0][1]
    assert inbox.watches() == {}          # ...and it fires exactly once
    inbox.tick(prev)
    assert len(sends) == 1


def test_completion_is_reported_even_with_no_baseline_at_all(
        store_file, windows, sends, monkeypatch):
    # The daemon restarted while the agent worked, so there is no previous sample to
    # compare against. The evidence path needs none — it must still report.
    _registered()
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity",
                        lambda cwd: watched_since + 5)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    inbox.tick({})                        # empty prev — a fresh daemon

    assert len(sends) == 1


def test_evidence_never_reports_an_agent_that_is_still_working(
        store_file, windows, sends, monkeypatch):
    # It has written assistant turns (it is mid-task, using tools) but is NOT idle.
    # Work-since-watch alone must never mean "done" — the idle gate still rules.
    _registered()
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity",
                        lambda cwd: watched_since + 5)

    for status in (inbox.BUSY, inbox.WAITING):
        _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: status})
        inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})
        assert [t for _, t in sends if "finished" in t] == []
    assert AGENT in inbox.watches()       # still watched — it still owes us the work


def test_evidence_ignores_work_that_predates_the_watch(
        store_file, windows, sends, monkeypatch):
    # An idle window whose last assistant turn is OLDER than the watch has not done
    # anything for THIS dispatch — reporting it would be a phantom completion.
    _registered()
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity",
                        lambda cwd: watched_since - 60)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    assert sends == []
    assert AGENT in inbox.watches()


# --- BUG 1 (live): a `chela watch` registered at RUNTIME was silently clobbered ---

def test_a_watch_registered_during_a_tick_is_not_clobbered(store_file, windows, sends, monkeypatch):
    # The real mechanism behind "my first watch never activates until I restart the
    # daemon": tick() loaded the store, spent ~1s probing `claude agents --json`, then
    # saved its STALE copy back — erasing a watch that landed in between. The CLI even
    # reported ok. Reproduced deterministically here with a slow status probe.
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    def slow_status():
        time.sleep(0.4)                    # the window between tick's read and write
        return {ORCH: inbox.IDLE, AGENT: inbox.IDLE}

    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", slow_status)

    t = threading.Thread(target=lambda: inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE}))
    t.start()
    time.sleep(0.15)                       # ...tick is mid-probe, holding a stale copy
    assert inbox.watch(AGENT, "fix the parser", by=ORCH)["ok"] is True
    t.join()

    assert AGENT in inbox.watches(), "the daemon's tick clobbered a concurrent watch"


def test_a_watch_registered_at_runtime_works_without_a_daemon_restart(
        store_file, windows, sends, monkeypatch):
    # The daemon has already ticked (orchestrator unregistered, nothing watched) — the
    # state a long-running daemon is really in when you first use the feature.
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    assert inbox.orchestrator_wid() is None

    # NOW the orchestrator dispatches and registers, with the daemon still running.
    _registered()
    assert inbox.orchestrator_wid() == ORCH        # re-read from the store, not latched

    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    assert len(sends) == 1                          # ...and it fires. No restart needed.


# --- BUG 3 (live, twice on 2026-07-13): a SUCCESSFUL agent was reported as DIED ---
#
# A dispatched agent that finishes normally EXITS — `chela task-finished` flips the run
# to awaiting_review and kills its tmux window. The watcher read that disappearance as
# death, so the one message a human is most likely to act on ("your agent died, the work
# was not finished") was false PRECISELY when everything went right — and the same tick
# queued that run's correct "awaiting review" line, contradicting itself in its own
# queue. Death is now CORROBORATED against the run state, never inferred from the window.

@pytest.fixture
def gone_agent(store_file, monkeypatch):
    """AGENT's window existed (named after its branch), was watched, and is now gone."""
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator", AGENT: "cmx-9"})
    _registered()
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {ORCH: "orchestrator"})
    _statuses(monkeypatch, {ORCH: inbox.IDLE})


@pytest.mark.parametrize("run", [
    _run("awaiting_review", pr_url="https://github.com/x/y/pull/45"),
    _run("done"),
    _run("running", pr_url="https://github.com/x/y/pull/45"),   # row still lagging, PR is proof
])
def test_a_window_that_vanishes_on_a_settled_run_is_not_a_death(
        store_file, sends, monkeypatch, gone_agent, run):
    _decide_immediately(monkeypatch)

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY}, runs=[run])

    assert [t for _, t in sends if "DIED" in t] == []      # the false alarm, silenced
    assert inbox.watches() == {}                           # ...and no stale watch left
    # Silent: run_events already announced this run. A second event here IS the bug.
    assert [e for e in inbox.load()["queue"] if e["wid"] == AGENT] == []


def test_one_run_never_produces_both_awaiting_review_and_died(
        store_file, sends, monkeypatch, gone_agent):
    # The self-contradiction, end to end: the run reaches awaiting_review in the same
    # tick that its window disappears. Exactly one message may come out of that, and it
    # is the true one.
    _decide_immediately(monkeypatch)
    run = _run("awaiting_review", pr_url="https://github.com/x/y/pull/45")

    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY}, runs=[run])
    inbox.tick(prev, runs=[run])

    texts = [t for _, t in sends]
    assert len(texts) == 1
    assert "awaiting review" in texts[0] and "pull/45" in texts[0]
    assert not any("DIED" in t for t in texts)


def test_a_vanished_window_with_no_run_row_admits_it_does_not_know(
        store_file, sends, monkeypatch, gone_agent):
    # An ad-hoc `tmux send-keys` dispatch has no run row, so there is nothing to
    # corroborate against. Say so honestly instead of asserting an outcome we can't see.
    _decide_immediately(monkeypatch)

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY}, runs=[])

    text = sends[0][1]
    assert "no run state to confirm the outcome" in text
    assert "DIED" not in text and "was not finished" not in text
    assert inbox.watches() == {}


def test_the_run_row_is_allowed_to_lag_the_window_it_belongs_to(
        store_file, sends, monkeypatch, gone_agent):
    # THE race that produced the live false positive: `task-finished` kills the window a
    # moment BEFORE the awaiting_review write lands, so the first sample sees a gone
    # window on a still-`running` run. Deciding on that first sample is what accused a
    # successful agent. The first tick may only STAMP; the claim waits for a re-read.
    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY}, runs=[_run("running")])

    assert sends == []                                     # nothing claimed yet...
    assert AGENT in inbox.watches()                        # ...and the watch is held

    run = _run("awaiting_review", pr_url="https://github.com/x/y/pull/45")
    inbox.tick({ORCH: inbox.IDLE}, runs=[run])             # the row lands a tick later

    assert not any("DIED" in t for _, t in sends)
    assert inbox.watches() == {}


def test_a_death_still_fires_once_the_settle_window_has_passed(
        store_file, sends, monkeypatch, gone_agent):
    # ...but the settle window only DELAYS the alarm — it must not swallow it. The run
    # is still `running` with no PR when we re-read it: that is a real crash.
    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY}, runs=[_run("running")])
    assert sends == []

    store = inbox.load()                                   # ...the settle window elapses
    store["watches"][AGENT]["gone_since"] -= inbox.DEATH_CONFIRM_SECONDS + 1
    inbox.save(store)
    inbox.tick({ORCH: inbox.IDLE}, runs=[_run("running")])

    assert "DIED mid-task" in sends[0][1]
    assert inbox.watches() == {}


# --- BUG (live 2026-07-13): an event is a PROSE SNAPSHOT, so it rots and it shouts ---
#
# Delivery is deliberately deferred until the orchestrator is idle, and the world moves
# in the meantime: `📥 run aeaae296 … is awaiting review — <PR #47>` was pushed AFTER
# that PR had been reviewed and merged, handing the orchestrator work already done. And
# because the event was a pre-rendered string built from the run's `title` — which for a
# markdown tracker is the WHOLE `- [ ]` line — the notification was the entire
# multi-paragraph task brief. One root cause: the event was a sentence, not a record.

TRACKER_LINE = (
    "**Make the dispatcher's default agent launch mode editable in Settings (the FIRST "
    "editable setting — there is no write path yet).** Today the launch command is "
    "hard-wired in `WORKFLOW.md` … ⚠️ **BEFORE implementing, read items 1–3 + Landmines.**"
)


def _review_run(**over):
    return {"task_id": "T7", "title": TRACKER_LINE, "status": "awaiting_review",
            "branch_name": "cmx-34", "window_name": "cmx-34", "pr_state": "open",
            "pr_url": "https://github.com/x/y/pull/47", **over}


def test_a_queued_review_event_whose_pr_merged_before_delivery_is_dropped(
        store_file, windows, sends, monkeypatch, caplog):
    # The orchestrator is BUSY when the run lands, so the event queues...
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    inbox.tick({}, runs=[_review_run()])
    assert len(inbox.load()["queue"]) == 1
    assert sends == []

    # ...and by the time it goes idle, the PR has been reviewed and merged. The claim
    # the event makes is no longer true, so it must never reach the orchestrator.
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    with caplog.at_level("WARNING"):
        inbox.tick({}, runs=[_review_run(status="done", pr_state="merged")])

    assert sends == []                              # NOT handed work that is already done
    assert inbox.load()["queue"] == []              # and the rotted event is retired
    assert "dropping stale run_review" in caplog.text   # loudly — never silently


def test_a_review_event_whose_pr_is_still_open_is_delivered_normally(
        store_file, windows, sends, monkeypatch):
    # The re-validation must not eat live events: an open PR is still awaiting review.
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    inbox.tick({}, runs=[_review_run()])

    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    inbox.tick({}, runs=[_review_run()])

    assert len(sends) == 1
    assert "awaiting review" in sends[0][1] and "pull/47" in sends[0][1]


def test_a_stale_event_does_not_spend_the_ticks_delivery_slot(
        store_file, windows, sends, monkeypatch):
    # Only ONE event is delivered per idle tick (a paste makes the orchestrator busy).
    # A dropped event isn't a delivery, so the live one behind it must still go out now.
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    store["queue"] = [
        inbox._event("run_review", "📥 stale", {"task_id": "T7"}),
        inbox._event("run_review", "📥 live", {"task_id": "T8"}),
    ]
    inbox.save(store)

    runs = [_review_run(status="done", pr_state="merged"),
            _review_run(task_id="T8")]
    with inbox.locked_store() as st:
        inbox.deliver(st, {ORCH: inbox.IDLE}, runs)

    assert [t for _, t in sends] == ["📥 live"]
    assert inbox.load()["queue"] == []


def test_a_failed_event_is_dropped_if_the_run_was_retried_into_flight(
        store_file, windows, sends, monkeypatch):
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    event = inbox._event("run_failed", "📥 T7 FAILED", {"task_id": "T7"})
    assert inbox.stale_reason(event, [_review_run(status="failed")]) is None
    assert inbox.stale_reason(event, [_review_run(status="running")])
    assert inbox.stale_reason(event, [])            # row deleted → the claim is unmoored


def test_a_window_event_is_never_re_validated_away(store_file):
    # `finished`/`died`/`blocked` assert something that ALREADY happened — there is no
    # later fact that makes them false, so re-validation must leave them alone.
    for kind in ("finished", "died", "blocked", "gone_unknown"):
        event = inbox._event(kind, "📥 @2 (cmx-9) …", {"wid": AGENT}, wid=AGENT)
        assert inbox.stale_reason(event, []) is None


# --- the notification is a SUMMARY; the essay is a PAYLOAD ----------------------

def test_a_run_event_carries_kind_summary_and_payload(store_file, windows, monkeypatch):
    _statuses(monkeypatch, {ORCH: inbox.BUSY})     # busy → it queues, so we can read it
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_review_run()])

    event = inbox.load()["queue"][0]
    assert event["kind"] == "run_review"
    assert set(event) >= {"kind", "summary", "payload", "ts"}

    summary = event["summary"]
    assert "\n" not in summary                     # ONE line — this is a notification
    assert len(summary) < 200                      # not the multi-paragraph task brief
    assert "cmx-34" in summary and "PR #47" in summary
    assert "Landmines" not in summary              # the essay does NOT ride along

    payload = event["payload"]                     # ...it lives here, in full, for the
    assert payload["task_id"] == "T7"              #    log/UI that will consume it next
    assert payload["title"] == TRACKER_LINE
    assert payload["pr_url"].endswith("/pull/47")
    assert payload["run_status"] == "awaiting_review"
    assert payload["branch_name"] == "cmx-34"


def test_the_tmux_push_renders_only_the_summary(store_file, windows, sends, monkeypatch):
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_review_run()])

    pushed = sends[0][1]
    assert "Landmines" not in pushed and "WORKFLOW.md" not in pushed
    assert pushed.startswith("📥 cmx-34 awaiting review — PR #47")


def test_a_legacy_pre_rendered_event_still_delivers(store_file, windows, sends, monkeypatch):
    # A daemon upgraded mid-flight finds `text`-only events already in inbox.json. They
    # must still go out — an upgrade may not swallow a queued event.
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    store["queue"] = [{"kind": "run_review", "wid": None, "text": "📥 legacy line"}]
    inbox.save(store)

    inbox.tick({}, runs=[])

    assert [t for _, t in sends] == ["📥 legacy line"]


def test_short_title_strips_markup_and_truncates_on_a_word_boundary():
    assert inbox._short_title("**bold** `code` title") == "bold code title"
    # `_` and `~` are NOT emphasis here — they are identifiers and approximations.
    assert inbox._short_title("pr_state after ~30s") == "pr_state after ~30s"
    cut = inbox._short_title(TRACKER_LINE)
    assert len(cut) <= inbox.SUMMARY_TITLE_CHARS + 1     # +1 for the ellipsis
    assert cut.endswith("…") and not cut.endswith(" …")
    assert inbox._short_title("") == ""


def test_pr_ref_reads_the_number_and_falls_back_to_the_url():
    assert inbox.pr_ref("https://github.com/x/y/pull/47") == "PR #47"
    assert inbox.pr_ref("https://example.test/nope") == "https://example.test/nope"
    assert inbox.pr_ref(None) == ""


def test_every_event_lands_in_the_durable_log(store_file, windows, sends, monkeypatch):
    """The inbox is the event log's first consumer — the queue is what the orchestrator
    is TOLD, the log is what HAPPENED.

    They are deliberately not the same list: an event dropped as stale, or a `silent`
    one that only retires a watch, still belongs in the record. Reconciling a bug like
    the false `DIED` against a queue that had already drained is exactly what was
    impossible before there was a log.
    """
    from chela import event_log

    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    runs = [{"task_id": "T1", "title": "**add** the parser", "status": "awaiting_review",
             "pr_url": "https://github.com/x/y/pull/3"}]

    inbox.tick({ORCH: inbox.IDLE}, runs=runs)

    logged = event_log.read()["events"]
    assert [e["type"] for e in logged] == ["run_review"]     # `kind` → `type`, one schema
    event = logged[0]
    assert event["seq"] == 1 and event["boot_id"]
    assert "pull/3" in event["summary"] and "\n" not in event["summary"]
    assert event["payload"]["task_id"] == "T1"
    assert event["payload"]["title"] == "**add** the parser"  # the essay, kept in the payload
