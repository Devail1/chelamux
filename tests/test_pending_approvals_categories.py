"""⚖️🔔 CMX-103 r4 — the CATEGORY pass over Pending approvals (the judge's round-4 survivors).

Round 4 corrupted four guards and all four stayed green; each was one instance of a category:

* A — an agent- or request-supplied field rendered into HTML must be ESCAPED, every field;
* B — computed values (age, time left) pinned to literals on a frozen clock;
* C — ONE audited decide path, asserted per entry point WITH an authenticated identity (an
  anonymous request cannot tell ``by="dashboard"`` from ``by="dashboard:<who>"``);
* D — oldest-first order and both record kinds with their kind-specific fields;
* E — a bad record must not take the list down, and must not read as "nothing waiting";
* F — a cross-site (collab relay) POST is refused on EVERY decide entry point.

The sibling ``test_pending_approvals.py`` owns the happy paths; this file owns the shapes
that let a corruption through.
"""
from __future__ import annotations

import json
import time

import pytest

from chela import config, contract, dispatcher, event_log, overrides

pytest.importorskip("flask")

HEAD = "a" * 40
WHO = "op@example.com"
DECIDER = f"dashboard:{WHO}"
IMG = "<img src=x onerror=alert(1)>"
SCRIPT = '"><script>alert(1)</script>'
RELAY = "https://chela-collab-relay.example.workers.dev"
ID = "override-" + "d" * 24
AUTH = {"Tailscale-User-Login": WHO}


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.delenv(config.ACTOR_ENV, raising=False)
    monkeypatch.delenv("CLAUDECODE", raising=False)
    monkeypatch.delenv(overrides.TTL_ENV, raising=False)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(contract.notify, "enabled", lambda: False)
    monkeypatch.setattr(overrides.notify, "enabled", lambda: False)


@pytest.fixture
def client():
    from chela.dashboard.app import app
    return app.test_client()


def _override(**meta) -> dict:
    return overrides.create({"task_id": "nope", "label": "cmx-9", "head_sha": HEAD,
                             "reason": "r", "actor": "orchestrator", **meta})


def _release(**meta) -> dict:
    return overrides.create({"kind": overrides.KIND_RELEASE, "version": "0.9.0", "tag": "v0.9.0",
                             "head_sha": HEAD, "reason": "cut", "actor": "orchestrator", **meta})


def _page(client, rid) -> str:
    resp = client.get(f"/override/{rid}")
    assert resp.status_code == 200, resp.status_code
    return resp.get_data(as_text=True)


def _assert_inert(html: str, *needles: str) -> None:
    """The payload is TEXT: no element was created from it, and its escaped form is present."""
    assert "<img src=x" not in html and "<script>alert" not in html, "a request field rendered raw"
    for n in needles:
        assert n in html, f"{n!r} missing from the page"


# === A. escaping ==============================================================================

PAYLOADS = [
    (IMG, "&lt;img src=x onerror=alert(1)&gt;"),
    (SCRIPT, "&quot;&gt;&lt;script&gt;alert(1)&lt;/script&gt;"),
]


@pytest.mark.parametrize("payload,escaped", PAYLOADS)
@pytest.mark.parametrize("field", ["label", "pr_url", "base", "head_sha", "judge_state",
                                   "reason", "actor", "question"])
def test_the_override_confirm_page_escapes_every_request_field(client, field, payload, escaped):
    """🔴 GUARD: each field the override confirm page prints is HTML-escaped — an agent that
    files a request chooses all of them."""
    rec = _override(**{field: payload})
    _assert_inert(_page(client, rec["id"]), escaped)


@pytest.mark.parametrize("payload,escaped", PAYLOADS)
@pytest.mark.parametrize("field", ["tag", "version", "theme", "dev_sha", "release_sha", "actor"])
def test_the_release_confirm_page_escapes_every_request_field(client, field, payload, escaped):
    rec = _release(**{field: payload})
    _assert_inert(_page(client, rec["id"]), escaped)


def test_the_release_confirm_page_escapes_the_task_ids(client):
    rec = _release(task_ids=[IMG, "cmx-2"])
    _assert_inert(_page(client, rec["id"]), "&lt;img src=x onerror=alert(1)&gt;")


def test_a_refused_decision_page_escapes_the_detail(client, monkeypatch):
    """🔴 GUARD: the POST result page prints ``detail`` (text from a git/GitHub failure) —
    escaped on the not-done branch."""
    rec = _override()
    monkeypatch.setattr(overrides, "decide", lambda *a, **k: {
        "ok": False, "state": overrides.FAILED, "detail": IMG})
    resp = client.post(f"/override/{rec['id']}", data={"decision": "approve"})
    _assert_inert(resp.get_data(as_text=True), "&lt;img src=x onerror=alert(1)&gt;")


def test_the_already_decided_page_escapes_state_and_detail(client):
    rec = _override()
    overrides.set_fields(rec["id"], state=SCRIPT, detail=IMG)
    resp = client.get(f"/override/{rec['id']}")
    assert resp.status_code == 404
    _assert_inert(resp.get_data(as_text=True), "&lt;img src=x onerror=alert(1)&gt;")


def test_the_merged_page_escapes_the_commit_sha(client, monkeypatch):
    rec = _override()
    monkeypatch.setattr(overrides, "decide", lambda *a, **k: {
        "ok": True, "state": overrides.MERGED, "merge_commit_sha": IMG})
    resp = client.post(f"/override/{rec['id']}", data={"decision": "approve"})
    assert "<img src=x" not in resp.get_data(as_text=True)


def test_the_api_serves_fields_as_json_never_as_html(client):
    """The list endpoint hands the raw text to the page as JSON (approvals.js escapes it on
    render); it must not be served as something a browser would render."""
    rec = _override(reason=IMG, label=SCRIPT)
    resp = client.get("/api/overrides")
    assert resp.mimetype == "application/json"
    row, = resp.get_json()["requests"]
    assert row["id"] == rec["id"] and row["reason"] == IMG and row["label"] == SCRIPT


# === B. computed values =======================================================================

def test_age_and_time_left_are_pinned_to_the_clock(monkeypatch):
    """🔴 GUARD: filed 90 s ago with 3600 s left → age_s == 90 and expires_in_s == 3600,
    literally; a tick later the age GREW and the expiry SHRANK. (The sign of age_s was once
    flipped and nothing noticed: ``max(0, …)`` hid it as a constant 0.)"""
    rec = _override()
    now = time.time()
    overrides.set_fields(rec["id"], created=now - 90, deadline=now + 3600)
    monkeypatch.setattr(overrides.time, "time", lambda: now)
    row, = overrides.listing(run_of=lambda t: None)
    assert row["age_s"] == 90.0
    assert row["expires_in_s"] == 3600.0
    monkeypatch.setattr(overrides.time, "time", lambda: now + 30)
    row, = overrides.listing(run_of=lambda t: None)
    assert row["age_s"] == 120.0 and row["expires_in_s"] == 3570.0


def test_a_created_in_the_future_has_age_zero_not_negative(monkeypatch):
    rec = _override()
    now = time.time()
    overrides.set_fields(rec["id"], created=now + 500, deadline=now + 3600)
    monkeypatch.setattr(overrides.time, "time", lambda: now)
    assert overrides.listing(run_of=lambda t: None)[0]["age_s"] == 0.0


# === C. one decide path, audited with the identity ============================================

@pytest.fixture
def decide_spy(monkeypatch):
    calls = []

    def fake(request_id, approve, by, *, channel="terminal", env=None):
        calls.append({"id": request_id, "approve": approve, "by": by, "channel": channel})
        return {"ok": True, "state": overrides.MERGED if approve else overrides.DENIED}
    monkeypatch.setattr(overrides, "decide", fake)
    return calls


def test_every_dashboard_entry_point_decides_as_the_authenticated_identity(client, decide_spy):
    """🔴 GUARD: list Approve, list Deny, and the confirm page's Approve and Deny each reach
    ``overrides.decide`` with ``by`` = the tailnet identity and channel "dashboard". A route
    that calls ``decide(by="dashboard")`` itself records no identity — and only an
    AUTHENTICATED request can tell the two apart."""
    assert client.post(f"/api/override/{ID}/approve", json={"confirm": True},
                       headers=AUTH).status_code == 200
    assert client.post(f"/api/override/{ID}/deny", json={}, headers=AUTH).status_code == 200
    client.post(f"/override/{ID}", data={"decision": "approve"}, headers=AUTH)
    client.post(f"/override/{ID}", data={"decision": "deny"}, headers=AUTH)
    assert decide_spy == [
        {"id": ID, "approve": True, "by": DECIDER, "channel": "dashboard"},
        {"id": ID, "approve": False, "by": DECIDER, "channel": "dashboard"},
        {"id": ID, "approve": True, "by": DECIDER, "channel": "dashboard"},
        {"id": ID, "approve": False, "by": DECIDER, "channel": "dashboard"},
    ]


def test_without_a_tailnet_identity_the_decider_is_plain_dashboard(client, decide_spy):
    client.post(f"/api/override/{ID}/deny", json={})
    client.post(f"/override/{ID}", data={"decision": "deny"})
    assert [c["by"] for c in decide_spy] == ["dashboard", "dashboard"]


@pytest.mark.parametrize("route", ["api", "page"])
def test_a_deny_is_audited_with_the_identity_in_the_event_log(client, route):
    """The audit trail (not only the request record) names who denied it — through the list
    AND the confirm page."""
    rec = _override()
    if route == "api":
        resp = client.post(f"/api/override/{rec['id']}/deny", json={}, headers=AUTH)
    else:
        resp = client.post(f"/override/{rec['id']}", data={"decision": "deny"}, headers=AUTH)
    assert resp.status_code == 200
    assert overrides.load(rec["id"])["decided_by"] == DECIDER
    events = event_log.read(types=["orchestrator.merge_override_denied"])["events"]
    assert any(DECIDER in e["summary"] and e["payload"].get("approved_by") == DECIDER
               for e in events), events


def test_a_release_deny_is_audited_with_the_identity(client):
    rec = _release()
    assert client.post(f"/override/{rec['id']}", data={"decision": "deny"},
                       headers=AUTH).status_code == 200
    events = event_log.read(types=["orchestrator.release_denied"])["events"]
    assert any(e["payload"].get("approved_by") == DECIDER for e in events), events


# === D. ordering, kinds =======================================================================

def test_the_list_is_oldest_first_whatever_the_ids_and_write_order(client):
    """🔴 GUARD: ordering is by ``created`` — ids are random, so the filing order below is
    neither id order nor write order."""
    now = time.time()
    newest = _override(label="newest")
    oldest = _override(label="oldest")
    middle = _release(label="middle")
    for rec, age in ((newest, 10), (oldest, 5000), (middle, 600)):
        overrides.set_fields(rec["id"], created=now - age)
    assert [r["label"] for r in overrides.listing(run_of=lambda t: None)] == [
        "oldest", "middle", "newest"]
    assert [r["label"] for r in client.get("/api/overrides").get_json()["requests"]] == [
        "oldest", "middle", "newest"]


def test_both_kinds_are_listed_each_with_its_own_fields(client):
    ov = _override(pr_url="https://github.com/o/r/pull/7", base="dev", judge_state="blocked")
    rel = _release(question="Release 0.9.0?")
    done = _release(version="0.1.0")
    overrides.decide(done["id"], False, "t", channel="dashboard")      # decided -> hidden
    rows = {r["id"]: r for r in client.get("/api/overrides").get_json()["requests"]}
    assert set(rows) == {ov["id"], rel["id"]}
    o, r = rows[ov["id"]], rows[rel["id"]]
    assert (o["kind"], o["head_sha"], o["judge_state"], o["base"], o["pr_url"]) == (
        "override", HEAD, "blocked", "dev", "https://github.com/o/r/pull/7")
    assert (r["kind"], r["version"], r["question"]) == ("release", "0.9.0", "Release 0.9.0?")


# === E. fail closed ===========================================================================

def _corrupt(name="b"):
    path = overrides.store_dir() / f"override-{name * 24}.json"
    path.write_text("{this is not json")
    return path


def test_a_corrupt_request_file_does_not_take_the_list_down(client):
    good = _override(label="good")
    bad = _corrupt()
    resp = client.get("/api/overrides")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.get_json()["requests"]] == [good["id"], bad.stem]


def test_a_missing_run_marks_the_row_instead_of_dropping_it(client):
    rec = _override()                                  # task "nope" has no run
    row, = client.get("/api/overrides").get_json()["requests"]
    assert row["id"] == rec["id"] and row["blocked"] == "the run no longer exists"


def test_a_run_lookup_that_raises_marks_the_row_not_a_500(client, monkeypatch):
    rec = _override()

    def boom(task_id):
        raise RuntimeError("db unreadable")
    monkeypatch.setattr(dispatcher, "resolve_run", boom)
    resp = client.get("/api/overrides")
    assert resp.status_code == 200
    assert [r["id"] for r in resp.get_json()["requests"]] == [rec["id"]]


def _bad_file(name, text):
    path = overrides.store_dir() / f"override-{name * 24}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path.stem


@pytest.mark.parametrize("fields", [
    {"deadline": "soon", "created": "x"}, {"deadline": "soon"}, {"created": [1]},
    {"deadline": True}, {"ttl": "long"}, {"deadline": float("nan")},
])
def test_a_wrong_typed_record_is_marked_and_does_not_take_the_list_down(client, fields):
    good = _override(label="good")
    bid = f"override-{'c' * 24}"
    (overrides.store_dir() / f"{bid}.json").write_text(json.dumps(
        {"id": bid, "state": "pending", "deadline": time.time() + 99, **fields}))
    resp = client.get("/api/overrides")
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.get_json()["requests"]}
    assert set(rows) == {good["id"], bid}
    assert rows[bid]["kind"] == "unreadable" and "is not a number" in rows[bid]["reason"]
    assert rows[bid]["blocked"].startswith(f"unreadable request {bid} — ")


@pytest.mark.parametrize("text,why", [
    ("{this is not json", "not valid JSON"), ("[1, 2]", "not a JSON object"),
    ('{"id": "override-zzz", "state": "pending"}', "does not match"), ("", "not valid JSON"),
])
def test_an_unusable_request_file_is_marked_not_silently_dropped(client, text, why):
    good = _override(label="good")
    bid = _bad_file("b", text)
    row_ids = [r["id"] for r in client.get("/api/overrides").get_json()["requests"]]
    assert row_ids == [good["id"], bid]            # the bad one last, marked
    bad = client.get("/api/overrides").get_json()["requests"][1]
    assert bad["kind"] == "unreadable" and why in bad["reason"]
    assert bad["blocked"] == f"unreadable request {bid} — {bad['reason']}"


def test_an_unreadable_file_cannot_be_decided(client):
    bid = _bad_file("b", "{nope")
    for action, body in (("approve", {"confirm": True}), ("deny", {})):
        resp = client.post(f"/api/override/{bid}/{action}", json=body)
        assert resp.status_code == 409 and resp.get_json()["ok"] is False


def test_an_unreadable_file_makes_the_sweeps_and_pending_survive(client):
    good = _override(label="good")
    _bad_file("b", json.dumps({"id": f"override-{'b' * 24}", "state": "pending", "deadline": "x"}))
    overrides.sweep()                                # used to raise TypeError
    assert [r["id"] for r in overrides.pending()] == [good["id"]]


def test_a_final_record_with_a_bad_field_stays_usable_history(client):
    rec = _override()
    overrides.set_fields(rec["id"], state=overrides.DENIED, created="x")
    assert client.get("/api/overrides").get_json() == {"requests": []}


def test_a_missing_store_means_nothing_was_ever_filed(client):
    assert client.get("/api/overrides").get_json() == {"requests": []}


def test_an_unreadable_store_is_an_explicit_error_not_nothing_waiting(client):
    """🔴 GUARD: unknown is not empty. A store that cannot be listed answers 503 with an
    ``error`` — never ``{"requests": []}``, which the page shows as "nothing is waiting"."""
    import os
    d = overrides.store_dir()
    d.mkdir(parents=True, exist_ok=True)
    _override()
    d.chmod(0)
    try:
        if os.access(d, os.R_OK):
            pytest.skip("running as a user that ignores directory permissions")
        resp = client.get("/api/overrides")
    finally:
        d.chmod(0o755)
    assert resp.status_code == 503
    body = resp.get_json()
    assert body["requests"] == [] and "cannot be read" in body["error"]


def test_a_store_path_that_is_a_file_is_an_error(client):
    d = overrides.store_dir()
    d.parent.mkdir(parents=True, exist_ok=True)
    d.write_text("not a directory")
    resp = client.get("/api/overrides")
    assert resp.status_code == 503 and "error" in resp.get_json()


def test_a_failure_inside_listing_is_an_error_reply_not_a_500_page(client, monkeypatch):
    monkeypatch.setattr(overrides, "listing_state", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
    resp = client.get("/api/overrides")
    assert resp.status_code == 503 and resp.get_json()["requests"] == [] and resp.get_json()["error"]


# === F. auth ==================================================================================

@pytest.mark.parametrize("headers", [
    {"Origin": RELAY, "Sec-Fetch-Site": "cross-site"},
    {"Origin": RELAY},                                  # a proxy that strips Sec-Fetch-Site
    {"Sec-Fetch-Site": "cross-site"},                   # no Origin at all
    {"Sec-Fetch-Site": "same-site"},
])
def test_no_decide_entry_point_accepts_a_cross_site_post(client, headers):
    """🔴 GUARD: the collab viewer's page is cross-site to the dashboard — the list's API and
    the confirm page refuse it for Approve and Deny alike, whichever header gave it away."""
    rec = _override()
    api_a = client.post(f"/api/override/{rec['id']}/approve", json={"confirm": True},
                        headers=headers)
    api_d = client.post(f"/api/override/{rec['id']}/deny", json={}, headers=headers)
    page_a = client.post(f"/override/{rec['id']}", data={"decision": "approve"}, headers=headers)
    page_d = client.post(f"/override/{rec['id']}", data={"decision": "deny"}, headers=headers)
    assert (api_a.status_code, api_d.status_code,
            page_a.status_code, page_d.status_code) == (403,) * 4
    assert overrides.load(rec["id"])["state"] == overrides.PENDING


def test_the_same_origin_post_still_works(client):
    """Negative control for the table above: the SAME request without the cross-site marks is
    accepted, so the 403s are the origin check and not a broken route."""
    rec = _override()
    resp = client.post(f"/api/override/{rec['id']}/deny", json={},
                       headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"})
    assert resp.status_code == 200
