"""A RED CI SENDS THE PR BACK — automatically, with no reviewer (CMX-69).

Before this, chela was BLIND to CI: `_pr_state` read `state,mergeable` off a PR, and
`mergeable` is GitHub's MERGE-CONFLICT field, not its checks. On 2026-07-14 both PR #80 and
PR #81 were red and nobody noticed — #80 was MERGED red and `dev` stayed broken until a
hotfix (23664e2). The agent said "tests pass" (true, on its machine); the orchestrator
reviewed the code; neither consulted the artifact that governs whether the thing can ship.

These tests pin the gate. `gh` is stubbed at ``subprocess.run`` — so the JSON shape, the
rollup reduction and the log fetch are all really exercised, not mocked past:

* a CONCLUSIVE failure sends the run back ONCE, and the verdict carries the failing job
  names plus the tail of the failing log;
* ⛔ a PENDING run is NOT a red one — nothing happens, on any tick, ever;
* each red fires exactly ONCE, keyed on the head SHA: the same failing commit cannot spend
  a second rework round, while a NEW push that is also red is a NEW verdict;
* ⛔ gh missing/offline = UNKNOWN, never a pass: nothing merges and nothing is sent back;
* a red PR whose run is `done` is NOT resurrected;
* `chela review --approve` REFUSES a red PR (and the dashboard's merge refuses it too);
* no checks ≠ failing checks — but it is said out loud rather than silently passed.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import dispatcher
from chela.sources import Task
from chela.workflow import WorkflowDef, WorkflowStatus


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    """A runs DB per test (``dispatcher.DB_PATH`` is latched at import — see conftest)."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _wf(tmp_path: Path, **cfg) -> WorkflowDef:
    (tmp_path / "TODO.md").write_text("- [ ] do a thing\n")
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={
            "project_key": "TEST",
            "tracker": {"kind": "markdown", "path": "TODO.md"},
            "workspace": {"root": str(tmp_path / "wts"), "base_branch": "dev"},
            **cfg,
        },
        prompt_template="fresh dispatch: {{task_title}}",
    )


def _status(wf: WorkflowDef) -> WorkflowStatus:
    return WorkflowStatus(path=wf.path, workflow=wf, error=None)


class _Source:
    def __init__(self, *task_ids: str):
        self._tasks = [
            Task(id=tid, title="do a thing", file="TODO.md", line_number=i + 1,
                 raw="- [ ] do a thing")
            for i, tid in enumerate(task_ids)
        ]

    def list_open_tasks(self):
        return list(self._tasks)


def _row(conn, task_id="abc123", **over):
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "do a thing",
        "status": "awaiting_review", "window_name": "test-1", "worktree_path": "/wt/abc123",
        "branch_name": "test-1", "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/80", "pr_state": "open",
        "rework_count": 0, "review_history": None,
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()


# --- the gh stub: the real JSON, the real argv ----------------------------------------

def _check_run(name, status="COMPLETED", conclusion="SUCCESS", run_id="42"):
    return {
        "__typename": "CheckRun", "name": name, "status": status,
        "conclusion": conclusion, "workflowName": "CI",
        "detailsUrl": f"https://github.com/o/r/actions/runs/{run_id}/job/7",
    }


class _FakeGh:
    """`gh` and `tmux`, at the argv boundary. Records what was asked and what it answered."""

    def __init__(self, rollup=None, sha="deadbee1", log="E   assert 1 == 2\nFAILED test_x\n",
                 gh_missing=False):
        self.rollup = rollup if rollup is not None else [_check_run("test (3.12)")]
        self.sha = sha
        self.log = log
        self.gh_missing = gh_missing
        self.calls: list[list[str]] = []
        self.comments: list[str] = []
        self.log_fetches = 0
        self.check_reads = 0
        self._next_id = 100
        self.windows: list[tuple[str, str]] = []

    def run(self, cmd, *a, **k):
        self.calls.append(cmd if isinstance(cmd, list) else [str(cmd)])

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        if not isinstance(cmd, list):
            return R()
        if cmd[0] == "gh":
            if self.gh_missing:
                raise FileNotFoundError("no gh on PATH")
            if cmd[:3] == ["gh", "pr", "view"] and "statusCheckRollup,headRefOid" in cmd:
                self.check_reads += 1
                R.stdout = json.dumps({"headRefOid": self.sha,
                                       "statusCheckRollup": self.rollup})
            elif cmd[:3] == ["gh", "pr", "view"]:
                R.stdout = json.dumps({"state": "OPEN", "mergeable": "MERGEABLE"})
            elif cmd[:3] == ["gh", "run", "view"]:
                self.log_fetches += 1
                R.stdout = self.log
            elif cmd[:3] == ["gh", "pr", "comment"]:
                self.comments.append(k.get("input") or "")
            return R()
        if cmd[:2] == ["tmux", "list-windows"]:
            R.stdout = "".join(f"{wid} {name}\n" for wid, name in self.windows)
        elif cmd[:2] == ["tmux", "new-window"]:
            wid = f"@{self._next_id}"
            self._next_id += 1
            self.windows.append((wid, cmd[cmd.index("-n") + 1]))
            R.stdout = wid + "\n"
        return R()


def _tick(wf, fake, source=None, worktree=None):
    """One dispatcher pass with gh + tmux stubbed and the re-spawn's worktree stood in for."""
    wt = worktree or (wf.path.parent / "wts" / "abc123")
    wt.mkdir(parents=True, exist_ok=True)
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=source or _Source("abc123")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.subprocess, "run", side_effect=fake.run), \
         patch.object(dispatcher, "attach_worktree", return_value=(wt, False)), \
         patch.object(dispatcher, "send_tmux", return_value=True), \
         patch.object(dispatcher, "_wait_for_ready", return_value=True):
        return dispatcher.tick(wf.path)


# --- (a) a CONCLUSIVE failure sends the run back, with the failure as the verdict ------

def test_a_red_ci_sends_the_pr_back_with_the_job_name_and_the_log_tail(tmp_path):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test (3.12)", conclusion="FAILURE")],
                   log="E   assert 1 == 2\nFAILED tests/test_wall.py::test_grid\n")

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    run = dispatcher.resolve_run("abc123")
    # It went back through the EXISTING carrier (CMX-68's request_changes) — one path, and
    # the rest of that path then runs itself: the SAME tick re-spawns the agent into its own
    # worktree (`reworked`), so the row is already `running` again by the time we look.
    assert summary["reworked"] == 1
    assert run["status"] == "running"
    assert (run["rework_count"] or 0) == 1
    assert run["pr_checks"] == dispatcher.CI_FAILING
    assert run["ci_failed_sha"] == "deadbee1"

    verdict = dispatcher.latest_verdict(run)
    assert "CI is RED" in verdict
    assert "CI / test (3.12)" in verdict            # the failing job, named
    assert "FAILED tests/test_wall.py::test_grid" in verdict   # the tail of its log
    assert "deadbee1" in verdict                    # the commit it is red on
    # And the verdict is the PR comment too — the record the reworking agent reads back.
    assert fake.comments and "CI is RED" in fake.comments[0]


def test_the_log_is_fetched_once_on_the_transition_into_red_and_never_on_the_poll(tmp_path):
    """`gh run view --log-failed` downloads a whole log archive. Once per red SHA — a poll
    that did it every 60s for every open PR would be a bad neighbour."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    _tick(wf, fake)
    fetched_after_first = fake.log_fetches
    _tick(wf, fake)          # the run is `changes_requested` now — nothing re-fires
    _tick(wf, fake)

    assert fetched_after_first == 1
    assert fake.log_fetches == 1


# --- (b) ⛔ A PENDING RUN IS NOT A RED ONE. The single most important test here. --------

@pytest.mark.parametrize("status", ["QUEUED", "IN_PROGRESS", "WAITING", "REQUESTED"])
def test_a_pending_check_does_NOTHING(tmp_path, status):
    """A PR opens with its checks PENDING. Sending the agent back mid-CI would be a loop
    fighting itself — and the run would be re-spawned against a verdict CI had not reached."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", status=status, conclusion=None)])

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"       # untouched
    assert run["pr_checks"] == dispatcher.CI_PENDING
    assert run["ci_failed_sha"] is None
    assert not dispatcher.reviews_of(run)
    assert fake.log_fetches == 0                    # and nothing expensive was fetched


def test_a_failing_job_beside_an_unfinished_one_is_still_PENDING(tmp_path):
    """Unsettled wins over failing: the other half of the run could fail too, and that would
    be a second red on the SAME sha — which the once-per-sha guard would then swallow."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[
        _check_run("lint", conclusion="FAILURE"),
        _check_run("test", status="IN_PROGRESS", conclusion=None),
    ])

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    assert dispatcher.resolve_run("abc123")["pr_checks"] == dispatcher.CI_PENDING


def test_neutral_and_skipped_checks_are_not_failures(tmp_path):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("optional", conclusion="SKIPPED"),
                           _check_run("advisory", conclusion="NEUTRAL"),
                           _check_run("test", conclusion="SUCCESS")])

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    assert dispatcher.resolve_run("abc123")["pr_checks"] == dispatcher.CI_PASSING


# --- (c) each red fires ONCE, keyed on the head SHA -----------------------------------

def test_the_same_failing_sha_cannot_spend_a_second_rework_round(tmp_path):
    """⛔ Without this, a run that fails CI, is reworked, and comes back red on an UNCHANGED
    commit burns the whole CHELA_MAX_REWORKS budget in three ticks having done nothing."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")], sha="samesha1")

    assert _tick(wf, fake)["ci_failed"] == 1

    # The agent reworked it and finished WITHOUT pushing anything: the run comes back to
    # awaiting_review on the very same commit, still red.
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET status='awaiting_review' WHERE task_id='abc123'")
        conn.commit()

    assert _tick(wf, fake)["ci_failed"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"        # NOT sent back again
    assert len(dispatcher.reviews_of(run)) == 1      # exactly one verdict, ever


def test_a_NEW_red_sha_is_a_new_verdict(tmp_path):
    """A new push that is also red is a new failure, and it may spend a round."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")], sha="firstsha")

    assert _tick(wf, fake)["ci_failed"] == 1

    with dispatcher._db() as conn:                   # the agent pushed a fix; it is red again
        conn.execute("UPDATE runs SET status='awaiting_review' WHERE task_id='abc123'")
        conn.commit()
    fake.sha = "secondsha"

    assert _tick(wf, fake)["ci_failed"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"               # sent back, and re-spawned, again
    assert [r["round"] for r in dispatcher.reviews_of(run)] == [1, 2]
    assert run["ci_failed_sha"] == "secondsha"


# --- (d) ⛔ gh missing / offline / rate-limited = UNKNOWN, and UNKNOWN IS NOT GREEN -----

def test_gh_unavailable_is_UNKNOWN_never_a_pass_and_it_says_so(tmp_path, caplog):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(gh_missing=True)

    with caplog.at_level("WARNING"):
        summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["pr_checks"] == dispatcher.CI_UNKNOWN     # ⛔ not `passing`, not NULL
    assert run["status"] == "awaiting_review"            # an unknown is not a red, either
    assert "UNKNOWN" in caplog.text and "NOT a pass" in caplog.text


def test_an_unknown_check_state_blocks_the_approval(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)
    fake = _FakeGh(gh_missing=True)
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        result = dispatcher.approve("abc123", "LGTM")
    assert result["ok"] is False
    assert "CANNOT BE READ" in result["error"]


# --- (e) no resurrection: a red CI on a run that already shipped does NOTHING ----------

def test_a_red_ci_never_resurrects_a_done_run(tmp_path):
    """The compare-and-swap (CMX-68) is reused, and the poll skips terminal PRs entirely."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="done", pr_state="merged")
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "done"
    assert fake.check_reads == 0        # it was not even asked about — the PR is terminal
    assert not dispatcher.reviews_of(run)


def test_a_run_a_human_merged_under_the_verdict_is_not_sent_back(tmp_path):
    """The CAS again, from the other side: the row moved to `done` between the check read
    and the verdict, and the verdict writes NOTHING."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    real = dispatcher.request_changes

    def _merged_first(ident, body):
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET status='done' WHERE task_id=?", (ident,))
            conn.commit()
        return real(ident, body)

    with patch.object(dispatcher, "request_changes", side_effect=_merged_first):
        summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    assert dispatcher.resolve_run("abc123")["status"] == "done"


# --- (f) the gate on merge: --approve refuses a red PR --------------------------------

def test_approve_refuses_a_red_pr(tmp_path):
    """⛔ The orchestrator must not be ABLE to approve a red PR by accident — it did exactly
    that on 2026-07-14, and the base branch broke."""
    with dispatcher._db() as conn:
        _row(conn)
    fake = _FakeGh(rollup=[_check_run("test (3.12)", conclusion="FAILURE")])

    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        result = dispatcher.approve("abc123", "LGTM")

    assert result["ok"] is False
    assert "CI is RED" in result["error"] and "CI / test (3.12)" in result["error"]
    assert not fake.comments                       # and it did not post the approval either


def test_approve_of_a_red_pr_can_be_FORCED_and_says_so(tmp_path):
    """A human may know the failure is unrelated. The override exists — and it is visible."""
    with dispatcher._db() as conn:
        _row(conn)
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        result = dispatcher.approve("abc123", "the failure is a flake in an unrelated job",
                                    force=True)

    assert result["ok"] is True and result["forced"] is True
    assert "FORCED" in result["ci_note"]


def test_approve_of_a_green_pr_still_works(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)
    fake = _FakeGh(rollup=[_check_run("test", conclusion="SUCCESS")])
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        result = dispatcher.approve("abc123", "LGTM")
    assert result["ok"] is True and result["pr_checks"] == dispatcher.CI_PASSING
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"


# --- (g) no checks ≠ failing checks — but say so --------------------------------------

def test_a_pr_with_no_ci_is_not_red_but_is_never_silently_green(tmp_path):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[])

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert run["pr_checks"] == dispatcher.CI_NONE   # ⛔ recorded as `none`, NOT as `passing`

    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        result = dispatcher.approve("abc123", "LGTM")
    assert result["ok"] is True
    assert "NO checks" in result["ci_note"]         # the approval says it out loud


# --- the rollup reduction itself ------------------------------------------------------

def test_the_legacy_status_context_shape_is_read_too():
    """`statusCheckRollup` carries TWO node shapes — a CheckRun (Actions) and a
    StatusContext (the commit-status API, e.g. a CI service posting a status). A reducer
    that knew only the first would read a red StatusContext as a pass."""
    state, failing, _ = dispatcher._rollup_state(
        [{"__typename": "StatusContext", "context": "ci/circleci", "state": "FAILURE"}])
    assert state == dispatcher.CI_FAILING and failing == ("ci/circleci",)

    state, _, _ = dispatcher._rollup_state(
        [{"__typename": "StatusContext", "context": "ci/circleci", "state": "PENDING"}])
    assert state == dispatcher.CI_PENDING


@pytest.mark.parametrize("conclusion", ["FAILURE", "ERROR", "TIMED_OUT", "CANCELLED",
                                        "STARTUP_FAILURE", "ACTION_REQUIRED"])
def test_every_conclusive_non_pass_is_a_failure(conclusion):
    state, failing, run_ids = dispatcher._rollup_state(
        [_check_run("test", conclusion=conclusion)])
    assert state == dispatcher.CI_FAILING
    assert failing == ("CI / test",) and run_ids == ("42",)


def test_a_matrix_that_fails_in_several_shards_names_each_job_once():
    state, failing, run_ids = dispatcher._rollup_state([
        _check_run("test (3.11)", conclusion="FAILURE"),
        _check_run("test (3.12)", conclusion="FAILURE"),
        _check_run("test (3.11)", conclusion="FAILURE"),
    ])
    assert state == dispatcher.CI_FAILING
    assert failing == ("CI / test (3.11)", "CI / test (3.12)")
    assert run_ids == ("42",)


def test_a_log_bigger_than_a_prompt_is_truncated_to_its_tail(tmp_path):
    fake = _FakeGh(log="x" * 50_000 + "\nFAILED the last thing\n")
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        tail = dispatcher._failing_log_tail(str(tmp_path), ("42",))
    assert len(tail) < dispatcher.CI_LOG_TAIL_CHARS + 200
    assert "FAILED the last thing" in tail
    assert "truncated" in tail


def test_a_log_that_cannot_be_fetched_costs_the_tail_and_not_the_verdict(tmp_path):
    """The failing job NAMES came from the rollup and are already in hand."""
    with patch.object(dispatcher.subprocess, "run", side_effect=FileNotFoundError("no gh")):
        tail = dispatcher._failing_log_tail(str(tmp_path), ("42",))
    assert "could not fetch" in tail
    body = dispatcher._ci_verdict_body(
        dispatcher.CIStatus(dispatcher.CI_FAILING, "abc", ("CI / test",)), tail, "u")
    assert "CI / test" in body


# --- the OTHER gate on merge: the dashboard's Merge button ----------------------------

def _merge_row(tmp_path) -> dict:
    (tmp_path / "WORKFLOW.md").write_text("---\nproject_key: TEST\n---\ngo\n")
    return {
        "task_id": "abc123", "pr_url": "https://github.com/o/r/pull/80",
        "workflow_path": str(tmp_path / "WORKFLOW.md"),
        "worktree_path": str(tmp_path), "branch_name": "test-1",
    }


@pytest.mark.parametrize("rollup,expected", [
    ([_check_run("test", conclusion="FAILURE")], "CI is RED"),
    ([_check_run("test", status="IN_PROGRESS", conclusion=None)], "not settled"),
])
def test_the_dashboard_refuses_to_merge_a_pr_whose_checks_are_not_green(tmp_path, rollup, expected):
    """⛔ The orchestrator must not be ABLE to merge a red PR by accident — the Merge button
    did exactly that on 2026-07-14. The check state is read back from GITHUB here, at the
    moment of merging, and not from the run row: the row is a 60s-old cache, and this is the
    one call where a stale answer IS the bug.
    """
    from chela.dashboard import app as dash

    fake = _FakeGh(rollup=rollup)
    with patch.object(dash.subprocess, "run", side_effect=fake.run):
        result = dash._merge_one(_merge_row(tmp_path))

    assert result["ok"] is False and result["status"] == 409
    assert expected in result["error"]
    # And it never got as far as asking GitHub to merge anything.
    assert not [c for c in fake.calls if c[:3] == ["gh", "pr", "merge"]]


def test_the_dashboard_refuses_to_merge_a_pr_whose_checks_it_could_not_read(tmp_path):
    """A check state nobody could read is NOT a pass — the doctor rule, on the merge path."""
    from chela.dashboard import app as dash

    fake = _FakeGh(gh_missing=True)
    with patch.object(dash.subprocess, "run", side_effect=fake.run):
        result = dash._merge_one(_merge_row(tmp_path))

    assert result["ok"] is False and result["status"] == 409
    assert "NOT a pass" in result["error"]
    assert not [c for c in fake.calls if c[:3] == ["gh", "pr", "merge"]]
