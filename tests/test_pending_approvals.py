"""⚖️ CMX-103 — Pending approvals on the dashboard, without Telegram.

A dashboard-only operator had no way to learn an override request was waiting. These pin:

* ``/api/approvals`` lists the PENDING requests and hides decided / expired ones;
* Approve / Deny go through the dashboard's ONE decide call (the same one the
  ``/override/<id>`` page makes) into :func:`chela.overrides.decide`, and record
  ``decided_by: "dashboard"``;
* Approve needs the confirm step, and a request chela knows cannot merge (head moved, CI not
  green, expired) is never approved — it says why;
* a share guest (the relay-served joiner) gets no list and cannot decide;
* filing works with Telegram unset, and the CLI names the absolute dashboard URL and
  ``chela merge-approve``;
* the SSE loop pushes an ``approvals`` frame when a request is filed.

GitHub is stubbed exactly as in ``test_merge_override.py``; the request store is real.
"""
from __future__ import annotations

import itertools
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import approvals, config, contract, dispatcher, mergegate, overrides
from chela.dispatcher import CI_FAILING, CI_PASSING, CIStatus
from chela.judge import J_BLOCKED

pytest.importorskip("flask")
from chela.dashboard import app as dash  # noqa: E402

HEAD = "a" * 40
MOVED = "c" * 40
PR = "https://github.com/o/r/pull/1"
RELAY = "wss://relay.example.test"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.delenv(config.ACTOR_ENV, raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv(overrides.TTL_ENV, raising=False)
    monkeypatch.delenv("CHELA_DASHBOARD_URL", raising=False)
    monkeypatch.setattr(contract.notify, "enabled", lambda: False)
    monkeypatch.setattr(overrides.notify, "enabled", lambda: False)
    monkeypatch.setattr(config, "COLLAB_RELAY", RELAY)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text("# wf\n")
    return tmp_path


@pytest.fixture
def client():
    return dash.app.test_client()


def _seed(repo: Path) -> None:
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, started_at, "
            "attempt, pr_url, pr_state, judge_state, judge_sha, branch_name, pr_head_sha, "
            "pr_checks, pr_mergeable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("t1", str(repo / "WORKFLOW.md"), "t", "changes_requested", "@9",
             dispatcher._now(), 1, PR, "open", J_BLOCKED, "b" * 40, "cmx-1", HEAD,
             CI_PASSING, "MERGEABLE"),
        )
        conn.commit()


def _set_run(**fields) -> None:
    cols = ", ".join(f"{k}=?" for k in fields)
    with dispatcher._db() as conn:
        conn.execute(f"UPDATE runs SET {cols} WHERE task_id='t1'", tuple(fields.values()))
        conn.commit()


@contextmanager
def _github(ci=CI_PASSING, mergeable="MERGEABLE", head=HEAD, base="dev"):
    with patch.object(contract, "_read_pr_base", return_value=base), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(ci, head_sha=head)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", mergeable)), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "m" * 40}) as squash:
        yield squash


def _file(reason="judge flaked on an unrelated test") -> str:
    with _github() as squash:
        result = contract.merge("t1", override=True, reason=reason)
    squash.assert_not_called()
    assert result["ok"] and result["pending"], result
    return result["request_id"]


def _listed(client, **kw) -> list[dict]:
    resp = client.get("/api/approvals", **kw)
    assert resp.status_code == 200
    return resp.get_json()["approvals"]


# --- the list --------------------------------------------------------------------------

def test_the_list_shows_pending_and_hides_decided_and_expired(repo, client):
    """🔴 GUARD: only PENDING, un-expired requests are listed — a denied one and one past its
    deadline are gone. Drop the state filter in approvals.override_rows → RED."""
    _seed(repo)
    live = _file()
    denied = overrides.create({"task_id": "t1", "label": "cmx-1", "head_sha": "d" * 40})
    overrides.decide(denied["id"], False, by="me")
    stale = overrides.create({"task_id": "t1", "label": "cmx-1", "head_sha": "e" * 40})
    overrides.set_fields(stale["id"], deadline=1.0)
    rows = _listed(client)
    assert [r["id"] for r in rows] == [live], rows
    row = rows[0]
    assert row["kind"] == "override"
    assert row["what"] == f"override-merge cmx-1 past {J_BLOCKED} on {HEAD[:12]}"
    assert row["reason"] == "judge flaked on an unrelated test"
    assert row["actor"] and row["pr_url"] == PR
    assert row["blocked"] is None
    assert 0 < row["expires_in_s"] <= overrides.ttl() and row["age_s"] >= 0


def test_an_expired_request_is_not_listed_even_before_a_sweep(repo):
    """🔴 GUARD: the deadline filter in approvals.pending — drop it and a dead request shows
    an Approve button that can only fail → RED."""
    _seed(repo)
    rid = _file()
    overrides.set_fields(rid, deadline=1.0)
    assert approvals.pending() == []


# --- approve / deny through the shared decide path ---------------------------------------

def test_approve_and_deny_go_through_the_one_dashboard_decide_call(repo, client, monkeypatch):
    """🔴 GUARD: the list's Approve and the /override/<id> page both call
    `_dashboard_decide` → overrides.decide. Short-circuit either into a second path → RED."""
    _seed(repo)
    seen = []
    real = overrides.decide

    def spy(request_id, approve, by, **kw):
        seen.append((request_id, approve, by, kw.get("channel")))
        return real(request_id, approve, by, **kw)

    monkeypatch.setattr(overrides, "decide", spy)
    rid = _file()
    with _github() as squash:
        resp = client.post(f"/api/approvals/{rid}/approve", json={"confirm": True})
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["state"] == overrides.MERGED
    squash.assert_called_once()
    assert seen == [(rid, True, "dashboard", "dashboard")]
    rec = overrides.load(rid)
    assert rec["state"] == overrides.MERGED and rec["decided_by"] == "dashboard"

    other = _file()
    with _github() as squash:
        page = client.post(f"/override/{other}", data={"decision": "deny"})
    assert page.status_code == 200
    squash.assert_not_called()
    assert seen[-1] == (other, False, "dashboard", "dashboard")


def test_deny_from_the_list_denies_and_records_the_dashboard(repo, client):
    """🔴 GUARD: Deny reaches decide as approve=False and is audited as the dashboard.
    Hard-wire the action to approve → it merges → RED."""
    _seed(repo)
    rid = _file()
    with _github() as squash:
        resp = client.post(f"/api/approvals/{rid}/deny", json={})
    assert resp.status_code == 200 and resp.get_json()["state"] == overrides.DENIED
    squash.assert_not_called()
    rec = overrides.load(rid)
    assert rec["state"] == overrides.DENIED and rec["decided_by"] == "dashboard"
    assert _listed(client) == []


def test_approve_without_the_confirm_step_merges_nothing(repo, client):
    """🔴 GUARD: a single tap (no `confirm: true`) never merges — drop the check → RED."""
    _seed(repo)
    rid = _file()
    with _github() as squash:
        resp = client.post(f"/api/approvals/{rid}/approve", json={})
    assert resp.status_code == 400
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.PENDING


# --- a stale request cannot be approved ---------------------------------------------------

def test_red_ci_shows_why_and_cannot_be_approved(repo, client):
    """🔴 GUARD: a request whose CI went red is listed WITH the reason and an approve is
    refused before decide — the request stays pending. Drop the `blocked` refusal → RED."""
    _seed(repo)
    rid = _file()
    _set_run(pr_checks=CI_FAILING)
    row, = _listed(client)
    assert row["blocked"] == "CI is failing, not green"
    with _github() as squash:
        resp = client.post(f"/api/approvals/{rid}/approve", json={"confirm": True})
    assert resp.status_code == 409 and "CI is failing" in resp.get_json()["error"]
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_a_conflicting_pr_shows_why(repo, client):
    _seed(repo)
    _file()
    _set_run(pr_mergeable="CONFLICTING")
    row, = _listed(client)
    assert "not MERGEABLE" in row["blocked"]


def test_a_moved_head_cannot_be_approved(repo):
    """🔴 GUARD: the head-moved check in approvals._override_blocked. Remove it and a request
    for a dead commit offers Approve → RED."""
    _seed(repo)
    rid = _file()
    _set_run(pr_head_sha=MOVED)
    row = approvals.find(rid)
    assert row is not None and "head moved" in row["blocked"]


def test_a_moved_head_or_expired_request_is_refused_by_the_api(repo, client):
    _seed(repo)
    rid = _file()
    _set_run(pr_head_sha=MOVED)
    with _github(head=MOVED) as squash:
        resp = client.post(f"/api/approvals/{rid}/approve", json={"confirm": True})
    assert resp.status_code in (404, 409)
    squash.assert_not_called()
    assert overrides.load(rid)["state"] != overrides.MERGED

    _set_run(pr_head_sha=HEAD)
    rid2 = _file()
    overrides.set_fields(rid2, deadline=1.0)
    with _github() as squash:
        resp = client.post(f"/api/approvals/{rid2}/approve", json={"confirm": True})
    assert resp.status_code == 404
    squash.assert_not_called()


# --- a share guest never sees controls ----------------------------------------------------

def test_a_share_guest_gets_no_list_and_cannot_decide(repo, client):
    """🔴 GUARD: a request from the relay-served joiner (a share guest) gets
    `can_decide: false` and no rows, and its POST is refused before decide. Drop the
    `_share_guest_request()` checks → RED."""
    _seed(repo)
    rid = _file()
    # The joiner's Referer: no Origin, so the same-origin check alone would let it through —
    # only the guest check refuses it.
    guest = {"Referer": "https://relay.example.test/join"}
    data = client.get("/api/approvals", headers=guest).get_json()
    assert data == {"approvals": [], "can_decide": False}
    owner = client.get("/api/approvals").get_json()
    assert owner["can_decide"] is True and len(owner["approvals"]) == 1
    with _github() as squash:
        for action in ("approve", "deny"):
            resp = client.post(f"/api/approvals/{rid}/{action}", json={"confirm": True},
                               headers=guest)
            assert resp.status_code == 403
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_a_cross_site_post_cannot_decide(repo, client):
    _seed(repo)
    rid = _file()
    with _github() as squash:
        resp = client.post(f"/api/approvals/{rid}/approve", json={"confirm": True},
                           headers={"Origin": "https://evil.example",
                                    "Sec-Fetch-Site": "cross-site"})
    assert resp.status_code == 403
    squash.assert_not_called()


def test_a_claude_session_curling_the_approvals_route_is_denied():
    assert mergegate._self_approval(
        ["curl", "-X", "POST", "http://127.0.0.1:5001/api/approvals/override-abc/approve"]
    ).deny


# --- filing never depends on Telegram ----------------------------------------------------

def test_filing_without_telegram_prints_the_dashboard_url_and_merge_approve(
        repo, capsys, monkeypatch):
    """🔴 GUARD: with no Telegram configured the request is still filed, and the CLI names
    the ABSOLUTE dashboard URL (CHELA_DASHBOARD_URL) and `chela merge-approve`. Drop the URL
    from contract.approve_hint → RED."""
    from chela import main
    for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CHELA_DASHBOARD_URL", "https://dash.example.test/")
    _seed(repo)
    with _github() as squash:
        main.cmd_merge(SimpleNamespace(run="t1", reason="flaky judge", override=True))
    squash.assert_not_called()
    rid, = [r["id"] for r in overrides.pending()]
    out = capsys.readouterr().out
    assert f"https://dash.example.test/override/{rid}" in out
    assert f"chela merge-approve {rid}" in out


def test_the_dashboard_url_falls_back_to_the_bound_address(monkeypatch):
    monkeypatch.delenv("CHELA_DASHBOARD_URL", raising=False)
    monkeypatch.setenv("CHELA_DASH_HOST", "0.0.0.0")
    monkeypatch.setattr(config, "live_dashboard_port", lambda: 5123)
    assert config.dashboard_url() == "http://127.0.0.1:5123"
    assert contract.approve_hint("override-x").startswith(
        "Approve on the dashboard: http://127.0.0.1:5123/override/override-x")


# --- live via SSE -------------------------------------------------------------------------

def test_filing_a_request_pushes_an_sse_approvals_frame(repo, monkeypatch):
    """🔴 GUARD: the SSE loop diffs the pending approvals — remove the diff and a live page
    never learns a request arrived until its poll → RED."""
    monkeypatch.setattr(dash, "_sse_windows_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_runs_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_terms_snapshot", lambda: set())
    monkeypatch.setattr(dash, "_sse_log_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_orchestrator_snapshot", lambda: {})
    monkeypatch.setattr(dash.time, "sleep", lambda _s: None)
    ticks = itertools.count(0.0, dash.SSE_KEEPALIVE_INTERVAL + 1.0)
    monkeypatch.setattr(dash.time, "monotonic", lambda: next(ticks))
    _seed(repo)
    stream = dash._sse_stream()
    assert next(stream).startswith("event: hello")
    _file()
    frame = next(stream)
    assert frame.startswith("event: approvals\n"), frame
    assert '"pending": 1' in frame
