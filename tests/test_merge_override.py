"""⚖️🔓 CMX-389 — `chela merge --override`: the ONE way past the judge, and it is not
autonomous. It merges only after an operator approves it (the gate-answer rendezvous,
`chela.gateanswer`), a timeout is a DENY, and the approval is audited — the event log, the
run's review history, and the squash body. CI red / not-mergeable still refuse.

GitHub is stubbed (`_read_pr_base`, `_read_pr_checks`, `_read_pr_status`, `_squash_merge`);
the approval is REAL — a second thread answers through the same files the dashboard's
confirm page and `chela merge-approve` write, under the sandboxed `CHELA_DIR` (conftest).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, contract, dispatcher, event_log, gateanswer
from chela.dispatcher import CI_FAILING, CI_PASSING, CIStatus
from chela.judge import J_BLOCKED

HEAD = "a" * 40


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.delenv(config.ACTOR_ENV, raising=False)
    monkeypatch.setattr(contract.notify, "enabled", lambda: False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text("# wf\n")
    return tmp_path


def _seed(repo: Path, *, status: str = "changes_requested", judge_state: str = J_BLOCKED) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, started_at, "
            "attempt, pr_url, pr_state, judge_state, judge_sha, branch_name) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("t1", str(repo / "WORKFLOW.md"), "t", status, "@9", dispatcher._now(), 1,
             "https://github.com/o/r/pull/1", "open", judge_state, "b" * 40, "cmx-1"),
        )
        conn.commit()


def _gh(ci=CI_PASSING, mergeable="MERGEABLE"):
    """The live-GitHub stubs every test here shares."""
    return (patch.object(contract, "_read_pr_base", return_value="dev"),
            patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(ci, head_sha=HEAD)),
            patch.object(dispatcher, "_read_pr_status", return_value=("open", mergeable)))


def _operator(decision: bool | None, by: str = "dashboard", timeout: float = 5.0):
    """A second thread playing the operator: waits for the request to appear on disk, then
    answers it (``None`` = never answers). Returns the thread and what it saw."""
    seen: dict = {}

    def run():
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            pending = gateanswer.pending_approvals()
            if pending:
                seen["request"] = pending[0]
                if decision is not None:
                    seen["answer"] = gateanswer.answer_approval(pending[0]["id"], decision, by)
                return
            time.sleep(0.02)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t, seen


def _merge(**kw):
    a, b, c = _gh(**{k: kw.pop(k) for k in ("ci", "mergeable") if k in kw})
    with a, b, c, patch.object(contract, "_squash_merge",
                               return_value={"ok": True, "merge_commit_sha": "m" * 40}) as squash:
        result = contract.merge("t1", **kw)
    return result, squash


def test_override_without_approval_refuses(repo):
    """🔴 GUARD: nobody approves → the wait times out → REFUSED, nothing merged, and the
    denial is on the log. Corrupt the override to skip the wait (treat any request as
    approved) and this merges → RED."""
    _seed(repo)
    op, seen = _operator(None)
    result, squash = _merge(override=True, reason="judge flaked", approval_wait=0.4)
    op.join()
    assert "request" in seen, "the override never even asked the operator"
    assert result["ok"] is False and result["tier"] == "escalate"
    assert "not approved" in result["error"]
    squash.assert_not_called()
    assert event_log.read(types=["orchestrator.merge_override_denied"])["events"]
    assert not event_log.read(types=["orchestrator.merge_override"])["events"]
    assert gateanswer.pending_approvals() == []            # the rendezvous was torn down
    assert dispatcher.merge_overrides(dispatcher.resolve_run("t1")) == []


def test_override_that_the_operator_denies_refuses(repo):
    _seed(repo)
    op, seen = _operator(False, by="terminal:op")
    result, squash = _merge(override=True, reason="judge flaked", approval_wait=5)
    op.join()
    assert seen["answer"] == (True, "denied")
    assert result["ok"] is False and "DENIED by terminal:op" in result["error"]
    squash.assert_not_called()


def test_override_with_approval_merges_and_writes_the_audit(repo):
    """🔴 GUARD: approved → merged, with the audit on the event log (who approved, the head
    sha, the judge state it overrode, the reason), on the run's review history, and the
    reason in the squash body — bound to the approved head via --match-head-commit."""
    _seed(repo)
    op, seen = _operator(True, by="dashboard")
    result, squash = _merge(override=True, reason="judge flaked on an unrelated test",
                            approval_wait=5)
    op.join()
    assert result["ok"] is True, result
    squash.assert_called_once()
    kwargs = squash.call_args.kwargs
    assert "judge flaked on an unrelated test" in kwargs["body"]
    assert kwargs["match_head"] == HEAD

    audit, = event_log.read(types=["orchestrator.merge_override"])["events"]
    payload = audit["payload"]
    assert payload["approved_by"] == "dashboard"
    assert payload["head_sha"] == HEAD
    assert payload["judge_state"] == J_BLOCKED
    assert payload["reason"] == "judge flaked on an unrelated test"
    assert payload["request_id"] == seen["request"]["id"]

    history = dispatcher.merge_overrides(dispatcher.resolve_run("t1"))
    assert len(history) == 1 and history[0]["approved_by"] == "dashboard"
    assert history[0]["head_sha"] == HEAD

    merged, = event_log.read(types=["orchestrator.merge"])["events"]
    assert merged["payload"]["override"]["approved_by"] == "dashboard"


@pytest.mark.parametrize("ci,mergeable", [(CI_FAILING, "MERGEABLE"), (CI_PASSING, "CONFLICTING")])
def test_override_still_refuses_red_ci_or_not_mergeable(repo, ci, mergeable):
    """🔴 GUARD: the override skips the JUDGE only. Red CI / a conflict refuse before the
    operator is even asked."""
    _seed(repo)
    op, seen = _operator(True, timeout=0.5)
    result, squash = _merge(override=True, reason="x", approval_wait=5, ci=ci,
                            mergeable=mergeable)
    op.join()
    assert result["ok"] is False
    squash.assert_not_called()
    assert "request" not in seen, "an operator was asked to approve an unmergeable PR"


def test_override_requires_a_reason(repo):
    _seed(repo)
    result, squash = _merge(override=True, reason="  ", approval_wait=5)
    assert result["ok"] is False and "--reason" in result["error"]
    squash.assert_not_called()


def test_without_override_a_blocked_judge_still_refuses(repo):
    """The override is opt-in: the plain gate is exactly what it was."""
    _seed(repo, status="awaiting_review")
    result, squash = _merge(reason="x")
    assert result["ok"] is False and result.get("judge_state") == J_BLOCKED
    squash.assert_not_called()


def test_an_approval_cannot_land_on_an_expired_request():
    assert gateanswer.open_approval("override-deadbeef", "q?", 0.01, {})
    time.sleep(0.05)
    ok, why = gateanswer.answer_approval("override-deadbeef", True, "dashboard")
    assert ok is False and "no longer waiting" in why
    gateanswer.close_gate("override-deadbeef")


def test_the_dashboard_confirm_page_approves(repo):
    """The dashboard confirm (`/override/<id>`) answers through the same rendezvous — and
    refuses a cross-site POST."""
    pytest.importorskip("flask")
    from chela.dashboard.app import app
    assert gateanswer.open_approval("override-cafe", "Override-merge cmx-1?", 30,
                                    {"label": "cmx-1", "reason": "r"})
    try:
        client = app.test_client()
        page = client.get("/override/override-cafe")
        assert page.status_code == 200 and b"Approve override" in page.data
        cross = client.post("/override/override-cafe", data={"decision": "approve"},
                            headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"})
        assert cross.status_code == 403
        ok = client.post("/override/override-cafe", data={"decision": "approve"})
        assert ok.status_code == 200
        approved, by = gateanswer.wait_for_approval("override-cafe", 1)
        assert approved is True and by == "dashboard"
    finally:
        gateanswer.close_gate("override-cafe")
