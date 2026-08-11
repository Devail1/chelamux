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

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, contract, dispatcher, event_log
from chela.dispatcher import CI_FAILING, CI_NONE, CI_PASSING, CI_PENDING, CI_UNKNOWN, CIStatus
from chela.judge import J_BLOCKED, J_BLOCKED_RACE, J_CANNOT_VERIFY, J_RUNNING
from chela.personas import lease


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    """A runs DB per test (``dispatcher.DB_PATH`` is latched at import — see conftest).

    This used to just ``DELETE FROM runs`` against the shared, import-time-latched
    ``dispatcher.DB_PATH`` before each test — which cleaned up for the NEXT test in this
    file, but left the last seeded row (``awaiting_review``, a fake ``pr_url``) sitting in
    that shared DB for whatever test ran after this module in the same worker. `chela
    doctor`'s ``pr.checks`` fact reads every real ``awaiting_review`` row from
    ``dispatcher.DB_PATH`` and asks GitHub about it — in CI, with no ``gh`` auth, that
    leftover row came back CANNOT VERIFY and failed an unrelated doctor test
    (``test_doctor_is_quiet_when_everything_agrees``) that never touched this file. Every
    other file that seeds runs already isolates with its own ``tmp_path`` DB; this one just
    hadn't followed suit.
    """
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A workflow dir the gate can resolve to (``workflow_path``'s parent must be a real dir)."""
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("# wf\n")
    return tmp_path


def _assert_actionable(result: dict) -> None:
    """Structural guard (CMX-242, rework round 1): an ``escalate``-tier refusal must carry
    a non-empty recommendation and a real menu (≥2 options — one option is not a choice),
    and the recommendation must actually NAME one of those options (or explicitly opt out
    with "none of these") rather than a label ("Recommendation:") with nothing useful
    behind it. Checks the FIELDS on the returned dict, not any rendered/formatted string,
    so it survives a pure formatting change."""
    assert result["tier"] == "escalate"
    recommendation = (result.get("recommendation") or "").strip()
    options = result.get("options") or []
    assert recommendation, "an escalate-tier refusal must carry a non-empty recommendation"
    assert len(options) >= 2, "one option is not a choice"
    assert (
        any(o in recommendation for o in options)
        or recommendation.lower().startswith("none of these")
    ), "the recommendation must name one of its own options (or explicitly opt out)"


def test_actionable_helper_flags_a_recommendation_not_among_its_own_options():
    """Sanity on the helper itself (the ticket's explicit example): a recommendation naming
    something that isn't in its own options list must fail this check — proving the helper
    actually inspects content, not just field presence."""
    with pytest.raises(AssertionError):
        _assert_actionable({
            "tier": "escalate",
            "recommendation": "do something else entirely",
            "options": ["do A", "do B"],
        })


def _seed_run(repo: Path, task_id: str = "t1", *, status: str = "awaiting_review",
              judge_state: str | None = "clean", pr_url: str = "https://github.com/o/r/pull/1",
              judge_sha: str | None = None) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "started_at, attempt, pr_url, pr_state, judge_state, judge_sha, branch_name, "
            "worktree_path) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, str(repo / "WORKFLOW.md"), "t", status, "@9", dispatcher._now(), 1,
             pr_url, "open", judge_state, judge_sha, "cmx-1", None),
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
    # CMX-242: a NEVER refusal is a hard line, not a human decision — it must not sprout
    # options (adding them here → RED).
    assert "recommendation" not in result and "options" not in result
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
    _assert_actionable(result)      # CMX-242: not just a bare reason
    squash.assert_not_called()


def test_an_unreadable_base_is_refused(repo):
    """Unknown is never a green light — a base nobody could read is not the allowed base."""
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value=None), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    _assert_actionable(result)
    squash.assert_not_called()


@pytest.mark.parametrize("base", ["staging", "develop", "feature-x"])
def test_merge_refuses_a_non_dev_base_even_when_all_else_passes(repo, base):
    """The ``base != AUTONOMOUS_BASE`` gate IN ISOLATION — judge clean, CI green, MERGEABLE,
    so the ONLY thing wrong is the base is not ``dev`` (and not forbidden). It must refuse,
    tier ``escalate`` (not ``never`` — ``never`` is the forbidden set alone).

    Every downstream gate is mocked to pass, so this gate is the sole possible refusal: corrupt
    ``if base != AUTONOMOUS_BASE`` to ``if False and …`` and the merge goes through, turning
    this red. (The sibling ``test_merge_to_an_unlisted_base_is_an_escalation`` does NOT mock the
    downstream gates, so it stays green under that mutation — it is not the guard.)"""
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value=base), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "escalate"
    assert result.get("pr_base") == base
    _assert_actionable(result)
    squash.assert_not_called()      # ⛔ nothing merged — the base gate held


@pytest.mark.parametrize("judge_state", [None, "blocked", "cannot_verify"])
def test_merge_refuses_unless_the_judge_is_clean(repo, judge_state):
    _seed_run(repo, judge_state=judge_state)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    _assert_actionable(result)
    squash.assert_not_called()


@pytest.mark.parametrize("judge_state", [J_BLOCKED, J_BLOCKED_RACE, J_CANNOT_VERIFY, J_RUNNING, None])
def test_merge_refuses_a_run_not_judge_clean_even_when_all_else_passes(repo, judge_state):
    """The ``judge_state != J_CLEAN`` gate IN ISOLATION — base ``dev``, CI green, MERGEABLE, so
    the ONLY thing wrong is the judge is not ``clean`` (blocked / cannot_verify / still running /
    never ran / blocked-but-the-CAS-lost-the-race — CMX-239). Each must refuse, tier ``escalate``.

    Every other gate is mocked to pass, so this gate is the sole possible refusal: corrupt
    ``if judge_state != J_CLEAN`` to ``if False and …`` and an un-vetted run merges, turning
    this red. (The sibling ``test_merge_refuses_unless_the_judge_is_clean`` does NOT mock the
    downstream CI/mergeable gates, so it stays green under that mutation — it is not the guard.)"""
    _seed_run(repo, judge_state=judge_state)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "escalate"
    assert result.get("judge_state") == judge_state
    _assert_actionable(result)
    squash.assert_not_called()      # ⛔ nothing merged — the judge gate held


def test_merge_refuses_a_judge_clean_verdict_that_is_stale_relative_to_the_current_head(repo):
    """⚖️🕳️ The judge's ``clean`` is for a specific commit (``judge_sha``), not a standing
    approval of the branch. Base ``dev``, judge ``clean``, CI green, MERGEABLE — the ONLY
    thing wrong is that GitHub's live head commit differs from the one the judge actually
    verified (a new commit landed on the PR after the judge finished, or while a slow judge
    was still mid-run on an older head and its verdict got raced/overwritten). This must
    refuse: merging a commit the judge never saw is exactly the "presents as approved and
    mergeable" hole a stale verdict opens.

    Corrupt the ``judge_sha != ci.head_sha`` check to ``False and …`` and this goes green —
    the run merges on a commit the judge is silent about."""
    _seed_run(repo, judge_sha="deadbeef0001")
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks",
                      return_value=CIStatus(CI_PASSING, head_sha="cafef00d0002")), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "escalate"
    assert "deadbeef0001" in result["error"]
    assert "cafef00d0002" in result["error"]
    squash.assert_not_called()      # ⛔ nothing merged — the stale-verdict gate held


def test_merge_proceeds_when_the_judge_sha_matches_the_live_head(repo):
    """The sibling of the staleness refusal above: when ``judge_sha`` DOES match the PR's
    live head, the new check must not add a spurious refusal — the ordinary happy path
    (already covered without an explicit sha by ``test_merge_when_the_whole_gate_holds``)
    still holds when the sha IS recorded and it agrees with GitHub."""
    _seed_run(repo, judge_sha="deadbeef0001")
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks",
                      return_value=CIStatus(CI_PASSING, head_sha="deadbeef0001")), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is True
    squash.assert_called_once()


def test_merge_proceeds_when_judge_sha_is_unset_even_though_the_live_head_is_known(repo):
    """The conservatism the staleness gate explicitly claims: an UNSET ``judge_sha`` (an
    older row, or a judge that never stamped one) is NOT positive evidence of staleness, so
    it must never refuse on that basis alone — even when GitHub's live head sha IS known and
    non-empty. ``test_merge_when_the_whole_gate_holds`` leaves ``ci.head_sha`` at its default
    ``None`` too, so it can't tell "unset judge_sha is deliberately ignored" apart from
    "neither sha was ever read" — this test pins a REAL head_sha against the unset judge_sha.

    Corrupt ``judge_sha = run.get("judge_sha")`` to ``run.get("judge_sha") or "0000unstamped"``
    and this goes red: the fabricated sha never matches the live head, so a run this gate
    already trusted gets refused."""
    _seed_run(repo)      # judge_sha defaults to None — never stamped
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks",
                      return_value=CIStatus(CI_PASSING, head_sha="cafef00d0002")), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is True
    squash.assert_called_once()


def test_merge_proceeds_when_the_live_head_sha_is_unset_even_though_judge_sha_is_known(repo):
    """The other half of the same conservatism: an UNREADABLE/unset live head
    (``ci.head_sha`` is ``None`` — GitHub could not report one) is NOT positive evidence of
    staleness either, so it must never refuse on that basis alone — even when ``judge_sha``
    IS stamped and non-empty. Every other proceed-path test leaves ``ci.head_sha`` at its
    default ``None`` *and* leaves ``judge_sha`` unset too, so none of them can tell "an unset
    live head is deliberately ignored" apart from "neither sha was ever read" — this test
    pins a REAL ``judge_sha`` against the unset live head.

    Corrupt ``if judge_sha and ci.head_sha and judge_sha != ci.head_sha:`` to
    ``if judge_sha and judge_sha != ci.head_sha:`` and this goes red: a stamped judge_sha
    never equals the unset ``None`` head, so a run this gate already trusted gets refused."""
    _seed_run(repo, judge_sha="deadbeef0001")
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks",
                      return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is True
    squash.assert_called_once()


@pytest.mark.parametrize("ci", [CI_FAILING, CI_PENDING, CI_NONE, CI_UNKNOWN])
def test_merge_requires_green_CI(repo, ci):
    """CMX-242: this is THE motivating case — a `chela merge` refused on CI_NONE with no
    other guidance is exactly what produced this ticket. Every CI refusal now names a
    recommendation and options, and for CI_NONE specifically the options must name the
    empty-commit trap (an empty commit changes the sha and makes the judge's clean verdict
    stale — the one consequence a caller cannot infer from the bare error)."""
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(ci)), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False and result["ci_state"] == ci
    _assert_actionable(result)
    if ci == CI_NONE:
        assert any("empty commit" in o for o in result["options"])
    squash.assert_not_called()


@pytest.mark.parametrize("pr_state", ["closed", "merged", "MERGED"])
def test_merge_refuses_a_pr_that_is_not_open(repo, pr_state):
    """The ``pr_state != 'open'`` refusal IN ISOLATION — base dev, judge clean, CI green,
    MERGEABLE, and the ONLY thing wrong is GitHub reports the PR already closed/merged. This
    is the ``not-open`` half of the mergeability gate: the existing ``test_merge_requires_MERGEABLE``
    always feeds ``("open", …)``, so it never exercises (nor guards) this branch. Corrupt
    ``if pr_state and pr_state != "open"`` and an already-closed PR "merges" — turning this red."""
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=(pr_state, "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "escalate"
    assert result.get("pr_state") == pr_state
    _assert_actionable(result)
    squash.assert_not_called()      # ⛔ nothing merged — the open-PR gate held


@pytest.mark.parametrize("mergeable", ["CONFLICTING", "UNKNOWN", None])
def test_merge_requires_MERGEABLE(repo, mergeable):
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", mergeable)), \
         patch.object(contract, "_squash_merge") as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    _assert_actionable(result)
    squash.assert_not_called()


@pytest.mark.parametrize("status", ["running", "changes_requested", "escalated", "done"])
def test_merge_refuses_a_run_not_awaiting_review_even_when_all_else_passes(repo, status):
    """The ``status != 'awaiting_review'`` gate IN ISOLATION — base ``dev``, judge clean, CI
    green, MERGEABLE, so the ONLY thing wrong is the run is not under review. It must refuse.

    Every downstream gate is mocked to pass, so this gate is the sole possible refusal: corrupt
    ``if run["status"] != "awaiting_review"`` to ``if False and …`` and the merge goes through,
    turning this red. Without the downstream mocks the flow would still hit the real
    ``_read_pr_base`` (None with no gh) and refuse on the base gate — staying green under the
    mutation and guarding nothing."""
    _seed_run(repo, status=status)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "escalate"
    assert status in result["error"]      # the refusal names the offending status
    _assert_actionable(result)
    squash.assert_not_called()      # ⛔ nothing merged — the status gate held


def test_merge_refuses_an_unknown_run(repo):
    result = contract.merge("nope")
    assert result["ok"] is False
    _assert_actionable(result)


def test_merge_refuses_a_run_with_no_pr_url(repo):
    _seed_run(repo, pr_url="")
    result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "escalate"
    _assert_actionable(result)


def test_merge_refuses_when_the_workflow_repo_dir_is_missing(tmp_path):
    """`workflow_path`'s parent must be a real dir — a workflow whose repo was deleted (or
    never existed on this machine) is refused, not crashed into, and still names next steps."""
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, "
            "started_at, attempt, pr_url, pr_state, judge_state, branch_name, worktree_path) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("t1", str(tmp_path / "gone" / "WORKFLOW.md"), "t", "awaiting_review", "@9",
             dispatcher._now(), 1, "https://github.com/o/r/pull/1", "open", "clean", "cmx-1", None),
        )
        conn.commit()
    result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "escalate"
    _assert_actionable(result)


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
    _assert_actionable(result)
    # A gate-passed-but-merge-failed is auditable too.
    assert event_log.read(types=["orchestrator.merge_failed"])["events"]


# --- the ATTENDED-LEASE: the auto-orchestrator may ACT only while a human is attending ---
#
# The launch gate (autolaunch.should_launch) only decides whether the orchestrator STARTS. The
# safety property the contract requires is that its autonomous ACTIONS stay attended: an
# auto-launched orchestrator may `chela merge` ONLY while a human's attended-lease is live, and
# when the lease is stale/absent it must escalate instead. These prove the ACTION-time gate — and,
# crucially, that a HUMAN's own merge is never touched by it (the human IS the attendance).


def _write_expired_lease() -> None:
    """A lease file whose window has already closed — present on disk, but NOT active."""
    lease.path().parent.mkdir(parents=True, exist_ok=True)
    past = time.time() - 100
    lease.path().write_text(
        json.dumps({"by": "h", "created_at": past - 600, "expires_at": past}), encoding="utf-8")
    assert lease.active() is None      # sanity: this fixture really is a stale lease


@pytest.mark.parametrize("make_stale", [
    lambda: None,                      # absent — no lease file at all
    _write_expired_lease,              # present but expired
])
def test_auto_orchestrator_merge_is_REFUSED_without_a_live_lease(repo, make_stale):
    """🔴 THE ACTION-GATE — the load-bearing guard the brief named. The auto-launched
    orchestrator (actor=auto-orchestrator) with a stale/absent lease, and an otherwise fully
    MERGEABLE run (base dev, judge clean, CI green, MERGEABLE), must be REFUSED — it has to
    escalate, not act, because no human is attending. Every downstream gate is mocked to pass,
    so the lease gate is the SOLE possible refusal: delete it (ignore the actor's stale lease on
    the merge path) and the merge goes through — turning this red."""
    _seed_run(repo)
    make_stale()
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1", actor=config.AUTO_ORCHESTRATOR_ACTOR)
    assert result["ok"] is False
    assert result["tier"] == "escalate"
    assert result.get("actor") == config.AUTO_ORCHESTRATOR_ACTOR
    _assert_actionable(result)
    squash.assert_not_called()         # ⛔ nothing merged — the unattended orchestrator was stopped


def test_auto_orchestrator_actor_is_read_from_the_environment(repo, monkeypatch):
    """The actor stamp is not just a param: the launched window exports CHELA_ACTOR, and the gate
    reads it live. Stamp it in the env (no explicit arg), no lease → the same refusal. Corrupt
    ``_actor`` to ignore the env and this goes red."""
    _seed_run(repo)
    monkeypatch.setenv(config.ACTOR_ENV, config.AUTO_ORCHESTRATOR_ACTOR)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")      # no actor= arg — it must come from the env
    assert result["ok"] is False
    assert result["tier"] == "escalate"
    _assert_actionable(result)
    squash.assert_not_called()


def test_auto_orchestrator_MAY_merge_while_the_lease_is_live(repo):
    """The other side of the gate: when a human IS attending (lease active), the auto-orchestrator
    merges normally — and the provenance log records that the auto-orchestrator was the actor."""
    _seed_run(repo)
    lease.grant(ttl_seconds=600)           # a human is attending right now
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "beef"}) as squash:
        result = contract.merge("t1", actor=config.AUTO_ORCHESTRATOR_ACTOR)
    assert result["ok"] is True
    squash.assert_called_once()
    payload = event_log.read(types=["orchestrator.merge"])["events"][0]["payload"]
    assert payload["actor"] == config.AUTO_ORCHESTRATOR_ACTOR


def test_a_HUMAN_merge_is_NOT_gated_by_the_lease(repo):
    """🔴 THE REVERSE GUARD — the human's own `chela merge` must be UNAFFECTED by the lease. A
    human carries no actor stamp, so with no lease at all the merge still proceeds (the human's
    presence IS the attendance). Corrupt the gate to apply to human merges too (drop the actor
    check) and this goes red — the lease would then block the very person who is attending."""
    _seed_run(repo)
    assert lease.active() is None          # no lease — a human never needs one
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "cafe"}) as squash:
        result = contract.merge("t1")      # actor defaults to "" (a human) — no stamp
    assert result["ok"] is True
    squash.assert_called_once()
    payload = event_log.read(types=["orchestrator.merge"])["events"][0]["payload"]
    assert payload["actor"] == "human"


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


# --- PER-WORKFLOW AUTONOMOUS MERGE BASE (CMX-103) --------------------------------------
#
# One repo's trunk convention must never leak into another's: the allowed autonomous base
# is resolved PER RUN from its OWN dispatching workflow's committed `workspace.base_branch`
# (lean-alpha declares `main`; chelamux declares `dev`), and a FORBIDDEN base is reachable
# ONLY as that workflow's own explicit, committed declaration — never via the env fallback,
# never on another workflow's behalf, and never for chela's own control repo.


def _repo_declaring(tmp_path: Path, base_branch: str, *, project_key: str = "CMX") -> Path:
    """A workflow dir whose WORKFLOW.md COMMITS to ``workspace.base_branch`` — the per-repo
    declaration ``contract._declared_base_branch`` resolves the allowed autonomous base from."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text(
        f"---\nproject_key: {project_key}\nworkspace:\n  base_branch: {base_branch}\n---\nbody\n"
    )
    return tmp_path


def test_declared_base_branch_is_none_when_workflow_path_is_falsy():
    assert contract._declared_base_branch(None) is None
    assert contract._declared_base_branch("") is None


def test_declared_base_branch_reads_the_committed_declaration(tmp_path):
    repo = _repo_declaring(tmp_path, "main")
    assert contract._declared_base_branch(str(repo / "WORKFLOW.md")) == "main"


def test_declared_base_branch_is_none_when_the_workflow_declares_nothing(tmp_path):
    wf = tmp_path / "WORKFLOW.md"
    wf.write_text("---\nproject_key: CMX\n---\nbody\n")
    assert contract._declared_base_branch(str(wf)) is None


def test_is_control_repo_detects_chelas_own_source_dir():
    control = Path(contract.__file__).resolve().parent.parent
    assert contract._is_control_repo(str(control)) is True


def test_is_control_repo_is_false_for_an_unrelated_dir(tmp_path):
    assert contract._is_control_repo(str(tmp_path)) is False


def test_a_workflow_declaring_main_may_merge_to_its_own_declared_main(tmp_path):
    """🔴 THE LEAN-ALPHA REPRO — a run whose OWN dispatching workflow commits to
    ``base_branch: main`` merges to that ``main``. Corrupt the resolver to ignore the
    declaration (always fall back to the global ``dev`` base) and this goes red."""
    repo = _repo_declaring(tmp_path, "main")
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="main"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "lap4"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is True
    assert result["base"] == "main"
    squash.assert_called_once()


def test_a_workflow_declaring_dev_is_still_forbidden_from_main(tmp_path):
    """🔴 THE SACROSANCT INVARIANT — a run whose own workflow declares ``dev`` may NEVER
    reach a PR based on ``main``, even with everything else green. Corrupt (let a
    non-matching declared base widen the forbidden check anyway) and this goes red."""
    repo = _repo_declaring(tmp_path, "dev")
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="main"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "never"
    squash.assert_not_called()


def test_env_override_cannot_widen_the_forbidden_base(tmp_path):
    """🔴 ENV CANNOT WIDEN — even with ``CHELA_MERGE_BASE=main`` (simulated here by patching
    the module constant it seeds), a run whose own workflow declares ``dev`` is still
    refused from ``main``. Only a COMMITTED per-workflow declaration may reach a forbidden
    base — never the env fallback. Corrupt (let ``AUTONOMOUS_BASE`` win over the declared
    base check) and this goes red."""
    repo = _repo_declaring(tmp_path, "dev")
    _seed_run(repo)
    with patch.object(contract, "AUTONOMOUS_BASE", "main"), \
         patch.object(contract, "_read_pr_base", return_value="main"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "never"
    squash.assert_not_called()


def test_env_main_cannot_widen_when_the_workflow_declares_nothing(tmp_path):
    """🔴 ENV CANNOT WIDEN AN UNSET DECLARATION — the most dangerous env case: a workflow
    that declares NO base_branch (declared_base None) must fall back to AUTONOMOUS_BASE for
    the *allowed* base, but a FORBIDDEN base is reachable ONLY via a COMMITTED declaration —
    never via the env fallback. So even with CHELA_MERGE_BASE=main (patched here) and a
    declaration-less workflow, a PR based on ``main`` is refused. Corrupt (key the forbidden
    bypass on ``allowed_base`` instead of ``declared_base``) and this goes red."""
    wf_dir = tmp_path
    (wf_dir / "WORKFLOW.md").write_text("---\nproject_key: CMX\n---\nbody\n")  # declares nothing
    _seed_run(wf_dir)
    with patch.object(contract, "AUTONOMOUS_BASE", "main"), \
         patch.object(contract, "_read_pr_base", return_value="main"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "never"
    squash.assert_not_called()


def test_a_declaration_does_not_widen_a_different_workflows_runs(tmp_path):
    """🔴 SCOPED TO ITS OWN RUNS — workflow A declares ``main``; the run under test is
    dispatched by workflow B (declares ``dev``). A's declaration must never apply to B's
    run. Corrupt (resolve the declared base from some other/cached workflow instead of THIS
    run's own ``workflow_path``) and this goes red."""
    _repo_declaring(tmp_path / "a", "main")               # a sibling workflow — unused here
    workflow_b = _repo_declaring(tmp_path / "b", "dev")
    _seed_run(workflow_b)
    with patch.object(contract, "_read_pr_base", return_value="main"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "never"
    squash.assert_not_called()


def test_unreadable_workflow_fails_closed_to_the_fallback_base(repo):
    """🔴 UNREADABLE FAILS CLOSED — an unparseable ``WORKFLOW.md`` (the ``repo`` fixture's
    front-matter-less stub) declares nothing, so the allowed base falls back to
    ``AUTONOMOUS_BASE`` (``dev``) — never to whatever the live PR base happens to be. A PR
    based on ``main`` is still refused. Corrupt (fail OPEN — treat an unreadable workflow as
    licensing whatever base the PR already has) and this goes red."""
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="main"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "never"
    squash.assert_not_called()


def test_a_workflow_declaring_dev_merges_normally_to_dev(tmp_path):
    """The happy path stays intact: a workflow that commits to ``dev`` and a PR based on
    ``dev`` merges exactly as before. Corrupt the resolver (e.g. always refuse) and this
    goes red."""
    repo = _repo_declaring(tmp_path, "dev")
    _seed_run(repo)
    with patch.object(contract, "_read_pr_base", return_value="dev"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is True
    assert result["base"] == "dev"
    squash.assert_called_once()


def test_control_repo_self_protection_still_forbids_its_own_declared_main(tmp_path):
    """Defense in depth (not today's live gap — chelamux's own WORKFLOW.md declares
    ``dev``): even if chela's OWN control repo declared ``base_branch: main``, that
    declaration must never be honored to widen ITS OWN autonomous merge into a forbidden
    base. Corrupt (drop the control-repo check from the widen condition) and this goes
    red."""
    repo = _repo_declaring(tmp_path, "main")
    _seed_run(repo)
    with patch.object(contract, "_is_control_repo", return_value=True), \
         patch.object(contract, "_read_pr_base", return_value="main"), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(CI_PASSING)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", "MERGEABLE")), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "x"}) as squash:
        result = contract.merge("t1")
    assert result["ok"] is False
    assert result["tier"] == "never"
    squash.assert_not_called()
