"""⚖️🔀 CMX-280 — a run whose head MOVED is judged afresh, never escalated on the OLD verdict.

Measured 10-19 on CMX-251 (#775): round 2 on `45e0878` came back `cannot_verify` twice and
the run went `needs_human`. After `chela retry` the rework agent pushed a new head — and the
tick that applied its `task-finished` escalated the run AGAIN, "could not verify this PR on
45e08787103b after 2 attempt(s)". A `running` row's head is never polled, so the row still
carried the pre-push `pr_head_sha`, which equals the old round's `judge_sha` — the stranded
`cannot_verify` escalation (tick 1e′) matched it. The new head was never judged.

And the old round's notice must not ride along: every later status change of that run
(needs_human → running → needs_human) re-fired the same "CANNOT VERIFY" for it.
"""
from __future__ import annotations

import json

import pytest

from chela import dispatcher, inbox, judge

from tests.test_judge import _run_row, _tick, _wf

OLD, NEW = "45e08787103b0000", "46f0739aaaa00000"


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setenv("CHELA_JUDGE_MAX_UNKNOWN_RETRIES", "2")


def _finished_rework(tmp_path, wf, *, pr_head_sha):
    """A rework round that just called `task-finished`: `running`, completion marker in its
    worktree, and the PREVIOUS round's spent `cannot_verify` still on the row."""
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-task-finished-request.json").write_text(
        json.dumps({"task_id": "abc123", "requested_at": "2026-10-19T19:30:00+00:00"}))
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), status="running",
                 worktree_path=str(wt), pr_head_sha=pr_head_sha, judge_sha=OLD,
                 judge_state=judge.J_CANNOT_VERIFY, judge_cannot_verify_tries=2,
                 judge_detail="suite TIMEOUT")


@pytest.mark.parametrize("row_head", [NEW, OLD], ids=["row-already-new", "row-stale-old"])
def test_a_moved_head_is_a_fresh_judge_candidate_not_needs_human(tmp_path, row_head):
    """The row's `pr_head_sha` is either already B (the brief's shape) or still the stale A
    (the live 10-19 shape); GitHub says the head is B. Neither escalates — B is judged, once,
    with the try counter reset by `_spawn_judge`'s same-sha rule."""
    wf = _wf(tmp_path)
    _finished_rework(tmp_path, wf, pr_head_sha=row_head)
    spawned: list[str] = []

    def spawn(w, row, sha, conn, task=None):
        spawned.append(sha)
        return True

    first = _tick(wf, spawn, sha=NEW)
    assert first["task_finished_applied"] == 1
    assert first["judge_stranded"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review", run["last_error"]
    assert "could not verify" not in (run["last_error"] or "")

    _tick(wf, spawn, sha=NEW)
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert run["pr_head_sha"] == NEW
    assert spawned and set(spawned) == {NEW}, "the NEW head must be judged, never the old one"


def test_the_completion_forgets_the_pre_push_head(tmp_path):
    """`mark_awaiting_review` writes the head as UNKNOWN (NULL) — the pre-push head it held
    is not this round's, and the next poll (tick step 0b) is what fills it in."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _run_row(conn, tmp_path, workflow_path=str(wf.path), status="running",
                 pr_head_sha=OLD, judge_sha=OLD)
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(dispatcher, "_read_pr_status", lambda url, d: ("open", "MERGEABLE"))
        mp.setattr(dispatcher, "_tmux_kill_window", lambda *a, **k: None, raising=False)
        mp.setattr(dispatcher.subprocess, "run",
                   lambda *a, **k: type("R", (), {"returncode": 0, "stdout": "", "stderr": ""}))
        assert dispatcher.mark_awaiting_review("abc123")["ok"]
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert run["pr_head_sha"] is None


def test_an_unmoved_head_still_escalates_once_the_budget_is_spent(tmp_path):
    """Negative control: a rework that pushed NOTHING (GitHub still says A) is the same
    commit — the spent budget stands and the run goes to a human, one tick later."""
    wf = _wf(tmp_path)
    _finished_rework(tmp_path, wf, pr_head_sha=OLD)

    def spawn(w, row, sha, conn, task=None):
        raise AssertionError("a spent budget on an unchanged head must not re-judge")

    assert _tick(wf, spawn, sha=OLD)["judge_stranded"] == 0
    assert _tick(wf, spawn, sha=OLD)["judge_stranded"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert OLD[:12] in run["last_error"]


def test_an_unreadable_head_waits_and_is_judged_once_github_answers(tmp_path):
    """The head stays UNKNOWN while GitHub cannot say it — no judge on a guessed head, no
    escalation on the old verdict — and the first readable poll makes it a candidate."""
    wf = _wf(tmp_path)
    _finished_rework(tmp_path, wf, pr_head_sha=OLD)
    spawned: list[str] = []

    def spawn(w, row, sha, conn, task=None):
        spawned.append(sha)
        return True

    for _ in range(2):
        assert _tick(wf, spawn, sha=None)["judge_stranded"] == 0
    run = dispatcher.resolve_run("abc123")
    assert (run["status"], run["pr_head_sha"], spawned) == ("awaiting_review", None, [])

    _tick(wf, spawn, sha=NEW)
    assert spawned == [NEW]


# --- the round's notice, once ------------------------------------------------------------

def _row(status, judge_state, **over):
    return {"task_id": "T1", "title": "do a thing", "status": status,
            "branch_name": "cmx-251", "pr_url": "https://github.com/o/r/pull/775",
            "judge_state": judge_state, "judge_sha": OLD, "judge_detail": "suite TIMEOUT",
            **over}


def _replay(*rows):
    seen: dict[str, str] = {}
    kinds: list[list[str]] = []
    for row in rows:
        events, seen = inbox.run_events([row], seen)
        kinds.append([e["kind"] for e in events])
    return kinds


def test_a_settled_cannot_verify_is_announced_once_across_later_transitions():
    """The 10-19 sequence: settled in review, escalated, retried, escalated again. The
    CANNOT VERIFY notice fires on the settle ONLY; the status edges still announce."""
    cv = judge.J_CANNOT_VERIFY
    kinds = _replay(_row("awaiting_review", cv), _row("needs_human", cv),
                    _row("running", cv), _row("needs_human", cv))
    assert kinds == [["run_judge_cannot_verify"], ["run_needs_human"], [],
                     ["run_needs_human"]]


def test_a_cas_race_cannot_verify_is_announced_once():
    """The CMX-229 race notice (verdict landed after the run left review) fires once, not
    again on each status the run passes through afterwards."""
    cv = judge.J_CANNOT_VERIFY
    kinds = _replay(_row("awaiting_review", judge.J_RUNNING), _row("changes_requested", cv),
                    _row("running", cv), _row("awaiting_review", cv))
    flat = [k for ks in kinds for k in ks]
    assert flat.count("run_judge_cannot_verify") == 1
    assert kinds[1] == ["run_judge_cannot_verify"]


def test_a_blocked_race_is_announced_once():
    br = judge.J_BLOCKED_RACE
    kinds = _replay(_row("awaiting_review", judge.J_RUNNING), _row("running", br),
                    _row("changes_requested", br), _row("needs_human", br))
    flat = [k for ks in kinds for k in ks]
    assert flat.count("run_judge_blocked_race") == 1
    assert kinds[-1] == ["run_needs_human"]


def test_a_NEW_round_with_the_same_verdict_is_announced_again():
    """Positive control: a fresh judgement passes through `running`, so a second
    cannot_verify on the new head is news and fires."""
    cv = judge.J_CANNOT_VERIFY
    kinds = _replay(_row("awaiting_review", cv), _row("needs_human", cv),
                    _row("awaiting_review", judge.J_RUNNING),
                    _row("awaiting_review", cv, judge_sha=NEW))
    assert kinds[-1] == ["run_judge_cannot_verify"]
