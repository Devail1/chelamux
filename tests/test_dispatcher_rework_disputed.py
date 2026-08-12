"""``chela rework-disputed`` (CMX-248, re-scope of CMX-244) — a rework agent's "nothing to
push" escape hatch.

A rework agent that reads the verdict and concludes it is wrong, already fixed, or
otherwise unfixable has no new commit to offer — `task-finished` assumes one landed (the
row flips to `awaiting_review` so the NEXT tick judges the new head), and the dispatcher
judges once per head commit, so a rework that just says "nothing to fix" in its final
message and stops leaves the row in `running` forever: nothing about that state changes
without a fresh judge verdict, and the idle watchdog just re-sends the same rework prompt
on a timer — every liveness signal (session status, idle-nudge) reads healthy while the
run itself never moves again.

These tests pin the escape hatch: a rework `running` row moves straight to `needs_human`
(never `awaiting_review`, which would carry the SAME already-judged head), `rework_count`
is left exactly as `_respawn_rework` spent it, and the branch/worktree/PR are untouched.
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

import pytest

from chela import dispatcher


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _row(conn, task_id="abc123", **over) -> sqlite3.Row:
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "do a thing",
        "status": "running", "window_name": "cmx-1", "worktree_path": "/wt/abc123",
        "branch_name": "cmx-1", "started_at": "2026-08-12T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/80", "pr_state": "open",
        "rework_count": 1, "review_history": json.dumps([
            {"round": 1, "at": "t1", "body": "the wire is loose", "verdict": "changes_requested"},
        ]),
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()


def _gh_router(comment_ok=True):
    def _run(cmd, *a, **k):
        class R:
            returncode = 0 if comment_ok else 1
            stdout = ""
            stderr = "" if comment_ok else "gh pr comment failed"
        return R()
    return _run


def _parse_escalation(last_error: str) -> tuple[str, str, list[str]]:
    """Split a composed ``last_error`` back into (reason, recommendation, options) — the
    inverse of ``dispatcher._format_escalation``. Same idiom as
    tests/test_dispatcher_rework.py and tests/test_dispatcher_ci.py."""
    reason, sep, rest = last_error.partition("\n\nRecommendation: ")
    if not sep:
        return last_error, "", []
    recommendation, sep2, rest2 = rest.partition("\n\nOptions:\n")
    if not sep2:
        return reason, recommendation, []
    options = [line[len("  - "):] for line in rest2.split("\n") if line.startswith("  - ")]
    return reason, recommendation, options


# --- (a) the happy path: running (rework) -> needs_human, everything else untouched ------

@pytest.mark.parametrize(
    "rework_count,max_reworks_env,prior_rounds",
    [
        (1, "4", [1]),
        (3, "9", [1, 2, 3]),
    ],
    ids=["1-of-4-round-2", "3-of-9-round-4"],
)
def test_dispute_flips_a_rework_to_needs_human_and_posts_a_comment(
    monkeypatch, rework_count, max_reworks_env, prior_rounds
):
    # Two cases, and every quantity that varies (numerator, denominator, prior-review
    # count, resulting round) is distinct within a case AND across both cases — so no
    # hardcoded literal (a headline fraction, a review round) can satisfy both.
    monkeypatch.setenv("CHELA_MAX_REWORKS", max_reworks_env)
    prior_reviews = [
        {"round": r, "at": f"t{r}", "body": f"prior issue #{r}", "verdict": "changes_requested"}
        for r in prior_rounds
    ]
    with dispatcher._db() as conn:
        _row(conn, rework_count=rework_count, review_history=json.dumps(prior_reviews))
    gh: list[list[str]] = []
    gh_inputs: list[str] = []

    def _run(cmd, *a, **k):
        gh.append(cmd)
        gh_inputs.append(k.get("input") or "")
        return _gh_router()(cmd, *a, **k)

    with patch.object(dispatcher.subprocess, "run", side_effect=_run), \
         patch.object(dispatcher, "_kill_window") as kill:
        result = dispatcher.mark_rework_disputed("abc123", "the verdict describes code that "
                                                             "was already fixed last round")

    assert result["ok"] is True
    assert result["status"] == "needs_human"
    assert result["comment_posted"] is True
    assert result["rework_count"] == rework_count          # unchanged — the round was already spent
    assert result["max_reworks"] == int(max_reworks_env)
    kill.assert_called_once_with("cmx-1")        # the window is killed, same as task-finished

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert run["rework_count"] == rework_count
    assert "the verdict describes code" in run["last_error"]
    # the headline itself — not just the agent's reason it's prefixed onto — must tell a
    # human this is a DISPUTE, that nothing was pushed, and which round it was. Every
    # sibling escalation pins its own discriminating headline text on top of the reason
    # (tests/test_dispatcher_rework.py:369, :393, :421; tests/test_dispatcher_ci.py:363,
    # :1626); this is that same pin for the dispute headline. The expected fraction is
    # built from the fixture's own inputs, not from `result` — so a headline that
    # hardcodes any single (numerator, denominator) pair fails at least one case.
    assert "disputed" in run["last_error"]
    assert "nothing was pushed" in run["last_error"]
    assert f"{rework_count}/{max_reworks_env}" in run["last_error"]
    # the branch/worktree/PR fields are untouched.
    assert run["branch_name"] == "cmx-1"
    assert run["worktree_path"] == "/wt/abc123"
    assert run["pr_url"] == "https://github.com/o/r/pull/80"

    # CMX-242: an automatic escalation must not hand a human a bare "I quit" — it must
    # carry a next step, same as every sibling _escalate call site.
    _, recommendation, options = _parse_escalation(run["last_error"])
    assert recommendation.strip(), "an automatic escalation must carry a non-empty recommendation"
    assert len(options) >= 2, "one option is not a choice"

    reviews = dispatcher.reviews_of(dict(run))
    # the new round is len(prior_rounds) + 1 — varied by the fixture, never a constant.
    assert [r["round"] for r in reviews] == prior_rounds + [len(prior_rounds) + 1]
    assert reviews[-1]["verdict"] == "disputed"
    assert "already fixed" in reviews[-1]["body"]

    posted = [c for c in gh if c[:3] == ["gh", "pr", "comment"]]
    assert posted and posted[0][3] == "80"
    # The PR comment and last_error must carry the exact SAME composed escalation — the
    # headline, recommendation and options together, byte for byte — not merely overlap
    # on the bare reason substring (posting just `reason` would also pass a substring
    # check, since the reason is itself a substring of the composed text).
    posted_body = gh_inputs[gh.index(posted[0])]
    assert posted_body == run["last_error"]


def test_a_failed_pr_comment_does_not_block_the_dispute():
    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router(comment_ok=False)), \
         patch.object(dispatcher, "_kill_window"):
        result = dispatcher.mark_rework_disputed("abc123", "nothing to fix")
    assert result["ok"] is True and result["comment_posted"] is False
    assert dispatcher.resolve_run("abc123")["status"] == "needs_human"


# --- (b) ONLY a rework `running` row (rework_count > 0) can be disputed ------------------

@pytest.mark.parametrize("status,rework_count", [
    ("running", 0),          # a first dispatch, not a rework — nothing to dispute
    ("awaiting_review", 1),
    ("changes_requested", 1),
    ("needs_human", 1),
    ("done", 1),
    ("failed", 1),
    ("claimed", 1),
])
def test_dispute_refuses_anything_that_is_not_a_rework_in_flight(status, rework_count):
    with dispatcher._db() as conn:
        _row(conn, status=status, rework_count=rework_count)
    with patch.object(dispatcher, "_kill_window") as kill:
        result = dispatcher.mark_rework_disputed("abc123", "nothing to fix")
    assert result["ok"] is False
    assert "in flight" in result["error"].lower()
    kill.assert_not_called()
    assert dispatcher.resolve_run("abc123")["status"] == status


def test_dispute_with_no_reason_is_refused():
    with dispatcher._db() as conn:
        _row(conn)
    result = dispatcher.mark_rework_disputed("abc123", "   ")
    assert result["ok"] is False
    assert "reason" in result["error"]
    assert dispatcher.resolve_run("abc123")["status"] == "running"


def test_dispute_on_a_missing_task_id_is_refused():
    result = dispatcher.mark_rework_disputed("no-such-task", "nothing to fix")
    assert result["ok"] is False
    assert "no run found" in result["error"]


# --- (c) the CLI wires straight through to the dispatcher function ----------------------

def test_cli_rework_disputed_prints_needs_human_on_success(capsys):
    with dispatcher._db() as conn:
        _row(conn)
    from chela import main as main_mod

    class Args:
        task_id = "abc123"
        reason = "already fixed last round"

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()), \
         patch.object(dispatcher, "_kill_window"):
        main_mod.cmd_rework_disputed(Args())

    out = capsys.readouterr().out
    assert "disputed" in out
    assert "needs_human" in out
    # the CLI must forward the AGENT's own reason, not a canned one — a human reads
    # this to resolve the dispute, so a constant string here would defeat the point.
    run = dispatcher.resolve_run("abc123")
    assert "already fixed last round" in run["last_error"]
    assert "already fixed last round" in dispatcher.reviews_of(dict(run))[-1]["body"]


def test_cli_rework_disputed_exits_nonzero_on_refusal(capsys):
    from chela import main as main_mod

    class Args:
        task_id = "no-such-task"
        reason = "nothing to fix"

    with pytest.raises(SystemExit) as exc:
        main_mod.cmd_rework_disputed(Args())
    assert exc.value.code == 1
    assert "no run found" in capsys.readouterr().out


def test_chela_rework_disputed_reaches_the_dispatcher_end_to_end():
    """``chela rework-disputed abc123 "reason"`` must actually parse AND reach
    ``dispatcher.mark_rework_disputed`` — the dispatch call-site is the guard here. Mutate
    ``elif args.command == "rework-disputed": …`` to anything else and this fails: a
    subparser that parses but is never wired is silent, same idiom as `retry`/`reopen`
    (tests/test_dispatcher_retry.py, tests/test_dispatcher_reopen.py)."""
    import sys

    from chela import main

    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()), \
         patch.object(dispatcher, "_kill_window"), \
         patch.object(sys, "argv", ["chela", "rework-disputed", "abc123", "nothing to fix"]):
        main.main()

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert dispatcher.reviews_of(dict(run))[-1]["verdict"] == "disputed"
