"""⏹️⚖️ CMX-286: a judge whose run was sent back (or whose head moved) is STOPPED at once.

Seen twice on 10-19/20: CMX-264's conflict handling sent a PR back while its judge kept
running on the obsolete head, holding one of the two judge slots for up to an hour, and the
first ended `cannot_verify … run already moved to running`. These tests pin: the judge tree
is signalled, the slot is free that tick, `judge_cancelled` is recorded, no verdict notice
fires — and a judge whose run did NOT move is untouched.

`os.kill` is a spy and the process tree is faked: no real pid is ever signalled.
"""
from __future__ import annotations

import json
import os
import signal
from unittest.mock import patch

import pytest

from chela import dispatcher, event_log, inbox, judge
from chela.worktree import NotAWorktree
from tests.test_judge import (
    FAKE_GUARD_TEST,
    _blocking_run_with_heads,
    _exp,
    _run_row,
    _tick,
    _wf,
    _workflow_repo,
)

JUDGE_PID, SUITE_PID = 4242, 4243           # fake: os.kill is a spy, never the real thing
STARTED = "2026-10-20T10:00:00+00:00"


@pytest.fixture(autouse=True)
def _own_state(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(judge, "judge_logs_dir", lambda: tmp_path / "judge-logs")


@pytest.fixture
def kills(monkeypatch):
    """Spy `os.kill` and fake the judge's live, detached run and its process tree."""
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(judge.os, "kill", lambda pid, sig: sent.append((pid, sig)))
    monkeypatch.setattr(judge.procguard, "process_tree",
                        lambda roots: [*roots, SUITE_PID] if roots == [JUDGE_PID] else [])
    monkeypatch.setattr(judge, "live_judge_run",
                        lambda task, wt=None: {"pid": JUDGE_PID, "detached": True})
    return sent


def _judging(tmp_path, **over):
    wf = _wf(tmp_path)
    fields = {"workflow_path": str(wf.path), "judge_state": judge.J_RUNNING,
              "judge_sha": "cafe1234", "judge_started_at": STARTED}
    fields.update(over)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, **fields)
    return wf


def _slots() -> int:
    with dispatcher._db() as conn:
        return conn.execute("SELECT COUNT(*) FROM runs WHERE judge_state=?",
                            (judge.J_RUNNING,)).fetchone()[0]


def _cancel(wf, conn=None):
    events: list[tuple] = []
    with patch.object(dispatcher, "_kill_windows_named") as killed, \
         patch.object(dispatcher, "remove_worktree", return_value=True) as removed, \
         patch.object(event_log, "append",
                      side_effect=lambda *a, **k: events.append((a, k))):
        if conn is None:
            with dispatcher._db() as conn:
                n = dispatcher._cancel_moot_judges(conn, wf)
        else:
            n = dispatcher._cancel_moot_judges(conn, wf)
    return n, events, killed, removed


@pytest.mark.parametrize("status", ["changes_requested", "running", "claimed"])
def test_a_sent_back_run_has_its_judge_tree_stopped_and_its_slot_freed(tmp_path, kills, status):
    wf = _judging(tmp_path, status=status)
    assert _slots() == 1
    n, events, killed, removed = _cancel(wf)
    assert n == 1
    assert kills == [(JUDGE_PID, signal.SIGTERM), (SUITE_PID, signal.SIGTERM)]
    assert _slots() == 0
    run = dispatcher.resolve_run("abc123")
    assert run["judge_state"] == "cancelled"
    assert run["judge_detail"] == f"judge cancelled: its run was sent back (now {status!r})"
    killed.assert_called_once_with("judge-test-1")
    assert removed.call_count == 1
    assert removed.call_args.args[1] == judge.judge_worktree_path(wf, "abc123")
    [(args, kwargs)] = events
    assert args[0] == "judge_cancelled"
    assert kwargs["payload"]["reason"] == f"its run was sent back (now {status!r})"
    assert kwargs["payload"]["pids"] == [JUDGE_PID, SUITE_PID]


def test_a_moved_head_under_review_cancels_the_round(tmp_path, kills):
    wf = _judging(tmp_path, pr_head_sha="beef5678")
    assert _cancel(wf)[0] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["judge_state"] == "cancelled"
    assert run["judge_detail"] == "judge cancelled: its PR head moved (cafe1234 → beef5678)"
    assert (JUDGE_PID, signal.SIGTERM) in kills


@pytest.mark.parametrize("over", [
    {},                                                    # under review, same head
    {"judge_sha": None, "pr_head_sha": "beef5678"},        # judged sha unknown
    {"pr_head_sha": None},                                 # live head unknown
    {"status": "done"},                                    # merged: CMX-239's race verdict
    {"status": "needs_human"},
    {"judge_state": judge.J_CLEAN, "status": "changes_requested"},   # not a live round
])
def test_a_judge_whose_run_did_not_move_is_untouched(tmp_path, kills, over):
    wf = _judging(tmp_path, **over)
    before = dispatcher.resolve_run("abc123")["judge_state"]
    n, events, killed, removed = _cancel(wf)
    assert (n, kills, events, killed.call_count, removed.call_count) == (0, [], [], 0, 0)
    assert dispatcher.resolve_run("abc123")["judge_state"] == before


def test_a_launch_in_flight_in_another_live_process_is_left_to_it(tmp_path, kills):
    owner = json.dumps(judge.owner_identity(os.getppid()))
    wf = _judging(tmp_path, status="changes_requested", judge_launch_owner=owner)
    assert _cancel(wf)[0] == 0
    assert dispatcher.resolve_run("abc123")["judge_state"] == judge.J_RUNNING
    assert kills == []


def test_an_interrupted_launch_is_cancelled(tmp_path, kills):
    owner = json.dumps(judge.owner_identity(os.getpid()))   # this process: interrupted
    wf = _judging(tmp_path, status="changes_requested", judge_launch_owner=owner)
    assert _cancel(wf)[0] == 1
    assert dispatcher.resolve_run("abc123")["judge_launch_owner"] is None


def test_a_verdict_that_landed_mid_cancel_is_left_alone(tmp_path, kills):
    """The CAS: the judge settled between the SELECT and the UPDATE — nothing is signalled."""
    wf = _judging(tmp_path, status="changes_requested")

    class Racing:
        def __init__(self, conn):
            self._c = conn

        def execute(self, sql, *a):
            cur = self._c.execute(sql, *a)
            if sql.startswith("SELECT"):
                rows = cur.fetchall()
                self._c.execute("UPDATE runs SET judge_state=?", (judge.J_BLOCKED,))
                self._c.commit()
                return type("C", (), {"fetchall": lambda _s: rows})()
            return cur

        def commit(self):
            self._c.commit()

    with dispatcher._db() as conn:
        n, events, killed, _ = _cancel(wf, Racing(conn))
    assert (n, kills, events, killed.call_count) == (0, [], [], 0)
    assert dispatcher.resolve_run("abc123")["judge_state"] == judge.J_BLOCKED


def test_a_worktree_the_reaper_refuses_does_not_crash_the_cancel(tmp_path, kills):
    wf = _judging(tmp_path, status="changes_requested")
    with patch.object(dispatcher, "_kill_windows_named"), \
         patch.object(dispatcher, "remove_worktree", side_effect=NotAWorktree("nope")), \
         patch.object(event_log, "append") as appended, \
         dispatcher._db() as conn:
        assert dispatcher._cancel_moot_judges(conn, wf) == 1
    assert appended.call_args.args[0] == "judge_cancelled"


def test_the_daemon_tick_cancels_before_the_watchdog_holds(tmp_path, kills):
    """Wired into `tick`: without the cancel, the watchdog's "still alive — holding" keeps
    the slot (the 10-19/20 incident)."""
    wf = _judging(tmp_path, status="changes_requested")
    summary = _tick(wf, lambda *a, **k: False)
    assert summary["judge_cancelled"] == 1
    assert _slots() == 0
    assert (JUDGE_PID, signal.SIGTERM) in kills


def test_a_cancelled_round_raises_no_verdict_notice(tmp_path, kills):
    for status, mark in (("changes_requested", "awaiting_review:running"),
                         ("awaiting_review", "awaiting_review:running")):
        task = f"t-{status}"
        wf = _judging(tmp_path, task_id=task, status=status,
                      pr_head_sha="beef5678" if status == "awaiting_review" else "cafe1234")
        _cancel(wf)
        run = dispatcher.resolve_run(task)
        assert run["judge_state"] == "cancelled"
        events, _ = inbox.run_events([dict(run)], {task: mark})
        assert [e for e in events if "judge" in e["kind"]] == [], events


def test_a_cancelled_round_on_the_same_head_is_judged_again(tmp_path):
    """A rework that pushed nothing lands back on the judged sha: it must not strand."""
    wf = _judging(tmp_path, judge_state="cancelled")
    with dispatcher._db() as conn:
        assert [r["task_id"] for r in dispatcher._judge_candidates(conn, wf)] == ["abc123"]


@pytest.mark.parametrize("by_judge, after", [
    (True, "blocked"),          # the judge's own verdict: it settles in the same write
    (False, "cancelled"),       # a human / CI send-back: the live judge is moot
])
def test_the_judges_own_send_back_is_never_read_as_someone_elses(tmp_path, kills, by_judge,
                                                                 after):
    """Review finding: `request_changes` then `set_judge_state(J_BLOCKED)` left a window where
    the judge's OWN verdict read as a send-back, and the cancel killed it mid-verdict."""
    wf = _judging(tmp_path)
    extra = {"judge_state": judge.J_BLOCKED} if by_judge else {}
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        assert dispatcher.request_changes("abc123", "a guard survived", **extra)["ok"]
    assert dispatcher.resolve_run("abc123")["status"] == "changes_requested"
    _cancel(wf)
    assert dispatcher.resolve_run("abc123")["judge_state"] == after
    assert bool(kills) is not by_judge


def test_judge_run_settles_its_verdict_in_the_send_back_write(tmp_path):
    exp_file = _blocking_run_with_heads(tmp_path, row_head="oldsha000001")
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET judge_state=?", (judge.J_RUNNING,))
        conn.commit()
    seen: list[str] = []
    real = dispatcher.request_changes

    def spy(*a, **k):
        out = real(*a, **k)
        seen.append(dispatcher.resolve_run("abc123")["judge_state"])
        return out

    with patch.object(dispatcher, "pr_live_head_sha", return_value="oldsha000001"), \
         patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")), \
         patch.object(dispatcher, "request_changes", side_effect=spy):
        assert judge.judge_run("abc123", exp_file, cleanup=False)["state"] == judge.J_BLOCKED
    assert seen == ["blocked"]


def test_a_stop_that_raises_still_frees_the_slot_and_reaps_the_worktree(tmp_path, monkeypatch,
                                                                         kills):
    def boom(task, wt):
        raise PermissionError("EPERM")

    monkeypatch.setattr(judge, "cancel_judge_run", boom)
    wf = _judging(tmp_path, status="changes_requested")
    n, events, _, removed = _cancel(wf)
    assert (n, _slots(), removed.call_count) == (1, 0, 1)
    assert events[0][1]["payload"]["pids"] == []


def test_every_tick_summary_shape_carries_judge_cancelled():
    assert dispatcher._refused(None)["judge_cancelled"] == 0


# --- judge.cancel_judge_run ---------------------------------------------------------------

def test_a_frozen_tree_is_thawed_on_the_ledger_then_continued_before_its_term(tmp_path, kills):
    judge.write_freeze("abc123", {"round": STARTED, "intervals": [[100.0, None]]})
    assert judge.cancel_judge_run("abc123", tmp_path / "wt") == [JUDGE_PID, SUITE_PID]
    assert kills == [(JUDGE_PID, signal.SIGCONT), (SUITE_PID, signal.SIGCONT),
                     (JUDGE_PID, signal.SIGTERM), (SUITE_PID, signal.SIGTERM)]
    assert judge.read_freeze("abc123")["intervals"][0][1] is not None


def test_a_closed_freeze_gets_no_sigcont(tmp_path, kills):
    judge.write_freeze("abc123", {"round": STARTED, "intervals": [[100.0, 200.0]]})
    judge.cancel_judge_run("abc123", tmp_path / "wt")
    assert kills == [(JUDGE_PID, signal.SIGTERM), (SUITE_PID, signal.SIGTERM)]


def test_one_gone_pid_does_not_spare_the_rest(tmp_path, monkeypatch, kills):
    sent: list[int] = []

    def kill(pid, sig):
        if pid == JUDGE_PID:
            raise ProcessLookupError(pid)
        sent.append(pid)

    monkeypatch.setattr(judge.os, "kill", kill)
    assert judge.cancel_judge_run("abc123", tmp_path / "wt") == [JUDGE_PID, SUITE_PID]
    assert sent == [SUITE_PID]


@pytest.mark.parametrize("live", [
    None,
    {"pid": JUDGE_PID, "detached": False},           # foreground: its window kill reaches it
    {"pid": os.getpid(), "detached": True},           # never this process
    {"pid": "4242", "detached": True},
])
def test_nothing_is_signalled_without_a_live_detached_run_of_another_process(
        tmp_path, monkeypatch, kills, live):
    monkeypatch.setattr(judge, "live_judge_run", lambda task, wt=None: live)
    assert judge.cancel_judge_run("abc123", tmp_path / "wt") == []
    assert kills == []


# --- judge_run: a cancelled round publishes nothing ---------------------------------------

def test_a_judge_that_outlives_its_cancel_publishes_no_verdict(tmp_path):
    repo = _workflow_repo(tmp_path, "abc123", FAKE_GUARD_TEST)
    with dispatcher._db() as conn:
        _run_row(conn, repo, judge_state="cancelled",
                 judge_detail="judge cancelled: its run was sent back (now 'running')")
    exp_file = tmp_path / "experiments.json"
    exp_file.write_text(json.dumps({"experiments": [_exp()]}))
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")) as posted:
        result = judge.judge_run("abc123", exp_file, cleanup=False)
    assert result["state"] == "cancelled"
    assert result["ok"] is False
    assert posted.call_count == 0
    run = dispatcher.resolve_run("abc123")
    assert (run["status"], run["judge_state"]) == ("awaiting_review", "cancelled")


@pytest.mark.parametrize("state, says, never", [
    ("cancelled", "no verdict was published", "every guard held"),
    ("clean", "every guard held", "no verdict was published"),      # control: only a cancel
])
def test_the_cli_reports_a_cancelled_round_not_a_clean_one(capsys, monkeypatch, state, says,
                                                           never):
    from chela import main

    fake = {"ok": state == "clean", "task_id": "cmx-99", "state": state, "outcomes": [],
            "error": "the round was cancelled: judge cancelled: its PR head moved"}
    monkeypatch.setattr(main.judge, "judge_run", lambda *a, **k: fake)
    monkeypatch.setattr("sys.argv", ["chela", "judge", "run", "cmx-99", "--experiments", "x.json"])
    main.main()
    out = capsys.readouterr().out
    assert says in out
    assert never not in out
