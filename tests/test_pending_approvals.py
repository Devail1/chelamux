"""⚖️🔔 CMX-103 — "Pending approvals" on the dashboard: one-tap Approve/Deny without Telegram.

A dashboard-only operator had no way to learn an override request was waiting. Now:

* ``GET /api/overrides`` lists the PENDING requests (decided / expired ones never), each with
  what, who, why, age, time to expiry, and ``blocked`` — why it cannot be approved now;
* ``POST /api/override/<id>/<approve|deny>`` goes through the SAME decide path as the
  ``/override/<id>`` confirm page (``chela.overrides.decide``, channel ``dashboard``), refuses a
  cross-site POST (the collab relay's guest page included), and refuses an Approve without an
  explicit ``confirm``;
* the SSE stream pushes an ``overrides`` frame when the pending set changes;
* filing (``chela merge --override``) works with no Telegram configured and prints the
  dashboard's ABSOLUTE confirm URL and ``chela merge-approve``.

GitHub is stubbed exactly as in ``test_merge_override.py``; the request store is real, under
the sandboxed ``CHELA_DIR``.
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import config, contract, dispatcher, overrides
from chela.dispatcher import CI_FAILING, CI_PASSING, CI_PENDING, CIStatus
from chela.judge import J_BLOCKED

pytest.importorskip("flask")

HEAD = "a" * 40
MOVED = "c" * 40
PR = "https://github.com/o/r/pull/1"
RELAY = "https://chela-collab-relay.example.workers.dev"
REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.delenv(config.ACTOR_ENV, raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv(overrides.TTL_ENV, raising=False)
    # No Telegram bridge anywhere in this file — the whole point is it is not needed.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.delenv(config.DASHBOARD_URL_ENV, raising=False)
    monkeypatch.setattr(contract.notify, "enabled", lambda: False)
    monkeypatch.setattr(overrides.notify, "enabled", lambda: False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "WORKFLOW.md").write_text("# wf\n")
    return tmp_path


@pytest.fixture
def client():
    from chela.dashboard.app import app
    return app.test_client()


def _seed(repo: Path, **cols) -> None:
    row = {"pr_checks": None, "pr_mergeable": None, "pr_state": "open", "pr_head_sha": HEAD}
    row.update(cols)
    with dispatcher._db() as conn:
        conn.execute(
            "INSERT INTO runs (task_id, workflow_path, title, status, window_name, started_at, "
            "attempt, pr_url, pr_state, judge_state, judge_sha, branch_name, pr_head_sha, "
            "pr_checks, pr_mergeable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("t1", str(repo / "WORKFLOW.md"), "t", "changes_requested", "@9",
             dispatcher._now(), 1, PR, row["pr_state"], J_BLOCKED, "b" * 40, "cmx-1",
             row["pr_head_sha"], row["pr_checks"], row["pr_mergeable"]),
        )
        conn.commit()


def _set_run(**cols) -> None:
    with dispatcher._db() as conn:
        for k, v in cols.items():
            conn.execute(f"UPDATE runs SET {k}=? WHERE task_id='t1'", (v,))
        conn.commit()


@contextmanager
def _github(ci=CI_PASSING, mergeable="MERGEABLE", head=HEAD, base="dev"):
    with patch.object(contract, "_read_pr_base", return_value=base), \
         patch.object(dispatcher, "_read_pr_checks", return_value=CIStatus(ci, head_sha=head)), \
         patch.object(dispatcher, "_read_pr_status", return_value=("open", mergeable)), \
         patch.object(contract, "_squash_merge",
                      return_value={"ok": True, "merge_commit_sha": "m" * 40}) as squash:
        yield squash


def _request(reason="judge flaked on an unrelated test") -> str:
    with _github() as squash:
        result = contract.merge("t1", override=True, reason=reason)
    squash.assert_not_called()
    assert result["ok"] and result["pending"], result
    return result["request_id"]


def _bare(**meta) -> dict:
    """A request record with no run behind it (listing-only tests)."""
    return overrides.create({"task_id": "nope", "label": "cmx-9", "head_sha": HEAD,
                             "reason": "r", "actor": "orchestrator", **meta})


# --- the list ----------------------------------------------------------------------------

def test_the_list_shows_pending_and_hides_decided_and_expired(repo):
    """🔴 GUARD: only PENDING requests inside their TTL are listed — a denied one and one
    past its deadline (even unswept) never are."""
    live, denied, old = _bare(), _bare(), _bare()
    assert overrides.decide(denied["id"], False, "dashboard", channel="dashboard")["ok"]
    overrides.set_fields(old["id"], deadline=time.time() - 1)
    assert [r["id"] for r in overrides.listing()] == [live["id"]]


def test_the_api_lists_what_who_why_age_and_expiry(repo, client):
    _seed(repo)
    rid = _request(reason="judge timed out")
    rows = client.get("/api/overrides").get_json()["requests"]
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == rid and row["kind"] == "override" and row["label"] == "cmx-1"
    assert row["head_sha"] == HEAD and row["judge_state"] == J_BLOCKED and row["base"] == "dev"
    assert row["reason"] == "judge timed out" and row["actor"]
    assert row["age_s"] >= 0 and 0 < row["expires_in_s"] <= overrides.ttl()
    assert row["blocked"] is None


# --- one decide path ---------------------------------------------------------------------

def test_list_approve_and_deny_call_the_shared_decide_path(repo, client, monkeypatch):
    """🔴 GUARD: the list's Approve/Deny land in `overrides.decide` on the dashboard channel —
    the very call the `/override/<id>` page makes. A second approve path → RED."""
    calls = []
    monkeypatch.setattr(overrides, "decide", lambda rid, approve, by, channel="terminal", env=None:
                        calls.append((rid, approve, by, channel))
                        or {"ok": True, "state": overrides.MERGED if approve else overrides.DENIED})
    rid = overrides.new_request_id()
    assert client.post(f"/api/override/{rid}/approve", json={"confirm": True}).status_code == 200
    assert client.post(f"/api/override/{rid}/deny", json={}).status_code == 200
    client.post(f"/override/{rid}", data={"decision": "approve"})
    assert calls == [(rid, True, "dashboard", "dashboard"), (rid, False, "dashboard", "dashboard"),
                     (rid, True, "dashboard", "dashboard")]


def test_approve_from_the_list_merges_once_and_records_decided_by(repo, client):
    """🔴 GUARD: a dashboard decision is audited as `dashboard` (or the tailnet identity)."""
    _seed(repo)
    rid = _request()
    with _github() as squash:
        resp = client.post(f"/api/override/{rid}/approve", json={"confirm": True},
                           headers={"Tailscale-User-Login": "op@example.com"})
        again = client.post(f"/api/override/{rid}/approve", json={"confirm": True})
    assert resp.status_code == 200 and resp.get_json()["state"] == overrides.MERGED
    assert again.status_code == 409
    squash.assert_called_once()
    rec = overrides.load(rid)
    assert rec["state"] == overrides.MERGED and rec["decided_by"] == "dashboard:op@example.com"
    assert overrides.listing() == []


def test_deny_from_the_list_merges_nothing_and_records_decided_by(repo, client):
    _seed(repo)
    rid = _request()
    with _github() as squash:
        resp = client.post(f"/api/override/{rid}/deny", json={})
    squash.assert_not_called()
    assert resp.status_code == 200 and resp.get_json()["state"] == overrides.DENIED
    rec = overrides.load(rid)
    assert rec["state"] == overrides.DENIED and rec["decided_by"] == "dashboard"


def test_approve_without_an_explicit_confirm_is_refused(repo, client):
    """🔴 GUARD: Approve is a deliberate SECOND action — an approve POST without
    `confirm: true` (a mis-tap, a replayed form) merges nothing and leaves it pending."""
    _seed(repo)
    rid = _request()
    with _github() as squash:
        bare = client.post(f"/api/override/{rid}/approve", json={})
        truthy = client.post(f"/api/override/{rid}/approve", json={"confirm": "yes"})
    squash.assert_not_called()
    assert bare.status_code == 400 and truthy.status_code == 400
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_a_cross_site_post_from_the_collab_relay_page_is_refused(repo, client):
    """🔴 GUARD: a share/collab viewer's page (the relay's origin) cannot decide — the decide
    API refuses any cross-site POST, Approve and Deny alike."""
    _seed(repo)
    rid = _request()
    hdrs = {"Origin": RELAY, "Sec-Fetch-Site": "cross-site"}
    with _github() as squash:
        a = client.post(f"/api/override/{rid}/approve", json={"confirm": True}, headers=hdrs)
        d = client.post(f"/api/override/{rid}/deny", json={}, headers=hdrs)
    squash.assert_not_called()
    assert a.status_code == 403 and d.status_code == 403
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_the_share_viewer_page_carries_no_approval_controls():
    """🔴 GUARD: the guest-facing collab SPA (what a share viewer loads) never ships the
    Pending-approvals surface — no list, no Approve/Deny, no decide URL."""
    public = REPO_ROOT / "chela" / "collab-relay" / "public"
    files = [p for p in public.rglob("*") if p.is_file() and p.suffix in (".html", ".js", ".mjs")]
    assert files
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in ("/api/override", "/override/", "approvals.js", "pa-approve", "openApprovals"):
            assert needle not in text, f"{path.name} carries {needle!r}"


# --- stale requests: say why, never a button that fails ------------------------------------

@pytest.mark.parametrize("cols,why", [
    ({"pr_checks": CI_FAILING}, "CI is failing"),
    ({"pr_checks": CI_PENDING}, "CI is still running"),
    ({"pr_mergeable": "CONFLICTING"}, "CONFLICTING"),
    ({"pr_state": "closed"}, "the PR is closed"),
])
def test_a_stale_request_shows_why_it_cannot_be_approved(repo, client, cols, why):
    """🔴 GUARD: the list names why a request cannot be approved (red CI, CI still running, a
    conflict, a closed PR) — the UI then shows that instead of an Approve button."""
    _seed(repo)
    rid = _request()
    _set_run(**cols)
    row, = client.get("/api/overrides").get_json()["requests"]
    assert row["id"] == rid and row["blocked"] and why in row["blocked"]


def test_a_head_moved_in_the_row_is_stale_even_before_the_sweep(repo):
    _seed(repo)
    rid = _request()
    _set_run(pr_head_sha=MOVED)
    row, = overrides.listing()
    assert row["id"] == rid and "head moved" in row["blocked"]


def test_never_read_caches_do_not_block(repo):
    """An unread / unknown CI cache is not a refusal — Approve re-checks live."""
    _seed(repo, pr_checks=dispatcher.CI_UNKNOWN)
    _request()
    row, = overrides.listing()
    assert row["blocked"] is None


def test_a_moved_head_cannot_be_approved_from_the_list(repo, client):
    """🔴 GUARD: Approve on a request whose PR head moved merges nothing — the live re-check
    expires it — and it drops off the list."""
    _seed(repo)
    rid = _request()
    with _github(head=MOVED) as squash:
        resp = client.post(f"/api/override/{rid}/approve", json={"confirm": True})
    squash.assert_not_called()
    assert resp.status_code == 409 and resp.get_json()["state"] == overrides.EXPIRED
    assert overrides.listing() == []


def test_an_expired_request_cannot_be_approved_from_the_list(repo, client):
    _seed(repo)
    rid = _request()
    overrides.set_fields(rid, deadline=time.time() - 1)
    assert client.get("/api/overrides").get_json()["requests"] == []
    with _github() as squash:
        resp = client.post(f"/api/override/{rid}/approve", json={"confirm": True})
    squash.assert_not_called()
    assert resp.status_code == 409 and resp.get_json()["state"] == overrides.EXPIRED


# --- live via SSE ------------------------------------------------------------------------

def test_the_sse_snapshot_tracks_the_pending_set(repo):
    """🔴 GUARD: the SSE `overrides` frame is diffed off the pending ids — a filed request
    changes the snapshot, a decided one changes it back."""
    from chela.dashboard import app as dash
    assert dash._sse_overrides_snapshot() == ()
    rec = _bare()
    assert dash._sse_overrides_snapshot() == (rec["id"],)
    overrides.decide(rec["id"], False, "dashboard", channel="dashboard")
    assert dash._sse_overrides_snapshot() == ()


# --- filing does not depend on Telegram ----------------------------------------------------

def test_filing_without_telegram_prints_the_absolute_dashboard_url(repo, capsys, monkeypatch):
    """🔴 GUARD: with no Telegram bridge configured, `chela merge --override` still files and
    names the dashboard's ABSOLUTE confirm URL (the configured base) and `chela merge-approve`
    — and does not send the operator looking for a Telegram button."""
    from chela import main
    monkeypatch.setenv(config.DASHBOARD_URL_ENV, "https://chela.example.ts.net/")
    _seed(repo)
    with _github() as squash:
        main.cmd_merge(SimpleNamespace(run="t1", reason="flaky judge", override=True))
    squash.assert_not_called()
    out = capsys.readouterr().out
    rid, = [r["id"] for r in overrides.pending()]
    assert f"https://chela.example.ts.net/override/{rid}" in out
    assert f"chela merge-approve {rid}" in out
    assert "Telegram" not in out


def test_the_dashboard_url_falls_back_to_the_bound_address(monkeypatch):
    monkeypatch.setenv("CHELA_DASH_HOST", "0.0.0.0")
    monkeypatch.setattr(config, "live_dashboard_port", lambda: 5099)
    assert config.dashboard_url("/override/x") == "http://127.0.0.1:5099/override/x"


def test_the_hint_names_telegram_only_when_a_bridge_is_configured(monkeypatch):
    monkeypatch.setattr(config, "live_dashboard_port", lambda: 5099)
    assert "Telegram" not in contract.approve_hint("override-" + "0" * 24)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "x")
    assert "Telegram" in contract.approve_hint("override-" + "0" * 24)


# --- CMX-87's release requests ride the same record ---------------------------------------

def test_a_release_request_is_listed_and_never_blocked_for_lacking_a_run(repo):
    """🔴 GUARD: a release request (kind "release", no dispatcher run behind it) is listed as
    approvable — it is never looked up as a run, nor refused as "the run no longer exists",
    which only an OVERRIDE whose run vanished deserves."""
    looked_up = []
    rel = overrides.create({"kind": overrides.KIND_RELEASE, "version": "0.9.0", "head_sha": HEAD,
                            "reason": "cut 0.9.0", "actor": "orchestrator"})
    orphan = _bare()                                  # an override whose run is gone
    rows = {r["id"]: r for r in overrides.listing(run_of=lambda t: looked_up.append(t))}
    assert rows[rel["id"]]["kind"] == "release" and rows[rel["id"]]["version"] == "0.9.0"
    assert rows[rel["id"]]["blocked"] is None
    assert rows[orphan["id"]]["blocked"] == "the run no longer exists"
    assert looked_up == ["nope"], "a release request was looked up as a dispatcher run"


def test_a_pending_release_is_listed_and_approves_through_the_shared_decide_path(
        repo, client, monkeypatch):
    """CMX-87 x CMX-103: a `chela release` request shows in Pending approvals with its
    version, and the list's Approve runs `overrides.decide` -> `release.complete` (the one
    path the `/override/<id>` page and Telegram use), ending `released` with decided_by."""
    from chela import release
    rec = overrides.create({"kind": overrides.KIND_RELEASE, "version": "0.9.0", "tag": "v0.9.0",
                            "head_sha": HEAD, "reason": "cut 0.9.0", "actor": "orchestrator"})
    rows = client.get("/api/overrides").get_json()["requests"]
    assert [(r["id"], r["kind"], r["version"], r["blocked"]) for r in rows] == [
        (rec["id"], "release", "0.9.0", None)]
    completed = []
    monkeypatch.setattr(release, "complete", lambda r, by: completed.append((r["id"], by))
                        or {"ok": True, "state": overrides.RELEASED, "pushed": ["main"]})
    resp = client.post(f"/api/override/{rec['id']}/approve", json={"confirm": True},
                       headers={"Tailscale-User-Login": "op@example.com"})
    assert resp.status_code == 200 and resp.get_json()["state"] == overrides.RELEASED
    assert completed == [(rec["id"], "dashboard:op@example.com")]
    done = overrides.load(rec["id"])
    assert done["state"] == overrides.RELEASED and done["decided_by"] == "dashboard:op@example.com"
    assert client.get("/api/overrides").get_json()["requests"] == []


def test_stale_reason_refuses_an_expired_request_of_any_kind():
    """🔴 GUARD: past its deadline nothing is approvable — override or release, run or not."""
    past = time.time() - 1
    run = {"pr_head_sha": HEAD, "pr_state": "open", "pr_checks": CI_PASSING}
    for kind in (overrides.KIND_OVERRIDE, overrides.KIND_RELEASE):
        rec = {"kind": kind, "head_sha": HEAD, "deadline": past}
        assert "expired" in overrides.stale_reason(rec, run)
        assert overrides.stale_reason({**rec, "deadline": time.time() + 60}, run) is None


def test_a_closed_pr_is_not_blocked_when_the_state_is_unread_or_open(repo):
    _seed(repo, pr_state=None)
    _request()
    assert overrides.listing()[0]["blocked"] is None
    _set_run(pr_state="OPEN")
    assert overrides.listing()[0]["blocked"] is None


def test_the_decide_api_rejects_an_unknown_action(repo, client):
    _seed(repo)
    rid = _request()
    with _github() as squash:
        assert client.post(f"/api/override/{rid}/merge", json={"confirm": True}).status_code == 404
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_an_ipv6_bind_is_bracketed_in_the_printed_url(monkeypatch):
    monkeypatch.setattr(config, "live_dashboard_port", lambda: 5099)
    monkeypatch.setenv("CHELA_DASH_HOST", "fd7a:115c::1")
    assert config.dashboard_url("/x") == "http://[fd7a:115c::1]:5099/x"
    monkeypatch.setenv("CHELA_DASH_HOST", "::")
    assert config.dashboard_url("/x") == "http://127.0.0.1:5099/x"
    monkeypatch.setenv("CHELA_DASH_HOST", "100.64.0.7")
    assert config.dashboard_url("/x") == "http://100.64.0.7:5099/x"


def test_a_filed_request_pushes_an_sse_overrides_frame(monkeypatch):
    """🔴 GUARD: the SERVER half of "live via SSE" — filing a request makes the real
    `_sse_stream` loop yield an `overrides` frame (approvals_wiring.test.mjs proves a frame
    re-fetches the list). Monotonic jumps past the keepalive so a dead diff fails fast."""
    import itertools

    from chela.dashboard import app as dash
    monkeypatch.setattr(dash, "_sse_windows_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_runs_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_terms_snapshot", lambda: set())
    monkeypatch.setattr(dash, "_sse_log_snapshot", lambda: {})
    monkeypatch.setattr(dash, "_sse_orchestrator_snapshot", lambda: {})
    monkeypatch.setattr(dash.time, "sleep", lambda _s: None)
    ticks = itertools.count(0.0, dash.SSE_KEEPALIVE_INTERVAL + 1.0)
    monkeypatch.setattr(dash.time, "monotonic", lambda: next(ticks))

    stream = dash._sse_stream()
    assert next(stream).startswith("event: hello")
    rec = _bare()
    frame = next(stream)
    assert frame.startswith("event: overrides\n"), f"no overrides frame after filing (got {frame!r})"
    assert rec["id"] in frame and '"pending": 1' in frame
    # 🔴 GUARD: edge-triggered — with the set unchanged the next ticks push NO overrides
    # frame (a baseline that never advances would re-send it on every tick forever).
    for _ in range(3):
        idle = next(stream)
        assert idle == ": keepalive\n\n", f"an unchanged pending set re-pushed {idle!r}"
    overrides.decide(rec["id"], False, "dashboard", channel="dashboard")
    gone = next(stream)
    assert gone.startswith("event: overrides\n") and '"pending": 0' in gone, gone
