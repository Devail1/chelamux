"""⚖️🌩️ CMX-379: a judge stopped by a Claude Code auto-mode classifier outage.

Measured 2026-09-28, twice on PR #529: every Bash call the judge made came back as
"The server-side auto mode classifier gave no verdict (error) …", the harness stopped the
turn after 10 in a row, and the judge sat IDLE with no verdict, holding the only judge slot
until the 60-minute timeout reaped it as a COUNTED `cannot_verify`. That is the same kind of
environment hiccup as CMX-282's expired login, so the watchdog now reaps it early, uncounted,
and backs the run off before it is judged again.

``fixtures/judge_classifier_outage_2026-09-28.jsonl`` is the tail of the first of those real
judge transcripts, trimmed to the tool calls and tool results and with the home directory
redacted.
"""
from __future__ import annotations

import json
import os
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher, inbox, judge, transcripts
from tests.test_judge import _run_row, _tick, _wf

FIXTURE = Path(__file__).parent / "fixtures" / "judge_classifier_outage_2026-09-28.jsonl"
JUDGE_WID = "@77"
REASON = (
    "the judge hit a Claude Code auto-mode classifier outage (every tool call came back "
    "\"classifier gave no verdict\") — classifier outage, not a verdict on the PR"
)


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


@pytest.fixture
def projects(tmp_path, monkeypatch):
    """A private ``~/.claude/projects`` so `transcript_for_cwd` resolves the judge's file."""
    base = tmp_path / "claude-projects"
    base.mkdir()
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", base)
    return base


def _judge_run_record() -> list[dict]:
    """A later, SUCCESSFUL `chela judge run` — the real verdict the judge got through to.

    Shaped like the recovered 14:29 judge on the same PR: the command went to the background
    and the tool result reported it running, with no error.
    """
    return [
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "tool_use", "id": "toolu_judge_run", "name": "Bash",
             "input": {"command": "chela judge run abc123 --experiments exp.json"}}]},
         "uuid": "u-run", "timestamp": "2026-09-28T11:40:00.000Z"},
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_judge_run", "is_error": False,
             "content": "Command running in background with ID: bqcz2a17w."}]},
         "uuid": "u-res", "timestamp": "2026-09-28T11:40:01.000Z"},
    ]


def _write_transcript(projects: Path, wf, extra: list[dict] = ()) -> Path:
    cwd = str(judge.judge_worktree_path(wf, "abc123"))
    d = projects / transcripts.encode_cwd(cwd)
    d.mkdir(parents=True, exist_ok=True)
    path = d / "judge-session.jsonl"
    text = FIXTURE.read_text()
    for rec in extra:
        text += json.dumps(rec) + "\n"
    path.write_text(text)
    return path


def _spawn(wf, sha="cafe1234"):
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        with patch.object(dispatcher, "detached_worktree", return_value=(None, True)), \
             patch.object(dispatcher, "render_prompt", return_value="x"), \
             patch.object(dispatcher, "_judge_vars", return_value={}), \
             patch.object(dispatcher, "_launch_agent", return_value=None):
            assert dispatcher._spawn_judge(wf, row, sha, conn) is True
        # `_launch_agent` stamps this in production; it is patched out above.
        conn.execute("UPDATE runs SET judge_window_id=? WHERE task_id='abc123'", (JUDGE_WID,))
        conn.commit()


def _state():
    r = dispatcher.resolve_run("abc123")
    return r["judge_state"], r["judge_cannot_verify_tries"], r["judge_no_verdict"]


def _watchdog(wf, status: str | None, *, lock_live=False):
    """One watchdog pass with the judge's window alive and its native status = ``status``.

    ⛔ Every tmux touch is spied: `_capture_pane` answers an ordinary idle pane,
    `_kill_windows_named` is a mock, and `_agent_status` answers only for the judge's OWN
    window id (a flat return_value could not tell a wrong-window read from the right one).
    """
    window = judge.judge_window_name("test-1")
    status_calls = []

    def _status(wid):
        status_calls.append(wid)
        return status if wid == JUDGE_WID else None

    with dispatcher._db() as conn:
        with patch.object(dispatcher, "_capture_pane", return_value="❯ "), \
             patch.object(dispatcher, "_agent_status", side_effect=_status), \
             patch.object(dispatcher, "_kill_windows_named") as kill, \
             patch.object(dispatcher, "remove_worktree", return_value=True) as rm, \
             patch.object(judge, "judge_lock_live", return_value=lock_live):
            handed = dispatcher._judge_watchdog(conn, wf, live_windows={window})
        conn.commit()
    return handed, kill, rm, window, status_calls


# --- the detector, on the real 09-28 transcript shape -----------------------------------

def test_the_real_0928_transcript_tail_reads_as_a_classifier_outage():
    assert dispatcher._transcript_shows_classifier_outage(FIXTURE)


def test_an_outage_followed_by_a_successful_judge_run_is_not_an_outage(tmp_path):
    """⭐ The case that must still be ACCEPTED: the signature EARLIER in the transcript, then
    a real `chela judge run` that got through. Only the tail counts."""
    path = tmp_path / "t.jsonl"
    path.write_text(FIXTURE.read_text() + "".join(json.dumps(r) + "\n" for r in _judge_run_record()))
    assert not dispatcher._transcript_shows_classifier_outage(path)


# --- the watchdog arm --------------------------------------------------------------------

def test_an_idle_judge_stuck_on_a_classifier_outage_is_reaped_uncounted(
        tmp_path, projects, monkeypatch):
    """Reaped like the login-expired arm: CANNOT VERIFY, `judge_no_verdict=1`, retry budget
    untouched, window killed, worktree removed, and a backoff stamped from the knob."""
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")
    monkeypatch.setenv("CHELA_JUDGE_OUTAGE_BACKOFF_S", "600")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
    _spawn(wf)
    _write_transcript(projects, wf)

    handed, kill, rm, window, status_calls = _watchdog(wf, "idle")

    assert handed == 1
    assert JUDGE_WID in status_calls           # idle was read from the judge's OWN window
    kill.assert_called_once_with(window)
    rm.assert_called_once()
    assert _state() == (judge.J_CANNOT_VERIFY, 0, 1)
    r = dispatcher.resolve_run("abc123")
    assert r["judge_detail"] == REASON
    retry_after = dispatcher._parse_ts(r["judge_retry_after"])
    now = dispatcher._parse_ts(dispatcher._now())
    assert timedelta(seconds=590) <= retry_after - now <= timedelta(seconds=600)

    # Re-launching the SAME sha is the first real attempt, not a retry.
    _spawn(wf)
    assert _state() == (judge.J_RUNNING, 0, 0)
    assert dispatcher.resolve_run("abc123")["judge_retry_after"] is None


def test_an_idle_judge_that_got_a_verdict_through_after_the_outage_is_not_reaped(
        tmp_path, projects):
    """⭐ Negative control for the tail-only rule, end to end through the watchdog."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
    _spawn(wf)
    _write_transcript(projects, wf, _judge_run_record())

    handed, kill, rm, _, _ = _watchdog(wf, "idle")

    assert handed == 0
    kill.assert_not_called()
    rm.assert_not_called()
    assert _state() == (judge.J_RUNNING, 0, 0)


def test_a_busy_judge_with_the_signature_is_left_alone(tmp_path, projects):
    """A busy judge may be retrying right now — the transcript alone never reaps it."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
    _spawn(wf)
    _write_transcript(projects, wf)

    handed, kill, _, _, _ = _watchdog(wf, "busy")

    assert handed == 0
    kill.assert_not_called()
    assert _state() == (judge.J_RUNNING, 0, 0)


def test_an_older_judges_outage_transcript_does_not_reap_a_new_judge(tmp_path, projects):
    """The judge worktree path is reused per task, so its transcript dir keeps earlier
    judges' files. One last written before THIS judge started is not its evidence."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
    _spawn(wf)
    path = _write_transcript(projects, wf)
    old = time.time() - 3600
    os.utime(path, (old, old))

    handed, kill, _, _, _ = _watchdog(wf, "idle")

    assert handed == 0
    kill.assert_not_called()
    assert _state() == (judge.J_RUNNING, 0, 0)


def test_a_live_judge_lock_still_holds_an_outage_reap(tmp_path, projects):
    """Unlike `login_expired`, an outage does NOT bypass the CMX-229 lock cross-check: a live
    judge lock means `chela judge run` is executing — a verdict in flight."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path))
    _spawn(wf)
    _write_transcript(projects, wf)

    handed, kill, _, _, _ = _watchdog(wf, "idle", lock_live=True)

    assert handed == 0
    kill.assert_not_called()
    assert _state() == (judge.J_RUNNING, 0, 0)


# --- the backoff at the trigger -----------------------------------------------------------

def _outage_row(tmp_path, wf, retry_after):
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), judge_sha="cafe1234",
                 judge_state=judge.J_CANNOT_VERIFY, judge_detail=REASON, judge_no_verdict=1,
                 judge_cannot_verify_tries=0, judge_retry_after=retry_after.isoformat())


def test_an_outage_run_is_not_re_judged_before_its_backoff(tmp_path):
    wf = _wf(tmp_path)
    now = dispatcher._parse_ts(dispatcher._now())
    _outage_row(tmp_path, wf, now + timedelta(minutes=5))
    spawns = []
    _tick(wf, lambda w, row, sha, conn, task=None: spawns.append(sha) or True)
    assert spawns == []


def test_an_outage_run_is_re_judged_once_its_backoff_passes(tmp_path):
    wf = _wf(tmp_path)
    now = dispatcher._parse_ts(dispatcher._now())
    _outage_row(tmp_path, wf, now - timedelta(seconds=1))
    spawns = []
    _tick(wf, lambda w, row, sha, conn, task=None: spawns.append(sha) or True)
    assert spawns == ["cafe1234"]


# --- the inbox ----------------------------------------------------------------------------

def _run_dict(**over):
    run = {"task_id": "abc123", "status": "awaiting_review", "branch_name": "cmx-9",
           "title": "t", "pr_url": "https://github.com/o/r/pull/91", "judge_sha": "cafe1234",
           "judge_state": judge.J_CANNOT_VERIFY, "judge_detail": REASON,
           "judge_retry_after": "2026-09-28T15:00:00+00:00"}
    run.update(over)
    return run


def test_the_outage_is_surfaced_once_on_the_inbox(monkeypatch):
    monkeypatch.setenv("CHELA_JUDGE_OUTAGE_BACKOFF_S", "600")
    seen = {"abc123": "awaiting_review:running"}
    events, seen = inbox.run_events([_run_dict()], seen)
    assert [e["summary"] for e in events] == [
        "⚖️🌩️ judge for cmx-9 hit a classifier outage — re-judging after 10m — "
        "PR #91 — https://github.com/o/r/pull/91"
    ]
    # The next tick, and the one after: nothing new.
    for _ in range(2):
        events, seen = inbox.run_events([_run_dict()], seen)
        assert events == []


def test_an_ordinary_cannot_verify_keeps_its_human_look_wording():
    """Negative control: without a backoff stamp the event is the old `needs a human look`."""
    events, _ = inbox.run_events([_run_dict(judge_retry_after=None, judge_detail="a flake")],
                                 {"abc123": "awaiting_review:running"})
    assert len(events) == 1
    assert "needs a human look" in events[0]["summary"]
    assert "🌩️" not in events[0]["summary"]


def test_any_later_judge_state_write_clears_the_outage_stamp(tmp_path):
    """`judge_retry_after` means "the CURRENT cannot_verify is an outage" — so a later
    verdict (or any other `set_judge_state`) must clear it, or a real cannot_verify after it
    would be misreported on the inbox as an outage with no human look needed."""
    wf = _wf(tmp_path)
    now = dispatcher._parse_ts(dispatcher._now())
    _outage_row(tmp_path, wf, now + timedelta(minutes=5))
    dispatcher.set_judge_state("abc123", judge.J_CANNOT_VERIFY, "a real flake")
    assert dispatcher.resolve_run("abc123")["judge_retry_after"] is None
