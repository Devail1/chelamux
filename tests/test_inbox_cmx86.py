"""CMX-86 — the orchestrator inbox: drop the redundant notices, add the missing ones.

Every notice lands in the orchestrator's context and is re-billed on every later request,
so a notice must carry information or ask for a decision. Locked in here:

  * an ECHO of the orchestrator's own reopen/retry/close is silent; the same by a human is
    a notice;
  * ``awaiting_review`` is silent by default; a run still waiting with no judge round after
    the threshold gets exactly ONE notice;
  * a dispatch hold that lapses (or that somebody else releases) is ONE notice; the
    orchestrator's own ``--resume`` is none;
  * CI red on the head is ONE notice naming the job and the first failing line;
  * a stuck agent / an unread completion marker is ONE notice; a CONFLICTING PR is one per
    head;
  * no notice repeats on a later tick.

Pure: tmux, ``claude agents --json``, the peer socket and gh are all stubbed.
"""
from __future__ import annotations

import subprocess
import time
from argparse import Namespace

import pytest

from chela import dispatcher, hold, inbox, judge, main, orchestrator

ORCH = "@1"
HUMAN = "@7"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.delenv("CHELA_ACTOR", raising=False)
    monkeypatch.delenv("CHELA_WID", raising=False)
    monkeypatch.delenv("CHELA_INBOX_NOTIFY_AWAITING_REVIEW", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")   # a DB per test
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [])
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: {ORCH: inbox.BUSY})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id", lambda: {ORCH: "orchestrator"})
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "wid_for_session", lambda sid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "transcript_for_window", lambda wid: None)
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    from chela import messenger
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(False, None))
    monkeypatch.setattr(inbox.messenger, "send_tmux", lambda wid, text: False)
    store = inbox.load()
    store["orchestrator"] = ORCH                    # busy → events queue, readable here
    inbox.save(store)


def _queued(kind: str | None = None) -> list[dict]:
    return [e for e in inbox.load()["queue"] if kind is None or e["kind"] == kind]


def _run(status, **over):
    return {"task_id": "T9", "title": "fix the parser", "status": status,
            "branch_name": "cmx-9", "pr_url": "https://github.com/o/r/pull/9",
            "pr_state": "open", **over}


def _as(monkeypatch, wid: str) -> None:
    """Run the CLI as the window ``wid``."""
    monkeypatch.setattr(orchestrator, "self_wid", lambda: wid)


# --- 1. the orchestrator's own actions are not news ----------------------------------

_ACTIONS = {
    # cmd, args, dispatcher fn, status before → after, the notice a human's action gets
    "reopen": (main.cmd_reopen, Namespace(run="T9", reason=""), "reopen",
               "needs_human", "awaiting_review", "run_reopened"),
    "retry": (main.cmd_retry, Namespace(run="T9", reason=""), "retry",
              "needs_human", "changes_requested", "run_changes_requested"),
    "close": (main.cmd_close, Namespace(run="T9", reason="superseded", requeue=False,
                                        keep_pr=True, close_pr=False, force=False,
                                        remove_worktree=False), "close_run",
              "needs_human", "closed", "run_was_closed"),
}


def _act(monkeypatch, name: str) -> tuple[str, str]:
    cmd, args, fn, before, after, kind = _ACTIONS[name]
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: _run(before))
    monkeypatch.setattr(dispatcher, fn, lambda *a, **k: {
        "ok": True, "task_id": "T9", "branch_name": "cmx-9", "reason": "superseded",
        "from_status": before, "comment_posted": True})
    inbox.tick({}, runs=[_run(before)])              # the run is known at `before`
    cmd(args)
    inbox.tick({}, runs=[_run(after, close_reason="superseded")])
    return after, kind


@pytest.mark.parametrize("name", sorted(_ACTIONS))
def test_the_orchestrators_own_action_does_not_notify_it(name, monkeypatch):
    _as(monkeypatch, ORCH)
    _, kind = _act(monkeypatch, name)
    assert _queued(kind) == [], f"the orchestrator's own {name} came back to it as news"
    assert inbox.load()["expected_edges"] == {}      # the announced edge was consumed


@pytest.mark.parametrize("name", sorted(_ACTIONS))
def test_the_same_action_by_a_human_is_a_notice(name, monkeypatch):
    _as(monkeypatch, HUMAN)
    _, kind = _act(monkeypatch, name)
    assert len(_queued(kind)) == 1, f"a human's {name} must reach the orchestrator"


def test_the_auto_orchestrator_actor_is_the_orchestrator_wherever_it_runs(monkeypatch):
    _as(monkeypatch, None)
    monkeypatch.setattr(orchestrator, "self_peer", lambda: None)
    assert inbox.caller_is_orchestrator() is False
    monkeypatch.setenv("CHELA_ACTOR", "auto-orchestrator")
    assert inbox.caller_is_orchestrator() is True


@pytest.mark.parametrize("name", sorted(_ACTIONS))
def test_a_failed_action_retracts_its_announcement(name, monkeypatch):
    """🔴 GUARD, per action: a refused reopen/retry/close leaves NO announcement behind — a
    leftover one would swallow the run's next REAL edge to that status as an "echo"."""
    cmd, args, fn, before, after, kind = _ACTIONS[name]
    _as(monkeypatch, ORCH)
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: _run(before))
    monkeypatch.setattr(dispatcher, fn, lambda *a, **k: {"ok": False, "error": "nope"})
    inbox.tick({}, runs=[_run(before)])
    with pytest.raises(SystemExit):
        cmd(args)
    assert inbox.load()["expected_edges"] == {}
    inbox.tick({}, runs=[_run(after, close_reason="by hand")])    # somebody else does it
    assert len(_queued(kind)) == 1, f"a refused {name} swallowed the next real edge"


def _merge(monkeypatch, result: dict) -> None:
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: _run("awaiting_review"))
    monkeypatch.setattr(main.contract, "merge", lambda *a, **k: result)
    monkeypatch.setattr(main, "_print_override_request", lambda result: None)
    main.cmd_merge(Namespace(run="T9", reason="", override=False))


_MERGED = {"ok": True, "task_id": "T9", "base": "dev", "merge_commit_sha": "f" * 40,
           "event_seq": 1}


def test_the_orchestrators_own_merge_is_announced_and_consumed(monkeypatch):
    """🔴 GUARD: ``chela merge`` is one of the four own-actions — the ``done`` edge it causes
    is announced BEFORE the merge, then consumed silently by the tick that sees it."""
    _as(monkeypatch, ORCH)
    _merge(monkeypatch, _MERGED)
    announced = inbox.load()["expected_edges"]["T9"]
    assert (announced["status"], announced["why"]) == ("done", inbox.ORCH_ECHO)
    inbox.tick({}, runs=[_run("done", judge_state=judge.J_UNJUDGED_MERGED)])
    assert inbox.load()["expected_edges"] == {}
    assert _queued("run_unjudged_merged") == []


def test_a_human_merge_is_not_announced(monkeypatch):
    _as(monkeypatch, HUMAN)
    _merge(monkeypatch, _MERGED)
    assert inbox.load()["expected_edges"] == {}


@pytest.mark.parametrize("result", [
    {"ok": True, "pending": True, "request_id": "r1"},        # an override a human approves
    {"ok": False, "error": "judge not clean", "tier": "escalate"},
])
def test_a_merge_that_did_not_happen_now_retracts_its_announcement(result, monkeypatch):
    """🔴 GUARD: a pending override (a HUMAN merges it later — news) or a refused merge must
    not leave the ``done`` announcement behind."""
    _as(monkeypatch, ORCH)
    if result["ok"]:
        _merge(monkeypatch, result)
    else:
        with pytest.raises(SystemExit):
            _merge(monkeypatch, result)
    assert inbox.load()["expected_edges"] == {}


def test_forget_edge_retracts_only_the_announcement_it_names():
    """🔴 GUARD: the CI loop retracting ITS echo must not wipe the orchestrator's own."""
    inbox.expect_edge("T9", "closed", inbox.ORCH_ECHO)
    inbox.forget_edge("T9", inbox.CI_RED_ECHO)
    assert inbox.load()["expected_edges"]["T9"]["why"] == inbox.ORCH_ECHO
    inbox.forget_edge("T9", inbox.ORCH_ECHO)
    assert inbox.load()["expected_edges"] == {}


def test_an_announcement_for_another_status_does_not_silence_this_edge():
    """🔴 GUARD: the orchestrator announced ``closed``; the run went ``needs_human`` instead.
    That is NOT its echo — it is exactly the news it must hear."""
    inbox.tick({}, runs=[_run("running")])
    inbox.expect_edge("T9", "closed", inbox.ORCH_ECHO)
    inbox.tick({}, runs=[_run("needs_human")])
    assert len(_queued("run_needs_human")) == 1
    assert inbox.load()["expected_edges"]["T9"]["status"] == "closed"     # still waiting


def test_an_announced_status_does_not_swallow_a_verdict_at_that_same_status(monkeypatch):
    """🔴 GUARD: an echo is a STATUS edge. A judge verdict landing on a run already sitting at
    the announced status is a different event, and must still be a notice."""
    run = _run("awaiting_review", pr_head_sha="abc", judge_state="")
    inbox.tick({}, runs=[run])
    inbox.expect_edge("T9", "awaiting_review", inbox.ORCH_ECHO)
    inbox.tick({}, runs=[{**run, "judge_state": judge.J_CLEAN, "judge_sha": "abc"}])
    assert len(_queued("run_judge_clean")) == 1


@pytest.mark.parametrize("settled", ["needs_human", "done", "closed", "failed"])
def test_a_reopen_from_every_settled_state_is_a_reopen_notice(settled):
    """🔴 GUARD: `chela reopen` brings a run back from ANY of these — each is `run_reopened`
    (a queued notice), never the silent plain `run_review`."""
    events, _ = inbox.run_events([_run("awaiting_review")], {"T9": settled})
    assert [(e["kind"], bool(e.get("silent"))) for e in events] == [("run_reopened", False)]


def test_a_queued_reopen_notice_rots_once_the_run_leaves_review():
    events, _ = inbox.run_events([_run("awaiting_review")], {"T9": "needs_human"})
    assert inbox.stale_reason(events[0], [_run("awaiting_review")]) is None
    assert inbox.stale_reason(events[0], [_run("changes_requested")])


def test_a_peer_orchestrator_is_matched_by_its_pid(monkeypatch):
    """🔴 GUARD: a windowless orchestrator (CMX-255) is itself only when the pids match."""
    _as(monkeypatch, None)
    store = inbox.load()
    store["orchestrator_peer"] = {"pid": 4242, "session": "s"}
    inbox.save(store)
    monkeypatch.setattr(orchestrator, "self_peer", lambda: {"pid": 4243, "session": "x"})
    assert inbox.caller_is_orchestrator() is False
    monkeypatch.setattr(orchestrator, "self_peer", lambda: {"pid": 4242, "session": "s"})
    assert inbox.caller_is_orchestrator() is True


def test_a_window_is_the_orchestrator_only_if_it_is_the_registered_one(monkeypatch):
    _as(monkeypatch, HUMAN)
    assert inbox.caller_is_orchestrator() is False
    _as(monkeypatch, ORCH)
    assert inbox.caller_is_orchestrator() is True


def test_an_announced_edge_that_never_happens_expires(monkeypatch):
    expected = {"T9": {"status": "closed", "why": inbox.ORCH_ECHO,
                       "ts": time.time() - inbox.EXPECT_EDGE_TTL_SECONDS - 1}}
    events, _ = inbox.run_events([_run("closed")], {"T9": "needs_human"}, expected=expected)
    assert [e["kind"] for e in events if not e.get("silent")] == ["run_was_closed"]


# --- 2. awaiting_review is silent; a judge that never starts is one notice -------------

def test_awaiting_review_is_silent_by_default_but_still_logged(monkeypatch):
    inbox.tick({}, runs=[_run("running")])
    inbox.tick({}, runs=[_run("awaiting_review")])
    assert _queued() == []
    events, _ = inbox.run_events([_run("awaiting_review")], {"T9": "running"})
    assert [(e["kind"], e.get("silent")) for e in events] == [("run_review", True)]


def test_awaiting_review_notice_can_be_turned_back_on(monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_NOTIFY_AWAITING_REVIEW", "true")
    inbox.tick({}, runs=[_run("running")])
    inbox.tick({}, runs=[_run("awaiting_review")])
    assert len(_queued("run_review")) == 1


def test_judge_not_started_fires_exactly_once_after_the_threshold(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "1200")
    store = inbox._empty()
    run = _run("awaiting_review", pr_head_sha="abc", judge_state="")
    t0 = 1_000_000.0
    assert inbox.judge_wait_events([run], store, t0) == []
    assert inbox.judge_wait_events([run], store, t0 + 1199) == []
    fired = inbox.judge_wait_events([run], store, t0 + 1201)
    assert [e["kind"] for e in fired] == ["run_judge_not_started"]
    assert "chela dispatch --hold-status" in fired[0]["summary"]
    assert "chela judge run" not in fired[0]["summary"]       # CMX-150: needs --experiments
    assert inbox.judge_wait_events([run], store, t0 + 5000) == []     # no repeat


@pytest.mark.parametrize("over", [
    {"judge_state": judge.J_RUNNING},                                   # a round is running
    {"judge_state": judge.J_CLEAN, "judge_sha": "abc"},                 # settled on this head
    {"judge_retry_after": "2026-10-12T10:00:00+00:00"},                 # outage backoff
])
def test_judge_not_started_is_silent_once_a_judge_round_exists(over, monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    store = inbox._empty()
    run = _run("awaiting_review", pr_head_sha="abc", **over)
    assert inbox.judge_wait_events([run], store, 1.0) == []
    assert inbox.judge_wait_events([run], store, 99_999.0) == []


def test_judge_not_started_skips_a_pr_that_already_merged_or_closed(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    for pr_state in ("merged", "closed"):
        run = _run("awaiting_review", pr_head_sha="abc", pr_state=pr_state)
        assert inbox.judge_wait_events([run], inbox._empty(), 99_999.0) == [], pr_state


def test_judge_not_started_a_new_head_is_a_new_wait(monkeypatch):
    """🔴 GUARD: the latch and the clock are PER HEAD — an agent's new push starts a new wait,
    and that wait gets its own notice once IT has run past the threshold."""
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "1200")
    store, t0 = inbox._empty(), 1_000_000.0
    a = _run("awaiting_review", pr_head_sha="aaa")
    inbox.judge_wait_events([a], store, t0)
    assert len(inbox.judge_wait_events([a], store, t0 + 1300)) == 1
    b = {**a, "pr_head_sha": "bbb"}
    assert inbox.judge_wait_events([b], store, t0 + 1400) == []          # its clock: 0s
    assert inbox.judge_wait_events([b], store, t0 + 1400 + 1199) == []
    fired = inbox.judge_wait_events([b], store, t0 + 1400 + 1201)
    assert [e["payload"]["pr_head_sha"] for e in fired] == ["bbb"]


def test_judge_not_started_said_once_survives_an_aborted_round(monkeypatch):
    """🔴 GUARD: alerted on this head, a judge round starts, then aborts back to "" on the SAME
    head — that is still the wait it was already told about, not a second notice."""
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    store = inbox._empty()
    run = _run("awaiting_review", pr_head_sha="abc", judge_state="")
    assert len(inbox.judge_wait_events([run], store, 10.0)) == 1
    assert inbox.judge_wait_events([{**run, "judge_state": judge.J_RUNNING}], store, 20.0) == []
    assert inbox.judge_wait_events([run], store, 99_999.0) == []


def test_a_verdict_on_an_OLDER_head_is_no_judge_round_on_this_one(monkeypatch):
    """🔴 GUARD: ``judge_sha`` names the head the judge settled on. The agent pushed since:
    nothing has judged THIS head, so the wait is real."""
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    run = _run("awaiting_review", pr_head_sha="new", judge_state=judge.J_CLEAN,
               judge_sha="old")
    fired = inbox.judge_wait_events([run], inbox._empty(), 99_999.0)
    assert [e["kind"] for e in fired] == ["run_judge_not_started"]


def test_judge_not_started_through_the_tick_is_one_notice(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    run = _run("awaiting_review", pr_head_sha="abc")
    for _ in range(3):
        inbox.tick({}, runs=[run])
    assert len(_queued("run_judge_not_started")) == 1
    assert _queued("run_review") == []


# --- 3. a dispatch hold that ends without the orchestrator ------------------------------

def test_a_hold_that_lapses_by_its_ttl_is_one_notice():
    held = hold.take(reason="rewriting the queue", ttl_seconds=60, by=ORCH)
    store = inbox._empty()
    assert inbox.hold_events(store, held, None, held.created_at + 1) == []      # live: seen
    ended = inbox.hold_events(store, held, None, held.expires_at + 1)           # lapsed
    assert [e["kind"] for e in ended] == ["dispatch_hold_expired"]
    assert inbox.hold_events(store, held, None, held.expires_at + 2) == []      # said once
    assert inbox.hold_events(store, None, None, held.expires_at + 3) == []      # file gone


def test_a_hold_released_by_somebody_else_is_one_notice_by_the_orchestrator_none():
    held = hold.take(ttl_seconds=600, by=ORCH)
    store = inbox._empty()
    inbox.hold_events(store, held, None, held.created_at + 1)
    hold.release(by="dashboard")
    ended = inbox.hold_events(store, None, hold.last_release(), held.created_at + 2)
    assert [e["kind"] for e in ended] == ["dispatch_hold_released"]
    assert "dashboard" in ended[0]["summary"]

    again = hold.take(ttl_seconds=600, by=ORCH)
    inbox.hold_events(store, again, None, again.created_at + 1)
    hold.release(by=ORCH, orchestrator=True)
    assert inbox.hold_events(store, None, hold.last_release(), again.created_at + 2) == []


def test_a_hold_that_lapsed_before_any_tick_saw_it_live_is_still_one_notice():
    """🔴 GUARD: a short hold can lapse between two ticks — the inbox never saw it live, and
    dispatch resumed all the same. One notice, not none (and not one per tick)."""
    held = hold.take(ttl_seconds=1, by=ORCH)
    store = inbox._empty()
    ended = inbox.hold_events(store, held, None, held.expires_at + 5)
    assert [e["kind"] for e in ended] == ["dispatch_hold_expired"]
    assert inbox.hold_events(store, held, None, held.expires_at + 6) == []


def test_a_stale_release_record_does_not_explain_a_later_lapse():
    """🔴 GUARD: the release record names the hold it released (``created_at``). An old
    record — the orchestrator's own resume of an EARLIER hold — must not silence the lapse
    of this one."""
    first = hold.take(ttl_seconds=600, by=ORCH)
    hold.release(by=ORCH, orchestrator=True)
    stale = hold.last_release()
    assert stale["created_at"] == first.created_at
    held = hold.Hold(reason="", by=ORCH, pid=1, created_at=first.created_at + 100,
                     expires_at=first.created_at + 160)
    store = inbox._empty()
    inbox.hold_events(store, held, stale, held.created_at + 1)
    ended = inbox.hold_events(store, held, stale, held.expires_at + 1)
    assert [e["kind"] for e in ended] == ["dispatch_hold_expired"]


def test_a_re_taken_hold_is_the_one_the_release_is_matched_against():
    """🔴 GUARD: re-taking a live hold replaces it (a new ``created_at``). The orchestrator's
    own resume of the NEW hold is an echo — not a "released by someone" notice."""
    store = inbox._empty()
    old = hold.Hold(reason="", by=ORCH, pid=1, created_at=1000.0, expires_at=1600.0)
    new = hold.Hold(reason="", by=ORCH, pid=1, created_at=1100.0, expires_at=1700.0)
    inbox.hold_events(store, old, None, 1001.0)
    inbox.hold_events(store, new, None, 1101.0)
    mine = {"created_at": 1100.0, "by": ORCH, "orchestrator": True, "ts": 1200.0}
    assert inbox.hold_events(store, None, mine, 1200.0) == []


def test_the_dashboards_resume_is_recorded_as_the_dashboards():
    """🔴 GUARD: the dashboard's Resume names itself, so the notice says WHO released it."""
    from chela.dashboard import app as dash
    hold.take(ttl_seconds=600, by=ORCH)
    assert dash.app.test_client().post("/api/dispatcher/resume").status_code == 200
    released = hold.last_release()
    assert (released["by"], released["orchestrator"]) == ("dashboard", False)


def test_the_orchestrators_own_resume_is_recorded_as_its_own(monkeypatch):
    _as(monkeypatch, ORCH)
    hold.take(ttl_seconds=600, by=ORCH)
    main.cmd_dispatch_hold(Namespace(resume=True, pause=False, hold_status=False))
    assert hold.last_release()["orchestrator"] is True


def test_the_tick_reads_the_release_record_someone_elses_release_is_released_not_expired():
    """🔴 GUARD (round 5): the tick must feed ``hold.last_release()`` to the hold events — a
    release by the dashboard is a 'released' notice, never mislabelled 'expired'; the
    orchestrator's own --resume stays silent."""
    hold.take(ttl_seconds=600, by=ORCH)
    inbox.tick({}, runs=[])                                  # the hold is seen live
    hold.release(by="dashboard", orchestrator=False)
    inbox.tick({}, runs=[])
    assert [e["kind"] for e in _queued() if e["kind"].startswith("dispatch_hold")] == [
        "dispatch_hold_released"]
    assert _queued("dispatch_hold_released")[0]["payload"]["released_by"] == "dashboard"
    # ...and the orchestrator's own release of the NEXT hold is an echo: nothing new queued.
    hold.take(ttl_seconds=600, by=ORCH)
    inbox.tick({}, runs=[])
    hold.release(by=ORCH, orchestrator=True)
    inbox.tick({}, runs=[])
    assert len([e for e in _queued() if e["kind"].startswith("dispatch_hold")]) == 1


def test_a_humans_resume_from_a_terminal_reaches_the_orchestrator(monkeypatch):
    """🔴 GUARD (round 5): ``chela dispatch --resume`` from a window that is NOT the registered
    orchestrator is recorded as not-the-orchestrator, and so is a notice."""
    _as(monkeypatch, HUMAN)
    hold.take(ttl_seconds=600, by=ORCH)
    inbox.tick({}, runs=[])
    main.cmd_dispatch_hold(Namespace(resume=True, pause=False, hold_status=False))
    assert hold.last_release()["orchestrator"] is False
    assert hold.last_release()["by"] == HUMAN
    inbox.tick({}, runs=[])
    assert len(_queued("dispatch_hold_released")) == 1
    assert _queued("dispatch_hold_expired") == []


def test_a_resume_still_releases_when_the_inbox_cannot_say_who_is_calling(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("inbox store unreadable")
    monkeypatch.setattr(inbox, "caller_is_orchestrator", boom)
    _as(monkeypatch, ORCH)
    hold.take(ttl_seconds=600, by=ORCH)
    main.cmd_dispatch_hold(Namespace(resume=True, pause=False, hold_status=False))
    assert hold.read() is None
    assert hold.last_release()["orchestrator"] is False      # unknown never reads as "mine"


def test_a_release_that_cannot_be_recorded_still_releases(monkeypatch, tmp_path):
    held = hold.take(ttl_seconds=600, by=ORCH)
    monkeypatch.setattr(hold, "release_path", lambda: tmp_path / "no-such-dir" / "r.json")
    assert hold.release(by="x").created_at == held.created_at
    assert hold.read() is None


def test_an_announced_edge_that_never_happens_cannot_swallow_a_real_one_later():
    """🔴 GUARD (round 5): the TTL is BOUNDED. An edge announced 30 minutes ago and never
    seen is forgotten, so a REAL edge to that status now is a notice."""
    expected = {"T9": {"status": "closed", "why": inbox.ORCH_ECHO, "ts": time.time() - 1800}}
    events, _ = inbox.run_events([_run("closed")], {"T9": "needs_human"}, expected=expected)
    assert [e["kind"] for e in events if not e.get("silent")] == ["run_was_closed"]
    assert "T9" not in expected


def test_a_fresh_announcement_still_silences_its_echo():
    """The other side of the TTL: a 60s-old announcement is live, the edge is an echo."""
    expected = {"T9": {"status": "closed", "why": inbox.ORCH_ECHO, "ts": time.time() - 60}}
    events, _ = inbox.run_events([_run("closed")], {"T9": "needs_human"}, expected=expected)
    assert [e["kind"] for e in events if not e.get("silent")] == []
    assert "T9" not in expected


def test_hold_expiry_through_the_tick_is_one_notice(monkeypatch):
    held = hold.take(ttl_seconds=1, by=ORCH)
    inbox.tick({}, runs=[])                          # seen live
    monkeypatch.setattr(inbox.time, "time", lambda: held.expires_at + 5)
    inbox.tick({}, runs=[])
    inbox.tick({}, runs=[])
    assert len(_queued("dispatch_hold_expired")) == 1


# --- 4. CI red on the head --------------------------------------------------------------

_GH_LOG = (
    "test (3.12)\tSet up job\t2026-10-12T10:00:00.0000000Z Current runner version: '2.3'\n"
    "test (3.12)\tRun tests\t2026-10-12T10:01:00.0000000Z E   assert 1 == 2\n"
    "test (3.12)\tRun tests\t2026-10-12T10:01:01.0000000Z FAILED tests/test_wall.py::test_grid"
    " - assert 1 == 2\n"
    "test (3.12)\tRun tests\t2026-10-12T10:01:02.0000000Z ##[error]Process completed with "
    "exit code 1.\n"
)


def test_first_failure_line_names_the_test():
    assert inbox.first_failure_line(_GH_LOG) == \
        "FAILED tests/test_wall.py::test_grid - assert 1 == 2"
    assert inbox.first_failure_line("lint\truff\t2026-10-12T10:00:00Z chela/x.py:3:1: F401 "
                                    "`os` imported but unused\n").startswith("chela/x.py:3:1")
    assert inbox.first_failure_line("") == ""


def test_ci_red_is_one_notice_with_the_job_and_the_failing_test():
    run = _run("awaiting_review", pr_head_sha="deadbee1")
    assert inbox.ci_red(run, sha="deadbee1", failing=("CI / test (3.12)",), run_ids=("42",),
                        log_tail=_GH_LOG, sent_back=True) is True
    assert inbox.ci_red(run, sha="deadbee1", failing=("CI / test (3.12)",), run_ids=("42",),
                        log_tail=_GH_LOG, sent_back=True) is False        # once per head
    [event] = _queued("run_ci_red")
    assert "job CI / test 3.12" in event["summary"]      # parens are sanitized out
    assert "FAILED tests/test_wall.py::test_grid" in event["summary"]
    assert "gh run rerun 42 --failed" in event["summary"]


def test_a_ci_red_notice_is_attributed_to_the_runs_agent_window(monkeypatch):
    """🔴 GUARD: the CI-red notice lands in the run's agent lane (its recorded window, in
    the current tmux epoch), never as an ownerless "chela itself" row."""
    monkeypatch.setattr(inbox.epoch, "current", lambda: "e1")
    run = _run("awaiting_review", pr_head_sha="cafe001", window_id="@41", window_epoch="e1")
    assert inbox.ci_red(run, sha="cafe001", failing=("test",), run_ids=(), log_tail="",
                        sent_back=False) is True
    [event] = _queued("run_ci_red")
    assert event["wid"] == "@41"


def test_a_red_on_a_NEW_head_is_a_new_notice():
    """🔴 GUARD: the latch is per (run, head sha) — the agent's next push going red is news."""
    run = _run("awaiting_review")
    for sha in ("aaa1111", "bbb2222"):
        assert inbox.ci_red({**run, "pr_head_sha": sha}, sha=sha, failing=("test",),
                            run_ids=(), log_tail="", sent_back=False) is True
    assert [e["payload"]["ci_sha"] for e in _queued("run_ci_red")] == ["aaa1111", "bbb2222"]


def test_first_failure_line_never_names_githubs_epilogue():
    """🔴 GUARD: ``##[error]Process completed with exit code 1`` says nothing about WHAT
    failed — with nothing better in the log, the answer is "" (no line), not that."""
    tail = ("test\tRun tests\t2026-10-12T10:01:02.0000000Z ##[error]Process completed with "
            "exit code 1.\n")
    assert inbox.first_failure_line(tail) == ""


def test_the_rework_a_ci_red_triggers_is_not_a_second_notice():
    inbox.tick({}, runs=[_run("awaiting_review")])
    inbox.ci_red(_run("awaiting_review"), sha="deadbee1", failing=("test",), run_ids=(),
                 log_tail=_GH_LOG, sent_back=True)
    inbox.tick({}, runs=[_run("changes_requested")])
    assert [e["kind"] for e in _queued()] == ["run_ci_red"]


def test_the_dispatchers_ci_loop_raises_the_notice(tmp_path, monkeypatch):
    from tests.test_dispatcher_ci import _check_run, _FakeGh, _row, _tick, _wf
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test (3.12)", conclusion="FAILURE")],
                   log="FAILED tests/test_wall.py::test_grid\n")
    _tick(wf, fake)
    [event] = _queued("run_ci_red")
    assert "FAILED tests/test_wall.py::test_grid" in event["summary"]
    assert event["payload"]["sent_back"] is True


def _red_row(tmp_path):
    from tests.test_dispatcher_ci import _row, _wf
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    return wf


def _real_edge_after(monkeypatch) -> list[dict]:
    """The run's NEXT real ``changes_requested`` edge (a human's verdict, say) — does it
    still reach the orchestrator, or does a leftover CI echo swallow it?"""
    inbox.tick({}, runs=[{**_run("awaiting_review"), "task_id": "abc123"}])
    inbox.tick({}, runs=[{**_run("changes_requested"), "task_id": "abc123"}])
    return _queued("run_changes_requested")


def test_a_cas_refused_ci_rework_retracts_the_echo_at_the_call_site(tmp_path, monkeypatch):
    """🔴 GUARD: drop the CAS-refused branch's ``_inbox_forget_ci_edge`` ⇒ the echo the CI
    loop announced outlives the rework that never happened ⇒ the run's next REAL
    ``changes_requested`` edge is swallowed as an echo ⇒ RED. Through ``dispatcher.tick``,
    not the helper."""
    from tests.test_dispatcher_ci import _check_run, _FakeGh, _tick
    wf = _red_row(tmp_path)
    refused = []
    monkeypatch.setattr(dispatcher, "request_changes",
                        lambda t, b: refused.append(t) or {"ok": False, "error": "moved"})
    _tick(wf, _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")]))
    assert refused == ["abc123"]                       # the refused branch really ran
    assert len(_queued("run_ci_red")) == 1
    assert "abc123" not in inbox.load()["expected_edges"]
    assert len(_real_edge_after(monkeypatch)) == 1


def test_a_db_refused_ci_rework_retracts_the_echo_at_the_call_site(tmp_path, monkeypatch):
    """🔴 GUARD: the same, for the branch where ``request_changes`` cannot even open the DB."""
    import sqlite3

    from tests.test_dispatcher_ci import _check_run, _FakeGh, _tick
    wf = _red_row(tmp_path)

    def busy(t, b):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(dispatcher, "request_changes", busy)
    _tick(wf, _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")]))
    assert len(_queued("run_ci_red")) == 1
    assert "abc123" not in inbox.load()["expected_edges"]
    assert len(_real_edge_after(monkeypatch)) == 1


def test_an_accepted_ci_rework_keeps_the_echo(tmp_path, monkeypatch):
    """The other side of the two guards above: when the rework IS written, the echo stays
    announced, so the ``changes_requested`` it causes is not a second notice."""
    from tests.test_dispatcher_ci import _check_run, _FakeGh, _tick
    wf = _red_row(tmp_path)
    monkeypatch.setattr(dispatcher, "request_changes", lambda t, b: {"ok": True})
    _tick(wf, _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")]))
    assert inbox.load()["expected_edges"]["abc123"]["why"] == inbox.CI_RED_ECHO


def test_an_infra_red_is_a_ci_red_notice_too_but_not_sent_back(tmp_path):
    """🔴 GUARD: drop the infra branch's ``_inbox_ci_red`` ⇒ a red the loop deliberately does
    NOT rework reaches nobody (no rework, no verdict row, no notice) ⇒ RED."""
    from tests.test_dispatcher_ci import _check_run, _FakeGh, _tick
    wf = _red_row(tmp_path)
    summary = _tick(wf, _FakeGh(rollup=[_check_run("test (3.12)",
                                                    conclusion="STARTUP_FAILURE")]))
    assert summary["ci_infra_failed"] == 1
    [event] = _queued("run_ci_red")
    assert event["payload"]["sent_back"] is False
    assert "NOT sent back" in event["summary"]
    assert "abc123" not in inbox.load()["expected_edges"]     # no rework ⇒ no echo


# --- 5. a stuck agent / an unread completion marker ---------------------------------------

def test_a_quiet_running_agent_is_one_notice_per_quiet_spell(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    store = inbox._empty()
    run = _run("running", worktree_path="/wt")
    now = 1_000_000.0
    probe = {"T9": {"activity": now - 1100, "markers": {}}}
    assert inbox.stuck_events([run], store, probe, now) == []
    probe = {"T9": {"activity": now - 1300, "markers": {}}}
    assert [e["kind"] for e in inbox.stuck_events([run], store, probe, now)] == ["run_stuck"]
    assert inbox.stuck_events([run], store, probe, now + 600) == []            # no repeat
    probe = {"T9": {"activity": now + 10, "markers": {}}}                      # woke up…
    assert inbox.stuck_events([run], store, probe, now + 1000) == []
    assert len(inbox.stuck_events([run], store, probe, now + 10 + 1300)) == 1   # …quiet again


def test_a_run_in_review_is_never_stuck(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "0")
    store = inbox._empty()
    probe = {"T9": {"activity": 1.0, "markers": {}}}
    assert inbox.stuck_events([_run("awaiting_review")], store, probe, 99_999.0) == []


def test_an_unread_marker_is_one_notice_with_the_daemon_uptime(monkeypatch):
    monkeypatch.setenv("CHELA_MARKER_UNREAD_ALERT_S", "300")
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "0")
    store = inbox._empty()
    now = 1_000_000.0
    probe = {"T9": {"activity": now - 5000, "markers": {"task-finished": now - 400}}}
    fired = inbox.stuck_events([_run("running")], store, probe, now, uptime=3 * 86400)
    assert [e["kind"] for e in fired] == ["run_marker_unread"]      # not ALSO "stuck"
    assert "daemon up 72h00m" in fired[0]["summary"]
    assert inbox.stuck_events([_run("running")], store, probe, now + 60, uptime=1) == []


def test_stuck_probes_read_the_worktree(tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    env = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_AUTHOR_DATE": "@1000000000 +0000", "GIT_COMMITTER_DATE": "@1000000000 +0000",
           "PATH": __import__("os").environ["PATH"], "HOME": str(tmp_path)}
    subprocess.run(["git", "init", "-q", str(wt)], check=True, env=env)
    subprocess.run(["git", "-C", str(wt), "commit", "-q", "--allow-empty", "-m", "x"],
                   check=True, env=env)
    (wt / ".chela-task-finished-request.json").write_text("{}")
    probes = inbox.stuck_probes([_run("running", worktree_path=str(wt))])
    assert probes["T9"]["activity"] == 1_000_000_000.0
    assert set(probes["T9"]["markers"]) == {"task-finished"}


def test_a_fresh_marker_is_not_yet_unread(monkeypatch):
    """🔴 GUARD: the daemon applies a marker on its next pass — one younger than the
    threshold is in flight, not unread."""
    monkeypatch.setenv("CHELA_MARKER_UNREAD_ALERT_S", "300")
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "999999")
    now = 1_000_000.0
    probe = {"T9": {"activity": now, "markers": {"push-request": now - 299}}}
    assert inbox.stuck_events([_run("running")], inbox._empty(), probe, now) == []


def test_a_just_started_agent_on_an_old_base_is_not_stuck(monkeypatch):
    """🔴 GUARD: a fresh worktree's last commit is its BASE — possibly days old. The run's own
    ``started_at`` is activity too, or every new agent is "stuck" on its first tick."""
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    now = time.time()
    run = _run("running", started_at=time.strftime("%Y-%m-%dT%H:%M:%S+00:00",
                                                    time.gmtime(now - 60)))
    probe = {"T9": {"activity": now - 5 * 86400, "markers": {}}}
    assert inbox.stuck_events([run], inbox._empty(), probe, now) == []
    assert len(inbox.stuck_events([run], inbox._empty(), probe, now + 1300)) == 1


def test_stuck_probes_count_the_transcript_as_activity(tmp_path, monkeypatch):
    """🔴 GUARD: an agent thinking (writing its transcript) without committing is NOT quiet."""
    wt = tmp_path / "wt"
    wt.mkdir()
    transcript = tmp_path / "t.jsonl"
    transcript.write_text("{}\n")
    import os
    os.utime(transcript, (2_000_000_000, 2_000_000_000))
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd",
                        lambda cwd: transcript if cwd == str(wt) else None)
    monkeypatch.setattr(inbox, "_git_last_commit", lambda w: 1_000_000_000.0)
    probes = inbox.stuck_probes([_run("running", worktree_path=str(wt))])
    assert probes["T9"]["activity"] == 2_000_000_000.0


@pytest.mark.parametrize("status", ["awaiting_review", "changes_requested", "done"])
def test_stuck_probes_skip_a_run_whose_agent_is_not_working(status, tmp_path, monkeypatch):
    wt = tmp_path / "wt"
    wt.mkdir()
    monkeypatch.setattr(inbox, "_git_last_commit", lambda w: 1.0)
    assert inbox.stuck_probes([_run(status, worktree_path=str(wt))]) == {}
    assert set(inbox.stuck_probes([_run("running", worktree_path=str(wt))])) == {"T9"}


def test_a_probe_that_misses_one_tick_does_not_repeat_the_stuck_notice(monkeypatch):
    """🔴 GUARD: the worktree briefly unreadable (no probe this tick) must not drop the latch —
    the same quiet spell, probed again, is not a second notice."""
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "60")
    store, now = inbox._empty(), 1_000_000.0
    probe = {"T9": {"activity": now - 3600, "markers": {}}}
    assert len(inbox.stuck_events([_run("running")], store, probe, now)) == 1
    assert inbox.stuck_events([_run("running")], store, {}, now + 30) == []
    assert inbox.stuck_events([_run("running")], store, probe, now + 60) == []


def test_an_unread_marker_through_the_tick_carries_the_daemon_uptime(monkeypatch, tmp_path):
    """🔴 GUARD: the tick must look the daemon up when a marker is pending — "a stale daemon
    is the usual cause" is only useful with its actual uptime."""
    monkeypatch.setenv("CHELA_MARKER_UNREAD_ALERT_S", "60")
    now = time.time()
    monkeypatch.setattr(inbox, "stuck_probes", lambda runs: {
        "T9": {"activity": now, "markers": {"task-finished": now - 3600}}})
    import json
    import os
    from chela import capabilities
    state = tmp_path / "daemon.json"
    state.write_text(json.dumps({"pid": os.getpid(), "ts": now - 2 * 86400,
                                 "capabilities": []}))
    monkeypatch.setattr(capabilities, "state_file", lambda: state)
    inbox.tick({}, runs=[_run("running")])
    [event] = _queued("run_marker_unread")
    assert "daemon up 48h" in event["summary"]
    assert 2 * 86400 - 60 < event["payload"]["daemon_uptime_s"] <= 2 * 86400 + 60


def test_stuck_through_the_tick_is_one_notice(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "60")
    quiet_since = time.time() - 3600
    monkeypatch.setattr(inbox, "stuck_probes", lambda runs: {
        "T9": {"activity": quiet_since, "markers": {}}})
    for _ in range(3):
        inbox.tick({}, runs=[_run("running")])
    assert len(_queued("run_stuck")) == 1


# --- 6. a PR that went CONFLICTING --------------------------------------------------------

def test_conflicting_is_one_notice_per_head():
    store = inbox._empty()
    a = _run("awaiting_review", pr_mergeable="CONFLICTING", pr_head_sha="aaa")
    assert [e["kind"] for e in inbox.conflict_events([a], store)] == ["run_pr_conflicting"]
    assert inbox.conflict_events([a], store) == []                              # same head
    assert inbox.conflict_events([{**a, "pr_mergeable": "UNKNOWN"}], store) == []
    assert inbox.conflict_events([a], store) == []                  # flapped back: same head
    b = {**a, "pr_head_sha": "bbb"}
    assert len(inbox.conflict_events([b], store)) == 1                          # new head
    assert inbox.conflict_events([{**b, "pr_mergeable": "MERGEABLE"}], store) == []


@pytest.mark.parametrize("over", [
    {"status": "done"}, {"status": "failed"}, {"status": "closed"},
    {"pr_state": "merged"}, {"pr_state": "closed"},
])
def test_a_settled_run_or_pr_is_never_conflicting_news(over):
    """🔴 GUARD: a CONFLICTING flag on a run nobody will merge (settled, or its PR already
    merged/closed) asks for no rebase."""
    run = {**_run("awaiting_review", pr_mergeable="CONFLICTING", pr_head_sha="aaa"), **over}
    assert inbox.conflict_events([run], inbox._empty()) == []


def test_conflicting_through_the_tick_is_one_notice():
    run = _run("awaiting_review", pr_mergeable="CONFLICTING", pr_head_sha="aaa")
    for _ in range(3):
        inbox.tick({}, runs=[run])
    assert len(_queued("run_pr_conflicting")) == 1


def test_a_conflict_notice_rots_once_the_pr_is_rebased():
    event = inbox.conflict_events([_run("awaiting_review", pr_mergeable="CONFLICTING",
                                        pr_head_sha="aaa")], inbox._empty())[0]
    assert inbox.stale_reason(event, [_run("awaiting_review", pr_mergeable="MERGEABLE",
                                           pr_head_sha="aaa")])          # resolved
    assert inbox.stale_reason(event, [_run("awaiting_review", pr_mergeable="CONFLICTING",
                                           pr_head_sha="bbb")])          # head moved
    assert inbox.stale_reason(event, [_run("awaiting_review", pr_mergeable="CONFLICTING",
                                           pr_head_sha="aaa")]) is None


# --- 7. every CMX-86 notice rots on delivery once its claim stops being true -------------

def _ci_red_event(sha="red1"):
    inbox.ci_red(_run("awaiting_review", pr_head_sha=sha), sha=sha, failing=("t",),
                 run_ids=(), log_tail="", sent_back=False)
    [event] = _queued("run_ci_red")
    return event


@pytest.mark.parametrize("run,stale", [
    (_run("awaiting_review", pr_head_sha="red1"), False),                # still red here
    (_run("changes_requested", pr_head_sha="red1"), False),              # being reworked
    (_run("running", pr_head_sha=None), False),                          # head unknown
    (_run("awaiting_review", pr_head_sha="new2"), True),                 # head moved on
    (_run("running", pr_head_sha="new2"), True),
    (_run("done", pr_head_sha="red1"), True),
    (_run("closed", pr_head_sha="red1"), True),
    (_run("awaiting_review", pr_head_sha="red1", pr_state="merged"), True),
])
def test_a_queued_ci_red_rots_once_its_red_commit_is_not_the_head(run, stale):
    """🔴 GUARD: disable the head-moved check ⇒ a notice about a commit the agent already
    pushed past is delivered as current news ⇒ RED."""
    reason = inbox.stale_reason(_ci_red_event(), [run])
    assert bool(reason) is stale, reason


def test_a_queued_ci_red_rots_through_the_delivery_path(monkeypatch):
    _ci_red_event()
    inbox.tick({}, runs=[_run("awaiting_review", pr_head_sha="new2")])
    assert _queued("run_ci_red") == []


@pytest.mark.parametrize("run,stale", [
    (_run("awaiting_review", pr_head_sha="abc"), False),
    (_run("running", pr_head_sha="abc"), True),
    (_run("awaiting_review", pr_head_sha="abc", judge_state=judge.J_RUNNING), True),
    (_run("awaiting_review", pr_head_sha="abc", pr_state="closed"), True),
])
def test_a_queued_judge_not_started_rots_once_a_judge_starts(run, stale, monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    [event] = inbox.judge_wait_events([_run("awaiting_review", pr_head_sha="abc")],
                                      inbox._empty(), 99_999.0)
    reason = inbox.stale_reason(event, [run])
    assert bool(reason) is stale, reason


@pytest.mark.parametrize("status,stale", [
    ("running", False), ("claimed", False), ("awaiting_review", True), ("failed", True)])
def test_a_queued_stuck_notice_rots_once_the_run_stops_running(status, stale, monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "0")
    probe = {"T9": {"activity": 1.0, "markers": {}}}
    [event] = inbox.stuck_events([_run("running")], inbox._empty(), probe, 99_999.0)
    reason = inbox.stale_reason(event, [_run(status)])
    assert bool(reason) is stale, reason


# --- orchestrator-added: every new CMX-86 notice lands in the run's agent lane -----------

def _attributed(over=None):
    return _run("awaiting_review", window_id="@41", window_epoch="e1", **(over or {}))


def test_judge_not_started_is_attributed_to_the_runs_window(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_NOT_STARTED_ALERT_S", "0")
    run = _attributed({"pr_head_sha": "abc", "judge_state": ""})
    [e] = inbox.judge_wait_events([run], inbox._empty(), 1_000_000.0, now_epoch="e1")
    assert e["kind"] == "run_judge_not_started" and e["wid"] == "@41"


def test_pr_conflicting_is_attributed_to_the_runs_window():
    run = _attributed({"pr_mergeable": "CONFLICTING", "pr_head_sha": "aaa"})
    [e] = inbox.conflict_events([run], inbox._empty(), now_epoch="e1")
    assert e["kind"] == "run_pr_conflicting" and e["wid"] == "@41"


def test_stuck_is_attributed_to_the_runs_window(monkeypatch):
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "1200")
    run = {**_attributed(), "status": "running", "worktree_path": "/wt"}
    now = 1_000_000.0
    probe = {"T9": {"activity": now - 1300, "markers": {}}}
    [e] = inbox.stuck_events([run], inbox._empty(), probe, now, now_epoch="e1")
    assert e["kind"] == "run_stuck" and e["wid"] == "@41"


def test_reopened_and_closed_notices_are_attributed_to_the_runs_window():
    """🔴 GUARD: run_reopened and run_was_closed land in the run's agent lane too."""
    att = {"window_id": "@41", "window_epoch": "e1"}
    events, _ = inbox.run_events([_run("awaiting_review", **att)], {"T9": "needs_human"},
                                 now_epoch="e1")
    assert [(e["kind"], e["wid"]) for e in events] == [("run_reopened", "@41")]
    expected = {"T9": {"status": "closed", "why": inbox.ORCH_ECHO,
                       "ts": time.time() - inbox.EXPECT_EDGE_TTL_SECONDS - 1}}
    events, _ = inbox.run_events([_run("closed", **att)], {"T9": "needs_human"},
                                 now_epoch="e1", expected=expected)
    closed = [e for e in events if e["kind"] == "run_was_closed"]
    assert [e["wid"] for e in closed] == ["@41"]


def test_a_rewritten_unread_marker_is_news_again(monkeypatch):
    """🔴 GUARD: once per marker WRITE — a marker rewritten (new mtime) and left unread is a
    new notice, not swallowed as the one already said."""
    monkeypatch.setenv("CHELA_MARKER_UNREAD_ALERT_S", "300")
    monkeypatch.setenv("CHELA_STUCK_AGENT_ALERT_S", "0")
    store = inbox._empty()
    now = 1_000_000.0
    first = {"T9": {"activity": now - 5000, "markers": {"task-finished": now - 400}}}
    assert len(inbox.stuck_events([_run("running")], store, first, now)) == 1
    again = {"T9": {"activity": now - 5000, "markers": {"task-finished": now + 100}}}
    fired = inbox.stuck_events([_run("running")], store, again, now + 500)
    assert [e["kind"] for e in fired] == ["run_marker_unread"]


# --- round 6: survivors of the judge's mutation battery ------------------------------------

@pytest.mark.parametrize("status,stale", [
    ("running", False), ("claimed", False), ("awaiting_review", True), ("failed", True)])
def test_a_queued_marker_unread_notice_rots_once_the_run_stops_running(status, stale,
                                                                       monkeypatch):
    """🔴 GUARD: the daemon applied the marker (run -> awaiting_review) while the notice sat
    queued: "your marker was not read" is now false and must not be delivered."""
    monkeypatch.setenv("CHELA_MARKER_UNREAD_ALERT_S", "60")
    now = 1_000_000.0
    probe = {"T9": {"activity": now, "markers": {"task-finished": now - 3600}}}
    [event] = inbox.stuck_events([_run("running")], inbox._empty(), probe, now)
    assert event["kind"] == "run_marker_unread"
    reason = inbox.stale_reason(event, [_run(status)])
    assert bool(reason) is stale, reason


# A run NAMED by something other than its task id: the inbox keys on `T9`, the operator types
# the branch (`cmx-9`) or the window name.
@pytest.mark.parametrize("typed", ["cmx-9", "the-window-name"])
@pytest.mark.parametrize("name", sorted(_ACTIONS))
def test_own_action_is_announced_under_the_resolved_task_id(name, typed, monkeypatch):
    """🔴 GUARD: ``chela <action> cmx-9`` must key the echo on the RESOLVED task id (T9), or the
    tick sees an unannounced edge and the orchestrator hears about its own action."""
    cmd, args, fn, before, after, kind = _ACTIONS[name]
    _as(monkeypatch, ORCH)
    run = lambda status, **o: _run(status, window_name="the-window-name", **o)  # noqa: E731
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: run(before))
    monkeypatch.setattr(dispatcher, fn, lambda *a, **k: {
        "ok": True, "task_id": "T9", "branch_name": "cmx-9", "reason": "superseded",
        "from_status": before, "comment_posted": True})
    inbox.tick({}, runs=[run(before)])
    cmd(Namespace(**{**vars(args), "run": typed}))
    assert list(inbox.load()["expected_edges"]) == ["T9"]
    inbox.tick({}, runs=[run(after, close_reason="superseded")])
    assert _queued(kind) == []
    assert inbox.load()["expected_edges"] == {}


@pytest.mark.parametrize("result", [
    {"ok": False, "error": "judge not clean", "tier": "escalate"},
    {"ok": True, "pending": True, "request_id": "r1"},
])
def test_merge_by_branch_name_announces_and_retracts_under_the_task_id(result, monkeypatch):
    """🔴 GUARD: ``chela merge cmx-9`` announces under T9, and a refused/pending merge retracts
    exactly that key (a retraction under the typed name would leave T9's echo behind)."""
    _as(monkeypatch, ORCH)
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: _run("awaiting_review"))
    seen = {}
    def merge(run, **k):
        seen["announced"] = list(inbox.load()["expected_edges"])
        return result
    monkeypatch.setattr(main.contract, "merge", merge)
    monkeypatch.setattr(main, "_print_override_request", lambda r: None)
    args = Namespace(run="cmx-9", reason="", override=False)
    if result["ok"]:
        main.cmd_merge(args)
    else:
        with pytest.raises(SystemExit):
            main.cmd_merge(args)
    assert seen["announced"] == ["T9"]
    assert inbox.load()["expected_edges"] == {}


# --- round 6: a changed-line mutation sweep (sweep.py vs origin/dev) -----------------------

def test_a_caller_with_no_window_and_no_registered_peer_is_not_the_orchestrator(monkeypatch):
    """🔴 GUARD: windowless caller, nothing registered via ``chela watch`` — False, without
    ever asking who the caller is (an own-peer with a pid must not be compared to nothing)."""
    _as(monkeypatch, None)
    store = inbox.load()
    store["orchestrator_peer"] = None
    monkeypatch.setattr(orchestrator, "self_peer", lambda: {"pid": 5})
    assert inbox.caller_is_orchestrator(store) is False


def test_a_windowless_peer_matching_the_registered_pid_is_the_orchestrator(monkeypatch):
    _as(monkeypatch, None)
    store = inbox.load()
    store["orchestrator_peer"] = {"pid": 5}
    monkeypatch.setattr(orchestrator, "self_peer", lambda: {"pid": 5})
    assert inbox.caller_is_orchestrator(store) is True
    monkeypatch.setattr(orchestrator, "self_peer", lambda: {"pid": 6})
    assert inbox.caller_is_orchestrator(store) is False


@pytest.mark.parametrize("task_id", [None, ""])
def test_no_edge_is_announced_without_a_task_id(task_id):
    assert inbox.expect_edge(task_id, "closed", inbox.ORCH_ECHO) is False
    assert inbox.load()["expected_edges"] == {}


def test_a_disabled_inbox_announces_and_retracts_nothing(monkeypatch):
    """🔴 GUARD: with the inbox off the CLI's own-action hooks are inert — nothing written,
    nothing retracted."""
    monkeypatch.setattr(inbox, "INBOX_ENABLED", False)
    assert inbox.expect_edge("T9", "closed", inbox.ORCH_ECHO) is False
    assert inbox.load()["expected_edges"] == {}
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    inbox.expect_edge("T9", "closed", inbox.ORCH_ECHO)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", False)
    inbox.forget_edge("T9")
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    assert "T9" in inbox.load()["expected_edges"]


def test_note_own_action_is_inert_when_disabled_or_without_a_task_id(monkeypatch):
    monkeypatch.setattr(inbox, "caller_is_orchestrator", lambda store=None: True)
    assert inbox.note_own_action(None, "closed") is False
    assert inbox.note_own_action("", "closed") is False
    monkeypatch.setattr(inbox, "INBOX_ENABLED", False)
    assert inbox.note_own_action("T9", "closed") is False
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    assert inbox.load()["expected_edges"] == {}
    assert inbox.note_own_action("T9", "closed") is True


def test_note_own_action_is_false_for_a_non_orchestrator_and_never_raises(monkeypatch):
    monkeypatch.setattr(inbox, "caller_is_orchestrator", lambda store=None: False)
    assert inbox.note_own_action("T9", "closed") is False
    assert inbox.load()["expected_edges"] == {}

    def boom(store=None):
        raise RuntimeError("registry unreadable")
    monkeypatch.setattr(inbox, "caller_is_orchestrator", boom)
    assert inbox.note_own_action("T9", "closed") is False
    assert inbox.load()["expected_edges"] == {}


def test_label_and_ref_helpers():
    assert inbox._label({"branch_name": "cmx-9", "task_id": "T9"}) == "cmx-9"
    assert inbox._label({"task_id": "T9"}) == "T9"
    assert inbox._label({}) == "?"
    assert inbox._ref({}) == "no PR link"
    ref = inbox._ref({"pr_url": "https://github.com/o/r/pull/9"})
    assert ref.endswith("— https://github.com/o/r/pull/9") and ref != "no PR link"


def test_judge_not_started_is_false_while_a_round_runs_or_backs_off():
    base = {"pr_head_sha": "abc"}
    assert inbox._judge_not_started(base) is True
    assert inbox._judge_not_started({**base, "judge_state": judge.J_RUNNING}) is False
    assert inbox._judge_not_started({**base, "judge_retry_after": "2026-10-12T10:00:00Z"}) is False
    assert inbox._judge_not_started({**base, "judge_sha": "abc"}) is False
    assert inbox._judge_not_started({**base, "judge_sha": "old"}) is True


def test_ci_red_declines_when_disabled_or_without_a_task_or_a_sha(monkeypatch):
    kw = dict(failing=("test",), run_ids=(), log_tail="", sent_back=True)
    run = _run("awaiting_review", pr_head_sha="abc")
    assert inbox.ci_red({**run, "task_id": ""}, sha="abc", **kw) is False
    assert inbox.ci_red(run, sha="", **kw) is False
    monkeypatch.setattr(inbox, "INBOX_ENABLED", False)
    assert inbox.ci_red(run, sha="abc", **kw) is False
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    assert _queued("run_ci_red") == [] and inbox.load()["expected_edges"] == {}
    assert inbox.ci_red(run, sha="abc", **kw) is True


def test_ci_red_is_written_to_the_event_log():
    """🔴 GUARD: the notice is also a durable log record (the dashboard feed reads the log)."""
    from chela import event_log
    inbox.ci_red(_run("awaiting_review", pr_head_sha="abc"), sha="abc", failing=("test",),
                 run_ids=(), log_tail="", sent_back=False)
    assert "run_ci_red" in [e["type"] for e in event_log.read()["events"]]


def test_stuck_probes_skips_a_run_whose_worktree_is_gone(tmp_path):
    gone = _run("running", worktree_path=str(tmp_path / "missing"))
    here = tmp_path / "wt"
    here.mkdir()
    probes = inbox.stuck_probes([gone, _run("running", task_id="T8", worktree_path=str(here))])
    assert list(probes) == ["T8"]


def test_parse_epoch_takes_a_number_as_is():
    assert inbox._parse_epoch(12) == 12.0
    assert inbox._parse_epoch(12.5) == 12.5
    assert inbox._parse_epoch(None) is None


def test_marker_unread_is_attributed_to_the_runs_window(monkeypatch):
    monkeypatch.setenv("CHELA_MARKER_UNREAD_ALERT_S", "60")
    run = {**_attributed(), "status": "running", "worktree_path": "/wt"}
    now = 1_000_000.0
    probe = {"T9": {"activity": now, "markers": {"task-finished": now - 3600}}}
    [e] = inbox.stuck_events([run], inbox._empty(), probe, now, now_epoch="e1")
    assert e["kind"] == "run_marker_unread" and e["wid"] == "@41"
