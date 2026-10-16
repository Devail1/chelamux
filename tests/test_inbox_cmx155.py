"""CMX-155 — a dispatched agent's classifier denial (``hook.permission_denied``) queues ONE
orchestrator-inbox notice per run per cooldown; the orchestrator's own window and human sessions
get none. tmux, statuses, the peer socket and the runs DB are stubbed; the event log is per-test."""
from __future__ import annotations

import pytest

from chela import dispatcher, event_log, hooks, inbox, notify

ORCH = "@1"
AGENT = "@41"
HUMAN = "@7"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(inbox.epoch, "current", lambda: "e1")
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: {ORCH: inbox.BUSY})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator", AGENT: "cmx-9", HUMAN: "notes"})
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "wid_for_session", lambda sid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "transcript_for_window", lambda wid: None)
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    monkeypatch.setattr(inbox, "stuck_probes", lambda runs: {})
    from chela import messenger
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(False, None))
    monkeypatch.setattr(inbox.messenger, "send_tmux", lambda wid, text: False)
    store = inbox.load()
    store["orchestrator"] = ORCH                    # busy → events queue, readable here
    inbox.save(store)


def _run(status="running", **over):
    return {"task_id": "CMX-9", "title": "fix the parser", "status": status,
            "branch_name": "cmx-9", "window_name": "cmx-9", "window_id": AGENT,
            "window_epoch": "e1", **over}


def _deny(wid, command="kill 4242", ts=None, epoch="e1"):
    body = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": command}}
    record = event_log.append(notify.DENIED_TYPE, hooks.summarize("PermissionDenied", body),
                              hooks.clip_payload(body), wid=wid, epoch=epoch)
    if ts is not None:
        record = {**record, "ts": ts}
    return record


def _queued(kind=inbox.DENIED_KIND):
    return [e for e in inbox.load()["queue"] if e["kind"] == kind]


def _events(denials, runs=None, store=None, now=1000.0, **kw):
    store = {**inbox._empty(), "orchestrator": ORCH} if store is None else store
    return inbox.denied_events(denials, [_run()] if runs is None else runs, store, now,
                               now_epoch="e1", **kw), store


# --- VERIFY: end to end through tick() and a real (isolated) event log ---------------------

def test_a_dispatched_agents_denial_queues_exactly_one_notice_naming_task_and_command():
    """🔴 GUARD: a denial from a running run's window → ONE notice naming the task, the
    window, the tool and the command."""
    runs = [_run()]
    inbox.tick({}, runs=runs)                       # arms the cursor at the log's tip
    _deny(AGENT, command="rm -rf build")
    inbox.tick({}, runs=runs)
    [event] = _queued()
    assert event["summary"] == (
        "🔒 CMX-9 · @41 was blocked by its permission classifier: Bash rm -rf build — the "
        "agent is stopped on it: hand the human a bang-prefixed command to run, or redirect "
        "the agent.")
    assert event["wid"] == AGENT
    assert event["payload"]["task_id"] == "CMX-9"
    assert event["payload"]["tool"] == "Bash"
    assert event["payload"]["detail"] == "rm -rf build"
    inbox.tick({}, runs=runs)                       # nothing new in the log → no repeat
    assert len(_queued()) == 1


def test_the_first_tick_only_anchors_and_never_replays_old_denials():
    """🔴 GUARD: an upgrade's first tick does not replay denials already in the log."""
    _deny(AGENT)
    inbox.tick({}, runs=[_run()])
    assert _queued() == []
    assert inbox.load()["denied_cursor"]["seq"] == event_log.tip()["seq"]


def test_the_detail_is_clipped_like_the_phone_push():
    [event], _ = _events([_deny(AGENT, command="x" * 300)])
    assert len(event["payload"]["detail"]) == 80 and event["payload"]["detail"].endswith("…")


# --- cooldown ------------------------------------------------------------------------

def test_a_second_denial_within_the_cooldown_is_not_a_second_notice_but_after_it_is():
    """🔴 GUARD: one notice per run per 600 s; after the cooldown, a new one."""
    assert (inbox.DENIED_COOLDOWN_S, inbox.DENIED_KIND) == (600, "run_permission_denied")
    out, store = _events([_deny(AGENT), _deny(AGENT)], now=1000.0)
    assert len(out) == 1
    assert store["denied_seen"] == {"CMX-9": 1000.0}
    out, store = _events([_deny(AGENT)], store=store, now=1599.0)
    assert out == []
    out, store = _events([_deny(AGENT)], store=store, now=1600.0)
    assert len(out) == 1
    assert store["denied_seen"] == {"CMX-9": 1600.0}


def test_expired_latches_are_pruned():
    store = inbox._empty()
    store["denied_seen"] = {"OLD": 0.0, "NEW": 900.0}
    _events([], store=store, now=1000.0)
    assert store["denied_seen"] == {"NEW": 900.0}


def test_the_cooldown_is_per_run_not_global():
    other = _run(task_id="CMX-10", window_id="@42")
    out, _ = _events([_deny(AGENT), _deny("@42")], runs=[_run(), other])
    assert [e["payload"]["task_id"] for e in out] == ["CMX-9", "CMX-10"]


# --- negative controls -----------------------------------------------------------------

def test_a_denial_from_the_orchestrators_own_window_is_not_a_notice():
    """🔴 GUARD (negative control): the orchestrator sees its own denials as tool results."""
    out, _ = _events([_deny(ORCH)], runs=[_run(window_id=ORCH)])
    assert out == []


def test_the_orchestrator_is_read_from_the_store_and_the_env_pin(monkeypatch):
    out, _ = _events([_deny(AGENT)], store={**inbox._empty(), "orchestrator": AGENT})
    assert out == []
    monkeypatch.setenv("CHELA_ORCHESTRATOR_WID", AGENT)
    out, _ = _events([_deny(AGENT)])
    assert out == []


def test_a_denial_from_a_window_with_no_active_run_is_not_a_notice():
    """🔴 GUARD (negative control): a human session keeps the phone push only."""
    out, _ = _events([_deny(HUMAN)])
    assert out == []


@pytest.mark.parametrize("status", ["awaiting_review", "needs_human", "done", "failed"])
def test_a_settled_runs_window_is_not_an_active_run(status):
    out, _ = _events([_deny(AGENT)], runs=[_run(status)])
    assert out == []


def test_a_claimed_run_counts_as_active():
    out, _ = _events([_deny(AGENT)], runs=[_run("claimed")])
    assert len(out) == 1


def test_a_run_without_a_task_id_or_window_is_skipped():
    out, _ = _events([_deny(AGENT)], runs=[_run(task_id=None)])
    assert out == []
    out, _ = _events([_deny(AGENT), _deny(None)], runs=[_run(window_id=None, window_name=None)])
    assert out == []


def test_a_denial_stamped_by_a_dead_tmux_server_is_not_attributed():
    """A ``@41`` from another tmux epoch is a different window — never this run's agent."""
    out, _ = _events([_deny(AGENT, epoch="e0")])
    assert out == []
    out, _ = _events([_deny(AGENT, epoch=None)])    # unstamped = unknown, not stale
    assert len(out) == 1


# --- the cursor across a log reset -------------------------------------------------------

def test_a_gap_only_returns_denials_since_the_previous_read(monkeypatch):
    old, new = _deny(AGENT, command="old"), _deny(AGENT, command="new")
    old_ts, new_ts = old["ts"], new["ts"]
    batch = {"events": [{**old, "ts": old_ts}, {**new, "ts": new_ts + 10}],
             "gap": {"reason": "boot_id changed"}, "next_seq": 7, "boot_id": "b2"}
    monkeypatch.setattr(inbox.event_log, "read", lambda *a, **k: batch)
    store = {"denied_cursor": {"seq": 99, "boot": "b1", "at": new_ts + 5}}
    events, cursor = inbox.read_denials(store, now=5000.0)
    assert [e["payload"]["tool_input"]["command"] for e in events] == ["new"]
    assert cursor == {"seq": 7, "boot": "b2", "at": 5000.0}


def test_without_a_gap_every_new_denial_is_returned():
    store = {}
    _, store["denied_cursor"] = inbox.read_denials(store, now=1.0)
    _deny(AGENT, command="a")
    _deny(AGENT, command="b")
    events, cursor = inbox.read_denials(store, now=2.0)
    assert [e["payload"]["tool_input"]["command"] for e in events] == ["a", "b"]
    assert cursor["seq"] == event_log.tip()["seq"] and cursor["at"] == 2.0



def test_a_queued_denial_notice_is_stale_once_the_run_settled():
    [event], _ = _events([_deny(AGENT)])
    assert inbox.stale_reason(event, [_run("running")]) is None
    assert inbox.stale_reason(event, [_run("awaiting_review")]) == (
        "run is now 'awaiting_review' — the agent is no longer stopped on the denial")
