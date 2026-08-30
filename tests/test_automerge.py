"""🔀⚠️ AUTO-MERGE (CMX-138) — the fully-UNATTENDED merge sweep, off by default.

The property under test is narrow but load-bearing: this sweep must (1) touch ONLY
`awaiting_review` runs the judge itself marked `clean` — nothing else, ever — and (2) merge them
through `contract.merge` with NO attended-lease required, because "unattended" is the entire
point of the feature. Everything else (base branch, CI, mergeable) is `contract.merge`'s own
gate, already proven in `test_contract.py`; these tests exist to prove the two things unique to
this module: the candidate filter, and that its actor stamp does NOT trip the orchestrator's
lease gate.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from chela import automerge, config, contract, dispatcher, event_log
from chela.dispatcher import CI_PASSING, CIStatus
from chela.judge import J_BLOCKED, J_CANNOT_VERIFY, J_CLEAN
from chela.personas import lease


@pytest.fixture(autouse=True)
def _clean_runs():
    # Wipe on BOTH sides of the test: `runs` lives in the module-global
    # `dispatcher.DB_PATH` (not the per-test `chela_dir`), so a seeded
    # `awaiting_review` row left behind here leaks into any LATER test that reads
    # that DB — e.g. `test_config_env.py::test_doctor_is_quiet_when_everything_agrees`,
    # whose `pr.checks` doctor fact then tries to verify our fake PR and reports
    # CANNOT VERIFY. Teardown cleanup keeps the pollution from crossing files.
    def _wipe() -> None:
        with dispatcher._db() as conn:
            conn.execute("DELETE FROM runs")
            conn.commit()

    _wipe()
    yield
    _wipe()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("# wf\n")
    return tmp_path


def _seed_run(repo: Path, task_id: str, *, status: str = "awaiting_review",
              judge_state: str | None = "clean",
              pr_url: str = "https://github.com/o/r/pull/1",
              judge_sha: str | None = None, pr_head_sha: str | None = None) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "started_at, attempt, pr_url, pr_state, judge_state, judge_sha, pr_head_sha, "
            "branch_name, worktree_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, str(repo / "WORKFLOW.md"), "t", status, "@9", dispatcher._now(), 1,
             pr_url, "open", judge_state, judge_sha, pr_head_sha, "cmx-1", None),
        )
        conn.commit()


# --- enabled() reflects the config flag, live -----------------------------------------

def test_disabled_by_default():
    assert config.AUTO_MERGE_ENABLED is False
    assert automerge.enabled() is False


def test_enabled_follows_the_flag(monkeypatch):
    monkeypatch.setattr(config, "AUTO_MERGE_ENABLED", True)
    assert automerge.enabled() is True


# --- candidates(): ONLY awaiting_review + judge-clean rows, nothing else --------------

def test_candidates_is_exactly_awaiting_review_and_judge_clean(repo):
    _seed_run(repo, "clean1", status="awaiting_review", judge_state=J_CLEAN)
    _seed_run(repo, "blocked1", status="awaiting_review", judge_state=J_BLOCKED)
    _seed_run(repo, "unknown1", status="awaiting_review", judge_state=J_CANNOT_VERIFY)
    _seed_run(repo, "unjudged1", status="awaiting_review", judge_state=None)
    _seed_run(repo, "running1", status="running", judge_state=J_CLEAN)
    _seed_run(repo, "done1", status="done", judge_state=J_CLEAN)

    ids = {run["task_id"] for run in automerge.candidates()}
    assert ids == {"clean1"}


# --- candidates(): a clean verdict on a SUPERSEDED commit is never a candidate --------

def test_candidates_excludes_a_clean_verdict_on_a_stale_sha(repo):
    """🔴 CMX-326. Mirrors ``cmx_321`` live 2026-08-30: ``judge_state=clean`` recorded against
    ``judge_sha`` while the PR's real head (``pr_head_sha``, refreshed by the dispatcher's own
    CI poll) has since moved past it — e.g. a promotion conflict resolved on the branch AFTER
    the judge cleared it. The filter must not treat that verdict as being about the current
    commit.

    Corrupt ``_judge_verdict_is_stale`` to always return ``False`` and this goes red."""
    _seed_run(repo, "stale1", judge_sha="888f9d6a1", pr_head_sha="5b1c708dd")
    ids = {run["task_id"] for run in automerge.candidates()}
    assert ids == set()


def test_candidates_includes_a_clean_verdict_matching_the_live_head(repo):
    """The sibling of the staleness exclusion above: when ``judge_sha`` DOES match the row's
    own ``pr_head_sha``, the row is still a candidate — this filter must not reject everything
    with a stamped sha, only a genuine mismatch."""
    _seed_run(repo, "current1", judge_sha="deadbeef0001", pr_head_sha="deadbeef0001")
    ids = {run["task_id"] for run in automerge.candidates()}
    assert ids == {"current1"}


def test_candidates_includes_a_clean_verdict_with_no_head_sha_recorded_yet(repo):
    """An unset ``pr_head_sha`` (an older row, or one the CI poll hasn't touched yet) is NOT
    positive evidence of staleness — same conservatism as ``contract.merge``'s own live check.
    ``contract.merge``'s live read is still the authority that actually gates the merge."""
    _seed_run(repo, "nohead1", judge_sha="deadbeef0001", pr_head_sha=None)
    ids = {run["task_id"] for run in automerge.candidates()}
    assert ids == {"nohead1"}


def test_candidates_includes_a_clean_verdict_with_no_judge_sha_stamped(repo):
    """Mirror of the test above, on the OTHER unset-sha clause: an unstamped ``judge_sha`` (a
    judge that ran before this column existed) is likewise NOT positive evidence of staleness,
    even though ``pr_head_sha`` is known — same conservatism, other half of the ``and``.

    Corrupt ``_judge_verdict_is_stale`` to drop the ``judge_sha and`` clause (leaving only
    ``head_sha and judge_sha != head_sha``) and this goes red: ``None != "deadbeef0001"`` is
    ``True``, so the row would wrongly read as stale and vanish from the candidate set."""
    _seed_run(repo, "nojudgesha1", judge_sha=None, pr_head_sha="deadbeef0001")
    ids = {run["task_id"] for run in automerge.candidates()}
    assert ids == {"nojudgesha1"}


# --- sweep(): merges through contract.merge, UNATTENDED (no lease needed) ------------

def test_sweep_merges_a_judge_clean_run_with_no_lease_at_all(repo):
    """🔴 THE LOAD-BEARING GUARD. With NO attended-lease active (the orchestrator's own merge
    would be refused here — see test_contract.py's lease tests), the auto-merge actor must still
    merge: that is the entire point of `CHELA_AUTO_MERGE`. If this goes red, the module has
    regressed into requiring attendance, defeating the feature."""
    _seed_run(repo, "t1")
    assert lease.active() is None  # sanity: nobody is attending
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "beef"}) as squash:
        results = automerge.sweep()
    assert len(results) == 1
    assert results[0]["ok"] is True
    squash.assert_called_once()
    payload = event_log.read(types=["orchestrator.merge"])["events"][0]["payload"]
    assert payload["actor"] == config.AUTO_MERGE_ACTOR
    assert payload["actor"] != config.AUTO_ORCHESTRATOR_ACTOR


def test_sweep_never_touches_a_non_candidate_run(repo):
    """A run the judge blocked (or never judged) must never even be OFFERED to contract.merge —
    proving the candidate filter, not just contract.merge's own gate, is what is under test
    here. Corrupt `candidates()` to return everything and this goes red (squash gets called)."""
    _seed_run(repo, "blocked1", judge_state=J_BLOCKED)
    with patch.object(contract, "_squash_merge") as squash:
        results = automerge.sweep()
    assert results == []
    squash.assert_not_called()


def test_sweep_skips_a_run_that_fails_the_downstream_gate_without_raising(repo):
    """A candidate that fails contract.merge's OWN gate (e.g. CI not actually green — the judge
    can go stale between ticks) is refused, not merged, and the sweep carries on."""
    _seed_run(repo, "t1")
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks",
                      return_value=CIStatus(dispatcher.CI_PENDING)), \
         patch.object(contract, "_squash_merge") as squash:
        results = automerge.sweep()
    assert len(results) == 1
    assert results[0]["ok"] is False
    squash.assert_not_called()


def test_sweep_stamps_a_distinct_actor_not_the_orchestrators(repo):
    """The actor string sweep() passes must never equal AUTO_ORCHESTRATOR_ACTOR — that string is
    precisely what triggers contract.merge's lease gate (clause 2). If a future edit accidentally
    reused it, every auto-merge would start silently requiring a live lease again."""
    assert config.AUTO_MERGE_ACTOR != config.AUTO_ORCHESTRATOR_ACTOR
    _seed_run(repo, "t1")
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "merge", wraps=contract.merge) as merge_spy:
        automerge.sweep()
    _, kwargs = merge_spy.call_args
    assert kwargs["actor"] == config.AUTO_MERGE_ACTOR


def test_sweep_never_raises_when_contract_merge_blows_up(repo):
    """One bad row must not take the whole daemon tick down with it."""
    _seed_run(repo, "t1")
    with patch.object(contract, "merge", side_effect=RuntimeError("boom")):
        results = automerge.sweep()  # must not raise
    assert results == []
