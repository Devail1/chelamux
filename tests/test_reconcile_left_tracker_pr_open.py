"""🗂️⏸️ CMX-308 — a run whose task LEFT the tracker while its PR is still OPEN is held, not done.

Linear's GitHub integration moves an issue to Done when any merged PR names it. On
2026-10-22 that closed CMX-119 through another task's PR while CMX-119's own PR sat OPEN
(green, mergeable); the next tick read "left the tracker" as done-evidence, marked the run
`done` and killed its window. `done` is terminal: reopen, adopt and requeue all refused.

Now a review-state row whose task leaves the tracker keeps its status, window and worktree
and carries a visible `tracker_note` until its PR merges or closes — the merged and
closed-PR branches of reconcile finish it, exactly as they finish any other review row.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from chela import dispatcher

from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf

PR_URL = "https://github.com/o/r/pull/831"
TID = "abc123"
# Literal on purpose — never the module's own constant (it is the text a human reads).
HELD_OPEN = "issue left the tracker's ready states; PR still open"
HELD_UNREADABLE = "issue left the tracker's ready states; PR state could not be read"
HELD_CLOSED = "issue left the tracker's ready states; PR is closed"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _tick(wf, pr_status, *, source=None, read_pr_url=None):
    """One real `tick()`. `pr_status` is a list of `_read_pr_status` answers, one per call
    in order (the last one repeats) — phase 0's refresh first, then reconcile's own read."""
    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    cleaned: list[str] = []
    answers = list(pr_status)
    asked: list[str] = []

    def read_pr_status(url, repo_dir):
        asked.append(url)
        return answers.pop(0) if len(answers) > 1 else answers[0]

    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source or _Source()), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-1"}), \
         patch.object(dispatcher, "_respawn_rework", return_value=False), \
         patch.object(dispatcher, "_fire_after_done", lambda wf: None), \
         patch.object(dispatcher, "_read_pr_url", return_value=read_pr_url), \
         patch.object(dispatcher, "_read_pr_status", side_effect=read_pr_status), \
         patch.object(dispatcher, "_cleanup_worktree_on_done",
                      side_effect=lambda wf_, row: cleaned.append(row["task_id"])), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)
    killed = [c for c in fake.calls if isinstance(c, list) and c[:2] == ["tmux", "kill-window"]]
    return summary, killed, cleaned, asked


def _seed(wf, status, **over):
    fields = dict(workflow_path=str(wf.path), status=status, window_name="test-1",
                  pr_url=PR_URL, pr_state="open")
    fields.update(over)
    with dispatcher._db() as conn:
        _row(conn, **fields)


def _run():
    return dispatcher.resolve_run(TID)


@pytest.mark.parametrize("status", dispatcher.REVIEW_STATUSES)
def test_a_run_whose_task_left_the_tracker_with_its_PR_open_is_held_not_done(tmp_path, status):
    """🔴 GUARD — the CMX-119 trap. Drop the hold (or read an open PR as done-evidence) ⇒
    the row goes `done`, its window is killed and its worktree freed ⇒ RED."""
    wf = _wf(tmp_path)
    _seed(wf, status)

    summary, killed, cleaned, _ = _tick(wf, [("open", "MERGEABLE")])

    run = _run()
    assert run["status"] == status
    assert run["tracker_note"] == HELD_OPEN
    assert run["pr_url"] == PR_URL
    assert summary["reconciled_done"] == 0
    assert summary["tracker_left_pr_open"] == 1
    assert killed == []
    assert cleaned == []


def test_an_unreadable_PR_state_holds_too(tmp_path):
    """🔴 GUARD: unknown never reads as merged — a PR whose state no read could get (phase 0
    and reconcile both fail) holds the row, saying so."""
    wf = _wf(tmp_path)
    _seed(wf, "awaiting_review", pr_state=None)

    summary, killed, cleaned, _ = _tick(wf, [(None, None)])

    run = _run()
    assert run["status"] == "awaiting_review"
    assert run["tracker_note"] == HELD_UNREADABLE
    assert summary["reconciled_done"] == 0
    assert killed == [] and cleaned == []


def test_the_hold_reads_the_PR_itself_and_a_close_it_sees_finishes_the_run_as_closed(tmp_path):
    """🔴 GUARD: reconcile reads the PR LIVE (phase 0's read failed here). A closed PR is
    recorded on the row, so the NEXT tick's closed-PR branch archives it `closed` — never
    `done` (everything in Done shipped)."""
    wf = _wf(tmp_path)
    _seed(wf, "awaiting_review")

    _summary, killed, _cleaned, asked = _tick(wf, [(None, None), ("closed", None)])

    run = _run()
    assert asked == [PR_URL, PR_URL]
    assert run["status"] == "awaiting_review"
    assert run["pr_state"] == "closed"
    assert run["tracker_note"] == HELD_CLOSED
    assert killed == []

    summary, _, _, _ = _tick(wf, [("closed", None)])
    assert _run()["status"] == "closed"
    assert _run()["tracker_note"] is None       # the finished card does not say "held"
    assert summary["reconciled_closed"] == 1
    assert summary["reconciled_done"] == 0


def test_a_held_run_goes_done_when_its_PR_merges(tmp_path):
    """⭐ MUST BE ACCEPTED: the hold is not a dead end — the merge finishes it, window
    killed, worktree freed (a judged run: the plain merged write)."""
    wf = _wf(tmp_path)
    _seed(wf, "awaiting_review", judge_state="clean", judge_sha="abc")
    _tick(wf, [("open", "MERGEABLE")])
    assert _run()["status"] == "awaiting_review"

    summary, killed, cleaned, _ = _tick(wf, [("merged", "MERGEABLE")])

    assert _run()["status"] == "done"
    assert _run()["tracker_note"] is None       # the finished card does not say "held"
    assert summary["reconciled_done"] == 1
    assert [c[-1].rsplit(":", 1)[-1] for c in killed] == ["test-1"]
    assert cleaned == [TID]


def test_a_held_run_whose_PR_merges_unjudged_also_drops_its_note(tmp_path):
    """🔴 GUARD: the unjudged-merge write is its own UPDATE — it must clear the note too."""
    wf = _wf(tmp_path)
    _seed(wf, "awaiting_review", judge_state="")
    _tick(wf, [("open", "MERGEABLE")])
    assert _run()["tracker_note"] == HELD_OPEN

    _tick(wf, [("merged", "MERGEABLE")])

    assert _run()["status"] == "done"
    assert _run()["judge_state"] == "unjudged_merged"
    assert _run()["tracker_note"] is None


def test_a_PR_the_reconcile_read_finds_merged_goes_done_on_this_tick(tmp_path):
    """⭐ MUST BE ACCEPTED: the transcript's PR (never on the row, so phase 0 never read it)
    read MERGED here ⇒ `done` on the same tick, the merged PR recorded."""
    wf = _wf(tmp_path)
    _seed(wf, "needs_human", pr_url=None, pr_state=None)

    summary, killed, cleaned, asked = _tick(wf, [("merged", "MERGEABLE")], read_pr_url=PR_URL)

    run = _run()
    assert asked == [PR_URL]
    assert run["status"] == "done"
    assert run["pr_url"] == PR_URL
    assert run["pr_state"] == "merged"          # what was read is what is recorded
    assert run["tracker_note"] is None
    assert summary["reconciled_done"] == 1
    assert [c[-1].rsplit(":", 1)[-1] for c in killed] == ["test-1"]
    assert cleaned == [TID]


def test_a_held_run_that_reconciles_own_read_finds_merged_drops_its_note(tmp_path):
    """🔴 GUARD: a HELD row (its note already written) whose PR phase 0 could not read, and
    reconcile's own read finds MERGED ⇒ `done`, and that done-write clears the note. Seeded
    with the note on purpose: from a row with no note, dropping the clear reads the same
    (DEFEAT_SHAPES 88 — a reset tested only from a row already holding the reset value)."""
    wf = _wf(tmp_path)
    _seed(wf, "awaiting_review", tracker_note=HELD_OPEN)

    summary, killed, cleaned, asked = _tick(wf, [(None, None), ("merged", "MERGEABLE")])

    run = _run()
    assert asked == [PR_URL, PR_URL]            # phase 0 failed; reconcile read it merged
    assert run["status"] == "done"
    assert run["pr_state"] == "merged"
    assert run["tracker_note"] is None          # the finished card does not say "held"
    assert summary["reconciled_done"] == 1
    assert [c[-1].rsplit(":", 1)[-1] for c in killed] == ["test-1"]
    assert cleaned == [TID]


def test_the_hold_records_a_PR_found_only_in_the_transcript(tmp_path):
    """🔴 GUARD: a PR known only from the transcript is written onto the held row, so
    phase 0 tracks it — and finishes the row — on later ticks."""
    wf = _wf(tmp_path)
    _seed(wf, "awaiting_review", pr_url=None, pr_state=None)

    _tick(wf, [("open", "MERGEABLE")], read_pr_url=PR_URL)

    run = _run()
    assert run["status"] == "awaiting_review"
    assert run["tracker_note"] == HELD_OPEN
    assert run["pr_url"] == PR_URL
    assert run["pr_state"] == "open"


def test_a_review_row_with_no_PR_at_all_still_goes_done(tmp_path):
    """⭐ NEGATIVE CONTROL: no PR anywhere ⇒ nothing to hold for — the tracker close stays
    done-evidence, as before CMX-308, and no PR read is attempted."""
    wf = _wf(tmp_path)
    _seed(wf, "needs_human", pr_url=None, pr_state=None)

    summary, killed, _, asked = _tick(wf, [("open", "MERGEABLE")])

    assert _run()["status"] == "done"
    assert _run()["tracker_note"] is None
    assert summary["reconciled_done"] == 1
    assert asked == []
    assert [c[-1].rsplit(":", 1)[-1] for c in killed] == ["test-1"]


def test_a_run_still_in_the_tracker_is_neither_held_nor_noted(tmp_path):
    """⭐ NEGATIVE CONTROL: the task is still open ⇒ no note, no hold counter."""
    wf = _wf(tmp_path)
    _seed(wf, "awaiting_review")

    summary, killed, _, _ = _tick(wf, [("open", "MERGEABLE")], source=_Source(TID))

    assert _run()["status"] == "awaiting_review"
    assert _run()["tracker_note"] is None
    assert summary["tracker_left_pr_open"] == 0
    assert killed == []


def test_the_note_clears_when_the_task_is_open_in_the_tracker_again(tmp_path):
    """🔴 GUARD: moving the issue back to a ready state makes the note moot — it is cleared,
    and the row is never claimed a second time (it is still the same live run)."""
    wf = _wf(tmp_path)
    _seed(wf, "awaiting_review")
    _tick(wf, [("open", "MERGEABLE")])
    assert _run()["tracker_note"] == HELD_OPEN

    summary, _, _, _ = _tick(wf, [("open", "MERGEABLE")], source=_Source(TID))

    assert _run()["tracker_note"] is None
    assert _run()["status"] == "awaiting_review"
    assert summary["dispatched"] == 0


def test_the_hold_is_logged_once_not_every_tick(tmp_path, caplog):
    """🔴 GUARD: the warning fires on the EDGE — a held row sits for days, and a 60s
    drumbeat is how an operator learns to skip logs."""
    wf = _wf(tmp_path)
    _seed(wf, "awaiting_review")
    with caplog.at_level("WARNING", logger=dispatcher.log.name):
        for _ in range(3):
            summary, _, _, _ = _tick(wf, [("open", "MERGEABLE")])
            assert summary["tracker_left_pr_open"] == 1
    hits = [r for r in caplog.records if "left the tracker but its PR is not merged" in r.getMessage()]
    assert len(hits) == 1
    assert TID in hits[0].getMessage()
