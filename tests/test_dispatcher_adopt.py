"""⚖️🚪 CMX-276 — a hand-opened PR has NO judge (``dispatcher.adopt_pr`` / ``chela adopt``).

A PR opened by ``gh pr create`` directly — not through ``_spawn`` — has no row in ``runs``.
Every gate that matters only ever reads that table: the per-sha judge trigger in ``tick()``
is a bare ``SELECT ... FROM runs``, and ``chela.contract.merge`` refuses with "no run
matches" before it even reaches the judge-clean check. The result is not "merge refused" —
it is silence: nothing judges the PR, and nothing stops a raw ``gh pr merge`` from landing
it unjudged. Live incident: PR #346 edited ``tests/test_judge.py`` — precisely the kind of
change the judge exists to interrogate — and merged with no judge ever having run, because
there was no run row for the trigger to find.

``adopt_pr`` closes the gap by creating that row: an ``awaiting_review`` run with no
worktree/window (the work is already pushed), carrying the PR's live head sha and nothing
on ``judge_sha``/``judge_state`` — so the very next dispatcher tick refreshes its CI state
and the per-sha trigger picks it up exactly like a freshly-dispatched PR's first pass. These
tests pin: the row shape adoption produces, the refusals (not open / already tracked / bad
identifier / gh unreachable), the CLI wiring, and — end to end, against a real git repo —
that an adopted PR actually gets a judge spawned on the next tick.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import dispatcher, judge
from chela.workflow import WorkflowDef, WorkflowStatus


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def origin(tmp_path) -> Path:
    o = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "dev", str(o)], check=True, capture_output=True)
    return o


@pytest.fixture
def repo(tmp_path, origin) -> Path:
    work = tmp_path / "repo"
    subprocess.run(["git", "clone", str(origin), str(work)], check=True, capture_output=True)
    for k, v in (("user.email", "t@example.com"), ("user.name", "T"), ("commit.gpgsign", "false")):
        _git("config", k, v, cwd=work)
    (work / "app.py").write_text("VALUE = 1\n")
    (work / "TODO.md").write_text("")
    _git("add", "app.py", "TODO.md", cwd=work)
    _git("commit", "-m", "seed", cwd=work)
    _git("push", "-u", "origin", "dev", cwd=work)
    return work


def _wf(repo: Path, tmp_path: Path) -> WorkflowDef:
    """``adopt_pr`` reads ``WORKFLOW.md`` off disk (:func:`chela.workflow.load_workflow`,
    like every other CLI entry point that takes a ``--workflow`` path) — so, unlike
    dispatcher-tick tests that only ever build the parsed object and patch
    ``load_workflow_cached``, this one must actually write the file."""
    path = repo / "WORKFLOW.md"
    path.write_text(
        "---\n"
        "project_key: TEST\n"
        "tracker:\n"
        "  kind: markdown\n"
        "  path: TODO.md\n"
        "workspace:\n"
        f"  root: {tmp_path / '.chela' / 'wts'}\n"
        "  base_branch: dev\n"
        "judge:\n"
        "  test_cmd: 'true'\n"
        "---\n"
    )
    return dispatcher.load_workflow(path)


def _branch_from_head(repo: Path, name: str) -> str:
    _git("branch", name, cwd=repo)
    return _git("rev-parse", name, cwd=repo).stdout.strip()


class _EmptySource:
    def list_open_tasks(self):
        return []


def _check_run(name="test", conclusion="SUCCESS"):
    return {"__typename": "CheckRun", "name": name, "status": "COMPLETED",
            "conclusion": conclusion, "workflowName": "CI",
            "detailsUrl": "https://github.com/o/r/actions/runs/1/job/1"}


def _router(sha: str, branch: str, pr_number: str = "1", rollup=None, gh_missing=False):
    """Fakes every ``gh`` call the adopt→tick pipeline makes (routed by the exact ``--json``
    field list each call site asks for — all three call sites end their argv with it), fakes
    ``tmux`` (window creation only), and passes any ``git`` call straight through to the real
    binary against the real repo fixture."""
    rollup = rollup if rollup is not None else [_check_run()]
    real_run = subprocess.run
    windows = {"n": 100}

    def _run(cmd, *a, **k):
        if cmd[:1] == ["git"]:
            return real_run(cmd, *a, **k)
        if cmd[:1] == ["tmux"]:
            if cmd[1:2] == ["new-window"]:
                wid = f"@{windows['n']}"
                windows["n"] += 1
                return SimpleNamespace(returncode=0, stdout=wid + "\n", stderr="")
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["gh", "pr", "view"] and "--json" in cmd:
            if gh_missing:
                raise FileNotFoundError("no gh on PATH")
            fields = cmd[-1]
            if fields == "url,title,state,headRefName,headRefOid":
                body = {"url": f"https://github.com/o/r/pull/{pr_number}",
                        "title": "fix judge test", "state": "OPEN",
                        "headRefName": branch, "headRefOid": sha}
            elif fields == "statusCheckRollup,headRefOid":
                body = {"headRefOid": sha, "statusCheckRollup": rollup}
            elif fields == "state,mergeable":
                body = {"state": "OPEN", "mergeable": "MERGEABLE"}
            else:
                return SimpleNamespace(returncode=1, stdout="", stderr=f"unhandled --json {fields}")
            return SimpleNamespace(returncode=0, stdout=json.dumps(body), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    return _run


# --- (a) adopt_pr: the row it creates -----------------------------------------------------

def test_adopt_creates_an_awaiting_review_row_with_no_worktree_or_window(tmp_path, repo):
    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)
    with patch.object(dispatcher.subprocess, "run", side_effect=_router(sha, "hand-opened-1")):
        result = dispatcher.adopt_pr("1", wf.path)

    assert result["ok"] is True
    assert result["task_id"] == "adopt-1"
    run = dispatcher.resolve_run("adopt-1")
    assert run["status"] == "awaiting_review"
    assert run["pr_state"] == "open"
    assert run["pr_head_sha"] == sha
    assert run["branch_name"] == "hand-opened-1"
    assert run["pr_url"] == "https://github.com/o/r/pull/1"
    # ⛔ no agent was spawned — the work is already pushed. A worktree/window here would mean
    # adopt tried to REDO the work instead of enrolling the existing PR.
    assert run["worktree_path"] is None
    assert run["window_name"] is None
    # not pre-judged: the NEXT tick's per-sha trigger must be the one that judges it.
    assert run["judge_sha"] is None
    assert run["judge_state"] is None


def test_adopt_refuses_a_pr_that_is_not_open(tmp_path, repo):
    wf = _wf(repo, tmp_path)

    def _closed(cmd, *a, **k):
        if cmd[:3] == ["gh", "pr", "view"] and cmd[-1] == "url,title,state,headRefName,headRefOid":
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                "url": "https://github.com/o/r/pull/9", "title": "old", "state": "MERGED",
                "headRefName": "old-branch", "headRefOid": "deadbeef",
            }), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(dispatcher.subprocess, "run", side_effect=_closed):
        result = dispatcher.adopt_pr("9", wf.path)

    assert result["ok"] is False
    assert "not" in result["error"].lower() and "open" in result["error"].lower()
    assert dispatcher.resolve_run("adopt-9") is None


def test_adopt_refuses_a_pr_already_tracked_by_pr_url(tmp_path, repo):
    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, pr_url, pr_state) "
            "VALUES (?, ?, ?, 'running', ?, 'open')",
            ("cmx-1", str(wf.path), "dispatched already", "https://github.com/o/r/pull/1"),
        )
        conn.commit()

    with patch.object(dispatcher.subprocess, "run", side_effect=_router(sha, "hand-opened-1")):
        result = dispatcher.adopt_pr("1", wf.path)

    assert result["ok"] is False
    assert "cmx-1" in result["error"]
    # a second row for the SAME PR would race two judges against one PR.
    assert dispatcher.resolve_run("adopt-1") is None


def test_adopt_refuses_a_second_adopt_of_the_same_pr(tmp_path, repo):
    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)
    with patch.object(dispatcher.subprocess, "run", side_effect=_router(sha, "hand-opened-1")):
        first = dispatcher.adopt_pr("1", wf.path)
        second = dispatcher.adopt_pr("1", wf.path)

    assert first["ok"] is True
    assert second["ok"] is False
    assert "adopt-1" in second["error"]


def test_adopt_refuses_when_gh_cannot_be_reached(tmp_path, repo):
    wf = _wf(repo, tmp_path)
    with patch.object(dispatcher.subprocess, "run", side_effect=FileNotFoundError("no gh")):
        result = dispatcher.adopt_pr("1", wf.path)
    assert result["ok"] is False
    assert dispatcher.resolve_run("adopt-1") is None


def test_adopt_refuses_a_non_pr_identifier(tmp_path, repo):
    wf = _wf(repo, tmp_path)
    result = dispatcher.adopt_pr("not-a-pr", wf.path)
    assert result["ok"] is False
    assert "not a PR" in result["error"]


def test_adopt_accepts_a_full_github_url_too(tmp_path, repo):
    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)
    with patch.object(dispatcher.subprocess, "run", side_effect=_router(sha, "hand-opened-1")):
        result = dispatcher.adopt_pr("https://github.com/o/r/pull/1", wf.path)
    assert result["ok"] is True
    assert result["task_id"] == "adopt-1"


# --- (b) end to end: the adopted row is what the per-sha judge trigger sees --------------

def test_an_adopted_pr_is_judged_on_the_very_next_tick(tmp_path, repo, monkeypatch):
    """The load-bearing claim: enrolling a hand-opened PR is not a paper trail — the next
    ordinary dispatcher tick (the SAME one that judges a dispatched PR's first pass) actually
    spawns a judge on it, with no special-casing anywhere else in ``tick()``.

    ⛔ Corrupt-guard target: land the adopted row in any status OTHER than
    ``awaiting_review`` (the per-sha trigger's ``SELECT`` requires it) and this goes red —
    the row exists, but the tick never looks at it, which is silently exactly the original
    bug with an extra row in the table.
    """
    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)

    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    monkeypatch.setattr(
        dispatcher, "load_workflow_cached",
        lambda *a, **k: WorkflowStatus(path=wf.path, workflow=wf, error=None),
    )
    monkeypatch.setattr(dispatcher, "get_source", lambda *a, **k: _EmptySource())
    monkeypatch.setattr(dispatcher, "_claim_order", lambda *a, **k: [])

    fake = _router(sha, "hand-opened-1")
    with patch.object(dispatcher.subprocess, "run", side_effect=fake):
        adopted = dispatcher.adopt_pr("1", wf.path)
        assert adopted["ok"] is True
        summary = dispatcher.tick(wf.path)

    assert summary["judged"] == 1
    run = dispatcher.resolve_run("adopt-1")
    assert run["judge_state"] == judge.J_RUNNING
    assert run["judge_sha"] == sha


# --- (c) the CLI ---------------------------------------------------------------------------

class _AdoptArgs:
    pr = "1"
    workflow = None
    reason = "opened by hand to fix a docs typo before the judge existed"


def test_cmd_adopt_success(tmp_path, repo, capsys):
    from chela import main

    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)
    args = _AdoptArgs()
    args.workflow = str(wf.path)
    with patch.object(dispatcher.subprocess, "run", side_effect=_router(sha, "hand-opened-1")):
        main.cmd_adopt(args)
    out = capsys.readouterr().out
    assert "adopt-1" in out
    assert "awaiting_review" in out
    assert dispatcher.resolve_run("adopt-1")["status"] == "awaiting_review"


def test_cmd_adopt_failure_exits_nonzero(tmp_path, repo, capsys):
    from chela import main

    wf = _wf(repo, tmp_path)
    args = _AdoptArgs()
    args.workflow = str(wf.path)
    with patch.object(dispatcher.subprocess, "run", side_effect=FileNotFoundError("no gh")), \
         pytest.raises(SystemExit) as exc:
        main.cmd_adopt(args)
    assert exc.value.code != 0
    out = capsys.readouterr().out
    assert "adopt:" in out


def test_chela_adopt_reaches_the_dispatcher_end_to_end(tmp_path, repo):
    """``chela adopt 1 --workflow ...`` must actually parse AND reach ``dispatcher.adopt_pr``
    — the dispatch call-site is the guard here. Mutate ``elif args.command == "adopt": …`` to
    ``pass`` and this fails: a subparser that parses but is never wired is silent, exactly
    the shape this whole feature exists to close."""
    from chela import main

    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)
    with patch.object(dispatcher.subprocess, "run", side_effect=_router(sha, "hand-opened-1")), \
         patch.object(sys, "argv", ["chela", "adopt", "1", "--workflow", str(wf.path)]):
        main.main()
    run = dispatcher.resolve_run("adopt-1")
    assert run is not None and run["status"] == "awaiting_review"


# ---------------------------------------------------------------------------
# CMX-321 — an adopted row survives reconcile even after it has been REWORKED
# ---------------------------------------------------------------------------
#
# CMX-276 stopped reconcile striking a freshly-adopted row `done` for "leaving the tracker"
# (it was never IN the tracker, so `task_id not in open_ids` is vacuously true and carries
# no completion evidence). It did that by testing `worktree_path IS NOT NULL` as a stand-in
# for "adopted" — which holds only until the row's FIRST REWORK. A rework gets a worktree,
# the proxy flips, and the next tick strikes the run.
#
# Measured 2026-08-21 on adopt-393/-396/-397: all three returned to `done` within one tick
# of being repaired by hand, which is what proved a hand-repair could not hold.

def _tick_with_empty_tracker(wf, monkeypatch, sha, branch):
    monkeypatch.setattr(dispatcher, "_kill_windows_named", lambda *a, **k: None)
    monkeypatch.setattr(dispatcher, "_wait_for_ready", lambda *a, **k: True)
    monkeypatch.setattr(dispatcher, "_send_seed", lambda *a, **k: True)
    monkeypatch.setattr(
        dispatcher, "load_workflow_cached",
        lambda *a, **k: WorkflowStatus(path=wf.path, workflow=wf, error=None),
    )
    monkeypatch.setattr(dispatcher, "get_source", lambda *a, **k: _EmptySource())
    monkeypatch.setattr(dispatcher, "_claim_order", lambda *a, **k: [])
    monkeypatch.setattr(dispatcher, "_cleanup_worktree_on_done", lambda *a, **k: None)


def test_an_adopted_row_that_has_been_REWORKED_is_not_struck_done(
        tmp_path, repo, monkeypatch):
    """🔴 The incident. Adopt a PR, give its row a worktree (what a rework round does), then
    run an ordinary tick against an EMPTY tracker. The row must stay under review.

    Revert `_is_adopted(row)` to `row["worktree_path"] is not None` and this goes red: the
    row is struck `done` and drops out of the judge loop for good.
    """
    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)
    _tick_with_empty_tracker(wf, monkeypatch, sha, "hand-opened-1")

    with patch.object(dispatcher.subprocess, "run", side_effect=_router(sha, "hand-opened-1")):
        assert dispatcher.adopt_pr("1", wf.path)["ok"] is True
        # A rework round gives the adopted row a worktree — the moment CMX-276's proxy flips.
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET worktree_path=?, rework_count=1 WHERE task_id=?",
                         (str(tmp_path / "wt" / "adopt-1"), "adopt-1"))
            conn.commit()
        dispatcher.tick(wf.path)

    run = dispatcher.resolve_run("adopt-1")
    assert run["status"] == "awaiting_review", (
        f"a reworked ADOPTED row was struck {run['status']!r} for leaving a tracker it was "
        "never in — it is now outside the judge loop with its PR still open"
    )


def test_a_DISPATCHED_row_that_leaves_the_tracker_is_still_struck_done(
        tmp_path, repo, monkeypatch):
    """⭐ MUST BE ACCEPTED — the legitimate behaviour this guard must not break. A row that
    really was claimed off the tracker, reached review, and then had its line struck (a
    human merged it) SHOULD reconcile to `done`. A fix that simply stopped striking rows
    would pass the test above while disabling reconcile entirely.
    """
    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)
    _tick_with_empty_tracker(wf, monkeypatch, sha, "hand-opened-1")

    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, branch_name, "
            "worktree_path, started_at, attempt, pr_url, pr_state) "
            "VALUES (?, ?, ?, 'awaiting_review', ?, ?, ?, 1, ?, 'open')",
            ("cmx-777", str(wf.path), "a real dispatched task", "cmx-777",
             str(tmp_path / "wt" / "cmx-777"), "2026-08-21T10:00:00+00:00",
             "https://github.com/o/r/pull/777"),
        )
        conn.commit()

    with patch.object(dispatcher.subprocess, "run", side_effect=_router(sha, "hand-opened-1")):
        dispatcher.tick(wf.path)

    assert dispatcher.resolve_run("cmx-777")["status"] == "done", (
        "a dispatched row that left the tracker from a review state must still reconcile "
        "to done — otherwise reconcile has simply been switched off"
    )


def test_adopt_records_the_origin_as_a_FACT_on_the_row(tmp_path, repo):
    """The column is written at adoption, so nothing downstream has to infer it.

    ⛔ Reads the row with a RAW sqlite connection, bypassing `dispatcher._db()` /
    `resolve_run`. Every `_db()` open re-runs the CMX-321 backfill migration
    (`UPDATE runs SET adopted=1 WHERE adopted=0 AND task_id LIKE 'adopt-%'`) — it exists to
    repair rows from BEFORE the column existed, but it cannot tell that apart from a row
    `adopt_pr` itself just got wrong: both are `adopted=0` with an `adopt-<n>` task_id. Going
    through `resolve_run` here would silently launder a broken `adopt_pr` insert back to 1
    before the assertion ever saw it — which is exactly how this went unnoticed before.
    """
    sha = _branch_from_head(repo, "hand-opened-1")
    wf = _wf(repo, tmp_path)
    with patch.object(dispatcher.subprocess, "run", side_effect=_router(sha, "hand-opened-1")):
        assert dispatcher.adopt_pr("1", wf.path)["ok"] is True

    raw = sqlite3.connect(str(dispatcher.DB_PATH))
    raw.row_factory = sqlite3.Row
    try:
        row = raw.execute("SELECT adopted FROM runs WHERE task_id='adopt-1'").fetchone()
    finally:
        raw.close()
    assert row["adopted"] == 1, "adopt_pr must write adopted=1 itself, not rely on a " \
        "later backfill to repair it"

    run = dispatcher.resolve_run("adopt-1")
    assert dispatcher._is_adopted(run) is True


def test_a_dispatched_row_is_not_marked_adopted(tmp_path, repo):
    """MUST BE ACCEPTED — the flag must discriminate, not simply be set everywhere."""
    wf = _wf(repo, tmp_path)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status) "
            "VALUES ('cmx-9', ?, 'dispatched', 'running')", (str(wf.path),),
        )
        conn.commit()
    assert dispatcher._is_adopted(dispatcher.resolve_run("cmx-9")) is False


def test_rows_adopted_before_the_column_existed_are_backfilled(tmp_path, repo):
    """A row written by an older chela reads 0 and would be struck exactly as before the
    fix. The one-time backfill in the schema migration repairs them by `adopt-<n>` task_id
    — `adopt_pr` is that shape's only writer.
    """
    wf = _wf(repo, tmp_path)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, adopted) "
            "VALUES ('adopt-4242', ?, 'legacy adopted row', 'awaiting_review', 0)",
            (str(wf.path),),
        )
        conn.commit()

    with dispatcher._db() as conn:            # reopening re-runs the migration + backfill
        row = conn.execute(
            "SELECT adopted FROM runs WHERE task_id='adopt-4242'").fetchone()

    assert row["adopted"] == 1, "a legacy adopted row was not backfilled"


def test_is_adopted_tolerates_a_row_without_the_column(tmp_path):
    """Runs unattended: a row read before the migration (or a hand-built dict in a test)
    must degrade to the pre-CMX-321 answer, never raise."""
    assert dispatcher._is_adopted({"task_id": "adopt-1"}) is False

