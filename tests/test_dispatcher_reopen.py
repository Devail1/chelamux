"""``chela reopen`` (CMX-96) — the human-takeover re-entry for a `needs_human` run.

Before this, `needs_human` was terminal EVERYWHERE in the CLI: `task-finished`, `review`
and `merge` all refuse any status that is not `awaiting_review`, and `judge run` needs an
`--experiments` file the dispatcher only generates on the `awaiting_review` path. A human
who fixed the branch themselves and pushed a new commit had no in-contract way back — the
only escape was a raw `gh pr merge`, which never re-verifies the fixed head and skips the
judge (a self-review hole).

These tests pin the missing edge: `needs_human` -> `awaiting_review`, using the SAME
compare-and-swap discipline as `request_changes`/`approve`, so the existing judge/review/
merge path picks the fixed head up exactly like a fresh PR.
"""
from __future__ import annotations

import json
import re
import sqlite3
from unittest.mock import patch

import pytest

from chela import dispatcher, event_log


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _row(conn, task_id="abc123", **over) -> sqlite3.Row:
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "do a thing",
        "status": "needs_human", "window_name": None, "worktree_path": "/wt/abc123",
        "branch_name": "test-1", "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/80", "pr_state": "open",
        "rework_count": 2, "review_history": json.dumps([
            {"round": 1, "at": "t1", "body": "the wire is loose", "verdict": "changes_requested"},
            {"round": 2, "at": "t2", "body": "still loose", "verdict": "changes_requested"},
        ]),
        "last_error": "rework cap reached (2/2) — the PR still fails review.",
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()


def _no_gh(cmd, *a, **k):
    class R:
        returncode = 0
        stdout = ""
        stderr = ""
    return R()


def _gh_view(sha="deadbeef0000"):
    """A successful `gh pr view --json statusCheckRollup,headRefOid` — reports `sha` as
    the PR's current head commit (the guard's new-commit read)."""
    class R:
        returncode = 0
        stdout = json.dumps({"headRefOid": sha, "statusCheckRollup": []})
        stderr = ""
    return R()


def _gh_router(sha="deadbeef0000", comment_ok=True):
    """Route `gh` subprocess calls by shape: the checks read (`--json ...`, used by the
    new-commit guard) always answers with `sha`; the PR comment succeeds unless
    `comment_ok=False` (in which case it fails the way a real `gh pr comment` would,
    without raising — matching the two "comment didn't post" tests below)."""
    def _run(cmd, *a, **k):
        if "--json" in cmd:
            return _gh_view(sha)
        if comment_ok:
            return _no_gh(cmd, *a, **k)
        class R:
            returncode = 1
            stdout = ""
            stderr = "gh pr comment failed"
        return R()
    return _run


_COMPARE_RE = re.compile(r"compare/([^.]+)\.\.\.([^.]+)")


def _gh_router_with_compare(sha="deadbeef0000", compare_files_by_base=None):
    """Same as `_gh_router`, plus routes `gh api .../compare/{base}...{head}` (the
    CMX-198 no-production-change diff) — one filename per line, the shape
    `--jq '.files[].filename'` actually prints.

    Routed by the BASE sha parsed out of the compare path, not a constant answer: a bug
    that diffs against the WRONG base (the previous round's head instead of the first
    reopen's — the corrupt-guard target for this whole feature) asks for a base this
    dict never mapped, and gets back a file that is unambiguously "production changed"
    (never what a test expects) instead of silently reusing the right answer.
    """
    compare_files_by_base = compare_files_by_base or {}
    def _run(cmd, *a, **k):
        if "--json" in cmd:
            return _gh_view(sha)
        if cmd[:2] == ["gh", "api"]:
            m = _COMPARE_RE.search(cmd[2])
            base = m.group(1) if m else None
            files = compare_files_by_base.get(base, ["chela/UNEXPECTED_BASE.py"])
            class R:
                returncode = 0
                stdout = "\n".join(files)
                stderr = ""
            return R()
        return _no_gh(cmd, *a, **k)
    return _run


# --- (a) the happy path: needs_human -> awaiting_review, everything else untouched -----

def test_reopen_flips_needs_human_to_awaiting_review_and_posts_a_comment(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)
    gh: list[list[str]] = []
    router = _gh_router(sha="freshfix00")

    def _run(cmd, *a, **k):
        gh.append(cmd)
        return router(cmd, *a, **k)

    with patch.object(dispatcher.subprocess, "run", side_effect=_run):
        result = dispatcher.reopen("abc123", "pushed a fix for the loose wire")

    assert result["ok"] is True
    assert result["status"] == "awaiting_review"
    assert result["comment_posted"] is True

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    # rework_count is left EXACTLY as it was — reopening spends no budget.
    assert run["rework_count"] == 2
    # last_error is cleared: it named a reason that is no longer true.
    assert run["last_error"] is None
    # the branch/worktree/PR fields are untouched.
    assert run["branch_name"] == "test-1"
    assert run["worktree_path"] == "/wt/abc123"
    assert run["pr_url"] == "https://github.com/o/r/pull/80"
    # the head sha read for the guard is persisted — the poller's own refresh.
    assert run["pr_head_sha"] == "freshfix00"

    reviews = dispatcher.reviews_of(dict(run))
    assert len(reviews) == 3
    assert reviews[-1]["verdict"] == "reopened"
    assert "loose wire" in reviews[-1]["body"]

    posted = [c for c in gh if c[:3] == ["gh", "pr", "comment"]]
    assert posted and posted[0][3] == "80"


def test_reopen_with_no_reason_still_writes_a_default_note(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()):
        result = dispatcher.reopen("abc123")
    assert result["ok"] is True
    reviews = dispatcher.reviews_of(dict(dispatcher.resolve_run("abc123")))
    assert reviews[-1]["verdict"] == "reopened"
    assert reviews[-1]["body"]          # non-empty default


def test_a_failed_pr_comment_does_not_block_the_reopen(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)

    def _run(cmd, *a, **k):
        if "--json" in cmd:
            return _gh_view()
        raise FileNotFoundError("no gh")

    with patch.object(dispatcher.subprocess, "run", side_effect=_run):
        result = dispatcher.reopen("abc123", "fixed it")
    assert result["ok"] is True and result["comment_posted"] is False
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"


# --- (b') 🔴 the new-commit gate: reopen must not resurrect an UNCHANGED head -----------
#
# The dispatcher judges ONE PASS PER HEAD COMMIT (`judge_sha` vs `pr_head_sha`). If a human
# reopens a `needs_human` run whose branch head never moved, the row flips to
# `awaiting_review` carrying its old failing verdict — and the judge will never re-run to
# catch it, since its own guard sees `judge_sha == pr_head_sha` and does nothing. That
# stale, already-rejected head becomes reachable by `review --approve` -> `merge`. This is
# the loop/merge hole the gate below closes.

def test_reopen_refuses_when_the_head_is_unchanged_since_the_judge(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, judge_sha="deadbeef0000", pr_head_sha="deadbeef0000")
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router(sha="deadbeef0000")):
        result = dispatcher.reopen("abc123", "fixed it")

    assert result["ok"] is False
    assert "same" in result["error"].lower()
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"          # NOT reopened
    assert len(dispatcher.reviews_of(dict(run))) == 2   # nothing appended


def test_reopen_succeeds_once_the_head_has_moved_past_the_judged_commit(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, judge_sha="oldsha000001", pr_head_sha="oldsha000001")
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router(sha="freshfix0002")):
        result = dispatcher.reopen("abc123", "pushed the fix")

    assert result["ok"] is True
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    # the refreshed head is persisted — the same read the poller would have done next tick.
    assert run["pr_head_sha"] == "freshfix0002"


def test_reopen_refuses_when_the_current_head_cannot_be_read_from_github(tmp_path):
    """A `gh` that cannot answer is CANNOT VERIFY, never a pass — same doctrine as the CI
    gate elsewhere in this file. Reopening blind would let an unchanged (or worse, reverted)
    head slip past the guard just because GitHub was unreachable."""
    with dispatcher._db() as conn:
        _row(conn, judge_sha="deadbeef0000", pr_head_sha="deadbeef0000")
    with patch.object(dispatcher.subprocess, "run", side_effect=FileNotFoundError("no gh")):
        result = dispatcher.reopen("abc123", "fixed it")

    assert result["ok"] is False
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"


# --- (b) ONLY needs_human can be reopened -----------------------------------------------

@pytest.mark.parametrize("status", ["running", "awaiting_review", "changes_requested",
                                     "done", "failed", "claimed"])
def test_reopen_refuses_every_status_that_is_not_needs_human(tmp_path, status):
    with dispatcher._db() as conn:
        _row(conn, status=status)
    result = dispatcher.reopen("abc123", "fixed it")
    assert result["ok"] is False
    assert status in result["error"]
    assert dispatcher.resolve_run("abc123")["status"] == status   # untouched


def test_reopen_refuses_an_unknown_run(tmp_path):
    result = dispatcher.reopen("no-such-run", "fixed it")
    assert result["ok"] is False


# --- (c) 🔴 the reopen cannot resurrect a run that MOVED --------------------------------

def test_reopen_will_not_resurrect_a_run_that_moved_under_it(tmp_path):
    """Same race `request_changes` guards against: a concurrent tick reconciles the row to
    `done` (the human merged the stale PR directly) in the gap between this call's read and
    its write. With no compare-and-swap the UPDATE lands anyway and a merged, needs_human
    run is dragged back into `awaiting_review`.

    Seen to go red: dropping `AND status='needs_human'` from the UPDATE.
    """
    with dispatcher._db() as conn:
        _row(conn)
    stale = dict(dispatcher.resolve_run("abc123"))        # read: needs_human
    with dispatcher._db() as conn:                        # ...and the world moves on
        conn.execute("UPDATE runs SET status='done' WHERE task_id='abc123'")
        conn.commit()

    with patch.object(dispatcher, "resolve_run", return_value=stale), \
         patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()):
        result = dispatcher.reopen("abc123", "fixed it")

    assert result["ok"] is False
    assert "done" in result["error"]
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "done"                        # NOT resurrected
    reviews = dispatcher.reviews_of(dict(run))
    assert len(reviews) == 2                              # nothing appended


# --- (d) a run reopened for review re-enters the SAME carrier if it fails again ---------

def test_a_reopened_run_that_fails_review_again_re_escalates_without_burning_a_slot(tmp_path):
    """If the "fixed" head still fails, `request_changes` sends it to `changes_requested`
    with `rework_count` untouched (still at the cap) — so the very next escalation check
    sends it straight back to `needs_human`, with no wasted automatic rework attempt."""
    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()):
        reopened = dispatcher.reopen("abc123", "fixed it")
    assert reopened["ok"] is True

    with patch.object(dispatcher.subprocess, "run", side_effect=_no_gh):
        blocked = dispatcher.request_changes("abc123", "still broken")
    assert blocked["ok"] is True
    assert blocked["status"] == "changes_requested"
    assert blocked["rework_count"] == 2               # untouched — still at the cap


# --- (f) 🔁🛑 CMX-198: `reopen_count` + the no-production-change nudge ------------------
#
# `CHELA_MAX_REWORKS` bounds the dispatcher's AUTOMATIC rework loop; it does not bound
# `reopen`, the human-takeover path — measured on cmx-197, `rework 1/2` printed unchanged
# across fourteen reopens. These tests pin the counter that makes the loop VISIBLE, and
# the advisory nudge that fires when a run has been reopened 3+ times with nothing under
# `chela/` touched since the first reopen.

def _escalate_back_to_needs_human(task_id: str, judge_sha: str) -> None:
    """Stand in for the dispatcher's own cap-check tick: a reopened run whose fixed head
    still fails review re-escalates to `needs_human` with a NEW judge_sha (the head the
    judge just rejected) — never re-derived through `request_changes`/the tick loop here,
    since those are exercised elsewhere; this test file only needs the resulting row."""
    with dispatcher._db() as conn:
        conn.execute(
            "UPDATE runs SET status='needs_human', judge_sha=? WHERE task_id=?",
            (judge_sha, task_id),
        )
        conn.commit()


def test_reopen_count_climbs_independently_of_rework_count(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, judge_sha="j0", pr_head_sha="j0")

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router_with_compare(sha="h1")):
        r1 = dispatcher.reopen("abc123", "fix 1")
    assert r1["ok"] is True and r1["reopen_count"] == 1
    assert dispatcher.resolve_run("abc123")["rework_count"] == 2

    _escalate_back_to_needs_human("abc123", "h1")
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router_with_compare(sha="h2")):
        r2 = dispatcher.reopen("abc123", "fix 2")
    assert r2["reopen_count"] == 2

    _escalate_back_to_needs_human("abc123", "h2")
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router_with_compare(sha="h3")):
        r3 = dispatcher.reopen("abc123", "fix 3")
    assert r3["reopen_count"] == 3

    run = dispatcher.resolve_run("abc123")
    # the AUTOMATIC counter never moves — reopening still spends no rework budget.
    assert run["rework_count"] == 2
    assert run["reopen_count"] == 3
    # the baseline is the FIRST reopen's head, fixed — not the most recent one.
    assert run["first_reopen_head_sha"] == "h1"


def test_nudge_fires_on_the_third_reopen_when_only_tests_changed(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, judge_sha="j0", pr_head_sha="j0")

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router_with_compare(sha="h1")):
        dispatcher.reopen("abc123", "fix 1")
    _escalate_back_to_needs_human("abc123", "h1")

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router_with_compare(sha="h2")):
        dispatcher.reopen("abc123", "fix 2")
    _escalate_back_to_needs_human("abc123", "h2")

    with patch.object(
        dispatcher.subprocess, "run",
        side_effect=_gh_router_with_compare(
            sha="h3", compare_files_by_base={"h1": ["tests/test_x.py"]},
        ),
    ):
        r3 = dispatcher.reopen("abc123", "fix 3")

    assert r3["ok"] is True
    assert r3["status"] == "awaiting_review"          # ⛔ the nudge never blocks
    assert "nudge" in r3
    assert "3 rounds" in r3["nudge"]
    assert "no production change" in r3["nudge"]

    events = event_log.read(types=["reopen_nudge"])["events"]
    assert len(events) == 1
    assert events[0]["payload"]["task_id"] == "abc123"
    assert events[0]["payload"]["reopen_count"] == 3
    assert events[0]["payload"]["first_reopen_head_sha"] == "h1"


def test_nudge_does_not_fire_when_production_code_changed(tmp_path):
    """Same three-reopen shape as above, except round 3's diff touches `chela/` — the
    nudge must stay silent. ⛔ Corrupt-guard target: comparing against the PREVIOUS head
    (h2) instead of the FIRST reopen's (h1) would see round 3 alone (chela/dispatcher.py
    only) and still suppress correctly here — this test alone cannot catch that
    corruption. `test_nudge_fires_on_the_third_reopen_when_only_tests_changed` is the one
    that does: a per-round diff against h2 is never empty, so the wrong-base bug makes
    the nudge STOP firing there instead."""
    with dispatcher._db() as conn:
        _row(conn, judge_sha="j0", pr_head_sha="j0")

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router_with_compare(sha="h1")):
        dispatcher.reopen("abc123", "fix 1")
    _escalate_back_to_needs_human("abc123", "h1")

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router_with_compare(sha="h2")):
        dispatcher.reopen("abc123", "fix 2")
    _escalate_back_to_needs_human("abc123", "h2")

    with patch.object(
        dispatcher.subprocess, "run",
        side_effect=_gh_router_with_compare(
            sha="h3", compare_files_by_base={"h1": ["chela/dispatcher.py", "tests/test_x.py"]},
        ),
    ):
        r3 = dispatcher.reopen("abc123", "fix 3")

    assert r3["ok"] is True
    assert "nudge" not in r3
    assert event_log.read(types=["reopen_nudge"])["events"] == []


def test_nudge_does_not_fire_when_the_diff_is_unreadable(tmp_path):
    """Same three-reopen shape again, except round 3's `gh api compare` call itself fails
    (a `gh` hiccup, a network blip, a compare API 500) — `_production_files_changed`
    returns `(None, ...)`, the UNKNOWN arm of the tri-state, and it must stay just as
    silent as the KNOWN-changed arm. ⛔ Corrupt-guard target: widening `if touched is
    False` to `if touched is not True` fires the nudge on exactly this case, and every
    other test in this file still passes under that mutation — this is the one that
    catches it."""
    with dispatcher._db() as conn:
        _row(conn, judge_sha="j0", pr_head_sha="j0")

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router_with_compare(sha="h1")):
        dispatcher.reopen("abc123", "fix 1")
    _escalate_back_to_needs_human("abc123", "h1")

    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router_with_compare(sha="h2")):
        dispatcher.reopen("abc123", "fix 2")
    _escalate_back_to_needs_human("abc123", "h2")

    def _run(cmd, *a, **k):
        if "--json" in cmd:
            return _gh_view("h3")
        if cmd[:2] == ["gh", "api"]:
            class R:
                returncode = 1
                stdout = ""
                stderr = "gh api compare failed"
            return R()
        return _no_gh(cmd, *a, **k)

    with patch.object(dispatcher.subprocess, "run", side_effect=_run):
        r3 = dispatcher.reopen("abc123", "fix 3")

    assert r3["ok"] is True
    assert "nudge" not in r3
    assert event_log.read(types=["reopen_nudge"])["events"] == []


def test_nudge_is_silent_before_the_third_reopen(tmp_path):
    """A single reopen — even one that changed nothing but tests — is not evidence of
    anything; the round-count gate must hold regardless of what the diff would say."""
    with dispatcher._db() as conn:
        _row(conn, judge_sha="j0", pr_head_sha="j0")
    gh_calls: list[list[str]] = []

    def _run(cmd, *a, **k):
        gh_calls.append(cmd)
        return _gh_router_with_compare(
            sha="h1", compare_files_by_base={"h1": ["tests/test_x.py"]},
        )(cmd, *a, **k)

    with patch.object(dispatcher.subprocess, "run", side_effect=_run):
        r1 = dispatcher.reopen("abc123", "fix 1")

    assert r1["ok"] is True and r1["reopen_count"] == 1
    assert "nudge" not in r1
    # not even asked — no gh api compare call before the 3rd round.
    assert not any(c[:2] == ["gh", "api"] for c in gh_calls)


def test_a_fresh_run_shows_no_reopen_count_in_the_listing(tmp_path):
    """Zero reopens ⇒ the CLI listing shows nothing extra — the counterweight against
    always-on chrome (see `chela.main._format_awaiting_run`)."""
    from chela import main

    with dispatcher._db() as conn:
        _row(conn, status="awaiting_review", reopen_count=0)
    row = dispatcher.resolve_run("abc123")
    assert "reopen=" not in main._format_awaiting_run(dict(row))


def test_a_reopened_runs_listing_shows_its_reopen_count(tmp_path):
    from chela import main

    with dispatcher._db() as conn:
        _row(conn, status="awaiting_review", reopen_count=3)
    row = dispatcher.resolve_run("abc123")
    assert "reopen=3" in main._format_awaiting_run(dict(row))


def test_cmd_reopen_prints_the_reopen_count_and_the_nudge(tmp_path, capsys):
    from chela import main

    with dispatcher._db() as conn:
        _row(conn, judge_sha="j0", pr_head_sha="j0", reopen_count=2, first_reopen_head_sha="h1")
    with patch.object(
        dispatcher.subprocess, "run",
        side_effect=_gh_router_with_compare(
            sha="h3", compare_files_by_base={"h1": ["tests/test_x.py"]},
        ),
    ):
        main.cmd_reopen(_ReopenArgs())
    out = capsys.readouterr().out
    assert "reopen #3" in out
    assert "no production change" in out


# --- (e) the CLI -------------------------------------------------------------------------

class _ReopenArgs:
    run = "abc123"
    reason = "fixed the loose wire"


def test_cmd_reopen_success(tmp_path, capsys):
    from chela import main

    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()):
        main.cmd_reopen(_ReopenArgs())
    out = capsys.readouterr().out
    assert "awaiting_review" in out
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"


def test_cmd_reopen_failure_exits_nonzero(tmp_path, capsys):
    from chela import main

    with dispatcher._db() as conn:
        _row(conn, status="running")
    with pytest.raises(SystemExit) as exc:
        main.cmd_reopen(_ReopenArgs())
    assert exc.value.code != 0
    out = capsys.readouterr().out
    assert "not 'needs_human'" in out


def test_chela_reopen_reaches_the_dispatcher_end_to_end(tmp_path):
    """``chela reopen cmx-96`` must actually parse AND reach ``dispatcher.reopen`` — the
    dispatch call-site is the guard here. Mutate ``elif args.command == "reopen": …`` to
    ``pass`` and this fails: a subparser that parses but is never wired is silent."""
    import sys

    from chela import main

    with dispatcher._db() as conn:
        _row(conn)
    with patch.object(dispatcher.subprocess, "run", side_effect=_gh_router()), \
         patch.object(sys, "argv", ["chela", "reopen", "abc123", "--reason", "fixed it"]):
        main.main()
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert dispatcher.reviews_of(dict(run))[-1]["body"] == "fixed it"
