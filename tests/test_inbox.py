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

import pytest

from chela import inbox

ORCH = "@1"      # the orchestrator's own window
AGENT = "@2"     # a window it delegated work to


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
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator", AGENT: "chelamux"})
    _registered()
    # The agent's window is gone (crashed / killed) — it never finished.
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {ORCH: "orchestrator"})
    _statuses(monkeypatch, {ORCH: inbox.IDLE})

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

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
