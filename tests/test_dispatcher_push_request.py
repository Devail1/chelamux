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

import io
import json
import sys
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
    """``calls`` collects ``(cmd, kwargs)`` pairs, not bare ``cmd`` — issue #502 B2 rework
    round 1, judge findings 3 and 4: a fake that discards kwargs makes `cwd` unobservable
    to any assertion, so a mutation that aims `gh pr create`/`git push` at the WRONG repo
    (dropping `cwd=str(worktree_path)`, or swapping `Path(worktree_path)` for `Path(".")`)
    stays green. Every caller below must assert on the recorded `cwd`, not just the argv
    shape."""
    def _run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))

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
                # PR #512 round 4, judge finding 3: real `gh pr create` can print more than
                # the URL on success (a branch-push notice, warnings) before it — a fake
                # returning a single line makes `splitlines()[-1]` and `splitlines()[0]`
                # indistinguishable to every assertion below. Noise line first, URL last,
                # matching the shape the `[-1]` index exists to handle.
                R.stdout = "remote: \nremote: Create a pull request...\n" + pr_url + "\n"
            else:
                R.stderr = "gh: could not create pull request"
            return R()
        return R()

    return _run


def test_apply_push_request_pushes_and_opens_the_pr_on_a_first_dispatch(tmp_path):
    """🔴 GUARD (accept case): a row with no `pr_url` yet, whose marker carries a title,
    gets BOTH a `git push` and a `gh pr create` — and the resulting URL lands on the run
    row, since nothing downstream (task-finished) scrapes the agent's transcript for it
    anymore once the daemon opens the PR itself.

    ⛔ round 3, judge finding 3: `base_branch` is deliberately set to neither the fixture's
    usual `"dev"` nor the production default `"master"` — a `base_branch` literal hardcoded
    anywhere (in the dispatcher OR by coincidentally matching the fixture default) must go
    red here. Only reading `wf.get("workspace", "base_branch", ...)` at call time survives.
    """
    wf = _wf(tmp_path, workspace={
        "root": str(tmp_path / ".chela" / "wts"), "base_branch": "release/9000-not-a-default",
    })
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a thing", "pr_body": "the body", "attempts": 0,
    }))
    calls: list[tuple[list[str], dict]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        with patch.object(dispatcher.subprocess, "run", side_effect=_gh_git_fake(calls)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied == {"ok": True, "pr_url": "https://github.com/o/r/pull/9"}
    assert dispatcher.resolve_run("t1")["pr_url"] == "https://github.com/o/r/pull/9"
    push_calls = [(c, kw) for c, kw in calls if "push" in c]
    assert push_calls and push_calls[0][0][:2] == ["git", "-C"]
    # ⛔ #502 B2 rework round 1, judge finding 4: the argv shape and branch name alone
    # don't pin WHICH repo `-C` targets — assert the actual path too, or `_git(Path("."),
    # ...)` stays green.
    assert push_calls[0][0][2] == str(wt)
    assert "cmx-1" in push_calls[0][0]
    # ⛔ PR #512 round 4, judge finding 2: `git push` runs INSIDE the daemon's tick loop and
    # is bounded by `timeout=GIT_NET_TIMEOUT_SECONDS` precisely so a hung push can't stall
    # every run on this workflow forever — pin the kwarg, or `timeout=None` stays green.
    # DEFEAT_SHAPES #5: the constant comparisons below read the SAME symbol a mutation would
    # edit, so pin the literal too, or widening/dropping the constant moves both sides
    # together and stays green.
    assert dispatcher.GIT_NET_TIMEOUT_SECONDS == 60, dispatcher.GIT_NET_TIMEOUT_SECONDS
    assert push_calls[0][1].get("timeout") == dispatcher.GIT_NET_TIMEOUT_SECONDS
    gh_calls = [(c, kw) for c, kw in calls if c[:3] == ["gh", "pr", "create"]]
    assert len(gh_calls) == 1
    assert "--base" in gh_calls[0][0] and "release/9000-not-a-default" in gh_calls[0][0]
    assert "dev" not in gh_calls[0][0] and "master" not in gh_calls[0][0]
    assert "CMX-1: a thing" in gh_calls[0][0]
    assert "the body" in gh_calls[0][0]
    # ⛔ #502 B2 rework round 1, judge finding 3: `cwd` decides which repo (and therefore
    # which head branch) the PR is opened from — pin it, or `cwd=None` stays green.
    assert gh_calls[0][1].get("cwd") == str(wt)
    # ⛔ PR #512 round 4, judge finding 2: same bound applies to `gh pr create`.
    assert gh_calls[0][1].get("timeout") == dispatcher.GIT_NET_TIMEOUT_SECONDS


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
    calls: list[tuple[list[str], dict]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url="https://github.com/o/r/pull/80")
        with patch.object(dispatcher.subprocess, "run", side_effect=_gh_git_fake(calls)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied == {"ok": True, "pr_url": "https://github.com/o/r/pull/80"}
    assert not any(c[:3] == ["gh", "pr", "create"] for c, _kw in calls)


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
    calls: list[tuple[list[str], dict]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url="https://github.com/o/r/pull/80")
        with patch.object(dispatcher.subprocess, "run", side_effect=_gh_git_fake(calls)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied == {"ok": True, "pr_url": "https://github.com/o/r/pull/80"}
    assert not any(c[:3] == ["gh", "pr", "create"] for c, _kw in calls)


def test_apply_push_request_never_opens_a_pr_when_pr_url_is_empty_and_pr_title_is_missing(
    tmp_path,
):
    """🔴 GUARD (issue #502 B2 rework round 1, judge finding 2): the gate is a two-operand
    `not pr_url and pr_title` — three siblings above pin the `pr_url` operand (first
    dispatch / rework / stray-title), but none of them reaches `_apply_push_request` with
    `pr_url` EMPTY and `pr_title` MISSING, which is exactly what a first-dispatch agent
    produces by running `chela request-push <id>` with no `--pr-title` (the spelling
    WORKFLOW.md prescribes for a REWORK). Corrupt by dropping the `pr_title` conjunct
    (`if not pr_url:`) → this now calls `gh pr create --title None ...`, which the real `gh`
    binary can't even accept (`subprocess` rejects a `None` argv element with a TypeError
    that no `except` clause here catches) → RED, since the fake below records a call that
    the correct code must never make."""
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": None, "pr_body": None, "attempts": 0,
    }))
    calls: list[tuple[list[str], dict]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        with patch.object(dispatcher.subprocess, "run", side_effect=_gh_git_fake(calls)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied == {"ok": True, "pr_url": None}
    assert not any(c[:3] == ["gh", "pr", "create"] for c, _kw in calls)
    assert dispatcher.resolve_run("t1")["pr_url"] is None


def test_apply_push_request_fails_on_a_bad_push_and_never_calls_gh(tmp_path):
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a thing", "pr_body": "body", "attempts": 0,
    }))
    calls: list[tuple[list[str], dict]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        with patch.object(dispatcher.subprocess, "run",
                           side_effect=_gh_git_fake(calls, push_ok=False)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied["ok"] is False
    assert "git push failed" in applied["error"]
    assert not any(c[:3] == ["gh", "pr", "create"] for c, _kw in calls)
    assert dispatcher.resolve_run("t1")["pr_url"] is None


def test_apply_push_request_fails_when_gh_pr_create_fails(tmp_path):
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a thing", "pr_body": "body", "attempts": 0,
    }))
    calls: list[tuple[list[str], dict]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        with patch.object(dispatcher.subprocess, "run",
                           side_effect=_gh_git_fake(calls, gh_ok=False)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied["ok"] is False
    assert "gh pr create failed" in applied["error"]
    assert dispatcher.resolve_run("t1")["pr_url"] is None


def _gh_view_fake(calls, *, create_rc=0, view_ok=True,
                   view_url="https://github.com/o/r/pull/9"):
    """`gh pr create` exits `create_rc` with EMPTY stdout — the rc=0-but-nothing-parseable
    shape orchestrator review round 1, note 2 diagnosed: `gh pr create` CAN exit 0 while
    printing nothing, which must never be read as full success with no URL recorded.
    `gh pr view <branch> --json url -q .url` is the recovery call `_apply_push_request`
    must fall back to.

    PR #512 round 2, finding 3: this fake used to answer ANY `gh pr view` call with
    `view_url`, matching on `cmd[:3]` alone — so a mutation that swapped the real call's
    `--json url -q .url` selectors for `--json number -q .number` (asking gh for the PR
    NUMBER instead of its URL) still got the URL back, and the test only ever asserted
    the branch argument and cwd, never what was actually requested. Real `gh -q` prints
    exactly the JMESPath-selected field, so this fake now does the same: it inspects the
    `-q` selector and only returns `view_url` for `.url`; any other selector (including a
    mutated `.number`) gets a plausible-but-wrong value instead, which fails the URL
    assertions below instead of silently passing them."""
    def _run(cmd, *args, **kwargs):
        calls.append((cmd, kwargs))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if isinstance(cmd, list) and cmd[:1] == ["git"] and "push" in cmd:
            return R()
        if isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "create"]:
            R.returncode = create_rc
            return R()
        if isinstance(cmd, list) and cmd[:3] == ["gh", "pr", "view"]:
            R.returncode = 0 if view_ok else 1
            if view_ok:
                selector = cmd[cmd.index("-q") + 1] if "-q" in cmd else None
                R.stdout = (view_url if selector == ".url" else "9") + "\n"
            else:
                R.stderr = "gh: no pull requests found for branch"
            return R()
        return R()

    return _run


def test_apply_push_request_recovers_the_url_when_gh_pr_create_exits_0_with_empty_stdout(
    tmp_path,
):
    """⚖️ orchestrator review round 1, note 2 (RULED IN): `gh pr create` exiting 0 with no
    parseable URL on stdout must not be read as full success with nothing recorded — that
    is the exact state the marker-before-task-finished ordering exists to prevent (a real
    PR live on GitHub that chela does not know about). The PR WAS opened (rc=0), so recover
    its URL via `gh pr view` rather than discard that fact. Corrupt by dropping the
    recovery call (treat empty stdout as `pr_url=None` and still return `ok: True`) → the
    row's `pr_url` stays unset here → RED."""
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a thing", "pr_body": "body", "attempts": 0,
    }))
    calls: list[tuple[list[str], dict]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        with patch.object(dispatcher.subprocess, "run", side_effect=_gh_view_fake(calls)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied == {"ok": True, "pr_url": "https://github.com/o/r/pull/9"}
    assert dispatcher.resolve_run("t1")["pr_url"] == "https://github.com/o/r/pull/9"
    view_calls = [(c, kw) for c, kw in calls if c[:3] == ["gh", "pr", "view"]]
    assert len(view_calls) == 1
    assert "cmx-1" in view_calls[0][0]
    assert view_calls[0][1].get("cwd") == str(wt)
    # PR #512 round 2, finding 3: pin WHAT is asked for, not just that gh pr view ran —
    # a selector swapped to `--json number -q .number` must not read back as the URL.
    assert view_calls[0][0][-4:] == ["--json", "url", "-q", ".url"]
    # ⛔ PR #512 round 4, judge finding 2: this recovery call is also bounded by
    # `timeout=GIT_NET_TIMEOUT_SECONDS` — pin it too.
    assert view_calls[0][1].get("timeout") == dispatcher.GIT_NET_TIMEOUT_SECONDS


def test_apply_push_request_fails_when_gh_pr_create_empty_stdout_and_recovery_also_fails(
    tmp_path,
):
    """The counterweight: when even `gh pr view` cannot recover a URL, refuse (`ok:
    False`) rather than unlink the marker with an empty `pr_url` — the bounded retry takes
    over instead of silently losing the request."""
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a thing", "pr_body": "body", "attempts": 0,
    }))
    calls: list[tuple[list[str], dict]] = []
    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="cmx-1", pr_url=None)
        with patch.object(dispatcher.subprocess, "run",
                           side_effect=_gh_view_fake(calls, view_ok=False)):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied["ok"] is False
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


def test_apply_push_request_refuses_when_the_row_has_no_branch_name(tmp_path):
    """🔴 GUARD (PR #512 round 5, judge finding 3): every other branch of this function has a
    dedicated test that reaches it, but nothing before this test ever calls
    `_apply_push_request` with an empty `branch_name` — every sibling in this file hardcodes
    `branch_name="cmx-1"`. Dead-code the check (e.g. `if False and not branch:`) and a row
    with no `branch_name` on record falls through to `_git(..., "push", "-u", "origin",
    None, ...)`, whose argv contains a `None` element — `subprocess.run` raises `TypeError`,
    which `_git` does not catch (only `TimeoutExpired`/`FileNotFoundError` are), so the
    exception escapes `_apply_push_request` into `tick()`'s row loop and takes the whole tick
    down for every run on the workflow. The fake `subprocess.run` below raises on ANY call,
    so this also catches a corruption that reaches the git/gh call at all rather than
    refusing first.
    """
    wf = _wf(tmp_path)
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".chela-push-request.json").write_text(json.dumps({
        "task_id": "t1", "pr_title": "CMX-1: a thing", "pr_body": "body", "attempts": 0,
    }))

    def _must_not_be_called(*args, **kwargs):
        raise AssertionError(f"subprocess.run must not be called when branch_name is empty: "
                              f"{args!r} {kwargs!r}")

    with dispatcher._db() as conn:
        row = _row(conn, task_id="t1", status="running", worktree_path=str(wt),
                    branch_name="", pr_url=None)
        with patch.object(dispatcher.subprocess, "run", side_effect=_must_not_be_called):
            applied = dispatcher._apply_push_request(conn, wf, row)

    assert applied == {"ok": False, "error": "run has no branch_name on record"}
    assert dispatcher.resolve_run("t1")["pr_url"] is None


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

    calls: list[tuple[list[str], dict]] = []
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

    calls: list[tuple[list[str], dict]] = []
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

    calls: list[tuple[list[str], dict]] = []
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
    # ⛔ DEFEAT SHAPE 05 (same remedy this file already applies to GIT_NET_TIMEOUT_SECONDS
    # above): expressing the fixture as `PUSH_REQUEST_MAX_ATTEMPTS - 1` reads the SAME symbol
    # a mutation would edit, so the fixture SLIDES with the constant and the bound is never
    # pinned. Widen it to 5000 and this test still passes while a permanently-failing push
    # burns 5000 ticks — days of a pinned concurrency slot with only a log.warning nobody
    # reads — before a human is ever told. Assert the literal, then compare against the symbol.
    assert dispatcher.PUSH_REQUEST_MAX_ATTEMPTS == 5, dispatcher.PUSH_REQUEST_MAX_ATTEMPTS
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

    calls: list[tuple[list[str], dict]] = []
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


def test_tick_resets_the_attempt_counter_on_escalation_so_a_reopen_gets_a_fresh_budget(
    tmp_path,
):
    """⚖️ orchestrator review round 1, note 3 (RULED IN): the escalation text tells a human
    to `chela reopen` if the work is actually done, but the marker's `attempts` was left
    pinned at the cap — a reopened run would then escalate again on its very first tick,
    before getting a single fresh attempt, contradicting its own remedy. Corrupt by
    dropping the reset call → `attempts` stays at `PUSH_REQUEST_MAX_ATTEMPTS` after
    escalation → RED.
    """
    # ⛔ DEFEAT SHAPE 05 (same remedy this file already applies to GIT_NET_TIMEOUT_SECONDS
    # above): expressing the fixture as `PUSH_REQUEST_MAX_ATTEMPTS - 1` reads the SAME symbol
    # a mutation would edit, so the fixture SLIDES with the constant and the bound is never
    # pinned. Widen it to 5000 and this test still passes while a permanently-failing push
    # burns 5000 ticks — days of a pinned concurrency slot with only a log.warning nobody
    # reads — before a human is ever told. Assert the literal, then compare against the symbol.
    assert dispatcher.PUSH_REQUEST_MAX_ATTEMPTS == 5, dispatcher.PUSH_REQUEST_MAX_ATTEMPTS
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

    calls: list[tuple[list[str], dict]] = []
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run",
                       side_effect=_gh_git_fake(calls, push_ok=False)):
        summary = dispatcher.tick(wf.path)

    assert summary["escalated"] == 1
    marker = json.loads((wt / ".chela-push-request.json").read_text())
    assert marker["attempts"] == 0, \
        "a reopened run must get a fresh retry budget, not re-escalate on its first tick"


# --- cmd_request_push CLI: parser wiring ------------------------------------------------
#
# ⛔ issue #502 B2 rework round 1 (THE JUDGE), finding 1 (WIRING): every test above drives
# `dispatcher.request_push`/`dispatcher._apply_push_request` DIRECTLY — nothing in this
# file (or anywhere else) ever drove `cmd_request_push` or the `request-push` argparse
# dispatch itself, so `elif False and args.command == "request-push":` in `main.py` stayed
# green. Mirrors the "end-to-end" contrast the judge drew against
# tests/test_dispatcher_task_finished.py's `cmd_task_finished` block: drive `main.main()`
# with a real `sys.argv`, through the real parser, into the real `cmd_request_push`.


def test_cmd_request_push_end_to_end_writes_the_marker_via_the_real_dispatcher_call(
    tmp_path, capsys,
):
    """🔴 GUARD (accept case, against the REAL `dispatcher.request_push` — nothing mocked
    below it): drives `chela request-push <id> --pr-title ...` through the real argparse
    dispatch and the real marker write. Corrupt the subcommand dispatch (`elif False and
    args.command == "request-push":`) → `cmd_request_push` never runs → no marker is ever
    written → RED."""
    from chela import main

    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(wt))

    with patch.object(sys, "argv", ["chela", "request-push", "t1",
                                     "--pr-title", "CMX-1: a thing"]):
        main.main()

    marker = wt / ".chela-push-request.json"
    assert marker.exists()
    payload = json.loads(marker.read_text())
    assert payload["task_id"] == "t1"
    assert payload["pr_title"] == "CMX-1: a thing"
    out = capsys.readouterr().out
    assert "push requested" in out
    assert "open the PR" in out


def test_cmd_request_push_end_to_end_omits_pr_open_language_without_a_title(tmp_path, capsys):
    """The rework spelling — no `--pr-title` — must not promise a PR open in its own
    printed confirmation."""
    from chela import main

    wt = tmp_path / "wt"
    wt.mkdir()
    with dispatcher._db() as conn:
        _row(conn, task_id="t1", status="running", worktree_path=str(wt))

    with patch.object(sys, "argv", ["chela", "request-push", "t1"]):
        main.main()

    payload = json.loads((wt / ".chela-push-request.json").read_text())
    assert payload["pr_title"] is None
    out = capsys.readouterr().out
    assert "push requested" in out
    assert "open the PR" not in out


def test_cmd_request_push_reaches_dispatcher_request_push_with_the_right_arguments(capsys):
    """Pins the exact forwarding — `task_id` positional, `pr_title`/`pr_body` as kwargs —
    against a mocked `dispatcher.request_push`, independent of the marker-write mechanics
    the end-to-end test above already covers."""
    from chela import main

    with patch.object(dispatcher, "request_push",
                       return_value={"ok": True, "task_id": "cmx-777",
                                     "requested": True}) as req:
        with patch.object(sys, "argv", ["chela", "request-push", "cmx-777",
                                         "--pr-title", "CMX-777: a title"]):
            main.main()

    req.assert_called_once_with("cmx-777", pr_title="CMX-777: a title", pr_body=None)


def test_cmd_request_push_reads_pr_body_from_a_file(tmp_path):
    from chela import main

    body_path = tmp_path / "body.md"
    body_path.write_text("a long-form PR body\nwith more than one line\n")

    with patch.object(dispatcher, "request_push",
                       return_value={"ok": True, "task_id": "t1", "requested": True}) as req:
        with patch.object(sys, "argv", ["chela", "request-push", "t1",
                                         "--pr-body-file", str(body_path)]):
            main.main()

    req.assert_called_once_with(
        "t1", pr_title=None, pr_body="a long-form PR body\nwith more than one line\n")


def test_cmd_request_push_reads_pr_body_from_stdin_when_the_file_is_a_dash(monkeypatch):
    from chela import main

    monkeypatch.setattr(sys, "stdin", io.StringIO("from stdin\n"))
    with patch.object(dispatcher, "request_push",
                       return_value={"ok": True, "task_id": "t1", "requested": True}) as req:
        with patch.object(sys, "argv", ["chela", "request-push", "t1",
                                         "--pr-body-file", "-"]):
            main.main()

    req.assert_called_once_with("t1", pr_title=None, pr_body="from stdin\n")


def test_cmd_request_push_exits_nonzero_and_prints_the_error_when_the_call_fails(capsys):
    from chela import main

    with patch.object(dispatcher, "request_push",
                       return_value={"ok": False, "error": "a very specific reason"}):
        with patch.object(sys, "argv", ["chela", "request-push", "t1"]):
            with pytest.raises(SystemExit) as exc:
                main.main()

    assert exc.value.code == 1
    assert "a very specific reason" in capsys.readouterr().out
