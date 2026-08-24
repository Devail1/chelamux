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

import json
import os
import threading
import time
from argparse import Namespace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from chela import (agent_manager, dispatcher, event_log, inbox, judge, main, sessionids,
                   sessions, transcripts)

# Captured at collection time, before the `no_transcript_evidence` autouse fixture
# (below) overwrites `sessions.transcript_for_window` on every test — the CMX-191
# guards need the REAL resolver reinstated to exercise the code they're pinning.
_REAL_TRANSCRIPT_FOR_WINDOW = sessions.transcript_for_window

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
    """Default: no window resolves a transcript (so status transitions are the only signal).

    Autouse so no test can reach the real tmux/transcripts of the LIVE fleet, and so the
    evidence path is opt-in per test rather than silently on. Stubs ``sessions.transcript_
    for_window`` — the per-WINDOW resolver ``did_work_since`` actually calls — not the old
    cwd-keyed ``transcripts.last_assistant_activity``, which two windows sharing a cwd
    cannot disambiguate (CMX-191).
    """
    monkeypatch.setattr(inbox.sessions, "transcript_for_window", lambda wid: None)


@pytest.fixture(autouse=True)
def no_session_identity(monkeypatch):
    """Purity: the sessions layer (tmux + /proc) is inert here.

    ``watch``/``register`` resolve the orchestrator's session identity (CMX-82 self-heal), and
    ``tick`` re-resolves a rotted address from it — both would reach the LIVE fleet's tmux and
    ``/proc``. Stubbed to None so these tests stay pure and record no identity; the CMX-82
    behaviour itself is covered in ``test_epoch.py``, which stubs these explicitly.
    """
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "wid_for_session", lambda sid, pane_map=None: None)


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


def _confirm_idle_immediately(monkeypatch):
    """Collapse the idle-confirm window (CMX-193) — that race is covered by its own
    tests below, and every test not specifically about it wants "idle" trusted on sight,
    same as before that fix existed."""
    monkeypatch.setattr(inbox, "IDLE_CONFIRM_SECONDS", 0)


# --- the edge trigger ----------------------------------------------------------

def test_busy_to_idle_on_a_watched_window_fires_once(store_file, windows, sends, monkeypatch):
    _confirm_idle_immediately(monkeypatch)
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    # The agent was busy; now it is idle → the task it was dispatched finished.
    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    assert len(sends) == 1
    wid, text = sends[0]
    assert wid == ORCH                                  # ...into the ORCHESTRATOR
    assert text.startswith("📥 @2 · chelamux finished")  # one compact, actionable line
    assert "note: “fix the parser”" in text             # curly quotes: `"` is shell meta
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
    _confirm_idle_immediately(monkeypatch)
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
    _confirm_idle_immediately(monkeypatch)
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


# --- CMX-195 objective 5: a disarmed identity must be visible in the RESULT --------
#
# Measured live 2026-07-30: `orchestrator_session` was `null` because `_identity_of`
# returned `None` at registration — and `null` looks IDENTICAL to a healthy registration
# once it's on disk, so nothing said the self-heal CMX-82 depends on was disarmed before
# it ever ran. These guard the reported result, never the stored (silently-null) field —
# a caller checking `result["session"]` is what lets `chela watch`/`register` warn a human.

def test_register_return_value_surfaces_a_failed_identity_resolution(store_file, windows):
    result = inbox.register(ORCH)
    assert result["ok"] is True
    assert "session" in result
    assert result["session"] is None


def test_register_return_value_reports_a_resolved_identity_too(store_file, windows, monkeypatch):
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: "sid-123")
    result = inbox.register(ORCH)
    assert result["session"] == "sid-123"


def test_watch_return_value_surfaces_a_failed_identity_resolution(store_file, windows):
    result = inbox.watch(AGENT, "note", by=ORCH)
    assert result["ok"] is True
    assert "session" in result
    assert result["session"] is None


def test_watch_return_value_reports_a_resolved_identity_too(store_file, windows, monkeypatch):
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: "sid-123")
    result = inbox.watch(AGENT, "note", by=ORCH)
    assert result["session"] == "sid-123"


def test_unregister_clears_the_address_only_when_it_names_that_wid(store_file):
    # unregister is the inverse of register (orchestrator teardown uses it). It clears the
    # recorded address so a killed window leaves no dead address behind — but ONLY if the
    # address still names the wid being torn down; a human who re-registered in the meantime
    # must never be cleared out from under.
    store = inbox.load()
    store["orchestrator"] = ORCH
    store["orchestrator_epoch"] = "e1"
    inbox.save(store)

    # a mismatching wid is a no-op — someone else is registered now
    res = inbox.unregister("@999")
    assert res["ok"] is False
    assert inbox.orchestrator_wid(inbox.load()) == ORCH

    # the matching wid clears the address back to inert (ADDR_NONE)
    res = inbox.unregister(ORCH)
    assert res["ok"] is True
    assert inbox.orchestrator_wid(inbox.load()) is None


# --- readdress / unregister_dangling — CMX-196's write half for `chela restore --apply` ---

def _assert_address_alarm_cleared(store, who):
    """🔴 Both writers call `_clear_address_alarm` — "the failure is over" — and a latched
    alarm is not cosmetic: `address_alarm_pushed` left True SUPPRESSES the push for the next
    genuine outage, so healing one address silences the report of the next one."""
    assert store.get("address_alarm") is None, f"{who} left address_alarm latched"
    assert store.get("address_alarm_since") is None, f"{who} left address_alarm_since latched"
    assert store.get("address_alarm_pushed") is False, (
        f"{who} left address_alarm_pushed latched — the NEXT real outage goes unreported"
    )


def _arm_the_address_alarm(store):
    store["address_alarm"] = "ADDR_GONE"
    store["address_alarm_since"] = 1.0
    store["address_alarm_pushed"] = True
    return store


def test_readdress_moves_the_orchestrator_to_its_new_live_address(store_file, windows, monkeypatch):
    """🔴 `readdress` re-derives the identity fresh rather than trusting the plan's stale
    session id (the docstring's defense-in-depth claim) — so the stored
    ``orchestrator_session`` must be the FRESHLY resolved one, never left blank/stale."""
    # ⛔ wid-DISCRIMINATING: a fake returning the same id for any window cannot tell whether
    # readdress resolved the NEW address or the dangling OLD one. Resolving the old wid
    # stores the identity of a window that is gone — the row would look healthy and heal to
    # nothing.
    monkeypatch.setattr(inbox.sessions, "session_of_window",
                        lambda wid, pane_map=None: {ORCH: "sid-fresh-live",
                                                    "@9": "sid-of-the-DEAD-window"}.get(wid))
    store = inbox.load()
    store["orchestrator"] = "@9"
    store["orchestrator_epoch"] = "OLD-epoch"
    store["orchestrator_session"] = "sid-old"
    _arm_the_address_alarm(store)          # ...so "the failure is over" is observable
    inbox.save(store)

    result = inbox.readdress("@9", "OLD-epoch", ORCH)

    assert result["ok"] is True
    assert result["session"] == "sid-fresh-live"
    reloaded = inbox.load()
    assert reloaded["orchestrator"] == ORCH
    # ⛔ NOT `!= "OLD-epoch"` — None satisfies that, and an UNSTAMPED address is exactly the
    # unclassifiable row this whole ticket exists to prevent: `is_dangling` needs both halves,
    # so a null epoch can never be proven stale OR current again.
    assert reloaded["orchestrator_epoch"] == inbox.epoch.current(), (
        f"readdress must stamp the CURRENT epoch, got {reloaded['orchestrator_epoch']!r}"
    )
    assert reloaded["orchestrator_name"] == "orchestrator"
    assert reloaded["orchestrator_session"] == "sid-fresh-live", (
        "readdress must resolve the NEW address's identity, not the dangling old one"
    )
    _assert_address_alarm_cleared(reloaded, "readdress")


def test_readdress_refuses_an_unknown_window(store_file, windows):
    store = inbox.load()
    store["orchestrator"] = "@9"
    store["orchestrator_epoch"] = "OLD-epoch"
    inbox.save(store)

    result = inbox.readdress("@9", "OLD-epoch", "@999")

    assert result["ok"] is False
    assert inbox.load()["orchestrator"] == "@9", "a refused readdress must not touch the store"


def test_readdress_is_a_noop_when_the_address_has_moved_on(store_file, windows):
    """🔴 The guard this function exists for: a human already re-registered (or a further
    restart reissued the old address) since classification ran, and this must not clobber
    whatever is there now with a plan computed before it happened."""
    store = inbox.load()
    store["orchestrator"] = "@9"
    store["orchestrator_epoch"] = "SOMETHING-ELSE"      # not the epoch classification saw
    store["orchestrator_session"] = "sid-fresh"
    inbox.save(store)

    before = inbox.load()
    result = inbox.readdress("@9", "OLD-epoch", ORCH)

    assert result["ok"] is False
    # The sibling of unregister_dangling's no-op guard, asserted the same way: the WHOLE
    # store, not the two fields this test happens to have set.
    assert inbox.load() == before, "a declined readdress must change nothing at all"


def test_readdress_is_a_noop_when_a_different_wid_is_registered(store_file, windows):
    store = inbox.load()
    store["orchestrator"] = "@2"                        # someone else, entirely
    store["orchestrator_epoch"] = "OLD-epoch"
    inbox.save(store)

    result = inbox.readdress("@9", "OLD-epoch", ORCH)

    assert result["ok"] is False
    assert inbox.load()["orchestrator"] == "@2"


def test_address_state_dangling_points_past_chela_watch_to_chela_restore():
    """CMX-254: `chela watch` (the remedy this message names first) only works from a LIVE
    session — a session restarted outside tmux cannot run it at all (`no window id`). The
    why-text must not leave a reader stuck on that dead end; it has to name the fallback
    that works with no live window: `chela restore`."""
    store = {"orchestrator": "@9", "orchestrator_epoch": "OLD-epoch",
             "orchestrator_name": "orchestrator"}

    state, why = inbox.address_state(store, {}, "NEW-epoch")

    assert state == inbox.ADDR_DANGLING
    assert "chela watch" in why
    assert "chela restore" in why
    assert "outside tmux" in why


def test_address_state_gone_points_past_chela_watch_to_chela_restore():
    store = {"orchestrator": "@9", "orchestrator_epoch": "SAME-epoch"}

    state, why = inbox.address_state(store, {"@2": "idle"}, "SAME-epoch")

    assert state == inbox.ADDR_GONE
    assert "chela watch" in why
    assert "chela restore" in why


def test_unregister_dangling_clears_only_when_both_wid_and_epoch_still_match(store_file):
    store = inbox.load()
    store["orchestrator"] = "@9"
    store["orchestrator_epoch"] = "OLD-epoch"
    store["orchestrator_session"] = "sid-dead"
    store["orchestrator_name"] = "liavedunix"
    _arm_the_address_alarm(store)
    inbox.save(store)

    # right wid, wrong epoch — a further restart reissued @9 to something new; must not clear it
    before = inbox.load()
    res = inbox.unregister_dangling("@9", "SOME-OTHER-epoch")
    assert res["ok"] is False
    # ⛔ A no-op means NOTHING changed, not just that the address survived. A declined call
    # that still blanks `orchestrator_session` disarms CMX-82's self-heal for a registration
    # it just decided it had no right to touch.
    assert inbox.load() == before, (
        "a declined unregister_dangling must leave the store byte-for-byte unchanged"
    )

    # 🔴 right EPOCH, wrong wid — the other half of the compound guard. Its sibling
    # `readdress` has this case (test_readdress_is_a_noop_when_a_different_wid_is_registered)
    # and this did not: with the wid half disabled, a stale plan clears whatever registration
    # happens to carry that epoch, which after a restart is a genuinely live one.
    before = inbox.load()
    res = inbox.unregister_dangling("@77", "OLD-epoch")
    assert res["ok"] is False
    assert inbox.load() == before, "a different wid must leave the store untouched entirely"

    # right wid AND right epoch — the exact dangling row classification saw
    res = inbox.unregister_dangling("@9", "OLD-epoch")
    assert res["ok"] is True
    # ⛔ The WHOLE registration, not just the address. A null orchestrator still holding a
    # dead `orchestrator_session`/`_name`/`_epoch` is a half-cleared row: `resolve_heal`
    # reads that session, and the next registrant inherits a stranger's identity.
    reloaded = inbox.load()
    for field in ("orchestrator", "orchestrator_epoch", "orchestrator_session",
                  "orchestrator_name"):
        assert reloaded[field] is None, (
            f"unregister_dangling left {field}={reloaded[field]!r} behind"
        )
    _assert_address_alarm_cleared(reloaded, "unregister_dangling")


def test_unregister_dangling_is_stricter_than_unregister(store_file):
    """The counterweight, spelled out: `unregister`'s own wid-only guard WOULD clear this
    row (it only checks the address), which is exactly why `unregister_dangling` exists as
    a separate, stricter function rather than a shared code path."""
    store = inbox.load()
    store["orchestrator"] = "@9"
    store["orchestrator_epoch"] = "A-NEW-EPOCH"          # NOT the dangling one
    inbox.save(store)

    assert inbox.unregister_dangling("@9", "OLD-epoch")["ok"] is False
    assert inbox.orchestrator_wid(inbox.load()) == "@9"


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
    _confirm_idle_immediately(monkeypatch)
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


# --- CMX-197: a clean judge verdict used to be structurally unnotifiable --------
#
# `judge.judge_run` never moves `status` off `awaiting_review` on a clean (or
# cannot-verify) verdict — only a BLOCKED one does, through `request_changes`, which
# already fires `run_changes_requested`. So the plain `run_review` edge (fired once,
# when the run first reaches `awaiting_review`) was the ONLY thing the orchestrator
# ever heard, and a judge that settled minutes or hours later said nothing new. Measured
# live twice (cmx-195, cmx-196): "every guard held" posted to the PR and the orchestrator
# never woke up for it.

def test_a_clean_judge_verdict_fires_its_own_event(store_file, windows, sends, monkeypatch):
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    run = {"task_id": "T1", "title": "x", "status": "awaiting_review",
           "pr_url": "https://github.com/x/y/pull/9"}
    inbox.tick({}, runs=[run])                     # the plain "awaiting review" edge
    assert len(sends) == 1
    assert "awaiting review" in sends[0][1]

    run = {**run, "judge_state": "clean"}
    inbox.tick({}, runs=[run])                     # the judge settles — a SECOND event

    assert len(sends) == 2
    assert "guard held" in sends[1][1] and "MERGEABLE" in sends[1][1]
    assert "pull/9" in sends[1][1]

    # Staying clean across further ticks does not re-announce.
    inbox.tick({}, runs=[run])
    assert len(sends) == 2


def test_a_cannot_verify_judge_verdict_also_fires(store_file, windows, sends, monkeypatch):
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    run = {"task_id": "T1", "title": "x", "status": "awaiting_review", "pr_url": None}
    inbox.tick({}, runs=[run])
    run = {**run, "judge_state": "cannot_verify", "judge_detail": "no experiments proposed"}
    inbox.tick({}, runs=[run])

    assert len(sends) == 2
    assert "CANNOT VERIFY" in sends[1][1]
    assert "no experiments proposed" in sends[1][1]


def test_a_running_judge_does_not_reannounce_or_duplicate(store_file, windows, sends, monkeypatch):
    # The transient states between "awaiting_review" and a settled verdict (no judge_state
    # yet, then "running") must be absorbed silently — the run already got its ONE plain
    # notice, and re-sending it on every judge state sample would be noise.
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    base = {"task_id": "T1", "title": "x", "status": "awaiting_review"}
    inbox.tick({}, runs=[dict(base)])
    inbox.tick({}, runs=[{**base, "judge_state": "running"}])
    inbox.tick({}, runs=[{**base, "judge_state": "running"}])

    assert len(sends) == 1                          # only the original "awaiting review"

    inbox.tick({}, runs=[{**base, "judge_state": "clean"}])
    assert len(sends) == 2                           # the settle IS announced


def test_a_verdict_already_clean_on_first_sight_skips_the_generic_notice(
        store_file, windows, sends, monkeypatch):
    # A daemon that starts (or restarts) after the judge already settled must not fire
    # BOTH the generic "awaiting review" and the judge verdict for the same run — the
    # judge event is strictly more informative, so it wins and is the only one sent.
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    run = {"task_id": "T1", "title": "x", "status": "awaiting_review",
           "judge_state": "clean", "pr_url": "https://github.com/x/y/pull/9"}
    inbox.tick({}, runs=[run])

    assert len(sends) == 1
    assert "MERGEABLE" in sends[0][1]


@pytest.mark.parametrize("gone_state", ["merged", "closed"])
def test_a_clean_verdict_event_goes_stale_once_the_run_moves_on(gone_state, store_file):
    """A queued CLEAN judge-verdict event is a claim about the PAST — re-checked at delivery
    (inbox.stale_reason), same as `run_review`. A merged/closed PR, or a run that moved off
    `awaiting_review` before delivery, must drop it rather than deliver stale work: "clean
    and MERGEABLE" about a run nothing is waiting to merge any more is moot.

    ⚠️ CMX-229 round: this test used to be shared with `run_judge_cannot_verify` (the
    "both-kinds" rule from CMX-197 round 4). It no longer is, ON PURPOSE — Objective 1
    makes `cannot_verify` diverge from `clean` on EXACTLY this axis; see its own
    deliberate, documented counterpart right below,
    `test_a_cannot_verify_verdict_event_survives_the_run_moving_on`.
    """
    event = {"kind": "run_judge_clean", "payload": {"task_id": "T1"}}
    still_open = [{"task_id": "T1", "status": "awaiting_review", "pr_state": "open"}]
    assert inbox.stale_reason(event, still_open) is None

    gone = [{"task_id": "T1", "status": "awaiting_review", "pr_state": gone_state}]
    assert gone_state in (inbox.stale_reason(event, gone) or ""), (
        f"a run_judge_clean for a {gone_state} PR must be dropped — there is nothing left "
        "to review"
    )

    moved_on = [{"task_id": "T1", "status": "changes_requested", "pr_state": "open"}]
    assert "changes_requested" in (inbox.stale_reason(event, moved_on) or ""), (
        "a run_judge_clean for a run that left awaiting_review must be dropped"
    )


@pytest.mark.parametrize("gone_state", ["merged", "closed"])
def test_a_cannot_verify_verdict_event_survives_the_run_moving_on(gone_state, store_file):
    """⚖️🔔 CMX-229 Objective 1 — the deliberate counterpart to the CLEAN test above.

    "The judge could not verify this commit" is not a claim about `awaiting_review`, it is
    a claim about a commit that was never actually checked — measured live on CMX-227, that
    is exactly the outcome of the CAS-refused race (the run merges/moves on WHILE the judge
    is still working). Dropping it the moment the run leaves `awaiting_review` or its PR
    merges/closes is the bug this objective closes: it is the ONE record that a
    SURVIVED-mutation finding might exist, undetected, on a commit already shipped.
    """
    event = {"kind": "run_judge_cannot_verify", "payload": {"task_id": "T1"}}
    still_open = [{"task_id": "T1", "status": "awaiting_review", "pr_state": "open"}]
    assert inbox.stale_reason(event, still_open) is None

    gone = [{"task_id": "T1", "status": "awaiting_review", "pr_state": gone_state}]
    assert inbox.stale_reason(event, gone) is None, (
        f"a run_judge_cannot_verify for a {gone_state} PR must still be delivered"
    )

    moved_on = [{"task_id": "T1", "status": "changes_requested", "pr_state": "open"}]
    assert inbox.stale_reason(event, moved_on) is None, (
        "a run_judge_cannot_verify for a run that left awaiting_review must still be "
        "delivered — that IS the CAS-refused race this objective closes"
    )

    gone_row = [{"task_id": "T1", "status": "done", "pr_state": "merged"}]
    assert inbox.stale_reason(event, gone_row) is None, (
        "a run_judge_cannot_verify for an already-merged run is the MOST important case, "
        "not a droppable one"
    )


@pytest.mark.parametrize("gone_state", ["merged", "closed"])
def test_a_blocked_race_verdict_event_survives_the_run_moving_on(gone_state, store_file):
    """⚖️🧊 CMX-239 — the twin of the CANNOT_VERIFY test above, for a CONFIRMED blocking
    finding whose CAS lost the race. "A guard survived corruption" is not a claim about
    `changes_requested`, it is a claim about a commit — dropping it the moment the run
    leaves review or its PR merges/closes is strictly worse than dropping the cannot_verify
    twin: this is a FACT, not an unknown."""
    event = {"kind": "run_judge_blocked_race", "payload": {"task_id": "T1"}}
    still_running = [{"task_id": "T1", "status": "running", "pr_state": "open"}]
    assert inbox.stale_reason(event, still_running) is None

    gone = [{"task_id": "T1", "status": "running", "pr_state": gone_state}]
    assert inbox.stale_reason(event, gone) is None, (
        f"a run_judge_blocked_race for a {gone_state} PR must still be delivered"
    )

    gone_row = [{"task_id": "T1", "status": "done", "pr_state": "merged"}]
    assert inbox.stale_reason(event, gone_row) is None, (
        "a run_judge_blocked_race for an already-merged run is the MOST important case, "
        "not a droppable one — the guard proven to survive corruption may already have shipped"
    )


def test_a_blocked_race_verdict_rots_once_the_head_moves_past_the_judged_commit(store_file):
    """The one legitimate way a `run_judge_blocked_race` claim goes stale: a newer commit
    superseded the one the judge actually found blocking. Mirrors `run_judge_cannot_verify`'s
    live-head check exactly."""
    event = {"kind": "run_judge_blocked_race",
             "payload": {"task_id": "T1", "judge_sha": _JUDGE_SHA}}
    run = [{"task_id": "T1", "status": "done", "pr_state": "merged"}]

    assert inbox.stale_reason(event, run, live_heads={"T1": _JUDGE_SHA}) is None, (
        "the live head still matches the judged commit — not stale"
    )
    reason = inbox.stale_reason(event, run, live_heads={"T1": _SUPERSEDING_SHA})
    assert reason and "moved past" in reason


def test_a_blocked_race_verdicts_payload_carries_the_judged_sha_and_state(
        store_file, windows, monkeypatch):
    """⚖️🧊 CMX-239 round 5 — the twin of `test_a_judge_verdicts_payload_carries_the_judged_sha`
    below, for `J_BLOCKED_RACE`. That test's `JUDGE_KINDS` parametrize does NOT cover this
    kind on purpose: `_live_judge_heads` never nominates a `blocked_race` run (see its own
    docstring), so folding this kind into the shared list would silently break every
    live-head-supersession test that shares it — a gap in `_live_judge_heads` this PR did not
    write, not something this test should paper over.

    Written standalone, and deriving the event from a REAL run row via `inbox.tick()` — not
    hand-fed. The test above (`..._rots_once_the_head_moves_past_the_judged_commit`) builds its
    event dict by hand (`{"payload": {"judge_sha": _JUDGE_SHA}}`), which proves `stale_reason`
    reads `judge_sha` correctly but says nothing about whether `run_events`'s `J_BLOCKED_RACE`
    branch ever PUTS the judged sha and verdict into the payload in the first place. A judge
    run that corrupted `payload["judge_sha"] = None` or `payload["judge_state"] = ""` in that
    branch survived the full suite before this test existed.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})     # busy → it queues, so we can read it
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_verdict_run(judge.J_BLOCKED_RACE)])

    queued = inbox.load()["queue"]
    assert len(queued) == 1
    assert queued[0]["kind"] == "run_judge_blocked_race"
    payload = queued[0]["payload"]
    # ⭐ The payload is the RECORD `stale_reason`'s live-head supersession check reads (see
    # the test above) — a blocked_race verdict with no judged commit attached can never be
    # checked against a live head, and would sail through as un-stale forever.
    assert payload["judge_sha"] == _JUDGE_SHA, (
        f"the judged sha did not reach the payload, got {payload['judge_sha']!r}"
    )
    # ...and the VERDICT itself has to reach it too — the kind string says a blocked_race
    # fired, but a consumer reading the record (the dashboard, a replay of the event log)
    # has only this field to learn the judge's own state string.
    assert payload["judge_state"] == judge.J_BLOCKED_RACE, (
        f"the verdict did not reach the payload, got {payload['judge_state']!r}"
    )
    assert payload["judge_detail"] == _LONG_DETAIL, (
        f"the judge detail did not survive into the payload, got {payload['judge_detail']!r}"
    )


# --- CMX-197 rework: a verdict is only meaningful against the commit it judged --------
#
# The status-staleness check above catches a run that moved OFF awaiting_review. It says
# nothing about a run that stays put while its PR's head moves PAST the judged commit — a
# rework agent, a human's own fix, or `chela reopen` can all do that, and the queue delay
# (the whole point of the inbox: hold until the orchestrator is idle) is exactly the
# window in which it happens. That must not read as "clean and MERGEABLE" about a commit
# that is no longer the head.

_JUDGE_SHA = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
_SUPERSEDING_SHA = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


_LONG_DETAIL = (
    "the suite could not be provisioned: npm ci failed after three attempts, so the two "
    "real-DOM suites would have SKIPPED silently and every mutation would have looked "
    "survivable — this string is deliberately longer than the 140-char summary truncation"
)


def _verdict_run(judge_state=None, **over):
    """A run row sitting in `awaiting_review` with a SETTLED judge verdict."""
    # `judge_detail` defaults to a long reason but MUST stay overridable — it is nullable
    # in the schema, and cannot_verify is exactly where a null one shows up.
    return _clean_run(judge_state=judge_state or judge.J_CLEAN,
                      judge_detail=over.pop("judge_detail", _LONG_DETAIL), **over)


def _clean_run(**over):
    return {"task_id": "T1", "title": TRACKER_LINE, "status": "awaiting_review",
            "pr_url": "https://github.com/x/y/pull/9", "judge_state": "clean",
            "window_id": "@6",
            # ⚠️ `workflow_path` is what `_live_judge_heads` derives `repo_dir` from, and
            # the fixture never carried one — so repo_dir was silently None in EVERY test,
            # which is precisely the state that makes `_read_pr_checks` return no sha.
            "workflow_path": "/repo/chelamux/WORKFLOW.md",
            "judge_sha": _JUDGE_SHA, **over}



# ⭐ CMX-197 round 4 — the rule from round 3, MECHANISED instead of intended.
#
# "When two kinds ship together, every site naming one must assert BOTH." I wrote that and
# then guarded three Python sites with a clean verdict only, so narrowing each of them to
# `("run_judge_clean",)` sailed through. A shared parametrize is the enforcement: a new
# judge kind added to this tuple makes every guard below cover it, and no site can quietly
# be single-kind again.
JUDGE_KINDS = [
    pytest.param(judge.J_CLEAN, "run_judge_clean", id="clean"),
    pytest.param(judge.J_CANNOT_VERIFY, "run_judge_cannot_verify", id="cannot_verify"),
]

@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_judge_verdicts_payload_carries_the_judged_sha(
        judge_state, kind, store_file, windows, monkeypatch):
    # The sha must reach the payload at all — a verdict with no judged commit attached
    # cannot ever be checked against a live head.
    _statuses(monkeypatch, {ORCH: inbox.BUSY})     # busy → it queues, so we can read it
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_verdict_run(judge_state)])

    queued = inbox.load()["queue"]
    assert len(queued) == 1
    assert queued[0]["kind"] == kind
    payload = queued[0]["payload"]
    assert payload["judge_sha"] == _JUDGE_SHA
    # ⭐ The payload is the RECORD, so the VERDICT itself has to reach it — the kind string
    # says which of the two fired, but a consumer reading the record (the dashboard, a
    # replay of the event log) has only this field to learn what the judge concluded.
    assert payload["judge_state"] == judge_state, (
        f"the verdict did not reach the payload, got {payload['judge_state']!r}"
    )
    # ...and the REASON survives in full. The summary truncates it to 140 chars for the
    # push; the payload is the durable copy, and a cannot-verify reason is the only thing
    # telling a human WHY the judge could not answer.
    assert payload["judge_detail"] == _LONG_DETAIL, (
        f"the judge detail did not survive into the payload, got {payload['judge_detail']!r}"
    )


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_judge_verdict_for_a_superseded_head_is_dropped_not_delivered(
        judge_state, kind, store_file, windows, sends, monkeypatch, caplog):
    # The orchestrator is BUSY when the judge settles clean on sha A — the event queues...
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    inbox.tick({}, runs=[_verdict_run(judge_state)])
    assert len(inbox.load()["queue"]) == 1
    assert sends == []

    # ...and by the time it goes idle, the PR's LIVE head has moved past the judged
    # commit — the run's own status never changed, so the status-staleness check alone
    # would wave this straight through and hand the orchestrator "clean and MERGEABLE"
    # about a commit nobody judged.
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, _SUPERSEDING_SHA))
    with caplog.at_level("WARNING"):
        inbox.tick({}, runs=[_verdict_run(judge_state)])

    assert sends == []                              # NEVER told the superseded commit is ready
    assert inbox.load()["queue"] == []              # and the rotted event is retired
    assert f"dropping stale {kind}" in caplog.text
    # ⛔ ...NAMING both commits. This drop is silent to the operator — log-only, no event_log
    # row and no push — so this line is the ONLY forensic record that a verdict was retired.
    # "PR head moved past the judged commit" without the shas cannot answer the one question
    # it exists for: WHICH commit was judged, and what is live now.
    # ⛔ NOT "both shas appear somewhere" — that passes with them swapped, or listed with
    # no relation. The reason exists to say WHICH commit was judged and WHAT IS LIVE NOW,
    # so the pair must render in that order, together.
    assert f"{_JUDGE_SHA[:12]} -> {_SUPERSEDING_SHA[:12]}" in caplog.text, (
        f"the drop reason must read judged -> live. Got: {caplog.text!r}"
    )   # loudly — never silently


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_superseded_verdict_is_dropped_even_when_it_NEVER_QUEUES(
        judge_state, kind, store_file, windows, sends, monkeypatch, caplog):
    """🔴 GUARD (CMX-197 round 2): the IDLE path — emitted and delivered in ONE tick.

    The staleness test above starts BUSY, so the event sits in the queue and the candidate
    set can be derived from `queue`. With an IDLE orchestrator there is no such moment: the
    verdict is emitted and delivered inside the same tick, the queue is empty the whole
    time, and a candidate set built from `queue` alone is EMPTY — so no live head is
    fetched, no staleness check runs, and the superseded commit is announced as "clean and
    MERGEABLE" on its very first appearance.

    ⭐ That is the bug this whole ticket exists to prevent, shipping through the one path a
    healthy fleet actually takes: an idle orchestrator is the NORMAL case.
    """
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, _SUPERSEDING_SHA))

    with caplog.at_level("WARNING"):
        inbox.tick({}, runs=[_verdict_run(judge_state)])          # emits AND delivers in this one tick

    assert sends == [], (
        "a verdict for a superseded head was announced on its first tick — the candidate "
        "set must include runs ABOUT TO queue a verdict, not only ones already queued"
    )
    assert inbox.load()["queue"] == []
    assert f"dropping stale {kind}" in caplog.text
    # ⛔ ...NAMING both commits. This drop is silent to the operator — log-only, no event_log
    # row and no push — so this line is the ONLY forensic record that a verdict was retired.
    # "PR head moved past the judged commit" without the shas cannot answer the one question
    # it exists for: WHICH commit was judged, and what is live now.
    # ⛔ NOT "both shas appear somewhere" — that passes with them swapped, or listed with
    # no relation. The reason exists to say WHICH commit was judged and WHAT IS LIVE NOW,
    # so the pair must render in that order, together.
    assert f"{_JUDGE_SHA[:12]} -> {_SUPERSEDING_SHA[:12]}" in caplog.text, (
        f"the drop reason must read judged -> live. Got: {caplog.text!r}"
    )


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_an_idle_orchestrator_still_gets_a_verdict_whose_head_is_CURRENT(
        judge_state, kind, store_file, windows, sends, monkeypatch):
    """The counterweight for the idle path: skipping the fetch entirely would satisfy the
    guard above by never delivering anything at all."""
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, _JUDGE_SHA))

    inbox.tick({}, runs=[_verdict_run(judge_state)])

    assert len(sends) == 1


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_PARKED_verdict_is_still_re_checked_once_its_run_row_moves_on(
        judge_state, kind, store_file, windows, sends, monkeypatch, caplog):
    """🔴 GUARD (CMX-197 round 3): the QUEUE-derived half of the candidate set.

    `_live_judge_heads` unions two sources — runs about to queue a verdict, and events
    ALREADY queued. Round 2 guarded the first. The second is the only source once the run
    row itself stops qualifying, and its docstring promises exactly that: a long-parked
    verdict "keeps getting re-checked every tick it sits there".

    Realistic shape: the verdict queues behind a busy orchestrator, then a NEW judge starts
    on a newer head, so the row's `judge_state` goes back to running. The runs-branch now
    skips it — and if the queue-branch is the thing that is broken, the parked event sails
    through with no live-head check at all and announces a commit two heads stale.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    inbox.tick({}, runs=[_verdict_run(judge_state)])
    assert len(inbox.load()["queue"]) == 1

    # A new judge is now running on a newer head: the row no longer qualifies via `runs`.
    moved_on = dict(_verdict_run(judge_state), judge_state=judge.J_RUNNING)
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, _SUPERSEDING_SHA))

    with caplog.at_level("WARNING"):
        inbox.tick({}, runs=[moved_on])

    assert sends == [], (
        "a PARKED verdict was delivered without a live-head re-check — the queue-derived "
        "half of the candidate set is the only source once the run row moves on"
    )
    assert f"dropping stale {kind}" in caplog.text
    # ⛔ ...NAMING both commits. This drop is silent to the operator — log-only, no event_log
    # row and no push — so this line is the ONLY forensic record that a verdict was retired.
    # "PR head moved past the judged commit" without the shas cannot answer the one question
    # it exists for: WHICH commit was judged, and what is live now.
    # ⛔ NOT "both shas appear somewhere" — that passes with them swapped, or listed with
    # no relation. The reason exists to say WHICH commit was judged and WHAT IS LIVE NOW,
    # so the pair must render in that order, together.
    assert f"{_JUDGE_SHA[:12]} -> {_SUPERSEDING_SHA[:12]}" in caplog.text, (
        f"the drop reason must read judged -> live. Got: {caplog.text!r}"
    )


def test_a_cannot_verify_verdict_emits_the_kind_the_consumers_key_on(
        store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 3): the EMITTED string for the cannot-verify kind.

    `run_judge_clean`'s emitted kind is asserted; its sibling's was not. The same literal is
    keyed on by `stale_reason`, the Feed's lane model and the Decisions panel's subscription
    — so a rename here silently unhooks the verdict from every consumer at once, and each of
    those consumers' own tests keep passing because they assert their OWN copy of the string.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[dict(_clean_run(), judge_state=judge.J_CANNOT_VERIFY)])

    queued = inbox.load()["queue"]
    assert len(queued) == 1
    assert queued[0]["kind"] == "run_judge_cannot_verify", (
        f"the emitted kind must be the literal every consumer keys on, got {queued[0]['kind']!r}"
    )
    assert queued[0]["payload"]["judge_sha"] == _JUDGE_SHA


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_judge_verdict_matching_the_live_head_still_delivers(
        judge_state, kind, store_file, windows, sends, monkeypatch):
    # The counterweight: an unmoved head must still deliver normally, or "drop everything"
    # would trivially satisfy the guard above too.
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    inbox.tick({}, runs=[_verdict_run(judge_state)])

    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, _JUDGE_SHA))
    inbox.tick({}, runs=[_verdict_run(judge_state)])

    assert len(sends) == 1


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
    _confirm_idle_immediately(monkeypatch)
    _registered()
    # Both samples see idle: the entire busy period fell between them. The transcript is
    # the evidence — the agent wrote an assistant turn AFTER the watch was registered.
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.sessions, "transcript_for_window",
                        lambda wid: Path(f"/proj/{wid}/session.jsonl"))
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity_at",
                        lambda path: watched_since + 5)
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
    _confirm_idle_immediately(monkeypatch)
    _registered()
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.sessions, "transcript_for_window",
                        lambda wid: Path(f"/proj/{wid}/session.jsonl"))
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity_at",
                        lambda path: watched_since + 5)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    inbox.tick({})                        # empty prev — a fresh daemon

    assert len(sends) == 1


def test_evidence_never_reports_an_agent_that_is_still_working(
        store_file, windows, sends, monkeypatch):
    # It has written assistant turns (it is mid-task, using tools) but is NOT idle.
    # Work-since-watch alone must never mean "done" — the idle gate still rules.
    _registered()
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.sessions, "transcript_for_window",
                        lambda wid: Path(f"/proj/{wid}/session.jsonl"))
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity_at",
                        lambda path: watched_since + 5)

    for status in (inbox.BUSY, inbox.WAITING):
        _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: status})
        inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})
        assert [t for _, t in sends if "finished" in t] == []
    assert AGENT in inbox.watches()       # still watched — it still owes us the work


def test_evidence_ignores_work_that_predates_the_watch(
        store_file, windows, sends, monkeypatch):
    # An idle window whose last assistant turn is OLDER than the watch has not done
    # anything for THIS dispatch — reporting it would be a phantom completion.
    _confirm_idle_immediately(monkeypatch)
    _registered()
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.sessions, "transcript_for_window",
                        lambda wid: Path(f"/proj/{wid}/session.jsonl"))
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity_at",
                        lambda path: watched_since - 60)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    assert sends == []
    assert AGENT in inbox.watches()


# --- BUG 3 (live, CMX-191): did_work_since used to be keyed on CWD, not the WINDOW --
#
# `did_work_since` used to resolve `discovery.get_window_cwd_by_id(wid)` then read
# `transcripts.last_assistant_activity(cwd)` — the newest transcript in that DIRECTORY,
# not this window's own session. Two sibling agents dispatched into the same cwd meant
# one's assistant turn got credited to the OTHER's watch: `@122` was reported "finished"
# 38s after dispatch, wedged mid a `pre_tool_use`/`post_tool_use` pair, with no commit
# and no output to show for it — the orchestrator went and "verified" work that never
# happened. Same root cause as CMX-190 (`chela read`/`peek`), same fix: resolve by
# window via :mod:`chela.sessions`, and REFUSE rather than guess when the cwd is shared.
# These bypass `tick()` and drive `inbox.did_work_since` directly against real files on
# disk, with real `sessions.Pane`/`panes()` plumbing — the two functions the old cwd
# shortcut skipped entirely.

def _write_assistant_turn(path, when_epoch):
    when = datetime.fromtimestamp(when_epoch, tz=timezone.utc).isoformat().replace("+00:00", "Z")
    path.write_text(json.dumps(
        {"type": "assistant", "timestamp": when, "message": {"content": "x"}}) + "\n")
    os.utime(path, (when_epoch, when_epoch))


@pytest.fixture
def no_native_status(monkeypatch):
    """`sessions.resolve_window`'s tier 3 (the native `claude agents --json` cache)
    answered and has nothing — deterministic, so tier 4 (cwd) is always reached."""
    monkeypatch.setattr(agent_manager, "session_and_cwd_for_pid", lambda pid: (None, None))
    monkeypatch.setattr(agent_manager, "native_status_ever_fetched", lambda: True)


def test_did_work_since_refuses_a_shared_cwd_rather_than_crediting_a_sibling(
        tmp_path, monkeypatch, no_native_status):
    """@7's watch is idle; its SIBLING @8 (launched in the same cwd) wrote an assistant
    turn after `since`, @7 did not. The old cwd-keyed mechanism hands @7's watch @8's
    evidence with full confidence — resolving by window must REFUSE instead."""
    cwd = "/home/x/proj"
    proj = tmp_path / transcripts.encode_cwd(cwd)
    proj.mkdir(parents=True)
    now = time.time()
    since = now - 100
    _write_assistant_turn(proj / "mine.jsonl", now - 500)     # @7's own — before `since`
    _write_assistant_turn(proj / "sibling.jsonl", now)        # @8's — after `since`

    monkeypatch.setattr(inbox.sessions, "transcript_for_window", _REAL_TRANSCRIPT_FOR_WINDOW)
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", tmp_path)
    pane_map = {
        "@7": sessions.Pane(wid="@7", launched_in=cwd, claude_pid=101),
        "@8": sessions.Pane(wid="@8", launched_in=cwd, claude_pid=102),
    }
    monkeypatch.setattr(inbox.sessions, "panes", lambda force=False: pane_map)
    # Without this, reverting the fix to the old `discovery.get_window_cwd_by_id(wid)` +
    # `transcripts.last_assistant_activity(cwd)` body hits the REAL (unstubbed) tmux call,
    # which returns None for a synthetic wid in a test environment — short-circuiting to
    # False for the wrong reason (couldn't look anything up) rather than the right one
    # (refused a shared cwd). Reinstated so the guard actually exercises the buggy cwd
    # lookup it is meant to pin.
    monkeypatch.setattr(inbox.discovery, "get_window_cwd_by_id", lambda wid: cwd)

    # Pins the fixture: the mechanism this guards against really does pick the sibling's
    # (newer) transcript with full confidence. If this stops holding, the assertion below
    # is testing nothing.
    assert transcripts.last_assistant_activity(cwd) > since

    assert inbox.did_work_since("@7", since) is False


def test_did_work_since_still_resolves_via_cwd_with_no_sibling_to_confuse_it(
        tmp_path, monkeypatch, no_native_status):
    """Same window, no ambiguity: CMX-191 fixes the ALIASING, not the cwd fallback
    itself — a lone window in its own directory must still be detected as finished."""
    cwd = "/home/x/proj"
    proj = tmp_path / transcripts.encode_cwd(cwd)
    proj.mkdir(parents=True)
    now = time.time()
    since = now - 100
    _write_assistant_turn(proj / "mine.jsonl", now)

    monkeypatch.setattr(inbox.sessions, "transcript_for_window", _REAL_TRANSCRIPT_FOR_WINDOW)
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", tmp_path)
    pane_map = {"@7": sessions.Pane(wid="@7", launched_in=cwd, claude_pid=101)}
    monkeypatch.setattr(inbox.sessions, "panes", lambda force=False: pane_map)
    # Same reasoning as the sibling guard above: reinstated so a reverted, pre-fix
    # `did_work_since` (which calls the real, unstubbed tmux lookup) exercises the actual
    # cwd path instead of failing on an environment quirk.
    monkeypatch.setattr(inbox.discovery, "get_window_cwd_by_id", lambda wid: cwd)

    assert inbox.did_work_since("@7", since) is True


# --- BUG 4 (live, CMX-193): `finished` fired on `idle` sampled MID-TASK -----------
#
# CMX-191's own ticket said, verbatim: "do NOT change the busy→idle edge detector; this
# ticket fixes the evidence path only." That scoping was wrong. `now == IDLE` is a single
# sample of Claude Code's OWN native status, and that status can read `idle` for one tick
# in the GAP BETWEEN TWO TOOL CALLS of an agent that has many more queued — a `finished`
# fired mid-task because idle was sampled in exactly that gap, not at genuine end-of-turn.
# Both detection paths (the busy→idle edge, AND the work-since-watch evidence, which needs
# no edge at all) trusted a lone sample; both needed the stamp-then-confirm discipline
# `gone_since`/`DEATH_CONFIRM_SECONDS` already applies to a vanished window.

def test_a_busy_to_idle_edge_that_snaps_back_to_busy_is_not_reported_finished(
        store_file, windows, sends, monkeypatch):
    # Pure edge, no transcript evidence at all: a busy→idle transition alone used to fire
    # instantly. It must not, when the very next sample shows the agent busy again — proof
    # the "idle" was the gap between two tool calls, not the finish.
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})
    assert sends == []
    assert AGENT in inbox.watches()          # it still owes the orchestrator the work

    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.BUSY})
    inbox.tick(prev)
    assert sends == []
    assert AGENT in inbox.watches()


def test_a_lone_idle_sample_mid_task_is_not_reported_finished(
        store_file, windows, sends, monkeypatch):
    # The evidence path is the more dangerous one: it needs no edge at all, so a single
    # `idle` sample plus "wrote an assistant turn at some point" used to be enough on its
    # own — exactly the same shape BUG 2 fires on for a genuinely short task. This agent
    # is not done, though: the transcript has assistant activity (it is working), and the
    # very next tick catches it busy again.
    _registered()
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.sessions, "transcript_for_window",
                        lambda wid: Path(f"/proj/{wid}/session.jsonl"))
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity_at",
                        lambda path: watched_since + 5)

    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})
    assert sends == []                       # NOT reported on the lone sample
    assert AGENT in inbox.watches()

    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.BUSY})
    inbox.tick(prev)
    assert sends == []
    assert AGENT in inbox.watches()


def test_finished_still_fires_once_idle_holds_through_the_confirm_window(
        store_file, windows, sends, monkeypatch):
    # The debounce only DELAYS the report — it must not swallow a real completion. Same
    # shape as test_a_death_still_fires_once_the_settle_window_has_passed uses for
    # gone_since/DEATH_CONFIRM_SECONDS.
    _registered()
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.sessions, "transcript_for_window",
                        lambda wid: Path(f"/proj/{wid}/session.jsonl"))
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity_at",
                        lambda path: watched_since + 5)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})
    assert sends == []                       # first idle sample only stamps idle_since

    store = inbox.load()                     # ...the confirm window elapses
    store["watches"][AGENT]["idle_since"] -= inbox.IDLE_CONFIRM_SECONDS + 1
    inbox.save(store)
    inbox.tick(prev)

    assert len(sends) == 1
    assert "finished the task you dispatched" in sends[0][1]
    assert inbox.watches() == {}


def test_finished_still_fires_via_the_edge_when_no_transcript_resolves(
        store_file, windows, sends, monkeypatch):
    # The evidence path (did_work_since) is CORRECTLY blind for a window whose transcript
    # can't be resolved (CMX-191/CMX-192: hook-blind sessions, same-cwd siblings that refuse
    # rather than guess). For those, the busy->idle EDGE is the only detector — it must
    # still fire once idle is confirmed, even though the immediately-previous sample (`was`)
    # is IDLE, not BUSY, by the time the confirm window has elapsed. `no_transcript_evidence`
    # (autouse) already keeps transcript_for_window -> None here, so only the edge path can
    # carry this test.
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    prev = inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})
    assert sends == []                       # first idle sample only stamps idle_since

    store = inbox.load()                     # ...the confirm window elapses
    store["watches"][AGENT]["idle_since"] -= inbox.IDLE_CONFIRM_SECONDS + 1
    inbox.save(store)
    inbox.tick(prev)                         # `was` here is IDLE (from prev's cur), not BUSY

    assert len(sends) == 1
    assert "finished the task you dispatched" in sends[0][1]
    assert inbox.watches() == {}


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
    _confirm_idle_immediately(monkeypatch)
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


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_the_live_head_is_read_for_THIS_runs_own_pr(
        judge_state, kind, store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 4): the live read must be aimed at this run's OWN PR.

    Every other test fakes `_read_pr_checks` with a lambda that IGNORES its arguments, so
    passing `None`, a constant, or another run's url is completely invisible to them — the
    returned sha is whatever the fake decided regardless. Reading the wrong PR's head means
    comparing this verdict against a commit from a different pull request: it would drop
    valid verdicts and pass superseded ones, both silently.
    """
    seen = {}

    def _capture(pr_url, repo_dir):
        seen["pr_url"] = pr_url
        return dispatcher.CIStatus(dispatcher.CI_PASSING, _JUDGE_SHA)

    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(dispatcher, "_read_pr_checks", _capture)

    run = _verdict_run(judge_state)
    inbox.tick({}, runs=[run])

    assert seen.get("pr_url") == run["pr_url"], (
        f"the live head was read for {seen.get('pr_url')!r}, not this run's own "
        f"{run['pr_url']!r}"
    )


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_FAILED_live_read_does_not_manufacture_staleness(
        judge_state, kind, store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 4): an unreadable head is UNKNOWN, never "moved".

    `_live_judge_heads`'s docstring commits to this: a task id present in the map but
    resolving to nothing means the live read FAILED. Treating that as a mismatch would drop
    a perfectly good verdict on any transient `gh` hiccup — and this event is the only
    notification the orchestrator gets, so a dropped one is a PR that sits unmerged in
    silence, which is the exact bug this whole ticket closes.

    ⛔ Same doctrine as `epoch.is_dangling`: staleness needs BOTH halves known.
    """
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_UNKNOWN, None))

    inbox.tick({}, runs=[_verdict_run(judge_state)])

    assert len(sends) == 1, (
        "a failed live read was treated as a moved head — an unreadable sha is unknown, "
        "not proof of staleness"
    )


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_the_live_head_is_read_against_a_REAL_repo_dir(
        judge_state, kind, store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 5): `repo_dir` must actually point somewhere.

    `dispatcher._read_pr_checks` returns CI_UNKNOWN with NO sha whenever `repo_dir` is
    falsy (`dispatcher.py:1459`). Combined with the (correct) rule that an unreadable head
    is UNKNOWN rather than moved, a blank repo_dir makes every verdict deliver
    unconditionally — the staleness check silently never runs at all, while every test
    that fakes `_read_pr_checks` keeps passing because the fake ignores the argument.
    """
    seen = {}

    def _capture(pr_url, repo_dir):
        seen["repo_dir"] = repo_dir
        return dispatcher.CIStatus(dispatcher.CI_PASSING, _JUDGE_SHA)

    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(dispatcher, "_read_pr_checks", _capture)

    inbox.tick({}, runs=[_verdict_run(judge_state)])

    assert seen.get("repo_dir") == "/repo/chelamux", (
        f"the live head was read with repo_dir={seen.get('repo_dir')!r} — it must be the "
        "run's own workflow dir; falsy means _read_pr_checks returns no sha at all, so "
        "nothing is ever compared and every verdict delivers unconditionally"
    )


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_verdict_with_NO_judged_sha_is_unknown_not_stale(
        judge_state, kind, store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 5): the JUDGED half of "staleness needs BOTH halves known".

    Its twin (`..._FAILED_live_read_does_not_manufacture_staleness`) pins the LIVE half. I
    cited `epoch.is_dangling`'s both-halves doctrine by name in that commit and then guarded
    only one of the two halves — so dropping `judged_sha and` from the condition turned a
    verdict carrying no judged commit (a legacy queued event, or a row whose judge_sha was
    never stamped) into a permanent "stale", silently retiring it.
    """
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, _SUPERSEDING_SHA))

    inbox.tick({}, runs=[_verdict_run(judge_state, judge_sha=None)])

    assert len(sends) == 1, (
        "a verdict with no judged sha was dropped as stale — an absent half is UNKNOWN, "
        "and only two KNOWN halves that differ prove staleness"
    )


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_the_judged_sha_comes_from_the_PAYLOAD_not_the_live_row(
        judge_state, kind, store_file, windows, sends, monkeypatch, caplog):
    """🔴 GUARD (CMX-197 round 5): the payload is the RECORD; the row is the present.

    ⭐ This is the whole reason `payload['judge_sha']` exists. Re-read the judged sha off
    the run row and BOTH sides of the comparison come from the same live source, so they
    can never disagree — the check becomes a no-op that always delivers, restoring exactly
    the bug this ticket closes, while looking like a working comparison.

    Shape: the verdict is queued for sha A; a new judge then starts on sha B, so the ROW's
    judge_sha is now B and the live head is B too. Reading the row: B == B, delivered.
    Reading the payload: A != B, dropped.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    inbox.tick({}, runs=[_verdict_run(judge_state)])          # queued with judged sha A
    assert inbox.load()["queue"][0]["payload"]["judge_sha"] == _JUDGE_SHA

    moved_on = _verdict_run(judge_state, judge_sha=_SUPERSEDING_SHA)   # row now says B
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, _SUPERSEDING_SHA))

    with caplog.at_level("WARNING"):
        inbox.tick({}, runs=[moved_on])

    assert sends == [], (
        "the judged sha was re-read off the live row, so both sides came from the same "
        "source and the comparison could never fail"
    )
    assert f"dropping stale {kind}" in caplog.text
    # ⛔ ...NAMING both commits. This drop is silent to the operator — log-only, no event_log
    # row and no push — so this line is the ONLY forensic record that a verdict was retired.
    # "PR head moved past the judged commit" without the shas cannot answer the one question
    # it exists for: WHICH commit was judged, and what is live now.
    # ⛔ NOT "both shas appear somewhere" — that passes with them swapped, or listed with
    # no relation. The reason exists to say WHICH commit was judged and WHAT IS LIVE NOW,
    # so the pair must render in that order, together.
    assert f"{_JUDGE_SHA[:12]} -> {_SUPERSEDING_SHA[:12]}" in caplog.text, (
        f"the drop reason must read judged -> live. Got: {caplog.text!r}"
    )


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_judge_verdict_is_ATTRIBUTED_to_the_agents_window(
        judge_state, kind, store_file, windows, monkeypatch):
    """🔴 GUARD (CMX-197 round 6): the event must carry the run's `wid`.

    This PR's own Feed rule — `buildLanes` keeps a gone lane out of the graveyard for the
    judge kinds — keys on the event's wid. Emit it with `wid=None` and the verdict is
    attributed to nobody: the lane it was meant to keep alive falls into the graveyard, and
    the JS guard added for exactly that behaviour still passes, because it constructs its
    own event with a wid rather than reading one the inbox produced.

    ⚠️ Producer and consumer each tested against their own copy of the contract — the same
    shape as round 3's emitted-kind finding.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})     # busy → it queues, so we can read it
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_verdict_run(judge_state)])

    queued = inbox.load()["queue"]
    assert len(queued) == 1
    assert queued[0]["wid"] == "@6", (
        f"the verdict was attributed to {queued[0]['wid']!r}, not the run's own window — "
        "the Feed's lane rule keys on this"
    )


def test_a_BLOCKED_verdict_never_uses_the_judge_verdict_path(
        store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 6): BLOCKED is already reported, and must not double-fire.

    A blocked verdict sends the run back through `run_changes_requested` — the path that
    has always worked and is the reason this ticket was scoped to the OTHER two states.
    Widening the tuple to include `J_BLOCKED` gives the orchestrator two notifications for
    one event, and the judge-kind one claims a verdict "on a run still sitting in
    awaiting_review" about a run that is on its way out of it.

    ⛔ Not parametrized: the point IS that this third state is excluded, so it must be
    named explicitly rather than swept into the both-kinds table.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_verdict_run(judge.J_BLOCKED)])

    kinds = [e["kind"] for e in inbox.load()["queue"]]
    assert "run_judge_clean" not in kinds and "run_judge_cannot_verify" not in kinds, (
        f"a BLOCKED verdict came through the judge-verdict path — it already fires "
        f"run_changes_requested. Queued: {kinds}"
    )


def test_a_run_ARRIVING_at_awaiting_review_still_gets_the_plain_review_edge(
        store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 7): the edge this ticket is built ON must still fire.

    The dedup mark became `status:judge_state` so a settling verdict re-announces. Its
    "already announced" test has TWO halves, per the PR's own comment — a fresh task id, OR
    one arriving at awaiting_review from another status. Collapse it to `bool(prev_mark)`
    and the second half dies: a run the orchestrator has already seen RUNNING never
    announces its arrival at review at all.

    ⛔ That is the ORIGINAL notification, working since long before this ticket. Adding a
    new one must not cost the old one — and no test on this branch drove a status change.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    running = dict(_verdict_run(), status="running", judge_state="")
    inbox.tick({}, runs=[running])                 # seen once, while it was still running
    assert [e["kind"] for e in inbox.load()["queue"]] == []

    arrived = dict(_verdict_run(), judge_state="")   # now at awaiting_review, judge not run
    inbox.tick({}, runs=[arrived])

    assert [e["kind"] for e in inbox.load()["queue"]] == ["run_review"], (
        "a run arriving at awaiting_review from another status lost its review edge"
    )


@pytest.mark.parametrize("other_status", ["running", "changes_requested", "done"])
def test_a_clean_verdict_is_announced_ONLY_while_the_run_SITS_in_awaiting_review(
        other_status, store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 7): the status half of the emit condition — for
    `run_judge_clean`.

    Every comment and docstring on this feature says the same thing — "the judge's own
    verdict on a run still SITTING in awaiting_review". Drop the status test and a stale
    `judge_state` left on a row that has since moved on fires a verdict announcement about
    a run that is running again, already sent back, or finished: the orchestrator is told a
    PR is "clean and MERGEABLE" when it is not even under review.

    ⚠️ CMX-229 round: this test used to be shared with `run_judge_cannot_verify` via
    `JUDGE_KINDS` (CMX-197 round 4's "every judge kind must cover every site" rule). It no
    longer is, ON PURPOSE: Objective 1 makes `cannot_verify` diverge from `clean` on
    EXACTLY this axis — see its own deliberate, documented counterpart right below,
    `test_a_cannot_verify_verdict_IS_announced_off_awaiting_review`.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[dict(_verdict_run(judge.J_CLEAN), status=other_status)])

    kinds = [e["kind"] for e in inbox.load()["queue"]]
    assert "run_judge_clean" not in kinds, (
        f"a clean verdict fired for a run in {other_status!r}. Queued: {kinds}"
    )


@pytest.mark.parametrize("other_status", ["running", "changes_requested", "done"])
def test_a_cannot_verify_verdict_IS_announced_off_awaiting_review(
        other_status, store_file, windows, sends, monkeypatch):
    """⚖️🔔 CMX-229 Objective 1 — the deliberate counterpart to the CLEAN guard above.

    Measured live on CMX-227: the judge's CAS-refused path sets `J_CANNOT_VERIFY` on a run
    that has ALREADY left `awaiting_review` (a merge, a fresh review, a rework respawn can
    all race it there first), and `run_judge_cannot_verify` used to never fire for it — the
    ONLY place this outcome could ever surface (`chela events --type run_judge_cannot_verify`
    showed nothing; the run row was the sole, silent record). It must fire regardless of
    what the run's status became.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[dict(_verdict_run(judge.J_CANNOT_VERIFY), status=other_status)])

    kinds = [e["kind"] for e in inbox.load()["queue"]]
    assert "run_judge_cannot_verify" in kinds, (
        f"a cannot_verify verdict did NOT fire for a run already in {other_status!r} — "
        f"Objective 1 requires it to. Queued: {kinds}"
    )


def test_the_judge_state_mark_is_scoped_to_awaiting_review_ONLY(
        store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 8): every OTHER status keeps the bare status as its mark —
    EXCEPT `cannot_verify`, carved out ON PURPOSE by CMX-229 Objective 1 (see its
    counterpart right below, `test_cannot_verify_churn_off_awaiting_review_DOES_reannounce`).

    Widening `mark` to `f"{status}:{judge_state}"` unconditionally makes the judge's own
    churn re-announce states that have nothing to do with it: a run parked at needs_human
    while a judge re-runs goes J_RUNNING → J_CLEAN → …, and each transition mints a NEW
    mark, so the orchestrator is pinged "NEEDS A HUMAN" again and again for one unchanged
    situation.

    ⛔ The re-announce is deliberately scoped to `awaiting_review` for RUNNING/CLEAN —
    everywhere else the status IS the news and the judge is noise for those two.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    parked = dict(_verdict_run(), status="needs_human", judge_state=judge.J_BLOCKED)
    inbox.tick({}, runs=[parked])
    first = [e["kind"] for e in inbox.load()["queue"]]
    assert first == ["run_needs_human"]

    # the judge re-runs on the same parked row — RUNNING/CLEAN churn is noise here
    for churn in (judge.J_RUNNING, judge.J_CLEAN):
        inbox.tick({}, runs=[dict(parked, judge_state=churn)])

    assert [e["kind"] for e in inbox.load()["queue"]] == first, (
        "judge churn (RUNNING/CLEAN) re-announced a status that never changed — the "
        "judge_state half of the mark must apply to awaiting_review only for these"
    )


def test_cannot_verify_churn_off_awaiting_review_DOES_reannounce(
        store_file, windows, sends, monkeypatch):
    """⚖️🔔 CMX-229 Objective 1 — the deliberate counterpart to the guard above. A run
    parked at `needs_human` (or anywhere else off `awaiting_review`) whose `judge_state`
    churns to `cannot_verify` DOES mint a new mark and DOES re-announce — that transition
    is itself the news the guard above says everywhere else is noise for RUNNING/CLEAN."""
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    parked = dict(_verdict_run(), status="needs_human", judge_state=judge.J_BLOCKED)
    inbox.tick({}, runs=[parked])
    assert [e["kind"] for e in inbox.load()["queue"]] == ["run_needs_human"]

    inbox.tick({}, runs=[dict(parked, judge_state=judge.J_CANNOT_VERIFY)])

    assert [e["kind"] for e in inbox.load()["queue"]] == [
        "run_needs_human", "run_judge_cannot_verify",
    ], "a cannot_verify churn off awaiting_review must re-announce (CMX-229 Objective 1)"


@pytest.mark.parametrize("other_status", ["running", "changes_requested", "done", "needs_human"])
def test_a_blocked_race_verdict_IS_announced_regardless_of_status(
        other_status, store_file, windows, sends, monkeypatch):
    """⚖️🧊 CMX-239 — the twin of the CANNOT_VERIFY test above, for `J_BLOCKED_RACE`.

    `judge.judge_run`'s CAS-refused path on a BLOCKING verdict now records `J_BLOCKED_RACE`
    (not `J_CANNOT_VERIFY`, which would downgrade a CONFIRMED finding to a shrug). It must
    fire `run_judge_blocked_race` regardless of what the run's status became — including
    `changes_requested`, since `J_BLOCKED_RACE` (unlike plain `J_BLOCKED`) can only ever
    mean the row never actually recorded the send-back.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[dict(_verdict_run(judge.J_BLOCKED_RACE), status=other_status)])

    kinds = [e["kind"] for e in inbox.load()["queue"]]
    assert "run_judge_blocked_race" in kinds, (
        f"a blocked_race verdict did NOT fire for a run already in {other_status!r}. "
        f"Queued: {kinds}"
    )


def test_a_blocked_race_verdict_is_never_confused_with_an_ordinary_blocked_run(
        store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-239): plain `J_BLOCKED` — the ORDINARY outcome, left on a row long after
    it settles (through rework rounds, even through an eventual `needs_human` escalation) —
    must NEVER trip the `run_judge_blocked_race` branch. Only the judge's own, distinct
    `J_BLOCKED_RACE` value may. Collapsing the two would make an unrelated LATER status
    change on an ordinary blocked run misread as the CAS-refused race."""
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    parked = dict(_verdict_run(), status="needs_human", judge_state=judge.J_BLOCKED)
    inbox.tick({}, runs=[parked])

    kinds = [e["kind"] for e in inbox.load()["queue"]]
    assert kinds == ["run_needs_human"]
    assert "run_judge_blocked_race" not in kinds


def test_blocked_race_churn_off_its_status_DOES_reannounce(
        store_file, windows, sends, monkeypatch):
    """⚖️🧊 CMX-239 — the twin of `test_cannot_verify_churn_off_awaiting_review_DOES_reannounce`.
    A run parked at `needs_human` whose `judge_state` churns from an ordinary `blocked` to
    `blocked_race` DOES mint a new mark and DOES re-announce — proving the mark computation
    (not just the branch dispatch) actually tracks `J_BLOCKED_RACE` off its terminal status."""
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    parked = dict(_verdict_run(), status="needs_human", judge_state=judge.J_BLOCKED)
    inbox.tick({}, runs=[parked])
    assert [e["kind"] for e in inbox.load()["queue"]] == ["run_needs_human"]

    inbox.tick({}, runs=[dict(parked, judge_state=judge.J_BLOCKED_RACE)])

    assert [e["kind"] for e in inbox.load()["queue"]] == [
        "run_needs_human", "run_judge_blocked_race",
    ], "a blocked_race churn off its status must re-announce (CMX-239)"


def test_the_blocked_race_reason_is_EXCERPTED_into_the_summary(
        store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-239 round 6): the twin of the CANNOT_VERIFY excerpt guard above, for
    `J_BLOCKED_RACE`.

    Round 5's judge triaged ~20 of these live and found the ones without an excerpted
    reason each cost a `gh pr view` to act on — the summary is the one line typed AT the
    orchestrator's prompt, so a `run_judge_blocked_race` that drops the excerpt is exactly
    as bad as the CMX-197 round 8 bug this mirrors, and MORE urgent: this branch fires when
    a guard survived corruption on a commit that may already have shipped.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_verdict_run(judge.J_BLOCKED_RACE)])

    summary = inbox.load()["queue"][0]["summary"]
    assert _LONG_DETAIL[:40] in summary, "an excerpt of the reason must reach the operator"
    assert _LONG_DETAIL not in summary, (
        "the WHOLE reason was pasted into a summary that gets typed at the prompt — it "
        "belongs in the payload, which already carries it in full"
    )
    assert len(summary) < len(_LONG_DETAIL) + 200


def test_the_cannot_verify_reason_is_EXCERPTED_into_the_summary(
        store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 8): the summary is one line TYPED AT the prompt.

    `_event`'s summary is delivered by typing it into the orchestrator's session
    (`sanitize_prompt`, CMX-79), so pasting a judge's whole reason — multi-line, arbitrary
    length, containing whatever a failing suite printed — is a different thing from
    recording it. The payload is where the full text belongs (guarded in round 7); the
    summary gets a bounded excerpt.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_verdict_run(judge.J_CANNOT_VERIFY)])

    summary = inbox.load()["queue"][0]["summary"]
    assert _LONG_DETAIL[:40] in summary, "an excerpt of the reason must reach the operator"
    assert _LONG_DETAIL not in summary, (
        "the WHOLE reason was pasted into a summary that gets typed at the prompt — it "
        "belongs in the payload, which already carries it in full"
    )
    assert len(summary) < len(_LONG_DETAIL) + 200


# --- CMX-247: the needs_human summary must say WHY, not always the same fixed guess ------
#
# `dispatcher._escalate` is the only writer of `needs_human`, and its call sites hand it
# DIFFERENT reasons — a spent rework budget, checks stuck pending, a rework that could not
# re-attach its worktree. Before this, `inbox.run_needs_human` always said "the PR still
# fails review", which is only true for the first of those.

def test_run_needs_human_summary_reflects_the_actual_escalation_reason(store_file, windows,
                                                                        sends, monkeypatch):
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    rework_cap = dict(_verdict_run(), task_id="T1", status="needs_human", rework_count=2,
                      review_history=json.dumps([{"verdict": "BLOCKING"},
                                                  {"verdict": "BLOCKING"}]),
                      last_error="rework cap reached (2/2) — the PR still fails review. "
                                 "Branch, worktree and PR are preserved.\n\n"
                                 "Recommendation: fix it yourself and `chela reopen`.")
    stuck_checks = dict(_verdict_run(), task_id="T2", status="needs_human",
                        last_error="the checks on this PR have not settled in 6h — they "
                                   "are not running, they are STUCK.\n\n"
                                   "Recommendation: approve the pending gate.")
    no_branch = dict(_verdict_run(), task_id="T3", status="needs_human",
                     last_error="rework: the run row has no branch — nothing to re-enter")
    # A SHORT first paragraph with a Recommendation attached: short enough that the
    # excerpt limit (60 chars) never kicks in, so this fixture is only clean if the
    # "\n\n" split itself is doing the work — unlike T1/T2 above, where the first
    # paragraph already exceeds the excerpt limit and would hide a missing split.
    short_with_recommendation = dict(_verdict_run(), task_id="T4", status="needs_human",
                     last_error="rework: no branch\n\nRecommendation: fix it yourself.")
    # A single-paragraph reason strictly BETWEEN 60 and 90 chars (68). This is the fixture
    # that pins the excerpt bound to the named SUMMARY_TITLE_CHARS constant (60) rather than
    # merely "shorter than whatever the fixture happens to be": a limit of 90 would let this
    # one through untruncated, so the exact-match assertion below only passes at limit=60.
    boundary_reason = "external approval is stuck on someone who is out of office this week"
    assert 60 < len(boundary_reason) < 90
    boundary = dict(_verdict_run(), task_id="T5", status="needs_human",
                     last_error=boundary_reason)

    # 🔴 GUARD (judge round 3): a real `_escalate` reason is routinely MULTI-LINE within its
    # own first paragraph — dispatcher.py:4302 interpolates raw git stderr, which wraps. This
    # first paragraph has an embedded "\n" but no "\n\n" before the Recommendation, so
    # splitting on a single "\n" (wrong) silently drops the second line while splitting on
    # "\n\n" (right) keeps it. Every other fixture above has a single-line first paragraph,
    # so none of them can tell "\n" and "\n\n" apart.
    multiline_first_line = "rework: could not attach a worktree for cmx-247"
    multiline_second_line = "fatal: 'cmx-247' is already checked out at '/tmp/other-wt'"
    multiline_paragraph = multiline_first_line + "\n" + multiline_second_line
    multiline_reason = dict(_verdict_run(), task_id="T6", status="needs_human",
                             last_error=multiline_paragraph +
                             "\n\nRecommendation: remove the stale worktree and retry.")

    # 🔴 GUARD (judge round 3): rework_count and len(review_history) are two DIFFERENT facts
    # that must each keep their own label. The only prior fixture (T1) sets both to 2, so
    # swapping the two interpolations is invisible there. Here they differ (1 vs 3), so a
    # swap surfaces as "reworks: 3 · verdicts on the row: 1" instead of the correct order.
    mismatched_counts = dict(_verdict_run(), task_id="T7", status="needs_human", rework_count=1,
                              review_history=json.dumps([{"verdict": "BLOCKING"},
                                                          {"verdict": "CANNOT_VERIFY"},
                                                          {"verdict": "BLOCKING"}]),
                              last_error="the checks on this PR never settled")

    # 🔴 GUARD (judge round 5): `_format_escalation` takes `recommendation` and `options`
    # INDEPENDENTLY (dispatcher.py:4238) — a real escalation can carry an Options: block
    # with NO Recommendation: at all. Every fixture above that has a trailing block pairs
    # it with "Recommendation:", so keying the paragraph split on that literal (instead of
    # the bare "\n\n" boundary) is invisible to all of them. This fixture is short enough
    # to stay under SUMMARY_TITLE_CHARS if — and only if — the split actually fires on the
    # blank line; a split keyed on "Recommendation:" leaves the Options: block attached and
    # pushes the joined string past the excerpt limit.
    options_only = dict(_verdict_run(), task_id="T8", status="needs_human",
                         last_error="budget approval never arrived\n\n"
                                    "Options:\n  - ping finance on slack\n"
                                    "  - approve manually via `chela reopen`")

    inbox.tick({}, runs=[rework_cap, stuck_checks, no_branch, short_with_recommendation,
                          boundary, multiline_reason, mismatched_counts, options_only])

    by_entry = {e["payload"]["task_id"]: e for e in inbox.load()["queue"]}
    by_task = {tid: e["summary"] for tid, e in by_entry.items()}
    assert set(by_task) == {"T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8"}

    # 🔴 GUARD (judge round 5): an Options:-only escalation (no Recommendation: paragraph)
    # must still have its trailing block excluded from the summary — exact match, since the
    # Options: block sits early enough in the joined string that a substring check on the
    # reason alone would stay green even with the whole block pasted in.
    assert "· budget approval never arrived —" in by_task["T8"], (
        "the reason must reach the summary unchanged when the paragraph split works — "
        "this only fails if the split is keyed on 'Recommendation:' instead of the bare "
        "paragraph boundary, in which case an Options:-only last_error never splits at all"
    )
    assert "Options:" not in by_task["T8"], (
        "an Options: block with no Recommendation: leaked into the summary — the "
        "paragraph split must key on the blank line, not the literal 'Recommendation:'"
    )
    assert "ping finance" not in by_task["T8"]

    assert "the PR still fails review" in by_task["T1"]

    # 🔴 GUARD: the payload must carry the FULL last_error, Recommendation/Options included
    # — the summary excerpts the reason, but the payload is not itself excerpted. If this
    # key is ever emptied, the excerpt stops being an excerpt and becomes data loss.
    assert by_entry["T1"]["payload"]["last_error"] == rework_cap["last_error"], (
        "the payload's last_error must be the run's full, unmodified last_error"
    )
    assert by_entry["T2"]["payload"]["last_error"] == stuck_checks["last_error"]

    # 🔴 GUARD: the reason is excerpted to EXACTLY SUMMARY_TITLE_CHARS (60), cut on a word
    # boundary, and marked with an ellipsis — not "some limit shorter than this fixture"
    # (caught a mutation to `limit = 90`) and not a raw mid-word slice with no ellipsis
    # (caught a mutation dropping the `.rsplit(" ", 1)[0] + "…"`). This literal is computed
    # independently of production code: reason[:60].rsplit(" ", 1)[0] + "…".
    assert "rework cap reached 2/2 — the PR still fails review.…" in by_task["T1"], (
        "the reason must be cut at exactly SUMMARY_TITLE_CHARS, on a word boundary, "
        "with a trailing ellipsis — got a differently-bounded or unmarked cut instead"
    )

    # 🔴 GUARD: the counts must survive alongside the reason — `reworks: N · verdicts on
    # the row: M` are useful on their own and a refactor of the reason must not drop them.
    assert "reworks: 2" in by_task["T1"], (
        "the rework count must still be in the summary alongside the reason"
    )
    assert "verdicts on the row: 2" in by_task["T1"], (
        "the verdict-history count must still be in the summary alongside the reason"
    )

    # 🔴 GUARD: the reason is EXCERPTED — the whole first paragraph must not be pasted
    # whole into the one line typed at the prompt (mirrors the cannot_verify excerpt
    # guard above, for this call site).
    assert "Branch, worktree and PR are preserved" not in by_task["T1"], (
        "the whole first paragraph was pasted into the summary instead of being "
        "excerpted to SUMMARY_TITLE_CHARS"
    )

    assert "checks on this PR have not settled" in by_task["T2"]
    assert "the PR still fails review" not in by_task["T2"], (
        "a run escalated for STUCK CHECKS must not be told it 'still fails review' — "
        "that sentence is a claim about a review verdict that never happened"
    )

    assert "no branch" in by_task["T3"]
    assert "the PR still fails review" not in by_task["T3"], (
        "a run escalated for a MISSING BRANCH must not be told it 'still fails review'"
    )
    # 🔴 GUARD (judge round 4): a reason that FITS under SUMMARY_TITLE_CHARS must reach the
    # summary VERBATIM and UNMARKED — no trailing "…". The ellipsis is this PR's own signal
    # that the reason was cut; slapping it on every reason (even ones that fit whole) makes
    # the signal lie about completeness in exactly the way CMX-247 exists to prevent. An
    # exact match (not a substring) is required to catch a mutation that appends "…" to a
    # reason that already fits — a substring check like "no branch" in ... cannot see it.
    # (Not also asserting "…" not in by_task["T3"] as a whole-line check: TRACKER_LINE — the
    # fixture title every task shares — already carries a literal "…" of its own, so that
    # check would fail for reasons that have nothing to do with the reason excerpt.)
    assert "· rework: the run row has no branch — nothing to re-enter —" in by_task["T3"], (
        "a reason that fits under SUMMARY_TITLE_CHARS must reach the summary unchanged, "
        "with no ellipsis appended"
    )

    # Only the reason (first paragraph) reaches the summary — Recommendation/Options stay
    # in the payload's last_error, not pasted into the one line typed at the prompt.
    assert "Recommendation:" not in by_task["T1"]
    assert "Recommendation:" not in by_task["T2"]

    # 🔴 GUARD: same rule, but with a reason SHORT enough that the excerpt limit alone
    # cannot be hiding a missing "\n\n" split (T1/T2's first paragraphs are both already
    # over SUMMARY_TITLE_CHARS, so they'd stay clean even without the split).
    assert "no branch" in by_task["T4"]
    assert "Recommendation:" not in by_task["T4"], (
        "a short reason (under SUMMARY_TITLE_CHARS) must still exclude a trailing "
        "Recommendation — this is only provable when the excerpt limit itself can't "
        "be the thing hiding it"
    )
    # 🔴 GUARD (judge round 4): same as T3's guard above, but on the OTHER fixture that
    # exercises the short (`len(reason) <= limit`) path — an exact match, since "no branch"
    # in ... would stay green even if a mutation appended "…" to this fixture's reason too.
    assert "· rework: no branch —" in by_task["T4"], (
        "a reason that fits under SUMMARY_TITLE_CHARS must reach the summary unchanged, "
        "with no ellipsis appended"
    )

    # 🔴 GUARD: T5's 68-char reason sits strictly between SUMMARY_TITLE_CHARS (60) and a
    # too-large limit (90) that would otherwise still pass every other fixture in this test.
    # At the real limit it must be visibly cut; a widened limit would let it through whole.
    assert "external approval is stuck on someone who is out of office…" in by_task["T5"], (
        "a reason longer than SUMMARY_TITLE_CHARS but shorter than 90 chars must still "
        "be excerpted — this only fails if the limit is not exactly SUMMARY_TITLE_CHARS"
    )
    assert boundary_reason not in by_task["T5"], (
        "the full boundary reason leaked through untruncated — the excerpt limit is "
        "wider than SUMMARY_TITLE_CHARS"
    )

    # 🔴 GUARD: the split must be on the PARAGRAPH boundary ("\n\n"), not the first line
    # ("\n") — this is the exact excerpt the production code computes for the multi-line
    # fixture above: reason[:60].rsplit(" ", 1)[0] + "…" where reason is BOTH lines joined
    # by a single space (via the whitespace collapse). Splitting on "\n" instead would stop
    # at `multiline_first_line` alone (47 chars, no ellipsis) and never reach "fatal:".
    assert "rework: could not attach a worktree for cmx-247 fatal:…" in by_task["T6"], (
        "the excerpt must include content from the SECOND line of the first paragraph — "
        "this only fails if the reason was split on the first newline instead of the "
        "first blank line"
    )
    assert "fatal:" in by_task["T6"], (
        "the second line of the first paragraph never reached the summary — the reason "
        "was split on '\\n' instead of '\\n\\n'"
    )

    # 🔴 GUARD: reworks and verdicts-on-the-row are two different facts with different
    # numbers here (1 vs 3) — a swap of the two interpolations produces the numbers under
    # the wrong labels instead of failing outright, so an exact-match on each label is
    # required to catch it.
    assert "reworks: 1" in by_task["T7"], (
        "the rework count (1) must appear under the 'reworks' label — got the verdict "
        "count instead, meaning the two interpolations were swapped"
    )
    assert "verdicts on the row: 3" in by_task["T7"], (
        "the verdict-history count (3) must appear under the 'verdicts on the row' "
        "label — got the rework count instead, meaning the two interpolations were swapped"
    )
    assert "reworks: 3" not in by_task["T7"]
    assert "verdicts on the row: 1" not in by_task["T7"]


def test_run_needs_human_reason_falls_back_when_last_error_is_empty(store_file, windows,
                                                                     sends, monkeypatch):
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    bare = dict(_verdict_run(), task_id="T1", status="needs_human", last_error=None)
    # 🔴 GUARD (judge round 5): the production code deliberately tests the READABLE reason
    # (`if not reason`, after coercion) rather than `last_error is None` — a row can reach
    # `needs_human` with `last_error=""` (or a first paragraph that collapses to nothing
    # but whitespace), and that must fall back exactly like a null column does. No fixture
    # anywhere else in this file sets `last_error=""`, so an `is None` check is invisible
    # to all of them.
    empty_string = dict(_verdict_run(), task_id="T2", status="needs_human", last_error="")
    whitespace_only = dict(_verdict_run(), task_id="T3", status="needs_human",
                            last_error="   \n\n  ")
    inbox.tick({}, runs=[bare, empty_string, whitespace_only])

    by_task = {e["payload"]["task_id"]: e["summary"] for e in inbox.load()["queue"]}
    assert set(by_task) == {"T1", "T2", "T3"}
    for tid in ("T1", "T2", "T3"):
        assert "no reason recorded" in by_task[tid], (
            f"{tid} has no readable reason and must fall back — got {by_task[tid]!r}"
        )
        assert "the PR still fails review" not in by_task[tid]


# --- the single-run blind spot -----------------------------------------------------------
#
# 🔴 GUARDS (CMX-197 round 9). EVERY judge test on this branch drives exactly ONE run, so
# code that picks "any" run instead of "this" run is invisible to all of them: with one
# entry, `d.get(task_id)` and `next(iter(d.values()))` are the same value, and any
# `if <scope>` collapses to `if True`. The fixture's CARDINALITY was the blind spot —
# distinct from the earlier ones, where a fixture FIELD was missing.

_OTHER_SHA = "cccccccccccccccccccccccccccccccccccccccc"


def _fleet():
    """Three runs that differ in every dimension the scoping rules read.

    Only `T1` qualifies: awaiting_review AND a settled verdict. `T2` is at
    awaiting_review with the judge still RUNNING; `T3` has a settled verdict but has
    already moved on to `running`.
    """
    a = _verdict_run(judge.J_CLEAN, task_id="T1",
                     pr_url="https://github.com/x/y/pull/9", judge_sha=_JUDGE_SHA)
    b = _verdict_run(judge.J_RUNNING, task_id="T2",
                     pr_url="https://github.com/x/y/pull/10", judge_sha=_SUPERSEDING_SHA)
    c = _verdict_run(judge.J_CLEAN, task_id="T3", status="running",
                     pr_url="https://github.com/x/y/pull/11", judge_sha=_OTHER_SHA)
    return [a, b, c]


def test_only_the_QUALIFYING_run_has_its_head_read(store_file, windows, sends, monkeypatch):
    """🔴 The candidate set is scoped on BOTH axes — settled verdict AND still sitting in
    awaiting_review. Unscope either and `gh` is called for runs that need nothing, which the
    docstring promises against ("a quiet fleet costs zero extra gh calls"); with one run in
    the fixture, both `if`s collapse to `if True` unnoticed."""
    asked = []

    def _capture(pr_url, repo_dir):
        asked.append(pr_url)
        return dispatcher.CIStatus(dispatcher.CI_PASSING, _JUDGE_SHA)

    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(dispatcher, "_read_pr_checks", _capture)

    inbox.tick({}, runs=_fleet())

    assert asked == ["https://github.com/x/y/pull/9"], (
        f"the live head was read for {asked} — only the run with a SETTLED verdict still "
        "SITTING in awaiting_review qualifies"
    )


def test_TWO_qualifying_verdicts_are_each_resolved_against_their_OWN_pr(
        store_file, windows, sends, monkeypatch):
    """🔴 Both halves of the lookup must be keyed by task_id — the run resolved from the
    candidate id, and the live sha resolved from the map.

    ⚠️ One qualifying candidate is not enough to see this: with a single entry,
    `d.get(task_id)` and `next(iter(d.values()))` are the same object, which is why the
    first version of this test passed under both mutations. TWO runs must qualify, with
    DIFFERENT heads, so "any" and "this" diverge.

    Both verdicts here match their own head, so both must deliver. Resolve either half by
    "the first entry" and the second run is compared against the FIRST run's commit —
    mismatch, dropped, and a perfectly good verdict is silently retired.
    """
    a = _verdict_run(judge.J_CLEAN, task_id="T1",
                     pr_url="https://github.com/x/y/pull/9", judge_sha=_JUDGE_SHA)
    b = _verdict_run(judge.J_CLEAN, task_id="T2",
                     pr_url="https://github.com/x/y/pull/10", judge_sha=_SUPERSEDING_SHA)
    heads = {"https://github.com/x/y/pull/9": _JUDGE_SHA,          # T1 unchanged
             "https://github.com/x/y/pull/10": _SUPERSEDING_SHA}   # T2 unchanged, DIFFERENT

    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, heads[pr_url]))

    inbox.tick({}, runs=[a, b])
    inbox.tick({}, runs=[a, b])      # the inbox delivers one event per tick — drain both

    assert len(sends) == 2, (
        f"both verdicts match their OWN head and must deliver; got {len(sends)} — a "
        "verdict was compared against another run's commit, or another run's row was read"
    )


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_verdict_summary_names_BOTH_the_run_and_its_PR(
        judge_state, kind, store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 10): the 2x2 — label AND pr ref, on BOTH kinds.

    The summary is one line arriving at a busy operator's prompt, and it has exactly two
    handles: `label` (the branch name, what a human recognises) and `ref` (the PR the
    verdict is ABOUT). Drop either and the notification is unactionable in a different way
    — no label and you cannot tell WHICH of several in-flight runs settled; no PR ref and
    you are told something is mergeable with nowhere to go and merge it.

    ⚠️ Both were asserted for `run_review` and for the clean verdict's label only, so each
    kind had a different half unpinned. Asserting the pair over both kinds is the whole
    guard — the same shape that has recurred all ticket: cover the matrix, not the cell
    that was named.
    """
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, _JUDGE_SHA))

    run = _verdict_run(judge_state)
    inbox.tick({}, runs=[run])

    assert len(sends) == 1
    summary = sends[0][1]
    assert run["task_id"] in summary, (
        f"the {kind} summary does not say WHICH run settled — with several in flight, an "
        f"unlabelled verdict cannot be acted on. Got: {summary!r}"
    )
    assert run["pr_url"] in summary, (
        f"the {kind} summary does not name the PR it is about. Got: {summary!r}"
    )


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
@pytest.mark.parametrize("ci_state", [
    dispatcher.CI_PENDING, dispatcher.CI_FAILING, dispatcher.CI_NONE, dispatcher.CI_PASSING,
])
def test_the_staleness_check_is_INDEPENDENT_of_the_prs_ci_state(
        ci_state, judge_state, kind, store_file, windows, sends, monkeypatch, caplog):
    """🔴 GUARD (CMX-197 round 12): a head is a head, whatever CI thinks of it.

    ⚠️ Every judge test on this branch fakes `CIStatus(CI_PASSING, sha)`, so `ci.state` was
    a fixture CONSTANT across the whole suite — the round-9 blind spot one axis over: not
    the fixture's cardinality, its UNIFORMITY. Narrowing the head read to
    `ci.state == CI_PASSING` was therefore invisible to all of them.

    ⭐ And PENDING is not an edge case here, it is the LIKELY one: the superseding push
    that makes a verdict stale is a brand-new commit whose checks have not finished yet. A
    check gated on green would go quiet in exactly the situation it exists to catch, and
    announce "clean and MERGEABLE" about a commit nobody judged.
    """
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(ci_state, _SUPERSEDING_SHA))

    with caplog.at_level("WARNING"):
        inbox.tick({}, runs=[_verdict_run(judge_state)])

    assert sends == [], (
        f"a superseded verdict was announced because CI was {ci_state!r} — the head "
        "comparison must not depend on what CI thinks of the commit"
    )
    assert f"dropping stale {kind}" in caplog.text


def test_a_cannot_verify_with_NO_reason_never_types_the_word_None(
        store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 12): `judge_detail` is nullable, and the summary is TYPED.

    `cannot_verify` is precisely the state where the judge could not produce a reason, so a
    NULL detail is its most likely shape — and `str(None)` is the four-character string
    "None", which would be typed at the orchestrator's prompt as though it were the
    explanation. The fixture always carried a detail, so the `or ""` fallback was never
    exercised.
    """
    _statuses(monkeypatch, {ORCH: inbox.BUSY})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)

    inbox.tick({}, runs=[_verdict_run(judge.J_CANNOT_VERIFY, judge_detail=None)])

    summary = inbox.load()["queue"][0]["summary"]
    assert "None" not in summary, (
        f"the literal string 'None' reached a summary that gets typed at the prompt. "
        f"Got: {summary!r}"
    )
    assert "CANNOT VERIFY" in summary        # ...and the verdict itself still lands


@pytest.mark.parametrize("judge_state,kind", JUDGE_KINDS)
def test_a_verdict_summary_carries_a_SNIPPET_of_the_title_not_the_whole_line(
        judge_state, kind, store_file, windows, sends, monkeypatch):
    """🔴 GUARD (CMX-197 round 14): the bug this repo already ate, on the new kinds.

    `tests/test_inbox.py`'s own section header records it: an event built from the run's
    `title` — which for a markdown tracker is the WHOLE `- [ ]` line — pushed the entire
    multi-paragraph task brief at the orchestrator. `_short_title` is the fix, and the
    summary is TYPED at a prompt, so this is the same class as round 8's excerpt rule.

    ⚠️ Invisible until now for a mundane reason: the judge fixture's title was the single
    character "x", so `snippet` and `title` rendered identically. It now carries the same
    TRACKER_LINE the run_review tests use — a fixture VALUE too small to distinguish two
    behaviours is the same blind spot as a missing field.
    """
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    store = inbox.load()
    store["orchestrator"] = ORCH
    inbox.save(store)
    monkeypatch.setattr(
        dispatcher, "_read_pr_checks",
        lambda pr_url, repo_dir: dispatcher.CIStatus(dispatcher.CI_PASSING, _JUDGE_SHA))

    inbox.tick({}, runs=[_verdict_run(judge_state)])

    summary = sends[0][1]
    # ⛔ NOT `TRACKER_LINE not in summary` — my first attempt asserted exactly that and could
    # never fail: the delivered line is SANITIZED (markdown stripped), so the raw constant is
    # never a substring of it either way. A guard that cannot fail is the bug this repo
    # keeps paying for, written into a test FOR that bug.
    #
    # The distinguishing fact is the TRUNCATION: `_short_title` cuts on a word boundary and
    # appends an ellipsis, so the title's TAIL survives only if the whole line was pasted.
    assert "BEFORE implementing" not in summary, (
        f"the tail of the tracker line reached the prompt — the summary must be a snippet, "
        f"with the full title left in the payload. Got: {summary!r}"
    )
    # (the snippet strips markdown emphasis, which takes the apostrophe with it)
    assert "default agent launch mode editable" in summary, (
        f"a readable snippet of the title must still reach the operator. Got: {summary!r}"
    )


# --- CMX-223: peer socket first, tmux paste as fallback, receipts recorded -------

def test_delivery_prefers_the_peer_socket_and_skips_tmux(store_file, windows, sends, monkeypatch):
    """The verdict-delivery path routes through the peer socket first, same as rooms
    and `chela msg` — `send_tmux` is the fallback, not the only path."""
    from chela import messenger

    _confirm_idle_immediately(monkeypatch)
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    calls: list[tuple[str, str, str]] = []
    monkeypatch.setattr(
        inbox.messenger, "send_peer",
        lambda wid, frm, text: (calls.append((wid, frm, text)),
                                messenger.PeerSendResult(True, "sent"))[1])

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    assert len(calls) == 1
    wid, _frm, text = calls[0]
    assert wid == ORCH
    assert text.startswith("📥 @2 · chelamux finished")
    assert sends == []                 # tmux never touched — the socket handled it


def test_a_held_receipt_holds_the_event_queued_and_records_a_receipt(
        store_file, windows, sends, monkeypatch):
    """⛔ Same fail-open fix as rooms/send_message: a socket accepting the bytes is
    a handoff, not a delivery. Unlike rooms (a receiver's own gate is treated as
    final), the inbox HOLDS — these events are merge verdicts the orchestrator must
    eventually see, worth retrying on a later tick rather than dropping."""
    from chela import messenger

    _confirm_idle_immediately(monkeypatch)
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(True, "held"))

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    assert sends == []                              # NOT delivered via tmux either
    assert inbox.load()["queue"]                    # HELD — still queued, not dropped
    receipts = [e for e in event_log.read()["events"] if e["type"] == "inbox_receipt"]
    assert len(receipts) == 1
    assert receipts[0]["payload"]["status"] == "held"
    assert receipts[0]["wid"] == ORCH


def test_peer_socket_unreachable_falls_back_to_tmux(store_file, windows, sends, monkeypatch):
    """The existing contract, unchanged: no live socket -> tmux paste."""
    from chela import messenger

    _confirm_idle_immediately(monkeypatch)
    _registered()
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(False, None))

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.BUSY})

    assert len(sends) == 1
    assert sends[0][0] == ORCH


# --- CMX-255: the windowless orchestrator — a raw pid, no tmux window at all ----------
#
# The delivery half of the mechanism CMX-254 deliberately deferred (PR #323's `## Scope`):
# a session registered via `register_peer` (no `@N` at all) is reachable purely over its
# own peer socket, addressed by pid — `deliver` falls back to it only when there is no
# LIVE wid-based orchestrator (never registered, or its address has rotted).

def _peer_status(monkeypatch, mapping: dict):
    monkeypatch.setattr(inbox.agent_manager, "session_status_map",
                        lambda: {"by_pid": dict(mapping)})


def test_register_peer_records_pid_session_and_started_without_touching_the_wid_address(
        store_file, windows, monkeypatch):
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)

    result = inbox.register_peer(4242, "sid-abc")

    assert result == {"ok": True, "pid": 4242, "session": "sid-abc", "queued": 0}
    peer = inbox.orchestrator_peer()
    assert peer["pid"] == 4242
    assert peer["session"] == "sid-abc"
    assert peer["started"] == 1000.0
    # A completely separate address kind — the wid-based one is untouched.
    assert inbox.orchestrator_wid() is None


def test_register_peer_clears_a_latched_address_alarm(store_file, windows, monkeypatch):
    """`register_peer` is a THIRD writer of the orchestrator address (alongside `register`
    and `readdress`/`unregister_dangling`) and must clear the alarm exactly like the other
    two: a latched `address_alarm_pushed=True` SUPPRESSES the push for the NEXT real outage,
    so a windowless re-registration after a rotted wid must not leave it latched."""
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    store = _arm_the_address_alarm(inbox.load())
    inbox.save(store)

    inbox.register_peer(4242, "sid-abc")

    _assert_address_alarm_cleared(inbox.load(), "register_peer")


def test_orchestrator_peer_and_orchestrator_wid_are_independent(
        store_file, windows, monkeypatch):
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")
    inbox.register(ORCH)

    assert inbox.orchestrator_wid() == ORCH
    assert inbox.orchestrator_peer()["pid"] == 4242    # neither registration clobbers the other


def test_peer_state_ok_when_the_pid_still_matches_its_recorded_start_time(monkeypatch):
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    state, why = inbox.peer_state({"pid": 4242, "started": 1000.0})
    assert (state, why) == (inbox.PEER_OK, "")


def test_peer_state_gone_when_nothing_is_running_at_that_pid(monkeypatch):
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: None)
    state, why = inbox.peer_state({"pid": 4242, "started": 1000.0})
    assert state == inbox.PEER_GONE
    assert "4242" in why


def test_peer_state_stale_when_the_os_reused_the_pid(monkeypatch):
    """⛔ CMX-48's guard, applied to a pid: a pid the OS handed to a DIFFERENT process
    since registration is a stranger, not the orchestrator, and must be refused exactly
    like a dangling wid — never delivered to."""
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 5000.0)
    state, why = inbox.peer_state({"pid": 4242, "started": 1000.0})
    assert state == inbox.PEER_STALE
    assert "4242" in why


def test_delivery_falls_back_to_a_registered_windowless_peer_when_nothing_is_registered_by_wid(
        store_file, windows, monkeypatch):
    from chela import messenger

    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")
    _peer_status(monkeypatch, {4242: inbox.IDLE})
    calls = []
    monkeypatch.setattr(
        inbox.messenger, "send_peer_to_pid",
        lambda pid, frm, text: (calls.append((pid, frm, text)),
                                messenger.PeerSendResult(True, "sent"))[1])

    with inbox.locked_store() as st:
        st["queue"] = [inbox._event("run_review", "📥 hello", {})]
        sent = inbox.deliver(st, {}, [])

    assert len(sent) == 1
    assert calls == [(4242, "chela-inbox", "📥 hello")]
    assert inbox.load()["queue"] == []


def test_delivery_to_a_windowless_peer_never_falls_back_to_tmux(
        store_file, windows, sends, monkeypatch):
    """There is no window, so there is no pane to paste into — a socket failure here
    must be a genuine drop (held queued), never routed through send_tmux."""
    from chela import messenger

    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")
    _peer_status(monkeypatch, {4242: inbox.IDLE})
    monkeypatch.setattr(inbox.messenger, "send_peer_to_pid",
                        lambda pid, frm, text: messenger.PeerSendResult(False, None))

    with inbox.locked_store() as st:
        st["queue"] = [inbox._event("run_review", "📥 hello", {})]
        sent = inbox.deliver(st, {}, [])

    assert sent == []
    assert sends == []                              # tmux never touched
    assert inbox.load()["queue"]                    # HELD, not dropped


def test_delivery_does_not_fall_back_to_the_peer_while_a_healthy_wid_orchestrator_is_busy(
        store_file, windows, sends, monkeypatch):
    """A live wid orchestrator that is merely BUSY is left alone exactly as before — it
    will go idle on its own, so the peer fallback must never race it."""
    from chela import messenger

    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    _registered()
    inbox.register_peer(4242, "sid-abc")
    _peer_status(monkeypatch, {4242: inbox.IDLE})
    peer_calls = []
    monkeypatch.setattr(
        inbox.messenger, "send_peer_to_pid",
        lambda pid, frm, text: (peer_calls.append(pid),
                                messenger.PeerSendResult(True, "sent"))[1])

    with inbox.locked_store() as st:
        st["queue"] = [inbox._event("run_review", "📥 hello", {})]
        sent = inbox.deliver(st, {ORCH: inbox.BUSY}, [])

    assert sent == []
    assert peer_calls == []                          # never even tried
    assert sends == []
    assert inbox.load()["queue"]                     # still queued for the wid orchestrator


def test_delivery_falls_back_to_the_peer_when_the_wid_address_has_rotted(
        store_file, windows, sends, monkeypatch):
    """The core CMX-255 scenario: a tmux restart dangled the wid address, and the only
    live registration left is the windowless one — the queue must not simply wait forever
    for a window that isn't coming back."""
    from chela import messenger

    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    with inbox.locked_store() as st:
        st["orchestrator"] = ORCH
        st["orchestrator_epoch"] = "1-1000"           # a dead epoch: current() below disagrees
        st["orchestrator_name"] = "orchestrator"
    inbox.register_peer(4242, "sid-abc")
    _peer_status(monkeypatch, {4242: inbox.IDLE})
    calls = []
    monkeypatch.setattr(
        inbox.messenger, "send_peer_to_pid",
        lambda pid, frm, text: (calls.append(pid), messenger.PeerSendResult(True, "sent"))[1])

    with inbox.locked_store() as st:
        st["queue"] = [inbox._event("run_review", "📥 hello", {})]
        sent = inbox.deliver(st, {ORCH: inbox.IDLE}, [], now_epoch="2-2000")

    assert len(sent) == 1
    assert calls == [4242]
    assert sends == []                                # never typed into the stranger at @1
    assert inbox.load()["queue"] == []


def test_delivery_skips_a_windowless_peer_that_is_busy(store_file, windows, monkeypatch):
    """⛔ Must prove the BUSY gate itself held the event, not merely that no socket
    exists for this pid — stub `send_peer_to_pid` to succeed and record its calls, then
    assert it was never even attempted, the same shape
    `test_delivery_does_not_fall_back_to_the_peer_while_a_healthy_wid_orchestrator_is_busy`
    uses for the wid path."""
    from chela import messenger

    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")
    _peer_status(monkeypatch, {4242: inbox.BUSY})
    peer_calls = []
    monkeypatch.setattr(
        inbox.messenger, "send_peer_to_pid",
        lambda pid, frm, text: (peer_calls.append(pid),
                                messenger.PeerSendResult(True, "sent"))[1])

    with inbox.locked_store() as st:
        st["queue"] = [inbox._event("run_review", "📥 hello", {})]
        sent = inbox.deliver(st, {}, [])

    assert sent == []
    assert peer_calls == []                          # never even tried — busy gate held it
    assert inbox.load()["queue"]


def test_a_held_receipt_from_the_windowless_peer_is_not_attributed_to_a_fabricated_window_id(
        store_file, windows, monkeypatch):
    """The peer-path counterpart to `test_a_held_receipt_holds_the_event_queued_and_records_a_
    receipt`: the windowless call site passes `event_wid=None` on purpose (`orchestrator_peer`:
    'a caller that needs an actual window id must never receive one from here'). A held/denied
    receipt from a windowless session must log under NO window id — never a fabricated
    `@<pid>` standing in for a real one."""
    from chela import messenger

    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")
    _peer_status(monkeypatch, {4242: inbox.IDLE})
    monkeypatch.setattr(inbox.messenger, "send_peer_to_pid",
                        lambda pid, frm, text: messenger.PeerSendResult(True, "held"))

    with inbox.locked_store() as st:
        st["queue"] = [inbox._event("run_review", "📥 hello", {})]
        sent = inbox.deliver(st, {}, [])

    assert sent == []
    assert inbox.load()["queue"]                    # HELD — still queued, not dropped
    receipts = [e for e in event_log.read()["events"] if e["type"] == "inbox_receipt"]
    assert len(receipts) == 1
    assert receipts[0]["payload"]["status"] == "held"
    assert receipts[0]["wid"] is None                # never a fabricated `@<pid>`


def test_delivery_skips_a_stale_windowless_peer(store_file, windows, monkeypatch):
    """⛔ Must prove the `peer_state` refusal itself held the event, not merely that the
    REAL (unstubbed) `claude agents --json` cache doesn't happen to say pid 4242 is idle —
    stub the idle gate to IDLE and `send_peer_to_pid` to succeed and record its calls, so
    the ONLY thing that can hold the event is the stale refusal, the same shape
    `test_delivery_skips_a_windowless_peer_that_is_busy` uses for the idle gate."""
    from chela import messenger

    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 9999.0)  # pid reused since
    _peer_status(monkeypatch, {4242: inbox.IDLE})
    peer_calls = []
    monkeypatch.setattr(
        inbox.messenger, "send_peer_to_pid",
        lambda pid, frm, text: (peer_calls.append(pid),
                                messenger.PeerSendResult(True, "sent"))[1])

    with inbox.locked_store() as st:
        st["queue"] = [inbox._event("run_review", "📥 hello", {})]
        sent = inbox.deliver(st, {}, [])

    assert sent == []
    assert peer_calls == []                          # never even tried — stale gate held it
    assert inbox.load()["queue"]


def test_delivery_skips_a_gone_windowless_peer(store_file, windows, monkeypatch):
    """The GONE counterpart at the `deliver` call site — `test_peer_state_gone_when_nothing_
    is_running_at_that_pid` only tests the `peer_state` helper in isolation, not that `deliver`
    actually consults it. Same discriminating shape as the stale test above."""
    from chela import messenger

    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: None)  # nothing runs there now
    _peer_status(monkeypatch, {4242: inbox.IDLE})
    peer_calls = []
    monkeypatch.setattr(
        inbox.messenger, "send_peer_to_pid",
        lambda pid, frm, text: (peer_calls.append(pid),
                                messenger.PeerSendResult(True, "sent"))[1])

    with inbox.locked_store() as st:
        st["queue"] = [inbox._event("run_review", "📥 hello", {})]
        sent = inbox.deliver(st, {}, [])

    assert sent == []
    assert peer_calls == []                          # never even tried — gone gate held it
    assert inbox.load()["queue"]


def test_no_delivery_at_all_when_neither_a_wid_nor_a_peer_orchestrator_is_registered(
        store_file, windows):
    with inbox.locked_store() as st:
        st["queue"] = [inbox._event("run_review", "📥 hello", {})]
        sent = inbox.deliver(st, {}, [])

    assert sent == []
    assert inbox.load()["queue"]


# --- `chela watching` — the windowless registration is the operator's only surface for it --

def test_watching_shows_the_windowless_peer_registration_and_its_state(
        store_file, windows, monkeypatch, capsys):
    """Nothing under tests/ used to drive `cmd_watching` with a peer in the store at all —
    the whole display block (`if peer:`) could be disabled outright and every existing test
    stayed green."""
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")

    main.cmd_watching(Namespace())

    out = capsys.readouterr().out
    assert "windowless orchestrator: pid 4242" in out
    assert "sid-abc" in out
    assert "[ok]" in out
    # ⛔ no wid orchestrator is registered at all — CMX-255's own primary scenario, where the
    # peer is the SOLE destination. Pins the `orch and` half of the suffix's condition: without
    # it, the suffix fires on ADDR_NONE (not in UNDELIVERABLE) even though nothing is registered
    # by wid to actually "fall back" to.
    assert "(fallback only" not in out


def test_watching_reports_why_a_stale_windowless_peer_cannot_be_used(
        store_file, windows, monkeypatch, capsys):
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 9999.0)  # pid reused since

    main.cmd_watching(Namespace())

    out = capsys.readouterr().out
    assert "[stale]" in out
    # ⛔ NOT just "4242" — the header line (`windowless orchestrator: pid 4242  [stale]`)
    # already prints the pid regardless of whether `pwhy` was ever reached, so an assertion
    # that only looks for the pid can't tell a full diagnosis from a stripped one. Assert on
    # text only `pwhy` can supply: the actual diagnosis and the `chela watch` remedy.
    tail = out.split("windowless orchestrator", 1)[1]
    assert "DIFFERENT process" in tail
    assert "chela watch" in tail


def test_watching_reports_why_a_gone_windowless_peer_cannot_be_used(
        store_file, windows, monkeypatch, capsys):
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register_peer(4242, "sid-abc")
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: None)  # nothing runs there now

    main.cmd_watching(Namespace())

    out = capsys.readouterr().out
    assert "[gone]" in out
    assert "no longer running" in out


def test_watching_marks_the_peer_as_fallback_only_when_the_wid_orchestrator_can_still_take_it(
        store_file, windows, monkeypatch, capsys):
    """The `(fallback only — ...)` suffix's condition (`orch and state not in UNDELIVERABLE`)
    is a second, independently-derived copy of `deliver`'s own fallback rule — if the two ever
    disagree, this tells the operator the peer is a mere fallback while `deliver` is in fact
    routing there, which is exactly the misreport CMX-254 was raised about."""
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register(ORCH)                                # a live, healthy wid orchestrator
    _statuses(monkeypatch, {ORCH: inbox.IDLE})
    inbox.register_peer(4242, "sid-abc")

    main.cmd_watching(Namespace())

    out = capsys.readouterr().out
    assert "windowless orchestrator: pid 4242" in out
    assert "(fallback only — a live wid orchestrator is registered above)" in out


def test_watching_does_not_call_the_peer_fallback_only_when_the_wid_address_cannot_take_it(
        store_file, windows, monkeypatch, capsys):
    """`deliver` falls through to the windowless peer whenever the wid orchestrator is
    UNDELIVERABLE (CMX-255's whole point) — here that must read as the peer being the REAL
    destination, not a mere fallback next to a healthy address."""
    monkeypatch.setattr(inbox.sessions, "proc_started", lambda pid: 1000.0)
    inbox.register(ORCH)
    _statuses(monkeypatch, {AGENT: inbox.IDLE})         # ORCH absent from a non-empty map: GONE
    inbox.register_peer(4242, "sid-abc")

    main.cmd_watching(Namespace())

    out = capsys.readouterr().out
    assert "windowless orchestrator: pid 4242" in out
    assert "(fallback only" not in out


# ---------------------------------------------------------------------------
# CMX-318 — the completion notice carries the agent's own closing words
# ---------------------------------------------------------------------------
#
# Before this, "finished" was a fixed template: the orchestrator learned that an event
# had happened and nothing about WHAT happened, so its next move was always to go and
# read the transcript by hand. The excerpt rides the summary (the only thing pushed);
# the untruncated text rides the payload (a record, read rather than typed).

def _finished_with_transcript(monkeypatch, said):
    """Drive the evidence path to a completion, with `said` as the agent's last words."""
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.sessions, "transcript_for_window",
                        lambda wid: Path(f"/proj/{wid}/session.jsonl"))
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity_at",
                        lambda path: watched_since + 5)
    monkeypatch.setattr(inbox.transcripts, "last_assistant_text", lambda path: said)
    _statuses(monkeypatch, {ORCH: inbox.IDLE, AGENT: inbox.IDLE})


def test_the_finished_notice_quotes_the_agents_last_message(
        store_file, windows, sends, monkeypatch):
    _confirm_idle_immediately(monkeypatch)
    _registered()
    _finished_with_transcript(monkeypatch, "Fixed the parser and added 3 tests, all green")

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    assert len(sends) == 1
    text = sends[0][1]
    assert "finished the task you dispatched" in text
    assert "Fixed the parser and added 3 tests, all green" in text, (
        f"the completion notice did not carry the agent's own words: {text!r}"
    )


def test_the_finished_notice_stays_one_line(store_file, windows, sends, monkeypatch):
    """`_event`'s contract: the summary is the ONE line pushed into a prompt. A multi-line
    sign-off must not turn a notification into an essay typed at someone's terminal."""
    _confirm_idle_immediately(monkeypatch)
    _registered()
    _finished_with_transcript(monkeypatch, "line one\nline two\nline three")

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    assert "\n" not in sends[0][1], f"the notice spans multiple lines: {sends[0][1]!r}"


def test_a_very_long_sign_off_is_truncated_in_the_notice(
        store_file, windows, sends, monkeypatch):
    _confirm_idle_immediately(monkeypatch)
    _registered()
    _finished_with_transcript(monkeypatch, "w" * 5000)

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    assert len(sends[0][1]) < 500, f"the notice is {len(sends[0][1])} chars — unbounded"


def test_a_mid_length_sign_off_is_not_cut_to_the_tracker_title_width(
        store_file, windows, sends, monkeypatch):
    """Pins `FINAL_MESSAGE_CHARS` (220) apart from `SUMMARY_TITLE_CHARS` (60) — every other
    CMX-318 fixture is either well under 60 (so it survives either limit unchanged) or far
    over both (5000 chars, only asserted as "some bound applies"), so nothing distinguishes
    the two constants. A 166-char sign-off sits strictly between them: it must reach the
    notice WHOLE if the limit really is 220, and would be cut with a trailing "…" if
    `FINAL_MESSAGE_CHARS` ever quietly collapsed to the tracker-title width.
    """
    said = ("Refactored the dispatcher retry queue to use exponential backoff, added "
            "coverage for the timeout edge case, and updated the docs to match the new "
            "behavior end to end.")
    assert inbox.SUMMARY_TITLE_CHARS < len(said) < inbox.FINAL_MESSAGE_CHARS, (
        "fixture no longer sits strictly between the two limits — it can't tell them apart"
    )
    _confirm_idle_immediately(monkeypatch)
    _registered()
    _finished_with_transcript(monkeypatch, said)

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    text = sends[0][1]
    assert said in text, (
        f"a 166-char sign-off (under FINAL_MESSAGE_CHARS) was cut short: {text!r}"
    )
    assert "…" not in text, f"the sign-off was truncated even though it fits: {text!r}"


def test_the_notice_falls_back_to_the_template_when_the_agent_said_nothing(
        store_file, windows, sends, monkeypatch):
    """MUST BE ACCEPTED — a tool-only final turn, an unreadable transcript, or a window
    that cannot be resolved must still produce the completion notice that shipped before
    this feature existed. Losing the event would be far worse than losing the excerpt.
    """
    _confirm_idle_immediately(monkeypatch)
    _registered()
    _finished_with_transcript(monkeypatch, None)

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    assert len(sends) == 1
    assert "finished the task you dispatched" in sends[0][1]
    assert "Said:" not in sends[0][1], (
        "an agent that said nothing must not be quoted as having said nothing — "
        f"got {sends[0][1]!r}"
    )


def test_the_untruncated_message_is_kept_in_the_payload(
        store_file, windows, sends, monkeypatch):
    """The excerpt is for reading at a glance; the record is what a UI or a log works
    with. `_event`'s docstring: the payload keeps the raw text, a record is read."""
    _confirm_idle_immediately(monkeypatch)
    _registered()
    long_text = "detail " * 40
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.sessions, "transcript_for_window",
                        lambda wid: Path(f"/proj/{wid}/session.jsonl"))
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity_at",
                        lambda path: watched_since + 5)
    monkeypatch.setattr(inbox.transcripts, "last_assistant_text", lambda path: long_text)
    # BUSY orchestrator → the event QUEUES instead of being pushed, so the record is
    # readable off the store (the idiom the judge-payload tests above use).
    _statuses(monkeypatch, {ORCH: inbox.BUSY, AGENT: inbox.IDLE})

    inbox.tick({ORCH: inbox.BUSY, AGENT: inbox.IDLE})

    finished = [e for e in inbox.load()["queue"] if e["kind"] == "finished"]
    assert finished, "no finished event queued"
    assert finished[0]["payload"]["final_message"] == long_text, (
        "the payload must keep the agent's text untruncated by the SUMMARY's limit — "
        f"got {finished[0]['payload']['final_message']!r}"
    )
    assert "…" in finished[0]["summary"], (
        "the summary carried the whole 280-char sign-off instead of an excerpt — the "
        f"one line pushed at a prompt must be cut: {finished[0]['summary']!r}"
    )


def test_a_shell_metacharacter_sign_off_is_neutralised(
        store_file, windows, sends, monkeypatch):
    """CMX-79: the summary is TYPED AT A PROMPT, and this feature makes it carry text an
    agent wrote freely. `$(...)` in a sign-off must not survive into the pushed line.
    """
    _confirm_idle_immediately(monkeypatch)
    _registered()
    _finished_with_transcript(monkeypatch, "done $(rm -rf /) and `whoami`")

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    text = sends[0][1]
    assert "$(" not in text, f"a command substitution survived into the prompt: {text!r}"


def test_the_said_excerpt_stays_curly_quoted_through_sanitization(
        store_file, windows, sends, monkeypatch):
    """`_line`'s docstring: the frame around the excerpt is curly quotes, NOT ``"``,
    because every summary is neutralised by ``sanitize_prompt`` before it reaches a
    prompt, and ``"`` is in ``SHELL_META_RE`` — a straight-quoted frame has its own
    delimiters stripped to spaces, so the excerpt would merge seamlessly into chela's
    own instruction text with nothing marking where the agent's free-form words start
    and end. Curly quotes (``“``/``”``) are not shell metacharacters, so they survive
    the sanitizer untouched and the frame stays intact end to end.
    """
    said = "Fixed the parser and added 3 tests, all green"
    _confirm_idle_immediately(monkeypatch)
    _registered()
    _finished_with_transcript(monkeypatch, said)

    inbox.tick({ORCH: inbox.IDLE, AGENT: inbox.IDLE})

    text = sends[0][1]
    assert f"“{said}”" in text, (
        "the excerpt lost its curly-quote delimiters on the way to the prompt — a "
        f"straight-quoted frame would be stripped by sanitize_prompt: {text!r}"
    )


def test_the_payload_final_message_is_capped_at_the_payload_limit(
        store_file, windows, sends, monkeypatch):
    """`FINAL_MESSAGE_PAYLOAD_CHARS` exists so the payload copy — a record, not a
    notification — cannot grow `inbox.json` without bound if an agent signs off with a
    wall of text. Only a fixture that actually crosses the 4000-char cap can tell a
    capped payload apart from an uncapped one; `test_the_untruncated_message_is_kept_in_
    the_payload`'s 280-char fixture sits well under it either way.
    """
    _confirm_idle_immediately(monkeypatch)
    _registered()
    wall_of_text = "detail " * 1000  # 7000 chars, well past the 4000-char payload cap
    assert len(wall_of_text) > inbox.FINAL_MESSAGE_PAYLOAD_CHARS
    watched_since = inbox.watches()[AGENT]["since"]
    monkeypatch.setattr(inbox.sessions, "transcript_for_window",
                        lambda wid: Path(f"/proj/{wid}/session.jsonl"))
    monkeypatch.setattr(inbox.transcripts, "last_assistant_activity_at",
                        lambda path: watched_since + 5)
    monkeypatch.setattr(inbox.transcripts, "last_assistant_text", lambda path: wall_of_text)
    _statuses(monkeypatch, {ORCH: inbox.BUSY, AGENT: inbox.IDLE})

    inbox.tick({ORCH: inbox.BUSY, AGENT: inbox.IDLE})

    finished = [e for e in inbox.load()["queue"] if e["kind"] == "finished"]
    assert finished, "no finished event queued"
    stored = finished[0]["payload"]["final_message"]
    assert stored == wall_of_text[:inbox.FINAL_MESSAGE_PAYLOAD_CHARS], (
        "the payload's final_message must be capped at FINAL_MESSAGE_PAYLOAD_CHARS — "
        f"got {len(stored)} chars"
    )


# Two session ids sharing one project directory below — chosen so that sorting the
# directory's filenames ALPHABETICALLY (as a buggy "just glob the dir" resolution would)
# puts SIBLING_SID last, regardless of which window is actually asking. A resolution that
# trusts the path it was actually given must not care about this ordering at all.
_MINE_SID = "11111111-1111-4111-8111-111111111117"
_SIBLING_SID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"


def test_final_message_refuses_to_quote_a_sibling_rather_than_this_window(
        tmp_path, monkeypatch):
    """The CMX-191 hazard `final_message`'s own docstring warns about, given a real
    negative control (defeat shape 311): every OTHER CMX-318 test stubs
    `transcripts.last_assistant_text` to a constant, so none of them can tell one
    window's transcript from another's — a `final_message` that silently resolved by
    the wrong window would still pass all of them. This one drives REAL transcript
    files through the REAL `sessions.transcript_for_window`, exactly like
    `test_did_work_since_refuses_a_shared_cwd_rather_than_crediting_a_sibling` does for
    the structurally identical hazard in `did_work_since`.

    Unlike an earlier version of this test, @7 and @8 share ONE cwd — the same fixture
    shape as the `did_work_since` guard — because the hazard is specifically a lookup
    that keys on the SHARED PROJECT DIRECTORY rather than on the window's own resolved
    session. Two distinct cwds cannot see that: each directory would then hold only one
    file, so even a cwd-keyed (or dir-keyed) lookup resolves "correctly" by accident.
    Here both `@7.jsonl`-equivalent transcripts live under the SAME project dir (real
    Claude Code behaviour for two agents launched in one cwd), resolved via each pane's
    own `--resume <sid>` (the "cmdline" tier), so `final_message("@7")` must return @7's
    own words even though @8's transcript sits right next to it in the same directory.
    """
    cwd = "/home/x/proj"
    proj = tmp_path / transcripts.encode_cwd(cwd)
    proj.mkdir(parents=True)
    (proj / f"{_MINE_SID}.jsonl").write_text(json.dumps(
        {"type": "assistant", "message": {"content": "SEVEN's own closing words"}}) + "\n")
    (proj / f"{_SIBLING_SID}.jsonl").write_text(json.dumps(
        {"type": "assistant", "message": {"content": "EIGHT's own closing words"}}) + "\n")

    monkeypatch.setattr(inbox.sessions, "transcript_for_window", _REAL_TRANSCRIPT_FOR_WINDOW)
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", tmp_path)
    # `resolve_window`'s cmdline tier promotes what it resolves into the durable
    # `sessionids` pin — point that store at a scratch file so the test never touches the
    # real one, mirroring `tests/test_sessions.py`'s `pins` fixture.
    monkeypatch.setattr(sessionids, "_STORE", tmp_path / "session-ids.json")
    monkeypatch.setattr(sessionids.epoch, "current", lambda: "111-222")
    pane_map = {
        "@7": sessions.Pane(wid="@7", launched_in=cwd, resumed=_MINE_SID),
        "@8": sessions.Pane(wid="@8", launched_in=cwd, resumed=_SIBLING_SID),
    }
    monkeypatch.setattr(inbox.sessions, "panes", lambda force=False: pane_map)

    assert inbox.final_message("@7") == "SEVEN's own closing words"
    assert inbox.final_message("@8") == "EIGHT's own closing words"

