"""🔐 CMX-392 — a dashboard decision (override approve/deny, access-request approve/deny/revoke)
must prove a HUMAN BROWSER SESSION, not merely "came from loopback".

The hole: a dispatched agent POSTed ``/api/override/<id>/approve`` with ``{"confirm": true}``
from ``python -c "import urllib.request…"`` — no Origin header, so ``_override_same_origin``
waved it through; the merge-gate hook only string-matches curl/wget argv; and the dashboard
is a SERVICE channel, so ``overrides.decide`` skipped its agent refusal. The always-human PR
merged on the agent's own say-so.

The fix (``chela/dashboard/humanproof.py``): the page a browser loads sets an HttpOnly,
SameSite=Strict session cookie and embeds that session's token (an HMAC under a secret that
lives only in the dashboard process's memory). A decide POST must carry the cookie, the
matching token, and an Origin naming this host. Absent any of the three ⇒ 403, nothing decided.

The request store is REAL (sandboxed ``CHELA_DIR``); GitHub is stubbed exactly as in
``test_merge_override``.
"""
from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest

from chela import overrides
from tests.test_merge_override import _github, _isolated, _request, _seed, repo  # noqa: F401

flask = pytest.importorskip("flask")

from chela.dashboard import humanproof  # noqa: E402
from chela.dashboard.app import app  # noqa: E402
from tests._browser_session import as_browser  # noqa: E402


@pytest.fixture
def server():
    """The REAL dashboard app behind a real HTTP socket on loopback — what an agent reaches."""
    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    t.join(timeout=5)


def _agent_post(url: str, body: dict, headers: dict | None = None) -> int:
    """Exactly the agent's one-liner: urllib, JSON, NO Origin, no cookie."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


# --- the repro: the agent's raw POST is refused and nothing merges ------------------------

def test_an_agent_urllib_post_without_origin_cannot_approve_an_override(repo, server):  # noqa: F811
    """🔴 REPRO (red on dev before CMX-392): the agent's raw POST with no Origin and no
    browser session is refused 403, the request stays PENDING, and nothing merges."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        code = _agent_post(f"{server}/api/override/{rid}/approve", {"confirm": True})
    assert code == 403
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_an_agent_urllib_form_post_cannot_approve_through_the_confirm_page(repo, server):  # noqa: F811
    """The confirm page's own form route, same shape: refused, nothing merges."""
    _seed(repo)
    rid = _request()["request_id"]
    req = urllib.request.Request(f"{server}/override/{rid}", data=b"decision=approve",
                                 method="POST",
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with _github() as squash:
        try:
            code = urllib.request.urlopen(req, timeout=10).status
        except urllib.error.HTTPError as e:
            code = e.code
    assert code == 403
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.PENDING


def _page_token(html: bytes) -> str:
    import re
    m = re.search(rb'name="session_token" value="([0-9a-f]{64})"', html)
    assert m, "the confirm page must embed its session token in the form"
    return m.group(1).decode()


# --- the browser flow still approves -------------------------------------------------------

def test_the_real_confirm_page_with_its_token_approves_and_merges(repo):  # noqa: F811
    """The operator's flow, unforged: GET the page (cookie + hidden token), POST its form
    from the page's Origin ⇒ merged, decided via the dashboard."""
    _seed(repo)
    rid = _request()["request_id"]
    client = app.test_client()
    page = client.get(f"/override/{rid}")
    assert page.status_code == 200
    assert humanproof.COOKIE in page.headers.get("Set-Cookie", "")
    assert "HttpOnly" in page.headers["Set-Cookie"]
    assert "SameSite=Strict" in page.headers["Set-Cookie"]
    token = _page_token(page.data)
    with _github() as squash:
        ok = client.post(f"/override/{rid}", data={"decision": "approve", "session_token": token},
                         headers={"Origin": "http://localhost"})
    assert ok.status_code == 200 and b"merged" in ok.data
    squash.assert_called_once()
    rec = overrides.load(rid)
    assert rec["state"] == overrides.MERGED and rec["decided_via"] == "dashboard"


def test_the_index_page_issues_the_session_the_spa_decides_with(repo):  # noqa: F811
    """The SPA's flow: `/` sets the cookie and puts its token in the boot block; the JSON
    decide POST with that header approves and merges."""
    import re
    _seed(repo)
    rid = _request()["request_id"]
    client = app.test_client()
    page = client.get("/")
    assert page.status_code == 200
    m = re.search(rb'"sessionToken":\s*"([0-9a-f]{64})"', page.data)
    assert m, "the boot block must carry the session token"
    with _github() as squash:
        ok = client.post(f"/api/override/{rid}/approve", json={"confirm": True},
                         headers={"Origin": "http://localhost",
                                  "X-Chela-Session-Token": m.group(1).decode()})
    assert ok.status_code == 200, ok.get_json()
    squash.assert_called_once()


# --- each missing proof is refused, alone -------------------------------------------------

def _shapes(sid: str, token: str) -> dict:
    good = {"Origin": "http://localhost", "X-Chela-Session-Token": token}
    return {
        "no Origin": ({k: v for k, v in good.items() if k != "Origin"}, sid),
        "foreign Origin": ({**good, "Origin": "http://evil.example"}, sid),
        "null Origin": ({**good, "Origin": "null"}, sid),
        "cross-site fetch": ({**good, "Sec-Fetch-Site": "cross-site"}, sid),
        "no cookie": (good, None),
        "no token": ({"Origin": "http://localhost"}, sid),
        "wrong token": ({**good, "X-Chela-Session-Token": "0" * 64}, sid),
        "another session's token": ({**good, "X-Chela-Session-Token":
                                     humanproof.token_for("someone-elses-session")}, sid),
    }


@pytest.mark.parametrize("shape", list(_shapes("s", "t")))
def test_each_missing_proof_alone_refuses_the_approval(repo, shape):  # noqa: F811
    _seed(repo)
    rid = _request()["request_id"]
    sid = humanproof.new_session_id()
    headers, cookie = _shapes(sid, humanproof.token_for(sid))[shape]
    client = app.test_client()
    if cookie:
        client.set_cookie(humanproof.COOKIE, cookie)
    with _github() as squash:
        resp = client.post(f"/api/override/{rid}/approve", json={"confirm": True},
                           headers=headers)
    assert resp.status_code == 403, shape
    assert resp.get_json()["ok"] is False and resp.get_json()["detail"]
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_the_positive_control_of_the_shapes_table_approves(repo):  # noqa: F811
    """Same headers as the table, nothing removed ⇒ approved: each refusal above is caused by
    the ONE thing that shape removed."""
    _seed(repo)
    rid = _request()["request_id"]
    sid = humanproof.new_session_id()
    client = app.test_client()
    client.set_cookie(humanproof.COOKIE, sid)
    with _github() as squash:
        resp = client.post(f"/api/override/{rid}/approve", json={"confirm": True},
                           headers={"Origin": "http://localhost",
                                    "X-Chela-Session-Token": humanproof.token_for(sid)})
    assert resp.status_code == 200, resp.get_json()
    squash.assert_called_once()


def test_a_refused_deny_denies_nothing(repo, server):  # noqa: F811
    """Deny is a decision too: the agent cannot deny the operator's request either."""
    _seed(repo)
    rid = _request()["request_id"]
    assert _agent_post(f"{server}/api/override/{rid}/deny", {}) == 403
    assert overrides.load(rid)["state"] == overrides.PENDING


# --- the store's own line: an unproven dashboard caller is not a service channel ----------

def test_decide_refuses_an_unproven_dashboard_caller(repo):  # noqa: F811
    """🔴 GUARD (item 2): `channel="dashboard"` alone no longer earns the service exemption —
    without `browser_proven` it is refused and nothing merges, markers or not."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "dashboard", channel="dashboard", env={})
    squash.assert_not_called()
    assert out["ok"] is False and out.get("refused") is True
    assert "browser session" in out["detail"]
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_decide_merges_for_a_proven_dashboard_caller_even_with_agent_markers(repo):  # noqa: F811
    """The proven dashboard keeps CMX-21's exemption: the service's own env may carry markers."""
    from chela import config
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "dashboard", channel="dashboard", browser_proven=True,
                               env={config.ACTOR_ENV: config.AUTO_ORCHESTRATOR_ACTOR,
                                    "CLAUDECODE": "1"})
    assert out["ok"] is True and out["state"] == overrides.MERGED
    squash.assert_called_once()


def test_the_telegram_channel_is_unchanged_and_needs_no_browser_proof(repo):  # noqa: F811
    from chela import config
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "telegram", channel="telegram",
                               env={config.ACTOR_ENV: config.AUTO_ORCHESTRATOR_ACTOR})
    assert out["ok"] is True and out["state"] == overrides.MERGED
    squash.assert_called_once()


def test_browser_proven_does_not_exempt_a_terminal_caller_with_agent_markers(repo):  # noqa: F811
    """The flag is the dashboard's word only: a terminal-channel agent cannot buy the
    exemption by passing it."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "x", channel="terminal", browser_proven=True,
                               env={"CLAUDECODE": "1"})
    squash.assert_not_called()
    assert out.get("refused") is True


# --- the literals --------------------------------------------------------------------------

def test_the_documented_names_are_pinned():
    assert humanproof.COOKIE == "chela_session"
    assert humanproof.HEADER == "X-Chela-Session-Token"
    assert humanproof.FORM_FIELD == "session_token"


def test_a_token_is_a_keyed_hmac_of_the_session_never_the_session_itself():
    import hashlib
    import hmac
    sid = "abcdefghijklmnopqrstuvwx"
    t = humanproof.token_for(sid)
    assert len(t) == 64 and t != sid
    assert t == hmac.new(humanproof._SECRET, sid.encode(), hashlib.sha256).hexdigest()
    # a fresh process (an agent's own `python -c`) has a different key, so its token misses
    assert hmac.new(b"\0" * 32, sid.encode(), hashlib.sha256).hexdigest() != t


@pytest.mark.parametrize("cookie,kept", [
    ("a" * 16, True), ("a" * 64, True), ("a" * 15, False), ("a" * 65, False),
    ("é" * 20, False), ("", False), (None, False)])
def test_a_session_cookie_is_reused_only_when_well_formed(cookie, kept):
    from types import SimpleNamespace
    req = SimpleNamespace(cookies={} if cookie is None else {humanproof.COOKIE: cookie})
    got = humanproof.session_for(req)
    assert (got == cookie) is kept
    assert 16 <= len(got) <= 64


# --- access requests: the same proof --------------------------------------------------------

@pytest.mark.parametrize("action", ["approve", "deny", "revoke"])
def test_an_access_request_decision_without_a_browser_session_is_refused(action, monkeypatch):
    """An agent cannot grant (or deny, or revoke) a guest's access on the operator's behalf:
    refused 403 before the store is touched."""
    from chela.dashboard import app as dash
    calls = []
    for name in ("approve", "deny", "revoke"):
        monkeypatch.setattr(dash.share_requests, name,
                            lambda *a, _n=name, **k: calls.append(_n) or (True, "ok"))
    resp = app.test_client().post(f"/api/share-requests/r-1/{action}", json={})
    assert resp.status_code == 403 and resp.get_json()["ok"] is False
    assert calls == []
    browser = as_browser(app.test_client())
    assert browser.post(f"/api/share-requests/r-1/{action}", json={}).status_code == 200
    assert calls == [action]
