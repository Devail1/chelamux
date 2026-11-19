"""🔐 CMX-392 slice 1 — an override decision on the dashboard must prove the operator's
BROWSER SESSION, not merely "came from loopback".

The hole: a dispatched agent POSTed ``/api/override/<id>/approve`` with ``{"confirm": true}``
from ``python -c "import urllib.request…"``. It sent no Origin header, so the old
same-origin check waved it through, and ``overrides.decide`` exempted ``channel="dashboard"``
by name. The always-human PR merged on the agent's own say-so.

The fix (``chela/dashboard/humanproof.py``): a page the browser loads sets an HttpOnly,
SameSite=Lax session cookie and embeds that session's token (an HMAC under a key that lives
only in the dashboard process's memory). A decide POST must carry the cookie, the matching
token and an Origin naming this host:port. Any miss ⇒ 403, nothing decided.

Every guard here is driven through EVERY decide entry point (JSON approve, JSON deny, the
confirm page's form approve and deny), on a host WITH a port, under BOTH schemes, with each
credential compared against a value only the fixture could produce
(docs/defeat_shapes/392*.md on the CMX-392 branch name how a narrower table was fooled).
"""
from __future__ import annotations

import hashlib
import hmac
import http.cookiejar
import json
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from chela import config, overrides
from tests.test_merge_override import _github, _isolated, _request, _seed, repo  # noqa: F401

flask = pytest.importorskip("flask")

from chela.dashboard import app as dash  # noqa: E402
from chela.dashboard import humanproof  # noqa: E402

app = dash.app
HOST = "localhost:5005"          # the dashboard really serves on a port
TOKEN_RE = rb'name="session_token" value="([0-9a-f]{64})"'
BOOT_RE = rb'"sessionToken":\s*"([0-9a-f]{64})"'


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


def _status(opener, req) -> int:
    try:
        with opener.open(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


# --- the repro: the agent's raw POST is refused and nothing merges ------------------------

@pytest.mark.parametrize("path,data,ctype", [
    ("/api/override/{rid}/approve", json.dumps({"confirm": True}).encode(), "application/json"),
    ("/api/override/{rid}/deny", b"{}", "application/json"),
    ("/override/{rid}", b"decision=approve", "application/x-www-form-urlencoded"),
    ("/override/{rid}", b"decision=deny", "application/x-www-form-urlencoded"),
])
def test_an_agent_urllib_post_without_origin_decides_nothing(repo, server, path, data, ctype):  # noqa: F811
    """🔴 REPRO (red on dev): the agent's raw urllib POST, no Origin, no browser session, is
    refused 403 at every decide entry point; the request stays PENDING and nothing merges."""
    _seed(repo)
    rid = _request()["request_id"]
    req = urllib.request.Request(server + path.format(rid=rid), data=data, method="POST",
                                 headers={"Content-Type": ctype})
    with _github() as squash:
        code = _status(urllib.request.build_opener(), req)
    assert code == 403
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_the_agent_on_a_sibling_port_cannot_approve_over_a_real_socket(repo, server):  # noqa: F811
    """End to end on a real port: a full session (the page's cookie + token) whose Origin
    names a SIBLING port of the dashboard's own host is refused; the same session from its
    own origin approves."""
    _seed(repo)
    rid = _request()["request_id"]
    port = urlparse(server).port
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    page = opener.open(f"{server}/override/{rid}", timeout=10).read()
    token = re.search(TOKEN_RE, page).group(1).decode()

    def post(origin: str) -> int:
        return _status(opener, urllib.request.Request(
            f"{server}/api/override/{rid}/approve", method="POST",
            data=json.dumps({"confirm": True}).encode(),
            headers={"Content-Type": "application/json", "Origin": origin,
                     humanproof.HEADER: token}))

    with _github() as squash:
        assert post(f"http://127.0.0.1:{port + 1}") == 403
        squash.assert_not_called()
        assert overrides.load(rid)["state"] == overrides.PENDING
        assert post(f"http://127.0.0.1:{port}") == 200
    squash.assert_called_once()


# --- the browser flows still decide ---------------------------------------------------------

def test_the_real_confirm_page_with_its_token_approves_and_merges(repo):  # noqa: F811
    """The operator's flow, unforged: GET the page (cookie + hidden token), POST its form
    from the page's Origin ⇒ merged, recorded as a dashboard decision."""
    _seed(repo)
    rid = _request()["request_id"]
    client = app.test_client()
    page = client.get(f"/override/{rid}", base_url=f"http://{HOST}")
    assert page.status_code == 200
    cookie = page.headers["Set-Cookie"]
    assert cookie.startswith("chela_session=")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie
    # a page that embeds a live token is never cached (a back-button copy can't replay it)
    assert page.headers["Cache-Control"] == "no-store"
    token = re.search(TOKEN_RE, page.data).group(1).decode()
    with _github() as squash:
        ok = client.post(f"/override/{rid}", base_url=f"http://{HOST}",
                         headers={"Origin": f"http://{HOST}"},
                         data={"decision": "approve", "session_token": token})
    assert ok.status_code == 200 and b"merged" in ok.data
    squash.assert_called_once()
    rec = overrides.load(rid)
    assert rec["state"] == overrides.MERGED and rec["decided_via"] == "dashboard"


def test_the_index_page_issues_the_session_the_approvals_sheet_decides_with(repo):  # noqa: F811
    """The SPA's flow: `/` sets the cookie and puts its token in the boot block; the
    approvals sheet's JSON POST with that header approves and merges."""
    _seed(repo)
    rid = _request()["request_id"]
    client = app.test_client()
    page = client.get("/", base_url=f"https://{HOST}")
    assert page.status_code == 200
    cookie = page.headers["Set-Cookie"]
    assert cookie.startswith("chela_session=")
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie
    token = re.search(BOOT_RE, page.data).group(1).decode()
    with _github() as squash:
        ok = client.post(f"/api/override/{rid}/approve", base_url=f"https://{HOST}",
                         json={"confirm": True},
                         headers={"Origin": f"https://{HOST}", "X-Chela-Session-Token": token})
    assert ok.status_code == 200, ok.get_json()
    squash.assert_called_once()
    assert overrides.load(rid)["state"] == overrides.MERGED


def test_a_second_page_load_keeps_the_session_so_an_open_tab_still_decides(repo):  # noqa: F811
    """The SPA tab holds the token it booted with. Reloading `/` or opening the confirm page
    from a notification in the same browser must REUSE the session, or the open tab's next
    Approve is refused as stale."""
    _seed(repo)
    rid = _request()["request_id"]
    client = app.test_client()
    spa = re.search(BOOT_RE, client.get("/").data).group(1).decode()
    assert re.search(BOOT_RE, client.get("/").data).group(1).decode() == spa
    assert re.search(TOKEN_RE, client.get(f"/override/{rid}").data).group(1).decode() == spa
    with _github() as squash:
        ok = client.post(f"/api/override/{rid}/approve", json={"confirm": True},
                         headers={"Origin": "http://localhost", humanproof.HEADER: spa})
    assert ok.status_code == 200, ok.get_json()
    squash.assert_called_once()


def test_two_browsers_get_two_sessions():
    """Session reuse is per cookie, never global: a fresh client is a fresh session."""
    tok = [re.search(BOOT_RE, app.test_client().get("/").data).group(1) for _ in range(2)]
    assert tok[0] != tok[1]


def test_the_release_confirm_form_carries_its_token_too(monkeypatch):
    """The release page builds its own form markup; it must get the hidden field as well."""
    sid = "release-page-session-0001"
    monkeypatch.setattr(overrides, "sweep", lambda: None)
    monkeypatch.setattr(overrides, "load", lambda rid: {
        "id": rid, "kind": "release", "state": overrides.PENDING, "tag": "v0.2.0",
        "deadline": 0})
    client = app.test_client()
    client.set_cookie(humanproof.COOKIE, sid)
    page = client.get("/override/r-1")
    assert re.search(TOKEN_RE, page.data).group(1).decode() == humanproof.token_for(sid)


# --- EVERY decide entry point × EVERY missing or wrong proof --------------------------------
#
# The side effect is `overrides.decide`, recorded instead of performed. Each row removes or
# corrupts ONE proof and keeps the other two valid, so that proof is the only thing that can
# refuse, and the response must name ITS reason. The host has a port; the token goes where
# that entry point's real client puts it (the SPA's header, the form's hidden field).

_ROUTES = {
    "api approve": ("/api/override/r-1/approve", "json", {"confirm": True}, True),
    "api deny": ("/api/override/r-1/deny", "json", {}, False),
    "form approve": ("/override/r-1", "form", {"decision": "approve"}, True),
    "form deny": ("/override/r-1", "form", {"decision": "deny"}, False),
}


@pytest.fixture
def sink(monkeypatch):
    calls: list = []

    def fake(rid, approve, by, *, channel="terminal", env=None, reason=None,
             browser_proven=False):
        calls.append((rid, approve, channel, browser_proven))
        return {"ok": True, "state": overrides.MERGED if approve else overrides.DENIED}
    monkeypatch.setattr(overrides, "decide", fake)
    monkeypatch.setattr(overrides, "sweep", lambda: None)
    monkeypatch.setattr(overrides, "load", lambda rid: None)
    return calls


def _drive(route: str, *, origin: str | None, cookie: str | None, token: str | None,
           scheme: str = "http", fetch_site: str | None = None):
    path, kind, body, _ = _ROUTES[route]
    client = app.test_client()
    if cookie is not None:
        client.set_cookie(humanproof.COOKIE, cookie, domain="localhost")
    headers = {} if origin is None else {"Origin": origin}
    if fetch_site is not None:
        headers["Sec-Fetch-Site"] = fetch_site
    if kind == "json":
        if token is not None:
            headers[humanproof.HEADER] = token
        return client.post(path, base_url=f"{scheme}://{HOST}", headers=headers, json=body)
    data = dict(body) if token is None else {**body, humanproof.FORM_FIELD: token}
    return client.post(path, base_url=f"{scheme}://{HOST}", headers=headers, data=data)


NO_ORIGIN = "no Origin header"
CROSS = "cross-origin request"
CROSS_SITE = "cross-site request"
NO_SESSION = "no dashboard session"
STALE = "missing or stale session token"

# shape -> (origin, cookie, token[, Sec-Fetch-Site]) over (sid, token), and its reason
_SHAPES = {
    # Origin: missing, blank, and every value one component away from http(s)://host:port
    "no Origin": (lambda s, t: (None, s, t), NO_ORIGIN),
    "empty Origin": (lambda s, t: ("", s, t), NO_ORIGIN),
    "null Origin": (lambda s, t: ("null", s, t), CROSS),
    "foreign Origin": (lambda s, t: ("http://evil.example", s, t), CROSS),
    "sibling port": (lambda s, t: ("http://localhost:5006", s, t), CROSS),
    "https sibling port": (lambda s, t: ("https://localhost:5006", s, t), CROSS),
    "hostname without the port": (lambda s, t: ("http://localhost", s, t), CROSS),
    "https hostname without the port": (lambda s, t: ("https://localhost", s, t), CROSS),
    "a prefix of host:port": (lambda s, t: ("http://localhost:500", s, t), CROSS),
    "host:port as a prefix": (lambda s, t: ("http://localhost:50055", s, t), CROSS),
    "host:port inside another host": (lambda s, t: ("http://localhost:5005.evil.example", s, t),
                                      CROSS),
    "another hostname on this port": (lambda s, t: ("http://127.0.0.1:5005", s, t), CROSS),
    "userinfo before host:port": (lambda s, t: ("http://evil@localhost:5005", s, t), CROSS),
    "https userinfo before host:port": (lambda s, t: ("https://evil@localhost:5005", s, t),
                                        CROSS),
    "host:port with a path": (lambda s, t: ("http://localhost:5005/x", s, t), CROSS),
    "another scheme": (lambda s, t: ("ftp://localhost:5005", s, t), CROSS),
    "no scheme": (lambda s, t: ("localhost:5005", s, t), CROSS),
    # the browser's own Sec-Fetch-Site, with an Origin that is otherwise right
    "cross-site fetch": (lambda s, t: (f"http://{HOST}", s, t, "cross-site"), CROSS_SITE),
    "same-site fetch": (lambda s, t: (f"http://{HOST}", s, t, "same-site"), CROSS_SITE),
    # the session cookie
    "no cookie": (lambda s, t: (f"http://{HOST}", None, t), NO_SESSION),
    "empty cookie": (lambda s, t: (f"http://{HOST}", "", t), NO_SESSION),
    "another session's cookie": (lambda s, t: (f"http://{HOST}", s + "x", t), STALE),
    # the token
    "no token": (lambda s, t: (f"http://{HOST}", s, None), STALE),
    "empty token": (lambda s, t: (f"http://{HOST}", s, ""), STALE),
    "wrong token": (lambda s, t: (f"http://{HOST}", s, "0" * 64), STALE),
    "another session's token": (lambda s, t: (f"http://{HOST}", s,
                                              humanproof.token_for(s + "x")), STALE),
    "truncated token": (lambda s, t: (f"http://{HOST}", s, t[:32]), STALE),
    "token with a suffix": (lambda s, t: (f"http://{HOST}", s, t + "0"), STALE),
    "token in upper case": (lambda s, t: (f"http://{HOST}", s, t.upper()), STALE),
    "the session id as its own token": (lambda s, t: (f"http://{HOST}", s, s), STALE),
}


@pytest.mark.parametrize("shape", list(_SHAPES))
@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_entry_point_refuses_every_missing_or_wrong_proof(route, shape, sink):
    build, reason = _SHAPES[shape]
    sid = humanproof.new_session_id()
    origin, cookie, token, *fetch_site = build(sid, humanproof.token_for(sid))
    resp = _drive(route, origin=origin, cookie=cookie, token=token,
                  fetch_site=fetch_site[0] if fetch_site else None)
    assert resp.status_code == 403, (route, shape, resp.status_code)
    assert sink == [], (route, shape)
    text = resp.get_data(as_text=True)
    assert reason in text, (route, shape, text)
    if _ROUTES[route][1] == "json":
        assert resp.get_json()["ok"] is False and resp.get_json()["state"] is None


@pytest.mark.parametrize("fetch_site", [None, "same-origin", "none"])
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_entry_point_decides_once_for_the_full_proof(route, scheme, fetch_site, sink):
    """Positive control, the same rows with nothing removed: decided exactly once, as a
    PROVEN dashboard call. Under BOTH schemes, each against a server that sees the other
    one too: behind the TLS-terminating proxy the browser's Origin is https while Flask sees
    http, so a check that compares schemes refuses the real Approve button."""
    sid = humanproof.new_session_id()
    for served in ("http", "https"):
        resp = _drive(route, origin=f"{scheme}://{HOST}", cookie=sid,
                      token=humanproof.token_for(sid), scheme=served, fetch_site=fetch_site)
        assert resp.status_code == 200, (route, scheme, served, resp.get_data(as_text=True))
    _, _, _, approve = _ROUTES[route]
    assert sink == [("r-1", approve, "dashboard", True)] * 2


# --- the session and the token ---------------------------------------------------------------

def test_the_documented_names_are_pinned():
    assert humanproof.COOKIE == "chela_session"
    assert humanproof.HEADER == "X-Chela-Session-Token"
    assert humanproof.FORM_FIELD == "session_token"


def test_a_token_is_an_hmac_sha256_of_the_session_under_the_process_key():
    sid = "abcdefghijklmnopqrstuvwx"
    t = humanproof.token_for(sid)
    assert t == hmac.new(humanproof._SECRET, sid.encode(), hashlib.sha256).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", t) and t != sid
    assert humanproof.token_for(sid + "y") != t


def test_the_key_is_random_per_process_so_an_agent_cannot_mint_a_token():
    """🔴 GUARD: two fresh interpreters (what an agent's `python -c` is) each compute the
    token for the SAME session id; neither matches the other or this process's. A key that
    is fixed, derived, empty or read from disk or the env makes them collide → RED."""
    sid = "abcdefghijklmnopqrstuvwx"
    code = f"from chela.dashboard import humanproof as h; print(h.token_for({sid!r}))"
    minted = [subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             timeout=60, check=True).stdout.strip() for _ in range(2)]
    assert all(re.fullmatch(r"[0-9a-f]{64}", m) for m in minted), minted
    assert len({*minted, humanproof.token_for(sid)}) == 3
    assert len(humanproof._SECRET) == 32


@pytest.mark.parametrize("cookies,kept", [
    ({humanproof.COOKIE: "an-existing-session-id"}, True),
    ({humanproof.COOKIE: ""}, False),
    ({}, False),
    ({"other_cookie": "an-existing-session-id"}, False),
])
def test_a_session_cookie_is_reused_when_present_else_a_fresh_one_is_minted(cookies, kept):
    got = humanproof.session_for(SimpleNamespace(cookies=cookies))
    assert (got == "an-existing-session-id") is kept
    assert got and humanproof.session_for(SimpleNamespace(cookies={})) != got


# --- the store's own line: an unproven dashboard caller is not a service channel ----------

AGENT_ENV = {config.ACTOR_ENV: config.AUTO_ORCHESTRATOR_ACTOR, "CLAUDECODE": "1"}


@pytest.mark.parametrize("env", [{}, AGENT_ENV])
def test_decide_refuses_an_unproven_dashboard_caller(repo, env):  # noqa: F811
    """🔴 GUARD: `channel="dashboard"` alone no longer earns the exemption: without
    `browser_proven` it is refused, markers or none, and nothing merges."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "dashboard", channel="dashboard", env=env)
    squash.assert_not_called()
    assert out == {"ok": False, "state": None, "refused": True,
                   "detail": overrides.UNPROVEN_DASHBOARD}
    assert "browser session" in out["detail"]
    assert overrides.load(rid)["state"] == overrides.PENDING


def test_decide_merges_for_a_proven_dashboard_caller_even_with_agent_markers(repo):  # noqa: F811
    """The proven dashboard keeps CMX-21's exemption: the service's own env may carry markers."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "dashboard", channel="dashboard",
                               browser_proven=True, env=dict(AGENT_ENV))
    assert out["ok"] is True and out["state"] == overrides.MERGED
    squash.assert_called_once()


def test_the_telegram_channel_is_unchanged_and_needs_no_browser_proof(repo):  # noqa: F811
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "telegram", channel="telegram", env=dict(AGENT_ENV))
    assert out["ok"] is True and out["state"] == overrides.MERGED
    squash.assert_called_once()


@pytest.mark.parametrize("channel", ["terminal", "telegramx", ""])
def test_browser_proven_does_not_exempt_any_other_channel(repo, channel):  # noqa: F811
    """The flag is the dashboard's word only: an agent on any other channel cannot buy the
    exemption by passing it, and a near-miss channel name is not Telegram."""
    _seed(repo)
    rid = _request()["request_id"]
    with _github() as squash:
        out = overrides.decide(rid, True, "x", channel=channel, browser_proven=True,
                               env={"CLAUDECODE": "1"})
    squash.assert_not_called()
    assert out["refused"] is True and out["detail"] != overrides.UNPROVEN_DASHBOARD
    assert overrides.load(rid)["state"] == overrides.PENDING
