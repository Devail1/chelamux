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
import subprocess
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


def _assert_actionable_escalation(last_error: str) -> None:
    """Structural guard (CMX-242, rework round 1): the recommendation must be non-empty, the
    menu must have ≥2 real options, and the recommendation must actually NAME one of them
    (or explicitly opt out) — not just that the "Recommendation:"/"Options:" labels render."""
    reason, sep, rest = last_error.partition("\n\nRecommendation: ")
    assert sep, "no recommendation section to parse"
    recommendation, sep2, rest2 = rest.partition("\n\nOptions:\n")
    assert sep2, "no options section to parse"
    options = [line[len("  - "):] for line in rest2.split("\n") if line.startswith("  - ")]
    assert recommendation.strip(), "an automatic escalation must carry a non-empty recommendation"
    assert len(options) >= 2, "one option is not a choice"
    assert (
        any(o in recommendation for o in options)
        or recommendation.lower().startswith("none of these")
    ), "the recommendation must name one of its own options (or explicitly opt out)"


def _wf(tmp_path: Path, **cfg) -> WorkflowDef:
    (tmp_path / "TODO.md").write_text("- [ ] do a thing\n")
    return WorkflowDef(
        path=tmp_path / "WORKFLOW.md",
        config={
            "project_key": "TEST",
            "tracker": {"kind": "markdown", "path": "TODO.md"},
            "workspace": {"root": str(tmp_path / ".chela" / "wts"), "base_branch": "dev"},
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


def _step(name, conclusion):
    """A `gh run view <id> --json jobs` step — `.jobs[].steps[]` shape."""
    return {"name": name, "status": "completed", "conclusion": conclusion}


def _job(name, steps):
    """A `gh run view <id> --json jobs` job — `.jobs[]` shape."""
    return {"name": name, "steps": steps}


class _FakeGh:
    """`gh` and `tmux`, at the argv boundary. Records what was asked and what it answered."""

    def __init__(self, rollup=None, sha="deadbee1", log="E   assert 1 == 2\nFAILED test_x\n",
                 gh_missing=False, mergeable="MERGEABLE", on_check_read=None,
                 jobs_by_run=None, steps_readable=True, rerun_rc=1):
        self.rollup = rollup if rollup is not None else [_check_run("test (3.12)")]
        self.sha = sha
        self.log = log
        self.gh_missing = gh_missing
        self.mergeable = mergeable
        self.on_check_read = on_check_read     # a hook that runs WHILE gh is "on the network"
        # `run_id -> [job dict, ...]`, the shape of `gh run view <id> --json jobs`'s `.jobs`
        # array — each job needs `name` and `steps` to be looked up by `_suite_step_ran`
        # (through `_fetch_ci_jobs`/`_validate_ci_jobs`). Empty by default: an untargeted
        # test gets "job not found", i.e. `None` (unknown, never infra).
        self.jobs_by_run: dict[str, list[dict]] = jobs_by_run or {}
        self.steps_readable = steps_readable   # False simulates `gh run view --json jobs` failing
        # 🔁 CMX-255: what `gh run rerun <id> --failed` answers. REFUSED (1) by default, so
        # every red here takes the pre-CMX-255 path straight to rework — the fall-through a
        # re-run GitHub will not start must take. tests/test_ci_rerun.py drives the rest.
        self.rerun_rc = rerun_rc
        self.reruns: list[list[str]] = []
        self.calls: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.comments: list[str] = []
        self.log_fetches = 0
        self.steps_fetches = 0
        self.check_reads = 0
        self._next_id = 100
        self.windows: list[tuple[str, str]] = []

    def run(self, cmd, *a, **k):
        self.calls.append(cmd if isinstance(cmd, list) else [str(cmd)])
        self.kwargs.append(dict(k))

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
                if self.on_check_read:
                    self.on_check_read(self)
                R.stdout = json.dumps({"headRefOid": self.sha,
                                       "statusCheckRollup": self.rollup})
            elif cmd[:3] == ["gh", "pr", "view"] and ".mergeable" in cmd:
                R.stdout = self.mergeable + "\n"     # `-q .mergeable`: a bare string, as gh does
            elif cmd[:3] == ["gh", "pr", "view"]:
                R.stdout = json.dumps({"state": "OPEN", "mergeable": "MERGEABLE"})
            elif cmd[:3] == ["gh", "run", "view"] and "--log-failed" in cmd:
                self.log_fetches += 1
                R.stdout = self.log
            elif cmd[:3] == ["gh", "run", "view"] and "--json" in cmd:
                self.steps_fetches += 1
                if not self.steps_readable:
                    R.returncode = 1
                    R.stderr = "gh: could not read run"
                else:
                    run_id = cmd[3]
                    R.stdout = json.dumps({"jobs": self.jobs_by_run.get(run_id, [])})
            elif cmd[:3] == ["gh", "run", "rerun"]:
                self.reruns.append(cmd)
                R.returncode = self.rerun_rc
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


# --- (a′) 🚦🏗️ CMX-245 (re-scope of CMX-243): infrastructure is not evidence about the
# code — it must not spend the bounded rework budget the way a real test failure does. ----

def test_an_infra_only_red_does_NOT_spend_a_rework_round(tmp_path):
    """STARTUP_FAILURE means the job's STEPS NEVER RAN — nothing here is evidence about the
    code, and a coding agent cannot fix a job that never executed. It must never enter the
    rework loop at all."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test (3.12)", conclusion="STARTUP_FAILURE")],
                   log="Error: the runner lost its connection before any step ran\n")

    summary = _tick(wf, fake)

    assert summary["ci_infra_failed"] == 1
    assert summary["ci_failed"] == 0             # not the "real evidence" counter
    assert summary["reworked"] == 0              # ⛔ no agent was spawned over it
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"    # ⛔ untouched — never entered changes_requested
    assert (run["rework_count"] or 0) == 0       # ⛔ the real budget is untouched
    assert run["pr_checks"] == dispatcher.CI_FAILING   # still red — still refuses the merge gate
    assert run["ci_failed_sha"] == "deadbee1"    # the once-per-sha guard still applies
    assert (run["ci_infra_streak"] or 0) == 1
    assert not dispatcher.reviews_of(run)        # no review verdict was ever written — no
                                                  # rework prompt exists to carry one

    assert fake.comments and "INFRASTRUCTURE" in fake.comments[0]
    assert "not charged against the" in fake.comments[0] and "rework budget" in fake.comments[0]
    assert "CI / test (3.12)" in fake.comments[0]
    # This comment is the ONLY evidence a human ever gets on this path — no agent is
    # dispatched and no verdict row is written — so it must actually carry the log tail.
    assert "the runner lost its connection before any step ran" in fake.comments[0]


def test_an_infra_red_beside_a_real_failure_IS_charged(tmp_path):
    """Real evidence wins, conservatively: one genuine failure alongside a STARTUP_FAILURE
    sibling in the same rollup still goes through the normal rework loop."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[
        _check_run("test (3.11)", conclusion="FAILURE"),
        _check_run("test (3.12)", conclusion="STARTUP_FAILURE"),
    ])

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0
    assert summary["reworked"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"
    assert (run["rework_count"] or 0) == 1


def test_an_infra_red_fires_once_per_sha_too(tmp_path):
    """The once-per-sha guard is shared with the real-failure path: an unchanged infra red
    does not re-comment or re-count on every tick."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="ACTION_REQUIRED")], sha="samesha1")

    assert _tick(wf, fake)["ci_infra_failed"] == 1
    assert len(fake.comments) == 1

    assert _tick(wf, fake)["ci_infra_failed"] == 0    # unchanged sha — no re-fire
    run = dispatcher.resolve_run("abc123")
    assert (run["ci_infra_streak"] or 0) == 1
    assert len(fake.comments) == 1


def test_an_infra_streak_escalates_without_ever_touching_rework_count(tmp_path, monkeypatch):
    """A permanently broken workflow file would otherwise retry free forever: infra rounds
    are bounded on their OWN streak (`ci_infra_streak`), capped the same as
    `CHELA_MAX_REWORKS` — but `rework_count`, the real budget, must stay at zero throughout."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="STARTUP_FAILURE")], sha="sha-one")

    summary1 = _tick(wf, fake)
    assert summary1["ci_infra_failed"] == 1
    assert summary1["escalated"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert (run["ci_infra_streak"] or 0) == 1

    fake.sha = "sha-two"    # a fresh push, still red the same infra way
    summary2 = _tick(wf, fake)

    assert summary2["ci_infra_failed"] == 1
    assert summary2["escalated"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert (run["ci_infra_streak"] or 0) == 2
    assert (run["rework_count"] or 0) == 0          # ⛔ the REAL budget was never touched
    assert "infrastructure" in run["last_error"].lower()
    assert not dispatcher.reviews_of(run)           # escalated without ever writing a verdict
    # CMX-242: automatic escalations carry a recommendation and options, this one included.
    _assert_actionable_escalation(run["last_error"])


def test_ci_infra_streak_resets_once_ci_is_seen_passing(tmp_path):
    """`ci_infra_streak` counts a STREAK, not a lifetime total: a PR that goes green has
    proven its runner/setup path works, so a LATER infra red is a fresh incident."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="STARTUP_FAILURE")], sha="sha-one")

    _tick(wf, fake)
    assert (dispatcher.resolve_run("abc123")["ci_infra_streak"] or 0) == 1

    fake.rollup = [_check_run("test", conclusion="SUCCESS")]
    fake.sha = "sha-two"
    _tick(wf, fake)

    assert (dispatcher.resolve_run("abc123")["ci_infra_streak"] or 0) == 0


# --- (a″) 🚦🏗️ CMX-245: `STARTUP_FAILURE`/`ACTION_REQUIRED` do not cover the incident
# that motivated the original ticket — a runner that dies MID-JOB (not before it starts)
# reports plain `FAILURE`, indistinguishable at the conclusion level from a genuine test
# failure. These pin the job's own STEPS, read through the validator+classifier split, as
# the real signal. -------------------------------------------------------------------------

def test_the_real_checkout_TLS_incident_classifies_as_infra(tmp_path):
    """Pinned VERBATIM from the incident that motivated CMX-243 — GitHub Actions run
    31527377082, attempt 1, job `test (3.12)`: `actions/checkout@v4` died on a TLS fault
    (`server certificate verification failed. CAfile: none CRLfile: none`, exit 128) and
    GitHub reported the job as plain `FAILURE`, not `STARTUP_FAILURE`. Corrupt the
    validator or the step-reading classifier and this goes RED."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "failure"),
        _step("Install uv", "skipped"),
        _step("Set up Python 3.12", "skipped"),
        _step("Sync (with dev + dashboard extras)", "skipped"),
        _step("Run actions/setup-node@v4", "skipped"),
        _step("Install jsdom (DOM test suites)", "skipped"),
        _step("Ruff", "skipped"),
        _step("Pytest", "skipped"),
        _step("Post Run actions/checkout@v4", "success"),
        _step("Complete job", "success"),
    ]
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="31527377082")],
        jobs_by_run={"31527377082": [_job("test (3.12)", steps)]},
    )

    summary = _tick(wf, fake)

    assert summary["ci_infra_failed"] == 1
    assert summary["ci_failed"] == 0
    assert summary["reworked"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert (run["rework_count"] or 0) == 0
    assert run["pr_checks"] == dispatcher.CI_FAILING
    assert (run["ci_infra_streak"] or 0) == 1


def test_a_real_pytest_failure_with_readable_steps_still_charges(tmp_path):
    """The suite DID run — `Pytest` reached a conclusion of its own — so this is real
    evidence, even though the job-level conclusion is the SAME plain `FAILURE` the infra
    incident reports. This is the load-bearing guard: a classifier that called every plain
    `FAILURE` infra would give every red PR a free pass."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "success"),
        _step("Install uv", "success"),
        _step("Ruff", "success"),
        _step("Pytest", "failure"),
        _step("Complete job", "success"),
    ]
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="999")],
        jobs_by_run={"999": [_job("test (3.12)", steps)]},
    )

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0
    assert summary["reworked"] == 1
    run = dispatcher.resolve_run("abc123")
    assert (run["rework_count"] or 0) == 1


def test_suite_ran_and_succeeded_but_the_job_still_failed_is_real_not_infra(tmp_path):
    """Both `Ruff` and `Pytest` concluded `success` — the suite ran clean — but something
    AFTER the suite (an artifact upload, a cache save, a coverage post step) failed, so
    GitHub still reports the job as plain `FAILURE`. Per `_suite_step_ran`'s own contract, a
    suite step concluding `success` counts as "ran" exactly like one concluding `failure` —
    this must stay real, not get waved through as infra."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "success"),
        _step("Ruff", "success"),
        _step("Pytest", "success"),
        _step("Upload coverage", "failure"),
        _step("Complete job", "failure"),
    ]
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="321")],
        jobs_by_run={"321": [_job("test (3.12)", steps)]},
    )

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0
    assert summary["reworked"] == 1
    run = dispatcher.resolve_run("abc123")
    assert (run["rework_count"] or 0) == 1


def test_ruff_failing_before_pytest_is_skipped_is_still_real(tmp_path):
    """A naive "was the LAST step skipped" rule would call this infra — `Pytest` never ran.
    But `Ruff` DID run and failed: that is real evidence about the code (a lint error), and
    the job never reached `Pytest` only because of it, not because of a runner fault."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "success"),
        _step("Install uv", "success"),
        _step("Ruff", "failure"),
        _step("Pytest", "skipped"),
        _step("Complete job", "success"),
    ]
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="777")],
        jobs_by_run={"777": [_job("test (3.12)", steps)]},
    )

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0


def test_unreadable_step_data_charges_the_round(tmp_path):
    """The steps API call can itself fail (rate limit, network, a run gh cannot find) —
    that must NOT be read as infra. Unknown is never a pass, the same rule `CI_UNKNOWN`
    already lives by."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="555")],
        steps_readable=False,
    )

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0


def test_a_job_missing_from_the_steps_response_charges_the_round(tmp_path):
    """The run id resolves but no job in it matches the failing job's name (a rename, a
    matrix that changed shape between polls) — still unknown, still charges."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="444")],
        jobs_by_run={"444": [_job("some other job", [_step("Pytest", "skipped")])]},
    )

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0


def test_a_job_with_no_steps_array_at_all_charges_the_round(tmp_path):
    """The job resolves and its NAME matches, but the job dict itself carries no `steps` key
    at all (a `gh` response shape chela has not seen). Under the validate-once-at-the-
    -boundary design, this invalidates the WHOLE payload — never silently reclassified as
    infra, or a malformed response would hand a red PR a free pass."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="222")],
        jobs_by_run={"222": [{"name": "test (3.12)"}]},   # no "steps" key at all
    )

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0


def test_a_mid_suite_timeout_never_even_asks_the_steps_api(tmp_path):
    """TIMED_OUT/CANCELLED are real evidence unconditionally (`CI_INFRA_CONCLUSIONS`
    excludes them on purpose, since both can happen mid-suite) — they must not cost an
    extra `gh` call, let alone be reclassified by one. Steps that would (wrongly) look like
    infra if ever consulted are wired in on purpose, to prove they are never read."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    steps = [_step("Pytest", "skipped")]
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="TIMED_OUT", run_id="333")],
        jobs_by_run={"333": [_job("test (3.12)", steps)]},
    )

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0
    assert fake.steps_fetches == 0


def test_a_green_run_never_touches_the_steps_api(tmp_path):
    """Negative control: nothing about a passing PR should ever reach the steps check."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test (3.12)", conclusion="SUCCESS")])

    summary = _tick(wf, fake)

    assert summary.get("ci_failed", 0) == 0
    assert summary.get("ci_infra_failed", 0) == 0
    assert fake.steps_fetches == 0


def test_pytest_reaching_a_conclusion_alone_is_still_real_even_if_ruffs_own_step_is_skipped(
    tmp_path,
):
    """`_CI_SUITE_STEP_NAMES` must recognise EITHER named step on its own — not just
    whichever one happens to run alongside the other. If `Ruff`'s own step is `skipped`
    but `Pytest` reached a real conclusion, the suite DID run and this is real evidence."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "success"),
        _step("Ruff", "skipped"),
        _step("Pytest", "failure"),
        _step("Complete job", "failure"),
    ]
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="888")],
        jobs_by_run={"888": [_job("test (3.12)", steps)]},
    )

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0
    assert summary["reworked"] == 1
    run = dispatcher.resolve_run("abc123")
    assert (run["rework_count"] or 0) == 1


def test_a_ci_yml_step_rename_with_no_recognised_suite_step_charges_the_round(tmp_path):
    """The job resolves and its steps ARE readable, but none of them is named `Ruff` or
    `Pytest` (a rename in `.github/workflows/ci.yml` that `_CI_SUITE_STEP_NAMES` was never
    updated to match). This must be treated as UNKNOWN, never silently reclassified as
    infra, or a step rename would hand every red PR a free pass with no signal anywhere
    that the feature had stopped working."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "success"),
        _step("Lint", "failure"),        # renamed from "Ruff"
        _step("Run tests", "skipped"),   # renamed from "Pytest"
        _step("Complete job", "failure"),
    ]
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="666")],
        jobs_by_run={"666": [_job("test (3.12)", steps)]},
    )

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0


def test_a_TIMED_OUT_sibling_blocks_infra_reclassification_of_a_plain_failure_beside_it(
    tmp_path,
):
    """A red mixed with a non-plain failure (here, `TIMED_OUT`) must be left alone
    UNCONDITIONALLY, even when the one plain-`FAILURE` job in the same rollup looks
    exactly like the checkout-TLS infra incident on its own steps. `plain_failures` must
    cover ALL of `failing` before any reclassification runs at all."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "failure"),
        _step("Ruff", "skipped"),
        _step("Pytest", "skipped"),
        _step("Complete job", "failure"),
    ]
    fake = _FakeGh(rollup=[
        _check_run("test (3.11)", conclusion="TIMED_OUT", run_id="111"),
        _check_run("test (3.12)", conclusion="FAILURE", run_id="222"),
    ], jobs_by_run={"222": [_job("test (3.12)", steps)]})

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0
    assert summary["reworked"] == 1
    run = dispatcher.resolve_run("abc123")
    assert (run["rework_count"] or 0) == 1


def test_one_plain_failure_that_never_ran_beside_one_that_DID_is_NOT_infra_only(tmp_path):
    """Two plain-`FAILURE` candidates in the SAME rollup — `test (3.12)` looks exactly like
    the checkout-TLS incident (every suite step skipped), `test (3.11)` genuinely ran
    `Pytest` and failed. `_ci_infra_by_steps` must reclassify to infra only when EVERY
    candidate's suite never ran — one candidate with real evidence keeps the WHOLE red real.
    The infra-looking job is listed FIRST on purpose: a classifier that returns `infra=True`
    on the first never-ran candidate it meets, instead of scanning for one that DID run,
    would misfire here before ever reaching the real evidence."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    dead_steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "failure"),
        _step("Ruff", "skipped"),
        _step("Pytest", "skipped"),
        _step("Complete job", "success"),
    ]
    ran_steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "success"),
        _step("Ruff", "success"),
        _step("Pytest", "failure"),
        _step("Complete job", "success"),
    ]
    fake = _FakeGh(rollup=[
        _check_run("test (3.12)", conclusion="FAILURE", run_id="222"),
        _check_run("test (3.11)", conclusion="FAILURE", run_id="111"),
    ], jobs_by_run={
        "222": [_job("test (3.12)", dead_steps)],
        "111": [_job("test (3.11)", ran_steps)],
    })

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["ci_infra_failed"] == 0
    assert summary["reworked"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"
    assert (run["rework_count"] or 0) == 1
    assert run["pr_checks"] == dispatcher.CI_FAILING


def test_the_steps_api_is_read_once_on_the_transition_into_red_and_never_on_the_poll(
    tmp_path,
):
    """Mirror of `test_the_log_is_fetched_once_on_the_transition_into_red_and_never_on_the_poll`
    for the OTHER heavy read this feature adds: `gh run view --json jobs` must only be asked
    once, on the once-per-sha transition into a new red — never on the 60s poll that keeps
    refreshing `pr_checks` for a red already delivered."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    steps = [
        _step("Set up job", "success"),
        _step("Run actions/checkout@v4", "failure"),
        _step("Ruff", "skipped"),
        _step("Pytest", "skipped"),
        _step("Complete job", "success"),
    ]
    fake = _FakeGh(
        rollup=[_check_run("test (3.12)", conclusion="FAILURE", run_id="999999")],
        jobs_by_run={"999999": [_job("test (3.12)", steps)]},
    )

    _tick(wf, fake)
    fetched_after_first = fake.steps_fetches
    _tick(wf, fake)          # already delivered — the poll must not re-ask
    _tick(wf, fake)

    assert fetched_after_first == 1
    assert fake.steps_fetches == 1


# --- (a‴) 🚦🏗️ CMX-245: THE VALIDATOR — one place that reasons about the untrusted gh
# payload shape. Every malformed-shape enumeration CMX-243 discovered one judge round at a
# time collapses into this one function's contract: ANY deviation, anywhere in the tree, is
# `None`. ------------------------------------------------------------------------------

def test_validate_ci_jobs_accepts_a_well_formed_payload():
    payload = {"jobs": [
        {"name": "test (3.12)", "steps": [
            {"name": "Ruff", "status": "completed", "conclusion": "success"},
            {"name": "Pytest", "status": "completed", "conclusion": "failure"},
        ]},
    ]}
    jobs = dispatcher._validate_ci_jobs(payload)
    assert jobs == (
        dispatcher.CIJob("test (3.12)", (
            dispatcher.CIStep("Ruff", "success"),
            dispatcher.CIStep("Pytest", "failure"),
        )),
    )


def test_validate_ci_jobs_an_empty_jobs_list_is_valid_not_a_deviation():
    """A run with zero jobs (or none matching) is a legitimately empty, VALIDATED list — not
    the same as an unreadable payload. `_suite_step_ran` still answers `None` on it (no job
    matches), but for a different reason: nothing to search, not "could not even parse"."""
    assert dispatcher._validate_ci_jobs({"jobs": []}) == ()


@pytest.mark.parametrize("payload", [
    None,
    "not a dict",
    ["a", "list"],
    {},                                            # no "jobs" key at all
    {"jobs": None},
    {"jobs": "not-a-list"},
    {"jobs": {"oops": "wrong shape"}},
    {"jobs": ["not a dict"]},
    {"jobs": [{"steps": []}]},                      # job missing "name"
    {"jobs": [{"name": 42, "steps": []}]},           # job "name" not a string
    {"jobs": [{"name": "j"}]},                       # job missing "steps"
    {"jobs": [{"name": "j", "steps": "not-a-list"}]},
    {"jobs": [{"name": "j", "steps": ["not a dict"]}]},
    {"jobs": [{"name": "j", "steps": [{"conclusion": "success"}]}]},   # step missing "name"
    {"jobs": [{"name": "j", "steps": [{"name": 1, "conclusion": "success"}]}]},
    {"jobs": [{"name": "j", "steps": [{"name": "Ruff", "conclusion": 1}]}]},   # bad type
    # one well-formed job beside one malformed one — the WHOLE payload is invalid, not just
    # the bad half. A partially-unreadable response is exactly as unusable as one that is
    # malformed everywhere, because the caller cannot tell which half to trust.
    {"jobs": [
        {"name": "test (3.11)", "steps": [{"name": "Pytest", "conclusion": "failure"}]},
        {"name": "test (3.12)"},   # missing "steps"
    ]},
])
def test_validate_ci_jobs_returns_none_on_any_deviation(payload):
    assert dispatcher._validate_ci_jobs(payload) is None


def test_validate_ci_jobs_normalizes_a_missing_conclusion_to_empty_string_not_skipped():
    """A step that never got as far as concluding at all (GitHub returns `null`, or omits
    the key) is NOT the same as one that was `skipped` — treating it as skipped would let a
    malformed/partial job payload slip a red PR an infra free pass. `""` compares unequal to
    `"skipped"`, so `_suite_step_ran` correctly counts it as "ran" (the safe direction)."""
    jobs = dispatcher._validate_ci_jobs(
        {"jobs": [{"name": "j", "steps": [{"name": "Ruff", "conclusion": None},
                                          {"name": "Pytest"}]}]}
    )
    assert jobs == (
        dispatcher.CIJob("j", (dispatcher.CIStep("Ruff", ""), dispatcher.CIStep("Pytest", ""))),
    )
    assert dispatcher._suite_step_ran(jobs, "j") is True


# --- THE CLASSIFIER — a pure function of already-validated data. -----------------------

def test_suite_step_ran_is_None_when_the_payload_could_not_be_validated():
    assert dispatcher._suite_step_ran(None, "test (3.12)") is None


def test_suite_step_ran_is_None_when_no_job_matches():
    jobs = dispatcher._validate_ci_jobs(
        {"jobs": [{"name": "some other job", "steps": [{"name": "Pytest", "conclusion": "skipped"}]}]}
    )
    assert dispatcher._suite_step_ran(jobs, "test (3.12)") is None


def test_suite_step_ran_is_None_when_no_recognised_suite_step_is_present():
    jobs = dispatcher._validate_ci_jobs(
        {"jobs": [{"name": "j", "steps": [{"name": "Lint", "conclusion": "failure"}]}]}
    )
    assert dispatcher._suite_step_ran(jobs, "j") is None


def test_suite_step_ran_is_False_when_every_suite_step_is_skipped():
    jobs = dispatcher._validate_ci_jobs({"jobs": [{"name": "j", "steps": [
        {"name": "Ruff", "conclusion": "skipped"},
        {"name": "Pytest", "conclusion": "skipped"},
    ]}]})
    assert dispatcher._suite_step_ran(jobs, "j") is False


def test_suite_step_ran_is_True_when_the_suite_ran_and_succeeded():
    """Pins the exact anchor a CMX-243 judge round broke: `!= "skipped"` narrowed to
    `== "failure"`. A step that concluded `success` is not `skipped` either — it ran."""
    jobs = dispatcher._validate_ci_jobs({"jobs": [{"name": "j", "steps": [
        {"name": "Ruff", "conclusion": "success"},
        {"name": "Pytest", "conclusion": "success"},
    ]}]})
    assert dispatcher._suite_step_ran(jobs, "j") is True


# --- THE I/O WRAPPER — `_fetch_ci_jobs` — subprocess + validation, never raises. -------

def test_fetch_ci_jobs_is_None_with_no_repo_dir_or_run_id(tmp_path):
    assert dispatcher._fetch_ci_jobs(None, "42") is None
    assert dispatcher._fetch_ci_jobs(str(tmp_path), "") is None


def test_fetch_ci_jobs_is_None_when_gh_cannot_be_executed(tmp_path):
    with patch.object(dispatcher.subprocess, "run", side_effect=OSError("no gh on PATH")):
        assert dispatcher._fetch_ci_jobs(str(tmp_path), "42") is None


def test_fetch_ci_jobs_is_None_when_gh_times_out(tmp_path):
    with patch.object(
        dispatcher.subprocess, "run",
        side_effect=subprocess.TimeoutExpired(cmd=["gh"], timeout=30),
    ):
        assert dispatcher._fetch_ci_jobs(str(tmp_path), "42") is None


def test_fetch_ci_jobs_is_None_on_a_non_zero_exit(tmp_path):
    class _R:
        returncode = 1
        stdout = ""
        stderr = "gh: rate limited"

    with patch.object(dispatcher.subprocess, "run", return_value=_R()):
        assert dispatcher._fetch_ci_jobs(str(tmp_path), "42") is None


def test_fetch_ci_jobs_is_None_on_unparseable_json(tmp_path):
    class _R:
        returncode = 0
        stdout = "not json{{{"
        stderr = ""

    with patch.object(dispatcher.subprocess, "run", return_value=_R()):
        assert dispatcher._fetch_ci_jobs(str(tmp_path), "42") is None


def test_fetch_ci_jobs_delegates_shape_validation_to_the_validator(tmp_path):
    """A payload that parses as JSON but fails validation (here: `jobs` is an object, not a
    list) must come back `None` — `_fetch_ci_jobs` does not re-implement any of that
    checking itself, it hands the parsed value straight to `_validate_ci_jobs`."""
    class _R:
        returncode = 0
        stdout = json.dumps({"jobs": {"oops": "wrong shape"}})
        stderr = ""

    with patch.object(dispatcher.subprocess, "run", return_value=_R()):
        assert dispatcher._fetch_ci_jobs(str(tmp_path), "42") is None


def test_fetch_ci_jobs_reads_gh_output_with_errors_replace(tmp_path):
    """Twin of `test_the_log_is_read_with_errors_replace_so_one_bad_byte_cannot_kill_the_tick`:
    `text=True` alone is a STRICT utf-8 decode, and a `gh run view --json jobs` payload with
    one invalid byte would raise `UnicodeDecodeError` — a `ValueError` the surrounding
    `except (OSError, subprocess.TimeoutExpired)` does NOT catch, so it would escape
    `_fetch_ci_jobs`, `_ci_infra_by_steps`, and `tick` itself. This pins the actual
    `subprocess.run` kwarg, not just the returned value."""
    captured = {}

    def fake_run(*args, **kwargs):
        captured.update(kwargs)

        class _R:
            returncode = 0
            stdout = json.dumps({"jobs": []})
            stderr = ""

        return _R()

    with patch.object(dispatcher.subprocess, "run", side_effect=fake_run):
        dispatcher._fetch_ci_jobs(str(tmp_path), "42")

    assert captured.get("errors") == "replace"


def test_a_code_fence_inside_the_log_cannot_break_out_of_the_infra_verdicts_code_block(
    tmp_path,
):
    """The same fence-widening rule `_ci_verdict_body` uses applies verbatim to the
    INFRASTRUCTURE comment `_ci_infra_verdict_body` writes — it is still a PR comment built
    from an untrusted log."""
    tail = "E   assert x == '```'\n``` and more\n"
    body = dispatcher._ci_infra_verdict_body(
        dispatcher.CIStatus(dispatcher.CI_FAILING, "abc", ("CI / test",), infra=True),
        tail, "u", 1, 3,
    )

    fence = "````"
    assert f"\n{fence}\n{tail}\n{fence}\n" in body
    assert "\n`````" not in body


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

    def _merged_first(ident, body, **kw):
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET status='done' WHERE task_id=?", (ident,))
            conn.commit()
        return real(ident, body, **kw)

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
    state, failing, _, infra, plain = dispatcher._rollup_state(
        [{"__typename": "StatusContext", "context": "ci/circleci", "state": "FAILURE"}])
    assert state == dispatcher.CI_FAILING and failing == ("ci/circleci",)
    # A StatusContext failure is never infra-only: that shape has no startup/approval
    # concept (only FAILURE/ERROR), so it is always treated as real evidence.
    assert infra is False
    # ...and never a plain-failure candidate either — it has no run id, no job steps.
    assert plain == ()

    state, _, _, _, _ = dispatcher._rollup_state(
        [{"__typename": "StatusContext", "context": "ci/circleci", "state": "PENDING"}])
    assert state == dispatcher.CI_PENDING


@pytest.mark.parametrize("conclusion", ["FAILURE", "ERROR", "TIMED_OUT", "CANCELLED"])
def test_every_conclusive_non_pass_is_a_failure_and_NOT_infra(conclusion):
    """These four are real evidence: a step ran and exited badly, or the job was cut off
    mid-run (a hang, a fail-fast sibling) — never "the steps never executed"."""
    state, failing, run_ids, infra, plain = dispatcher._rollup_state(
        [_check_run("test", conclusion=conclusion)])
    assert state == dispatcher.CI_FAILING
    assert failing == ("CI / test",) and run_ids == ("42",)
    assert infra is False
    # Only a plain FAILURE is a step-check candidate: TIMED_OUT/CANCELLED/ERROR are real
    # evidence unconditionally and never reach the steps API.
    assert plain == (("test", "42"),) if conclusion == "FAILURE" else plain == ()


@pytest.mark.parametrize("conclusion", ["STARTUP_FAILURE", "ACTION_REQUIRED"])
def test_startup_failure_and_action_required_are_failures_AND_infra(conclusion):
    """GitHub's own semantics: the job's steps never ran at all — a runner/workflow-file
    problem, or a pending approval gate. Still CI_FAILING (the merge gate must still refuse
    it), but flagged `infra` so the dispatcher does not spend a rework round on something no
    coding agent can act on."""
    state, failing, run_ids, infra, plain = dispatcher._rollup_state(
        [_check_run("test", conclusion=conclusion)])
    assert state == dispatcher.CI_FAILING
    assert failing == ("CI / test",) and run_ids == ("42",)
    assert infra is True
    assert plain == ()   # already infra by conclusion alone — never a step-check candidate


def test_one_real_failure_beside_an_infra_one_is_NOT_infra_only():
    """Real evidence wins, conservatively: a matrix with one genuine failure and one
    STARTUP_FAILURE sibling is still evidence about the code."""
    state, failing, _, infra, _ = dispatcher._rollup_state([
        _check_run("test (3.11)", conclusion="FAILURE"),
        _check_run("test (3.12)", conclusion="STARTUP_FAILURE"),
    ])
    assert state == dispatcher.CI_FAILING
    assert failing == ("CI / test (3.11)", "CI / test (3.12)")
    assert infra is False


def test_a_matrix_that_fails_in_several_shards_names_each_job_once():
    state, failing, run_ids, infra, plain_failures = dispatcher._rollup_state([
        _check_run("test (3.11)", conclusion="FAILURE"),
        _check_run("test (3.12)", conclusion="FAILURE"),
        _check_run("test (3.11)", conclusion="FAILURE"),
    ])
    assert state == dispatcher.CI_FAILING
    assert failing == ("CI / test (3.11)", "CI / test (3.12)")
    assert run_ids == ("42",)
    assert infra is False
    # `plain_failures` must be deduped exactly like `failing`/`run_ids` (same job re-failing
    # across matrix shards collapses to one entry): `_ci_infra_by_steps` compares
    # `len(ci.plain_failures) != len(ci.failing)` to decide whether a non-plain failure sits
    # alongside these, and both lists must dedupe the same way or that length check misfires
    # on exactly the matrix-shard case this test is named for.
    assert plain_failures == (("test (3.11)", "42"), ("test (3.12)", "42"))


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


def test_the_auto_resolve_of_a_conflict_moves_the_head_and_the_merge_must_re_verify(tmp_path):
    """⛔ THE GATE MUST NOT VERIFY ONE COMMIT AND SHIP ANOTHER.

    The single-card Merge on a CONFLICTING PR auto-resolves the TODO.md conflict — which
    MERGES BASE INTO THE BRANCH AND PUSHES. The head is now a different commit, and it is
    precisely the commit most likely to be red (it carries everything base moved by). The
    first cut read the checks BEFORE that push and merged AFTER it: the PR-#80 hole, re-cut
    inside the feature built to close it. Nothing merges on the strength of a check that
    covered a commit that no longer exists.
    """
    from chela.dashboard import app as dash

    fake = _FakeGh(rollup=[_check_run("test", conclusion="SUCCESS")],  # green, pre-push
                   sha="oldhead1", mergeable="CONFLICTING")

    def _resolved_and_pushed(*a, **k):
        # What the real thing does: a merge commit lands on the branch and is pushed. The
        # PR's head is a NEW commit, whose checks have not even started.
        fake.sha = "newhead2"
        fake.rollup = [_check_run("test", status="QUEUED", conclusion=None)]
        return {"ok": True, "struck": ["do a thing"]}

    with patch.object(dash.subprocess, "run", side_effect=fake.run), \
         patch.object(dash, "_auto_resolve_todo_conflict", side_effect=_resolved_and_pushed):
        result = dash._merge_one(_merge_row(tmp_path))

    assert result["ok"] is False and result["status"] == 409
    assert "newhead2" in result["error"]             # it says WHICH commit it refused to ship
    assert "pending" in result["error"]              # and what that commit's checks say
    # ⛔ And it did not merge. The green it saw belonged to `oldhead1`, which is not what
    # `gh pr merge` would have shipped.
    assert not [c for c in fake.calls if c[:3] == ["gh", "pr", "merge"]]


# --- the log tail is not text until we MAKE it text -----------------------------------

def test_the_log_tail_never_carries_a_keypress_into_the_agents_terminal(tmp_path):
    """⛔ The verdict is PASTED into the agent's tmux pane. `gh run view --log-failed` returns
    the RAW Actions log — ANSI colour from any job that forces it, and whatever control bytes
    the failing process printed. To a terminal those are not characters, they are KEYPRESSES:
    a `\\x03` is a Ctrl-C aimed at that agent's prompt, and a half-eaten seed costs a whole
    rework round for nothing. `rooms.sanitize` was written for exactly this hazard, on
    exactly this paste path (it is `tui_text.sanitize` now, so both callers share it).
    """
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(
        rollup=[_check_run("test", conclusion="FAILURE")],
        log="\x1b[31mE   assert 1 == 2\x1b[0m\n\x03\x07##[error]\x1b]0;title\x07FAILED test_x\n",
    )

    _tick(wf, fake)

    verdict = dispatcher.latest_verdict(dispatcher.resolve_run("abc123"))
    assert "FAILED test_x" in verdict            # the content survives...
    assert "\x1b" not in verdict                 # ...and not one escape does
    assert "\x03" not in verdict and "\x07" not in verdict
    assert "[31m" not in verdict                 # nor the payload of a stripped escape
    # And the same body is what went to the PR comment.
    assert "\x1b" not in fake.comments[0]


def test_a_code_fence_inside_the_log_cannot_break_out_of_the_verdicts_code_block(tmp_path):
    """A CI log that prints ``` would close the fence early and spill the rest of itself into
    the comment as markdown. The fence is widened past the longest run of backticks in it."""
    tail = "E   assert x == '```'\n``` and more\n"
    body = dispatcher._ci_verdict_body(
        dispatcher.CIStatus(dispatcher.CI_FAILING, "abc", ("CI / test",)), tail, "u")

    fence = "````"
    assert f"\n{fence}\n{tail}\n{fence}\n" in body
    # The log's own fences are strictly shorter than the one holding them.
    assert "\n`````" not in body


def test_the_log_is_read_with_errors_replace_so_one_bad_byte_cannot_kill_the_tick(tmp_path):
    """`text=True` alone is a STRICT utf-8 decode: a CI log with one invalid byte (a test that
    prints raw bytes is enough) raises UnicodeDecodeError out of a function whose contract is
    to be best-effort — and out of `tick`, which does not catch it."""
    fake = _FakeGh()
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        dispatcher._failing_log_tail(str(tmp_path), ("42",))
    assert fake.kwargs[-1]["errors"] == "replace"


def test_a_log_fetch_that_blows_up_does_not_burn_the_red_forever(tmp_path):
    """⛔ THE SHA IS THE RIGHT TO FIRE THE VERDICT — do not spend it before the verdict exists.

    The first cut committed `ci_failed_sha` BEFORE fetching the log. Anything that went wrong
    in the fetch (and it decodes a stranger's bytes) escaped the tick with the red already
    marked delivered: that red never fired again, and the run sat in awaiting_review, un-sent
    -back, until a human noticed. The sha is now burned only once the tail is in hand.
    """
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")], sha="redsha01")

    boom = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")
    with patch.object(dispatcher, "_failing_log_tail", side_effect=boom), \
         pytest.raises(UnicodeDecodeError):
        _tick(wf, fake)

    run = dispatcher.resolve_run("abc123")
    assert run["ci_failed_sha"] is None          # ⛔ un-burned: this red has NOT been delivered
    assert not dispatcher.reviews_of(run)

    # So the very next tick still fires it — the failure cost a tick, not the verdict.
    assert _tick(wf, fake)["ci_failed"] == 1
    assert dispatcher.resolve_run("abc123")["ci_failed_sha"] == "redsha01"


def test_a_locked_db_under_the_verdict_neither_kills_the_tick_nor_eats_the_red(tmp_path):
    """`request_changes` opens its own connection and a busy DB can refuse it. The once-per
    -sha guard exists to stop a red firing TWICE; it is not a licence to fire it zero times."""
    import sqlite3

    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    locked = sqlite3.OperationalError("database is locked")
    with patch.object(dispatcher, "request_changes", side_effect=locked):
        summary = _tick(wf, fake)               # ⛔ does not raise: the tick still has work

    assert summary["ci_failed"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert run["ci_failed_sha"] is None         # un-burned — the red is retried, not lost

    assert _tick(wf, fake)["ci_failed"] == 1


# --- ⛔ nothing reaches `passing` that was not SEEN to pass ----------------------------

def test_a_rollup_node_we_cannot_recognise_is_not_a_pass():
    """The reducer's own docstring promised this and the code did the opposite: a node with
    none of status/conclusion/state fell through every branch and came out GREEN. A shape
    GitHub adds tomorrow would have merged itself."""
    state, failing, _, _, _ = dispatcher._rollup_state([{"__typename": "SomethingNew", "id": "x"}])
    assert state == dispatcher.CI_PENDING and not failing

    # A conclusion we have never heard of is not one we may call green either.
    state, _, _, _, _ = dispatcher._rollup_state([_check_run("test", conclusion="MOON_PHASE")])
    assert state == dispatcher.CI_PENDING

    # Nor is a node that is not even a JSON object.
    assert dispatcher._rollup_state(["not a node"])[0] == dispatcher.CI_PENDING


def test_a_rollup_of_only_skipped_checks_is_NONE_and_never_PASSING():
    """SKIPPED/NEUTRAL means the check did not run — a `paths-ignore` filter, a skipped
    required job. Zero checks is `none`, said out loud; all-skipped is the SAME fact, and it
    was silently green. Both mean *nothing evaluated this code*."""
    state, _, _, _, _ = dispatcher._rollup_state([_check_run("test", conclusion="SKIPPED"),
                                                   _check_run("lint", conclusion="NEUTRAL")])
    assert state == dispatcher.CI_NONE

    # One check that really ran and really passed is still a pass, skipped siblings and all.
    state, _, _, _, _ = dispatcher._rollup_state([_check_run("test", conclusion="SKIPPED"),
                                                   _check_run("lint", conclusion="SUCCESS")])
    assert state == dispatcher.CI_PASSING


def test_gh_returning_json_that_is_not_an_object_is_UNKNOWN_not_a_crash(tmp_path):
    class _Weird(_FakeGh):
        def run(self, cmd, *a, **k):
            r = super().run(cmd, *a, **k)
            if isinstance(cmd, list) and "statusCheckRollup,headRefOid" in cmd:
                r.stdout = "[1, 2, 3]"     # valid JSON, wrong shape — `.get` would explode
            return r

    fake = _Weird()
    with patch.object(dispatcher.subprocess, "run", side_effect=fake.run):
        ci = dispatcher._read_pr_checks("https://github.com/o/r/pull/80", str(tmp_path))
    assert ci.state == dispatcher.CI_UNKNOWN


def test_a_gh_that_cannot_be_EXECUTED_is_UNKNOWN_not_a_crash(tmp_path):
    """FileNotFoundError was caught; PermissionError (a `gh` on PATH that is not executable)
    was not — and it took the whole tick with it, when the honest answer is CANNOT VERIFY."""
    with patch.object(dispatcher.subprocess, "run", side_effect=PermissionError("denied")):
        ci = dispatcher._read_pr_checks("https://github.com/o/r/pull/80", str(tmp_path))
    assert ci.state == dispatcher.CI_UNKNOWN and "could not be run" in ci.detail


# --- the poll: scoped, bounded, and NOT holding the write lock over the network --------

def test_the_poll_only_asks_about_THIS_workflows_prs(tmp_path):
    """`tick` runs once per workflow. An unscoped poll asked GitHub about every PR in the
    fleet once PER WORKFLOW — W×P `gh` spawns a cycle, and the prize for hitting the rate
    limit is CI_UNKNOWN on everything, which correctly (and catastrophically) refuses every
    merge at once."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, task_id="other1", workflow_path="/some/other/WORKFLOW.md")
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    _tick(wf, fake, source=_Source("other1"))

    assert fake.check_reads == 0
    assert not [c for c in fake.calls if c[0] == "gh"]
    assert dispatcher.resolve_run("other1")["pr_checks"] is None


def test_a_run_that_escalated_to_needs_human_is_not_polled_forever(tmp_path):
    """`needs_human` is TERMINAL — a human owns that run now. Polling it adds a permanent gh
    call per tick, for every run that ever escalated, forever."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="needs_human")
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    summary = _tick(wf, fake)

    assert fake.check_reads == 0
    assert summary["ci_failed"] == 0
    assert dispatcher.resolve_run("abc123")["status"] == "needs_human"


def test_the_poll_does_not_hold_the_sqlite_WRITE_LOCK_across_the_network(tmp_path):
    """⛔ The gh calls used to run INSIDE an open write transaction (the first UPDATE takes
    the lock; the commit was after the loop). Twenty PRs × a network round-trip each of held
    lock, and every concurrent writer — a `chela review`, a dashboard merge, a
    `task-finished` — got `database is locked` back. The probe below IS that writer."""
    import sqlite3

    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, task_id="abc123", workflow_path=str(wf.path))
        _row(conn, task_id="def456", workflow_path=str(wf.path), window_name="test-2")
    refusals: list[str] = []

    def _a_concurrent_writer(_fake):
        # Someone else's write, landing WHILE we are "on the network" talking to gh.
        probe = sqlite3.connect(str(dispatcher.DB_PATH))
        probe.execute("PRAGMA busy_timeout=200")   # fail fast rather than sit out the 5s
        try:
            probe.execute("UPDATE runs SET title='a concurrent writer got in' "
                          "WHERE task_id='abc123'")
            probe.commit()
        except sqlite3.OperationalError as e:
            refusals.append(str(e))
        finally:
            probe.close()

    fake = _FakeGh(rollup=[_check_run("test", conclusion="SUCCESS")],
                   on_check_read=_a_concurrent_writer)

    _tick(wf, fake, source=_Source("abc123", "def456"))

    assert fake.check_reads == 2      # both PRs were really asked about
    assert refusals == []             # and nobody was locked out while we asked


# --- a check that never settles is not one we are still waiting for -------------------

def test_a_pending_check_that_never_settles_ages_out_into_needs_human(tmp_path):
    """`pending` is the state with no exit of its own: no verdict fires on it, no merge gate
    passes it, and the Kanban renders no Merge button. A `WAITING` deployment gate, or a check
    an app registered and never reported, parks the run there FOREVER — silently. The loop is
    allowed to give up. It is not allowed to go quiet."""
    from datetime import datetime, timedelta, timezone

    wf = _wf(tmp_path)
    stale = (datetime.now(timezone.utc) - timedelta(hours=9)).isoformat()
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), pr_checks=dispatcher.CI_PENDING,
             pr_head_sha="deadbee1", ci_pending_since=stale)
    fake = _FakeGh(rollup=[_check_run("deploy", status="WAITING", conclusion=None)])

    summary = _tick(wf, fake)

    assert summary["escalated"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert "not settled in 9h" in run["last_error"] and "STUCK" in run["last_error"]
    # CMX-242: an automatic escalation is not just a bare reason — it names a
    # recommendation and concrete next steps, the same as a human-typed `chela escalate`.
    assert "Recommendation:" in run["last_error"]
    assert "Options:\n  - " in run["last_error"]
    _assert_actionable_escalation(run["last_error"])


def test_a_pending_check_that_is_merely_SLOW_is_left_alone(tmp_path):
    """The escape hatch must not become the thing it escapes: a normal CI run is pending for
    minutes, and nothing about that is stuck."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", status="IN_PROGRESS", conclusion=None)])

    summary = _tick(wf, fake)          # first sight of the pending spell: the clock starts

    assert summary["escalated"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert run["ci_pending_since"]     # ...and it is now being timed

    # The checks settle green: the clock is CLEARED, not left running against the next spell.
    fake.rollup = [_check_run("test", conclusion="SUCCESS")]
    _tick(wf, fake)
    assert dispatcher.resolve_run("abc123")["ci_pending_since"] is None


# --- the ordering invariant: a red at the cap escalates, it does not get a free round --

def test_a_red_ci_at_the_rework_cap_escalates_on_the_SAME_tick_and_is_not_respawned(
    tmp_path, monkeypatch,
):
    """⛔ THE ORDERING IS THE INVARIANT. 1c (the CI verdict) sits ABOVE 1d (escalate) because
    3b re-spawns every `changes_requested` row WITHOUT re-checking the cap — it trusts 1d to
    have taken the spent ones out of that state first. A verdict written after 1d would be
    re-spawned this same tick, one round OVER budget, and every red-at-cap run would get a
    free extra round forever. That justification was defended only by a comment. It is a test
    now, and any reorder turns it red.

    🏗️🔁 CMX-192: a CI send-back spends the INFRA budget, not the review one, so "at the cap"
    here is the infra cap — and the reason it escalates with names infra.
    """
    monkeypatch.setenv("CHELA_MAX_REWORKS", "1")
    monkeypatch.setenv("CHELA_MAX_INFRA_RESTARTS", "2")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), rework_count=1,
             infra_restarts=1)                  # one infra round left — this red spends it
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1      # the verdict still fires — the red is a FACT
    assert summary["escalated"] == 1
    assert summary["reworked"] == 0       # ⛔ and nothing gave it a round it had not got
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert (run["rework_count"] or 0) == 1
    assert run["infra_restarts"] == 2
    assert "infra restart cap reached (2/2)" in run["last_error"]
    assert "still fails review" not in run["last_error"]
    assert len(dispatcher.reviews_of(run)) == 1


def test_a_red_ci_at_the_REVIEW_cap_is_a_free_infra_round_not_an_escalation(
    tmp_path, monkeypatch,
):
    """🏗️🔁 CMX-192: the review cap is spent (`rework_count == cap`), but a red CI is not a
    review verdict — it is charged to `infra_restarts`, refunded on the rework cap, and the
    run is re-spawned instead of being told it "still fails review"."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "1")
    monkeypatch.setenv("CHELA_MAX_INFRA_RESTARTS", "5")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), rework_count=1)
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    summary = _tick(wf, fake)

    assert summary["ci_failed"] == 1
    assert summary["escalated"] == 0
    assert summary["reworked"] == 1
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "running"
    assert run["infra_restarts"] == 1
    assert run["rework_count"] == 2
    assert dispatcher.review_reworks(run) == 1      # still only the one review round


# --- issue #426: a CONFLICTING PR reports a state distinct from `none`, without ever
# latching on it once GitHub resolves the conflict -------------------------------------

@pytest.mark.parametrize("checks", [
    dispatcher.CI_NONE, dispatcher.CI_PASSING, dispatcher.CI_PENDING,
    dispatcher.CI_FAILING, dispatcher.CI_UNKNOWN, None,
])
def test_ci_report_state_conflicting_overrides_any_checks_cache(checks):
    """GUARD (a): a conflicting PR reports distinctly, whatever the (possibly stale) checks
    cache says — the merge gate refuses a conflicting PR regardless of CI, so a checks state
    left over from before the base drifted is not the useful thing to show."""
    assert dispatcher.ci_report_state(checks, "CONFLICTING") == dispatcher.CI_BLOCKED_CONFLICTING


@pytest.mark.parametrize("checks", [dispatcher.CI_NONE, dispatcher.CI_PENDING, None])
def test_ci_report_state_not_yet_mergeable_falls_through_unchanged(checks):
    """GUARD (b): a genuinely fresh push, where `mergeable` has not been read at all yet
    (None) or GitHub is still computing it (`UNKNOWN`), must NOT read as conflicted — only
    the literal `CONFLICTING` string may override the checks cache."""
    assert dispatcher.ci_report_state(checks, None) == checks
    assert dispatcher.ci_report_state(checks, "UNKNOWN") == checks


def test_ci_report_state_mergeable_does_not_override_checks():
    assert dispatcher.ci_report_state(dispatcher.CI_PASSING, "MERGEABLE") == dispatcher.CI_PASSING


def test_ci_report_state_does_not_latch_once_conflict_resolves():
    """GUARD (c)/no-latch: the function is pure and reads the CURRENT `pr_mergeable` on every
    call — once GitHub resolves the conflict away, the very next read must stop reporting
    blocked_conflicting, with nothing cached anywhere to un-stick."""
    assert dispatcher.ci_report_state(dispatcher.CI_NONE, "CONFLICTING") == (
        dispatcher.CI_BLOCKED_CONFLICTING
    )
    assert dispatcher.ci_report_state(dispatcher.CI_PASSING, "MERGEABLE") == dispatcher.CI_PASSING


# --- ⛔ CMX-256: an unread or unnamed red is PENDING, never a rework ---------------------

def _reopen_mid_tick(new_sha):
    """Stand-in for `chela reopen` landing between the 0b poll and 1c: the row was
    `needs_human` when the poll ran (so its checks were not read), and is `awaiting_review`
    on a NEW head, still carrying the OLD head's `pr_checks='failing'`, by the time 1c looks."""
    real = dispatcher._read_pr_status

    def _status(pr_url, repo_dir):
        out = real(pr_url, repo_dir)
        with dispatcher._db() as conn:
            conn.execute("UPDATE runs SET status='awaiting_review', pr_head_sha=? "
                         "WHERE task_id='abc123'", (new_sha,))
            conn.commit()
        return out
    return patch.object(dispatcher, "_read_pr_status", side_effect=_status)


def test_a_stale_failing_row_with_no_read_this_tick_is_NOT_sent_back(tmp_path, caplog):
    """The 10-18 incident: CMX-176 at its rework cap, reopened on a fresh head whose checks
    were all IN_PROGRESS, was sent to needs_human by a "CI RED — job ?" on that head."""
    from chela import inbox
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="needs_human",
             pr_checks=dispatcher.CI_FAILING, pr_head_sha="oldhead1", ci_failed_sha="oldhead1",
             rework_count=2)
    fake = _FakeGh(rollup=[_check_run("test (3.11)", status="IN_PROGRESS", conclusion=None),
                           _check_run("frontend")], sha="5d44118a")

    with _reopen_mid_tick("5d44118a"), \
            patch.object(inbox, "ci_red", side_effect=AssertionError("no notice")):
        summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0 and summary["escalated"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review"
    assert run["ci_failed_sha"] == "oldhead1"      # the new head was never burned
    assert len(dispatcher.reviews_of(run)) == 0
    assert fake.log_fetches == 0
    assert "treated as PENDING" in caplog.text
    # The next tick reads the new head for real: still running ⇒ pending, not red.
    _tick(wf, fake)
    run = dispatcher.resolve_run("abc123")
    assert run["pr_checks"] == dispatcher.CI_PENDING and run["status"] == "awaiting_review"


def test_a_fresh_read_that_disagrees_with_a_failing_row_is_not_a_red(tmp_path):
    """Negative control on `ci.state`: the row says failing, this tick's read (kept in
    `ci_now`) says passing — a concurrent writer put the stale value back."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test")])
    reap = dispatcher._reap_terminal_windows

    def _restale(conn, wf_path):     # runs after 0b wrote `passing`, before 1c reads
        conn.execute("UPDATE runs SET pr_checks='failing' WHERE task_id='abc123'")
        conn.commit()
        return reap(conn, wf_path)
    # A non-failing read that still carries a job name, so only the STATE can stop it.
    passing = dispatcher.CIStatus(dispatcher.CI_PASSING, "deadbee1", ("CI / test",), ("42",))
    with patch.object(dispatcher, "_reap_terminal_windows", side_effect=_restale), \
            patch.object(dispatcher, "_read_pr_checks", return_value=passing):
        summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    assert dispatcher.resolve_run("abc123")["status"] == "awaiting_review"


def test_a_failing_read_that_names_no_job_is_PENDING_not_red(tmp_path, caplog):
    """An empty job list means the classifier met a shape it does not know ⇒ PENDING, stored
    as pending (so 1c′ can age it out), never a red that sits unhandled on the row."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    with patch.object(dispatcher, "_rollup_state",
                      return_value=(dispatcher.CI_FAILING, (), ("42",), False, ())):
        summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "awaiting_review" and run["ci_failed_sha"] is None
    assert run["pr_checks"] == dispatcher.CI_PENDING and run["ci_pending_since"]
    assert "no failing job named" in caplog.text
    assert fake.log_fetches == 0


def test_a_failure_beside_a_running_job_goes_red_naming_it_once_it_settles(tmp_path):
    """One FAILURE + one IN_PROGRESS is pending (unsettled wins); when the other job lands,
    the red names the job that FAILED — not the one that passed."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("lint", conclusion="FAILURE"),
                           _check_run("test", status="IN_PROGRESS", conclusion=None)])
    assert _tick(wf, fake)["ci_failed"] == 0

    fake.rollup = [_check_run("lint", conclusion="FAILURE"), _check_run("test")]
    assert _tick(wf, fake)["ci_failed"] == 1
    verdict = dispatcher.latest_verdict(dispatcher.resolve_run("abc123"))
    assert "CI / lint" in verdict and "CI / test" not in verdict


@pytest.mark.parametrize("rollup, expected", [
    ([_check_run("a", status="IN_PROGRESS", conclusion=None),
      _check_run("b", status="QUEUED", conclusion=None),
      _check_run("c", status="PENDING", conclusion=None)], dispatcher.CI_PENDING),
    ([_check_run("a"), _check_run("b")], dispatcher.CI_PASSING),
    ([], dispatcher.CI_NONE),
])
def test_a_rollup_with_no_failure_never_sends_back(tmp_path, rollup, expected):
    """All running ⇒ pending; all SUCCESS ⇒ green; a just-pushed head with no checks yet ⇒
    `none` — none of them is red, none sends the run back or announces anything."""
    from chela import inbox
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=rollup)

    with patch.object(inbox, "ci_red", side_effect=AssertionError("no notice")):
        summary = _tick(wf, fake)

    assert summary["ci_failed"] == 0
    run = dispatcher.resolve_run("abc123")
    assert run["pr_checks"] == expected and run["status"] == "awaiting_review"


# --- 🏗️🔁 CMX-192: infra restarts are NOT review rounds -------------------------------
#
# CMX-180 hit "rework cap reached 3/3: the PR still fails review" with ZERO judge rounds: a
# red CI from the CMX-188 clock flake, a human `chela retry`, and a rework agent killed by a
# tmux death. The rework cap counts review verdicts only; the rest is `infra_restarts`, with
# its own higher cap.

def _set_status(status):
    with dispatcher._db() as conn:
        conn.execute("UPDATE runs SET status=? WHERE task_id='abc123'", (status,))
        conn.commit()


def test_the_cmx180_history_spends_no_review_round_and_does_not_escalate(tmp_path, monkeypatch):
    """GUARD: CI-flake rework → human retry → killed agent leaves the review counter at 0 and
    the infra counter at 2, and the run is NOT needs_human. The review cap is 1 on purpose:
    before CMX-192 the killed agent's tick escalated it with "the PR still fails review"."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "1")
    monkeypatch.setenv("CHELA_MAX_INFRA_RESTARTS", "8")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])

    # (1) the clock flake: CI red → sent back, re-spawned.
    summary = _tick(wf, fake)
    assert summary["ci_failed"] == 1 and summary["reworked"] == 1
    run = dispatcher.resolve_run("abc123")
    assert (run["status"], run["rework_count"], run["infra_restarts"]) == ("running", 1, 1)

    # (2) a human retry (the run had been parked in needs_human).
    _set_status("needs_human")
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        assert dispatcher.retry("abc123")["ok"]
    summary = _tick(wf, fake)
    assert summary["reworked"] == 1 and summary["escalated"] == 0
    assert dispatcher.resolve_run("abc123")["infra_restarts"] == 1   # a retry costs nothing

    # (3) tmux death: the rework agent's window is gone.
    fake.windows.clear()
    summary = _tick(wf, fake)

    run = dispatcher.resolve_run("abc123")
    assert summary["escalated"] == 0
    assert run["status"] != "needs_human"
    assert run["status"] == "running"                  # re-spawned once more, not parked
    assert dispatcher.review_reworks(run) == 0
    assert run["infra_restarts"] == 2
    assert not dispatcher.rework_cap_reached(run)


def test_a_real_judge_block_spends_a_REVIEW_round(tmp_path, monkeypatch):
    """Negative control: a judge `blocked` verdict is a review round — it increments the
    review counter, not the infra one."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "3")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="SUCCESS")])

    assert dispatcher.request_changes("abc123", "a guard survived", post_comment=False,
                                      judge_state="blocked")["ok"]
    summary = _tick(wf, fake)

    run = dispatcher.resolve_run("abc123")
    assert summary["reworked"] == 1
    assert run["rework_count"] == 1
    assert dispatcher.review_reworks(run) == 1
    assert (run["infra_restarts"] or 0) == 0


def test_judge_blocks_at_the_review_cap_escalate_with_still_fails_review(tmp_path, monkeypatch):
    """The review cap still bites, and still says "still fails review" — infra restarts on
    the same run are refunded, not counted, and named in the reason."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "1")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), rework_count=3, review_reworks=1,
             infra_restarts=2)
    assert dispatcher.request_changes("abc123", "still broken", post_comment=False)["ok"]

    summary = _tick(wf, _FakeGh(rollup=[_check_run("test", conclusion="SUCCESS")]))

    run = dispatcher.resolve_run("abc123")
    assert summary["escalated"] == 1 and summary["reworked"] == 0
    assert run["status"] == "needs_human"
    assert "still fails review" in run["last_error"]
    assert "rework cap reached (1/1; infra restarts: 2)" in run["last_error"]
    assert "infra restart cap" not in run["last_error"]


def test_dead_rework_agents_hit_the_INFRA_cap_with_an_infra_reason(tmp_path, monkeypatch):
    """GUARD: N infra restarts reach the infra cap → needs_human, with a reason that names
    infra and never "still fails review" — even though the review cap is far away."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "5")
    monkeypatch.setenv("CHELA_MAX_INFRA_RESTARTS", "2")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path))
    fake = _FakeGh(rollup=[_check_run("test", conclusion="SUCCESS")])
    assert dispatcher.request_changes("abc123", "fix it", post_comment=False)["ok"]
    assert _tick(wf, fake)["reworked"] == 1             # the review round itself

    fake.windows.clear()
    assert _tick(wf, fake)["escalated"] == 0            # death 1: re-spawned
    assert dispatcher.resolve_run("abc123")["infra_restarts"] == 1
    fake.windows.clear()
    _tick(wf, fake)                                     # death 2: the infra budget is spent

    run = dispatcher.resolve_run("abc123")
    assert run["status"] == "needs_human"
    assert run["infra_restarts"] == 2
    assert "infra restart cap reached (2/2)" in run["last_error"]
    assert "review reworks: 1/4" in run["last_error"]       # risk normal: 4
    assert "still fails review" not in run["last_error"]
    _assert_actionable_escalation(run["last_error"])


def test_a_locked_db_under_the_ci_verdict_hands_the_infra_restart_back(tmp_path):
    """`except sqlite3.Error` on the CI verdict: nothing was sent back, so nothing is charged
    — `infra_restarts` returns to its value BEFORE the red (3, not 0, not 4)."""
    import sqlite3

    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), infra_restarts=3)
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])
    with patch.object(dispatcher, "request_changes",
                      side_effect=sqlite3.OperationalError("database is locked")):
        _tick(wf, fake)
    assert dispatcher.resolve_run("abc123")["infra_restarts"] == 3


def test_a_refused_ci_verdict_hands_the_infra_restart_back(tmp_path):
    """The CAS refused the CI verdict (the row moved): no round, so no infra charge."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), infra_restarts=3)
    fake = _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")])
    with patch.object(dispatcher, "request_changes",
                      return_value={"ok": False, "error": "moved"}):
        summary = _tick(wf, fake)
    assert summary["ci_failed"] == 0
    assert dispatcher.resolve_run("abc123")["infra_restarts"] == 3


def test_a_ci_send_back_charges_one_infra_restart_on_top_of_the_existing_ones(tmp_path):
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), infra_restarts=3)
    _tick(wf, _FakeGh(rollup=[_check_run("test", conclusion="FAILURE")]))
    assert dispatcher.resolve_run("abc123")["infra_restarts"] == 4


@pytest.mark.parametrize("row, review, reached", [
    # base cap 2 (CHELA_MAX_REWORKS); cap = base + retry + conflict
    ({"rework_count": 2}, 2, True),                                  # legacy row: NULL → count
    ({"rework_count": 5, "review_reworks": 1}, 1, False),            # the column wins
    ({"rework_count": 5, "review_reworks": 2}, 2, True),
    ({"rework_count": 5, "review_reworks": 2, "rework_cause": "infra"}, 2, False),
    ({"rework_count": 5, "review_reworks": 2, "retry_count": 1}, 2, False),
    ({"rework_count": 5, "review_reworks": 3, "retry_count": 1}, 3, True),
    ({"rework_count": 5, "review_reworks": 2, "conflict_reworks": 1}, 2, False),
    ({"rework_count": 5, "review_reworks": 0}, 0, False),            # 0 is not NULL
    ({"rework_count": 5, "review_reworks": 2, "rework_cause": "review"}, 2, True),
    ({"rework_count": 5, "review_reworks": 2, "rework_cause": "bogus"}, 2, True),
])
def test_the_review_cap_counts_review_rounds_only(monkeypatch, row, review, reached):
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    monkeypatch.setenv("CHELA_MAX_INFRA_RESTARTS", "8")
    full = {"retry_count": 0, "risk": None, **row}
    assert dispatcher.review_reworks(full) == review
    assert dispatcher.review_cap_reached(full) is reached
    assert dispatcher.rework_cap_reached(full) is reached


@pytest.mark.parametrize("infra, retry, reached", [
    (2, 0, False), (3, 0, True), (4, 0, True), (3, 1, False), (4, 1, True),
])
def test_infra_cap_is_its_own_knob_plus_human_retries(monkeypatch, infra, retry, reached):
    monkeypatch.setenv("CHELA_MAX_REWORKS", "5")
    monkeypatch.setenv("CHELA_MAX_INFRA_RESTARTS", "3")
    row = {"rework_count": 0, "retry_count": retry, "risk": None, "infra_restarts": infra,
           "rework_cause": "infra"}
    assert dispatcher.infra_restart_cap(row) == 3 + retry
    assert dispatcher.infra_cap_reached(row) is reached
    assert dispatcher.rework_cap_reached(row) is reached     # either budget counts
    assert dispatcher.review_cap_reached(row) is False
    # a REVIEW round waiting is never stopped by the infra budget (review cap is far off)
    assert dispatcher.infra_cap_reached({**row, "rework_cause": "review"}) is False


def test_an_old_row_without_the_infra_column_reads_as_zero():
    assert dispatcher.infra_restarts({"rework_count": 1}) == 0


@pytest.mark.parametrize("run, text", [
    ({"rework_count": 3, "review_reworks": 1, "rework_cause": "review"}, "rework 2 of 2"),
    ({"rework_count": 3, "review_reworks": 1, "rework_cause": "infra", "infra_restarts": 2},
     "infra restart 2 of 8, not a review round"),
    ({"rework_count": 3}, "rework 4 of 2"),             # legacy row: NULL → rework_count
    ({"rework_count": 3, "review_reworks": 0}, "rework 1 of 2"),
])
def test_the_inbox_numbers_the_next_round_against_the_budget_it_spends(monkeypatch, run, text):
    """🏗️🔁 CMX-192: "sent back for rework — rework N of M" counts REVIEW rounds; an infra
    send-back is named as one, against its own cap. `_cap_fields` keeps `review_reworks`'
    NULL (a 0 there would hand a legacy row a fresh budget)."""
    from chela import inbox
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    monkeypatch.setenv("CHELA_MAX_INFRA_RESTARTS", "8")
    assert inbox._next_round(run) == text


def test_request_changes_writes_the_cause_it_is_given(tmp_path):
    """Both kinds through `request_changes`: the default is a review, `infra` is stored as
    such — and anything else is a review, never silently free."""
    with dispatcher._db() as conn:
        _row(conn)
    assert dispatcher.request_changes("abc123", "x", post_comment=False, cause="infra")["ok"]
    assert dispatcher.resolve_run("abc123")["rework_cause"] == "infra"
    for cause, stored in ((None, "review"), ("bogus", "review")):
        _set_status("awaiting_review")
        kw = {} if cause is None else {"cause": cause}
        assert dispatcher.request_changes("abc123", "x", post_comment=False, **kw)["ok"]
        assert dispatcher.resolve_run("abc123")["rework_cause"] == stored


def test_retry_keeps_the_cause_so_its_round_spends_what_escalated(tmp_path, monkeypatch):
    """A retry after an INFRA escalation re-spawns as infra (review counter untouched); the
    retry grant lifts the infra cap by one so it is not re-escalated on the spot."""
    monkeypatch.setenv("CHELA_MAX_INFRA_RESTARTS", "2")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="needs_human", rework_count=3,
             review_reworks=1, infra_restarts=2, rework_cause="infra")
    with patch.object(dispatcher, "_post_pr_comment", return_value=(True, "")):
        assert dispatcher.retry("abc123")["ok"]
    summary = _tick(wf, _FakeGh(rollup=[_check_run("test", conclusion="SUCCESS")]))
    run = dispatcher.resolve_run("abc123")
    assert summary["escalated"] == 0 and summary["reworked"] == 1
    assert (run["rework_cause"], run["review_reworks"], run["rework_count"]) == ("infra", 1, 4)


def test_a_legacy_row_respawned_on_a_review_verdict_counts_from_rework_count(tmp_path):
    """A pre-CMX-192 row (`review_reworks` NULL) starts its review count at `rework_count`:
    it is not handed a fresh budget by the migration."""
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="changes_requested", rework_count=2,
             review_reworks=None, rework_cause=None)
    _tick(wf, _FakeGh(rollup=[_check_run("test", conclusion="SUCCESS")]))
    run = dispatcher.resolve_run("abc123")
    assert (run["rework_count"], run["review_reworks"]) == (3, 3)


def _failed_row(tmp_path, db_status, snap_status, cause="review"):
    with dispatcher._db() as conn:
        _row(conn, status=snap_status, rework_count=1, review_reworks=1, infra_restarts=1,
             rework_cause=cause)
        snap = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
        conn.execute("UPDATE runs SET status=? WHERE task_id='abc123'", (db_status,))
        conn.commit()
        dispatcher._rework_failed(conn, snap, "boom")
    return dispatcher.resolve_run("abc123")


def test_a_rework_that_died_after_it_was_spawned_turns_the_cause_infra(tmp_path):
    run = _failed_row(tmp_path, "running", "running")
    assert run["status"] == "changes_requested"
    assert (run["infra_restarts"], run["rework_cause"]) == (2, "infra")
    assert (run["rework_count"], run["review_reworks"]) == (1, 1)


def test_a_rework_that_died_BEFORE_its_spawn_write_keeps_its_verdicts_cause(tmp_path):
    """The re-spawn raised before it wrote `running` (so it never charged its verdict): the
    death is still an infra restart, but the verdict's own cause stays to be charged."""
    run = _failed_row(tmp_path, "changes_requested", "changes_requested")
    assert (run["infra_restarts"], run["rework_cause"]) == (2, "review")


def test_a_rework_that_died_after_its_spawn_write_from_the_3b_snapshot_turns_infra(tmp_path):
    """3b's `except` hands `_rework_failed` its PRE-spawn snapshot (`changes_requested`) —
    the DB row is what says the round was spawned."""
    run = _failed_row(tmp_path, "running", "changes_requested")
    assert (run["infra_restarts"], run["rework_cause"]) == (2, "infra")


def test_a_review_verdict_on_a_spent_infra_budget_is_the_review_caps_call(tmp_path, monkeypatch):
    """A judge block lands on a run whose infra budget is spent but whose review budget is
    not: it is re-spawned as a review round — never escalated with an infra reason."""
    monkeypatch.setenv("CHELA_MAX_REWORKS", "3")
    monkeypatch.setenv("CHELA_MAX_INFRA_RESTARTS", "2")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), rework_count=2, review_reworks=0,
             infra_restarts=2, rework_cause="infra")
    assert dispatcher.request_changes("abc123", "a guard survived", post_comment=False)["ok"]
    summary = _tick(wf, _FakeGh(rollup=[_check_run("test", conclusion="SUCCESS")]))
    run = dispatcher.resolve_run("abc123")
    assert summary["escalated"] == 0 and summary["reworked"] == 1
    assert (run["status"], run["review_reworks"]) == ("running", 1)


def test_the_needs_human_notice_names_review_rounds_and_infra_restarts_apart(monkeypatch):
    from chela import inbox
    monkeypatch.setenv("CHELA_MAX_REWORKS", "2")
    assert inbox._review_reworks({"rework_count": 4, "review_reworks": 1}) == 1
    assert inbox._review_reworks({"rework_count": 4}) == 4          # legacy NULL row
