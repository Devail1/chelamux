"""issue #502 B2 — the push/PR-open hop moves off the dispatched agent's own process, the
same shape #502 B1 already moved `chela task-finished`'s completion hop in.

Masking the GitHub token cannot rescue this Done Criteria step (measured in
docs/SANDBOX_BOUNDARY.md §5 B2, with a matched unsandboxed control): `gh` sends
`Authorization: token <token>` verbatim, so the sandbox proxy's substring substitution
catches it — but `git` sends `Authorization: Basic base64(user:token)`, the token is never
present verbatim in that header, nothing is substituted, and GitHub rejects the push with
`Invalid username or token`. The fix moves the hop rather than masking it harder:
`dispatcher.request_push` (agent-side, called from `chela request-push`) only ever writes a
marker file into the run's OWN worktree — the guard is that it NEVER shells out, exactly
like `request_task_finished` — and `dispatcher.tick()` (daemon-side, unsandboxed, already
polling) is the only thing that reads the marker, pushes the branch, and — on a first
dispatch, gated on the run having no `pr_url` yet — opens the PR.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from chela import dispatcher

from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    """A runs DB per test — mirrors the identically-named fixture elsewhere; without it every
    test in the session would share one `scheduler.db`."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


# --- dispatcher.request_push (agent-side) -------------------------------------------------


def _runs_snapshot() -> list[dict]:
    with dispatcher._db() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM runs ORDER BY task_id").fetchall()]


def test_request_push_writes_a_marker_and_touches_no_run_row(tmp_path):
    """🔴 GUARD (accept case): moving this off the agent's process is only real if it
    touches NOTHING privileged — snapshot the WHOLE runs table (two rows, not just the one
    being pushed) before/after, mirroring the same shape
    test_request_task_finished_writes_a_marker_and_touches_no_run_row pins for issue #502 B1.
    """
    wt = tmp_path / "wt"
    wt.mkdir()
    wt2 = tmp_path / "wt2"
    wt2.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(wt), pr_url=None)
        _row(conn, task_id="t2", status="running", worktree_path=str(wt2),
             window_name="test-2", branch_name="test-2")

    before = _runs_snapshot()

    with patch.object(dispatcher.subprocess, "run") as run:
        result = dispatcher.request_push("t1", pr_title="CMX-1: a thing", pr_body="the body")

    run.assert_not_called()
    assert result == {"ok": True, "task_id": "t1", "requested": True}
    marker = wt / ".chela-push-request.json"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert payload["task_id"] == "t1"
    assert payload["pr_title"] == "CMX-1: a thing"
    assert payload["pr_body"] == "the body"
    assert payload["attempts"] == 0
    assert "requested_at" in payload
    after = _runs_snapshot()
    assert after == before, "request_push must not write ANY row in the runs table"


def test_request_push_accepts_a_claimed_row_too(tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="claimed", worktree_path=str(wt))

    result = dispatcher.request_push("t1")

    assert result == {"ok": True, "task_id": "t1", "requested": True}
    assert (wt / ".chela-push-request.json").exists()


def test_request_push_defaults_to_no_pr_title_for_a_rework(tmp_path):
    """A rework calls this with neither kwarg — the PR already exists, so only the push
    should ever run. Pinning the default here matters: a truthy default for `pr_title`
    would make `_apply_push_request` try to open a SECOND PR for every rework."""
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(wt))

    dispatcher.request_push("t1")

    payload = json.loads((wt / ".chela-push-request.json").read_text())
    assert payload["pr_title"] is None
    assert payload["pr_body"] is None


def test_request_push_unknown_task_id_errors():
    result = dispatcher.request_push("no-such-task")
    assert result == {"ok": False, "error": "no run found for task_id no-such-task"}


@pytest.mark.parametrize("status", ["awaiting_review", "done", "failed", "needs_human"])
def test_request_push_refuses_a_status_that_is_not_claimed_or_running(tmp_path, status):
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status=status, worktree_path=str(wt))

    result = dispatcher.request_push("t1")

    assert result["ok"] is False
    assert f"status {status!r}" in result["error"]
    assert not (wt / ".chela-push-request.json").exists()


def test_request_push_errors_when_the_row_has_no_worktree_path(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=None)

    result = dispatcher.request_push("t1")

    assert result["ok"] is False
    assert "worktree_path" in result["error"]


# --- dispatcher._apply_push_request (daemon-side, unit level) -----------------------------


def _gh_git_fake(calls, *, push_ok=True, gh_ok=True, pr_url="https://github.com/o/r/pull/9"):
    def _run(cmd, *args, **kwargs):
        calls.append(cmd)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if isinstance(cmd, list) and cmd[:1] == ["git"] and "push" in cmd:
            R.returncode = 0 if push_ok else 1
            if not push_ok:
                R.stderr = "remote: Invalid username or token. Password authentication " \
                           "is not supported for Git operations."
            return R()
        if isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "create"]:
            R.returncode = 0 if gh_ok else 1
            if gh_ok:
                R.stdout = pr_url + "\n"
            else:
                R.stderr = "gh: could not create pull request"
            return R()
        return R()

    return _run


def test_apply_push_request_pushes_and_opens_the_pr_on_a_first_dispatch(tmp_path):
    """🔴 GUARD (accept case): a row with no `pr_url` yet, whose marker carries a title,
    gets BOTH a `git push` and a `gh pr create` — and the resulting URL lands on the run
    row, since nothing downstream (task-finished) scrapes the agent's transcript for it
    anymore once the daemon opens the PR itself."""
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a thing", "pr_body": "the body", "attempts": 0,
    }))
    calls: list[list[str]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        with patch.object(dispatcher.subprocess, "run", side_effect=_gh_git_fake(calls)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied == {"ok": True, "pr_url": "https://github.com/o/r/pull/9"}
    assert dispatcher.resolve_run("t1")["pr_url"] == "https://github.com/o/r/pull/9"
    push_calls = [c for c in calls if "push" in c]
    assert push_calls and push_calls[0][:2] == ["git", "-C"]
    assert "cmx-1" in push_calls[0]
    gh_calls = [c for c in calls if c[:3] == ["gh", "pr", "create"]]
    assert len(gh_calls) == 1
    assert "--base" in gh_calls[0] and "dev" in gh_calls[0]
    assert "CMX-1: a thing" in gh_calls[0]
    assert "the body" in gh_calls[0]


def test_apply_push_request_skips_pr_create_when_a_pr_already_exists(tmp_path):
    """🔴 GUARD (rework case, and the no-duplicate-PR invariant): once `pr_url` is set,
    `gh pr create` must NEVER run again — a rework's marker carries no `pr_title` at all,
    but even a marker that somehow did carry one must not re-open a second PR for the same
    branch. Corrupt by gating PR-create on the marker instead of `row["pr_url"]` → a
    daemon restart or a retried tick opens a duplicate PR → RED.
    """
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": None, "pr_body": None, "attempts": 0,
    }))
    calls: list[list[str]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url="https://github.com/o/r/pull/80")
        with patch.object(dispatcher.subprocess, "run", side_effect=_gh_git_fake(calls)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied == {"ok": True, "pr_url": "https://github.com/o/r/pull/80"}
    assert not any(c[:3] == ["gh", "pr", "create"] for c in calls)


def test_apply_push_request_skips_pr_create_even_if_the_marker_carries_a_stray_pr_title(tmp_path):
    """The real gate is `row["pr_url"]`, not the marker — this is what actually exercises
    that: a marker with a (stray, should-never-happen-in-practice) `pr_title` set, against a
    row that already has a `pr_url`. Corrupt by gating PR-create on `marker.get("pr_title")`
    alone → this calls `gh pr create` a second time → RED."""
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a stray title", "pr_body": None, "attempts": 0,
    }))
    calls: list[list[str]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url="https://github.com/o/r/pull/80")
        with patch.object(dispatcher.subprocess, "run", side_effect=_gh_git_fake(calls)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied == {"ok": True, "pr_url": "https://github.com/o/r/pull/80"}
    assert not any(c[:3] == ["gh", "pr", "create"] for c in calls)


def test_apply_push_request_fails_on_a_bad_push_and_never_calls_gh(tmp_path):
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a thing", "pr_body": "body", "attempts": 0,
    }))
    calls: list[list[str]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        with patch.object(dispatcher.subprocess, "run",
                           side_effect=_gh_git_fake(calls, push_ok=False)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied["ok"] is False
    assert "git push failed" in applied["error"]
    assert not any(c[:3] == ["gh", "pr", "create"] for c in calls)
    assert dispatcher.resolve_run("t1")["pr_url"] is None


def test_apply_push_request_fails_when_gh_pr_create_fails(tmp_path):
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a thing", "pr_body": "body", "attempts": 0,
    }))
    calls: list[list[str]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        with patch.object(dispatcher.subprocess, "run",
                           side_effect=_gh_git_fake(calls, gh_ok=False)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied["ok"] is False
    assert "gh pr create failed" in applied["error"]
    assert dispatcher.resolve_run("t1")["pr_url"] is None


def test_apply_push_request_errors_when_the_marker_is_missing(tmp_path):
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied["ok"] is False
    assert "push request marker" in applied["error"]


# --- dispatcher.tick(): applying a pending push request (daemon-side, integration) --------


def test_tick_applies_a_pending_push_request_end_to_end(tmp_path):
    """🔴 GUARD (accept case): a `running` row carrying a push-request marker gets pushed
    and its PR opened by `tick()` itself, and the marker is consumed so it is never
    re-applied. Corrupt by dropping the marker check from `tick()` → the row is never
    picked up → RED.
    """
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "abc123", "pr_title": "CMX-1: a thing", "pr_body": "the body",
    }))
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, task_id="abc123", workflow_path=str(wf.path), status="running",
             window_name="test-1", worktree_path=str(wt), branch_name="abc123",
             pr_url=None, pr_state=None)

    calls: list[list[str]] = []
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run", side_effect=_gh_git_fake(calls)):
        summary = dispatcher.tick(wf.path)

    assert summary["push_applied"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["pr_url"] == "https://github.com/o/r/pull/9"
    assert run["status"] == "running", "opening the PR must not itself transition the run"
    assert not (wt / ".chela-push-request.json").exists(), \
        "the marker must be consumed so it is never re-applied"


def test_tick_leaves_a_running_row_with_no_push_marker_alone(tmp_path):
    """⛔⛔ THE COUNTERWEIGHT: a `running` row with NO push-request marker must never be
    swept into this path. Corrupt by matching on status alone (dropping the `.exists()`
    check) → every running row tries to push on the first tick → RED.
    """
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, task_id="abc123", workflow_path=str(wf.path), status="running",
             window_name="test-1", worktree_path=str(wt), branch_name="abc123",
             pr_url=None, pr_state=None, started_at=dispatcher._now())

    fake = _FakeTmux()
    fake.windows = [("@1", "test-1")]
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher, "_tmux_windows", return_value={"test-1"}), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        summary = dispatcher.tick(wf.path)

    assert summary["push_applied"] == 0
    assert dispatcher.resolve_run("abc123")["status"] == "running"


def test_tick_checks_the_push_marker_before_the_task_finished_marker(tmp_path):
    """🔴 GUARD: an agent can request a push and then immediately request task-finished,
    both before the daemon's next tick — so both markers can exist at once. The push must
    resolve BEFORE `mark_awaiting_review` ever runs, or a run reaches `awaiting_review`
    with no real PR to review. Corrupt by checking the task-finished marker first (or by
    letting both apply on the SAME tick) → RED.
    """
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "abc123", "pr_title": "CMX-1: a thing", "pr_body": "the body",
    }))
    (wt / ".chela-task-finished-request.json").write_text(json.dumps({"task_id": "abc123"}))
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, task_id="abc123", workflow_path=str(wf.path), status="running",
             window_name="test-1", worktree_path=str(wt), branch_name="abc123",
             pr_url=None, pr_state=None)

    calls: list[list[str]] = []
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run", side_effect=_gh_git_fake(calls)):
        summary = dispatcher.tick(wf.path)

    assert summary["push_applied"] == 1
    assert summary["task_finished_applied"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"
    assert not (wt / ".chela-push-request.json").exists()
    assert (wt / ".chela-task-finished-request.json").exists(), \
        "the task-finished marker must wait for a LATER tick, once the push has resolved"


def test_tick_retries_a_failing_push_without_escalating_before_the_attempt_cap(tmp_path):
    """A transient failure (a network blip) costs a retry, not an immediate escalation —
    mirrors the bounded-retry shape used elsewhere in this file (`judge_max_unknown_retries`,
    rework's own `MAX_ATTEMPTS`)."""
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "abc123", "pr_title": "CMX-1: a thing", "pr_body": "body", "attempts": 0,
    }))
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, task_id="abc123", workflow_path=str(wf.path), status="running",
             window_name="test-1", worktree_path=str(wt), branch_name="abc123",
             pr_url=None, pr_state=None)

    calls: list[list[str]] = []
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run",
                       side_effect=_gh_git_fake(calls, push_ok=False)):
        summary = dispatcher.tick(wf.path)

    assert summary["push_applied"] == 0
    assert summary["escalated"] == 0
    assert dispatcher.resolve_run("abc123")["status"] == "running"
    marker = json.loads((wt / ".chela-push-request.json").read_text())
    assert marker["attempts"] == 1


def test_tick_escalates_after_the_attempt_cap_on_a_persistent_push_failure(tmp_path):
    """🔴 GUARD (hazard 3 shape, mirrored from #502 B1): a push that NEVER succeeds must not
    strand the run in `running` forever, pinning a concurrency slot with only a
    `log.warning` nobody reads. Corrupt by dropping the attempt-cap check (never escalate)
    → `summary["escalated"]` stays 0 forever → RED.
    """
    wf = _wf(tmp_path)
    wt = tmp_path / "wt" / "abc123"
    wt.mkdir(parents=True)
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "abc123", "pr_title": "CMX-1: a thing", "pr_body": "body",
        "attempts": dispatcher.PUSH_REQUEST_MAX_ATTEMPTS - 1,
    }))
    source = _Source("abc123")
    with dispatcher._db() as conn:
        _row(conn, task_id="abc123", workflow_path=str(wf.path), status="running",
             window_name="test-1", worktree_path=str(wt), branch_name="abc123",
             pr_url=None, pr_state=None)

    calls: list[list[str]] = []
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run",
                       side_effect=_gh_git_fake(calls, push_ok=False)):
        summary = dispatcher.tick(wf.path)

    assert summary["escalated"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert "request-push" in run["last_error"]
    assert (wt / ".chela-push-request.json").exists(), \
        "the marker is the agent's request evidence and must survive an escalation"
