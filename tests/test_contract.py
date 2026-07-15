"""⚙️⚖️ CONTRACT-AS-CODE — and the whole point is that a wrong LLM opinion CANNOT act unsafely,
so these tests are the adversary: they push every disallowed action at the gate and watch it
refuse. The merge gate is a pure function of (run row, live GitHub facts); GitHub is the one
thing stubbed — the gate's decisions are what is under test.

Each refusal below is one clause of ``docs/ESCALATION_CONTRACT.md`` made mechanical:

* base = ``main`` → the NEVER line: no ``--force``, no override, tier=``never``, nothing merged;
* base ≠ ``dev`` (or unreadable) → an escalation, not an autonomous act;
* judge not ``clean`` / CI not green / not MERGEABLE → refused, fail-closed;
* only when ALL hold does it merge — and it logs the decision with its justification.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from chela import contract, dispatcher, event_log
from chela.dispatcher import CI_FAILING, CI_NONE, CI_PASSING, CI_PENDING, CI_UNKNOWN, CIStatus


@pytest.fixture(autouse=True)
def _clean_runs():
    """The runs DB path is import-time latched, so it is shared across tests — start each
    with an empty table so seeded task ids never collide."""
    with dispatcher._db() as conn:
        conn.execute("DELETE FROM runs")
        conn.commit()
    yield


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A workflow dir the gate can resolve to (``workflow_path``'s parent must be a real dir)."""
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("# wf\n")
    return tmp_path


def _seed_run(repo: Path, task_id: str = "t1", *, status: str = "awaiting_review",
              judge_state: str | None = "clean", pr_url: str = "https://github.com/o/r/pull/1") -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "started_at, attempt, pr_url, pr_state, judge_state, branch_name, worktree_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, str(repo / "WORKFLOW.md"), "t", status, "@9", dispatcher._now(), 1,
             pr_url, "open", judge_state, "cmx-1", None),
        )
        conn.commit()


# --- the NEVER line: production-facing branches are never autonomous ------------------

@pytest.mark.parametrize("base", ["main", "master", "MAIN", "production", "release"])
def test_merge_to_a_production_branch_is_NEVER(repo, base):
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value=base), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "never"
    squash.assert_not_called()      # ⛔ nothing merged — the hard line held


def test_no_force_flag_exists_on_the_merge_gate():
    """Overriding a gate is an escalation, not an autonomous act — so the API takes no force."""
    import inspect
    assert "force" not in inspect.signature(contract.merge).parameters


# --- the escalate tier: a judgment the orchestrator may not settle alone ---------------

def test_merge_to_an_unlisted_base_is_an_escalation(repo):
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="feature-x"), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False and result["tier"] == "escalate"
    squash.assert_not_called()


def test_an_unreadable_base_is_refused(repo):
    """Unknown is never a green light — a base nobody could read is not the allowed base."""
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value=None), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    squash.assert_not_called()


@pytest.mark.parametrize("judge_state", [None, "blocked", "cannot_verify"])
def test_merge_refuses_unless_the_judge_is_clean(repo, judge_state):
    _seed_run(repo, judge_state=judge_state)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    squash.assert_not_called()


@pytest.mark.parametrize("ci", [CI_FAILING, CI_PENDING, CI_NONE, CI_UNKNOWN])
def test_merge_requires_green_CI(repo, ci):
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(ci)), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False and result["ci_state"] == ci
    squash.assert_not_called()


@pytest.mark.parametrize("mergeable", ["CONFLICTING", "UNKNOWN", None])
def test_merge_requires_MERGEABLE(repo, mergeable):
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", mergeable)), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    squash.assert_not_called()


def test_merge_refuses_a_run_not_awaiting_review(repo):
    _seed_run(repo, status="running")
    with patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    squash.assert_not_called()


def test_merge_refuses_an_unknown_run(repo):
    result = contract.merge("nope")
    assert result["ok"] is False


# --- the happy path: every clause holds → it merges AND logs its justification ---------

def test_merge_when_the_whole_gate_holds(repo):
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "deadbeef1234"}) as squash:
        result = contract.merge("t1", reason="dogfood merge")
    assert result["ok"] is True
    assert result["base"] == "dev"
    assert result["merge_commit_sha"] == "deadbeef1234"
    squash.assert_called_once()

    # Provenance: the decision is on the event log with the facts it relied on.
    events = event_log.read(types=["orchestrator.merge"])["events"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["base"] == "dev"
    assert payload["judge_state"] == "clean"
    assert payload["ci_state"] == CI_PASSING
    assert payload["reason"] == "dogfood merge"
    assert payload["merge_commit_sha"] == "deadbeef1234"


def test_a_gate_that_holds_but_a_merge_that_fails_is_reported(repo):
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": False, "error": "gh pr merge timed out"}):
        result = contract.merge("t1")
    assert result["ok"] is False
    assert "gh pr merge timed out" in result["error"]
    # A gate-passed-but-merge-failed is auditable too.
    assert event_log.read(types=["orchestrator.merge_failed"])["events"]


# --- escalate: the one structured way to reach the human -------------------------------

def test_escalate_records_the_decision(repo):
    _seed_run(repo)
    with patch("chela.notify.enabled", return_value=False):
        result = contract.escalate(
            "Which of A/B/C to do next?", kind="priority",
            recommendation="B — smallest blast radius", run="t1",
            options=["A", "B", "C"],
        )
    assert result["ok"] is True and result["kind"] == "priority"
    assert result["run"] == "t1"
    assert result["notified"] is False      # no notifier configured

    events = event_log.read(types=["orchestrator.escalation.priority"])["events"]
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["tier"] == "escalate"
    assert payload["recommendation"] == "B — smallest blast radius"
    assert payload["options"] == ["A", "B", "C"]
    assert payload["run"] == "t1"


def test_escalate_pushes_to_the_human_when_a_notifier_is_configured():
    with patch("chela.notify.enabled", return_value=True), \
         patch("chela.notify.send", return_value=True) as send:
        result = contract.escalate("Ship the thing?", kind="merge")
    assert result["notified"] is True
    send.assert_called_once()
    # The recommendation and summary ride in the pushed body.
    assert "Ship the thing?" in send.call_args.args[0]


def test_escalate_refuses_an_empty_summary():
    result = contract.escalate("   ")
    assert result["ok"] is False
