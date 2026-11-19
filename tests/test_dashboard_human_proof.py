"""🔐 CMX-392 — a dashboard decision (override approve/deny, access-request approve/deny/revoke)
must prove a HUMAN BROWSER SESSION, not merely "came from loopback".

The hole: a dispatched agent POSTed ``/api/override/<id>/approve`` with ``{"confirm": true}``
from ``python -c "import urllib.request…"`` — no Origin header, so ``_override_same_origin``
waved it through; the merge-gate hook only string-matches curl/wget argv; and the dashboard
is a SERVICE channel, so ``overrides.decide`` skipped its agent refusal. The always-human PR
merged on the agent's own say-so.

The fix (``chela/dashboard/humanproof.py``): the page a browser loads sets an HttpOnly,
SameSite=Lax session cookie and embeds that session's token (an HMAC under a secret that
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
    assert "SameSite=Lax" in page.headers["Set-Cookie"]
    # the page embeds a live token: never cached, so a back-button copy can't replay a stale one
    assert page.headers["Cache-Control"] == "no-store"
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


_REASON = {
    "no Origin": "no Origin header",
    "foreign Origin": "cross-origin request",
    "null Origin": "cross-origin request",
    "cross-site fetch": "cross-site request",
    "no cookie": "no dashboard session",
    "no token": "missing or stale session token",
    "wrong token": "missing or stale session token",
    "another session's token": "missing or stale session token",
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
    assert resp.get_json()["ok"] is False
    # each shape is refused for ITS reason, not by a later check that happens to also miss
    assert _REASON[shape] in resp.get_json()["detail"], (shape, resp.get_json()["detail"])
    squash.assert_not_called()
    assert overrides.load(rid)["state"] == overrides.PENDING


@pytest.mark.parametrize("fetch_site", [None, "same-origin", "none"])
def test_the_positive_control_of_the_shapes_table_approves(repo, fetch_site):  # noqa: F811
    """Same headers as the table, nothing removed ⇒ approved: each refusal above is caused by
    the ONE thing that shape removed. A real browser's fetch sends ``Sec-Fetch-Site:
    same-origin`` (a typed-in navigation sends ``none``); both must still approve."""
    _seed(repo)
    rid = _request()["request_id"]
    sid = humanproof.new_session_id()
    client = app.test_client()
    client.set_cookie(humanproof.COOKIE, sid)
    with _github() as squash:
        resp = client.post(f"/api/override/{rid}/approve", json={"confirm": True},
                           headers={"Origin": "http://localhost",
                                    "X-Chela-Session-Token": humanproof.token_for(sid),
                                    **({"Sec-Fetch-Site": fetch_site} if fetch_site else {})})
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


def test_the_key_is_random_per_process_so_an_agent_cannot_mint_a_token():
    """🔴 GUARD: the token is only a proof while its key exists solely in the dashboard's
    memory. Two fresh interpreters (what an agent's `python -c` is) each compute the token for
    the SAME session id; neither matches the other or this process's. A key that is fixed,
    derived, empty or read from anywhere an agent can read makes them collide → RED."""
    import subprocess
    import sys
    sid = "abcdefghijklmnopqrstuvwx"
    code = f"from chela.dashboard import humanproof as h; print(h.token_for({sid!r}))"
    minted = [subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                             timeout=60, check=True).stdout.strip() for _ in range(2)]
    assert all(len(m) == 64 for m in minted), minted
    assert len({*minted, humanproof.token_for(sid)}) == 3
    assert len(humanproof._SECRET) >= 32


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


# --- EVERY decide route × EVERY missing proof (the invariant, not a sample of it) ----------
#
# The shapes table above drives one route. A guard pinned to one route lets a sibling route's
# check be dropped, weakened, or made to always refuse without a test going red. Here every
# route that decides on the operator's behalf is driven through every missing-proof shape
# (refused 403 for THAT shape's reason, its side effect never reached) AND through the full
# proof (side effect reached exactly once: a check that refuses everyone is a broken button).

_EXTRA_SHAPES = {
    # a sibling port on localhost is `same-site`, not `same-origin`: still not this page
    "same-site fetch": "cross-site request",
    "empty Origin": "no Origin header",
    "truncated token": "missing or stale session token",
    "token with a suffix": "missing or stale session token",
    "token in upper case": "missing or stale session token",
    "token without its cookie's session": "no dashboard session",
}


def _all_shapes(sid: str, token: str) -> dict:
    good = {"Origin": "http://localhost", "X-Chela-Session-Token": token}
    return {**_shapes(sid, token),
            "same-site fetch": ({**good, "Sec-Fetch-Site": "same-site"}, sid),
            "empty Origin": ({**good, "Origin": ""}, sid),
            "truncated token": ({**good, "X-Chela-Session-Token": token[:32]}, sid),
            "token with a suffix": ({**good, "X-Chela-Session-Token": token + "0"}, sid),
            "token in upper case": ({**good, "X-Chela-Session-Token": token.upper()}, sid),
            "token without its cookie's session": (good, None)}


def _sinks(monkeypatch) -> list:
    """Every decide route's side effect, recorded instead of performed."""
    from chela.dashboard import app as dash
    calls: list = []
    monkeypatch.setattr(dash, "_override_decide", lambda rid, approve:
                        calls.append(("override", rid, approve))
                        or {"ok": True, "state": overrides.MERGED if approve else overrides.DENIED})
    for name in ("approve", "deny"):
        monkeypatch.setattr(dash.share_requests, name,
                            lambda *a, _n=name, **k: calls.append(("share", _n)) or (True, "ok"))
    monkeypatch.setattr(dash.share_requests, "revoke",
                        lambda *a, **k: calls.append(("share", "revoke")) or True)
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [{"task_id": "CMX-0"}])
    monkeypatch.setattr(dash, "_merge_one", lambda row: calls.append(("merge", row["task_id"]))
                        or {"ok": True})
    monkeypatch.setattr(overrides, "sweep", lambda: None)
    monkeypatch.setattr(overrides, "load", lambda rid: None)
    return calls


# route → (method kwargs, the call the full proof must reach, where the reason is reported)
_ROUTES = {
    "api override approve": ("/api/override/r-1/approve", {"json": {"confirm": True}},
                             ("override", "r-1", True)),
    "api override deny": ("/api/override/r-1/deny", {"json": {}}, ("override", "r-1", False)),
    "confirm page approve": ("/override/r-1", {"data": {"decision": "approve"}},
                             ("override", "r-1", True)),
    "confirm page deny": ("/override/r-1", {"data": {"decision": "deny"}},
                          ("override", "r-1", False)),
    "access approve": ("/api/share-requests/q-1/approve", {"json": {}}, ("share", "approve")),
    "access deny": ("/api/share-requests/q-1/deny", {"json": {}}, ("share", "deny")),
    "access revoke": ("/api/share-requests/q-1/revoke", {"json": {}}, ("share", "revoke")),
    "card merge": ("/api/dispatcher/runs/CMX-0/merge", {"json": {}}, ("merge", "CMX-0")),
}


def _drive(route: str, headers: dict, cookie: str | None):
    path, kwargs, _ = _ROUTES[route]
    client = app.test_client()
    if cookie:
        client.set_cookie(humanproof.COOKIE, cookie)
    return client.post(path, headers=headers, **kwargs)


@pytest.mark.parametrize("shape", [*_REASON, *_EXTRA_SHAPES])
@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_decide_route_refuses_every_missing_proof(route, shape, monkeypatch):
    calls = _sinks(monkeypatch)
    sid = humanproof.new_session_id()
    headers, cookie = _all_shapes(sid, humanproof.token_for(sid))[shape]
    resp = _drive(route, headers, cookie)
    assert resp.status_code == 403, (route, shape, resp.status_code)
    assert calls == [], (route, shape, calls)
    reason = {**_REASON, **_EXTRA_SHAPES}[shape]
    assert reason in resp.get_data(as_text=True), (route, shape, resp.get_data(as_text=True))


@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_decide_route_acts_once_for_the_full_proof(route, monkeypatch):
    calls = _sinks(monkeypatch)
    sid = humanproof.new_session_id()
    resp = _drive(route, {"Origin": "http://localhost",
                          "X-Chela-Session-Token": humanproof.token_for(sid)}, sid)
    assert resp.status_code == 200, (route, resp.get_data(as_text=True))
    assert calls == [_ROUTES[route][2]]


def test_the_confirm_form_token_alone_without_a_cookie_is_refused(monkeypatch):
    """The form-field path is the same proof as the header: the token alone is not a session."""
    calls = _sinks(monkeypatch)
    sid = humanproof.new_session_id()
    resp = app.test_client().post("/override/r-1", headers={"Origin": "http://localhost"},
                                  data={"decision": "approve",
                                        humanproof.FORM_FIELD: humanproof.token_for(sid)})
    assert resp.status_code == 403 and calls == []


# --- one browser, many tabs: a page load never strands another tab's token -----------------

def test_a_second_page_load_keeps_the_session_so_an_open_tab_still_decides(repo):  # noqa: F811
    """The SPA tab holds the token it booted with. Opening the confirm page from the
    notification (or reloading `/`) in the same browser must REUSE the session, or the open
    tab's next Approve is refused with "stale token". Each page load returns the same token,
    and the SPA's token still approves after the confirm page was opened."""
    import re
    _seed(repo)
    rid = _request()["request_id"]
    client = app.test_client()
    first = client.get("/")
    spa = re.search(rb'"sessionToken":\s*"([0-9a-f]{64})"', first.data).group(1).decode()
    cookie = first.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Lax" in cookie and "Path=/" in cookie
    again = re.search(rb'"sessionToken":\s*"([0-9a-f]{64})"', client.get("/").data).group(1)
    assert again.decode() == spa
    assert _page_token(client.get(f"/override/{rid}").data) == spa
    with _github() as squash:
        ok = client.post(f"/api/override/{rid}/approve", json={"confirm": True},
                         headers={"Origin": "http://localhost", humanproof.HEADER: spa})
    assert ok.status_code == 200, ok.get_json()
    squash.assert_called_once()


def test_two_browsers_get_two_sessions_and_neither_token_works_in_the_other():
    """Session reuse is per-cookie, never global: a fresh client is a fresh session."""
    import re
    tok = [re.search(rb'"sessionToken":\s*"([0-9a-f]{64})"', app.test_client().get("/").data)
           .group(1).decode() for _ in range(2)]
    assert tok[0] != tok[1]


# --- the Origin must name THIS host:port, not merely this hostname -------------------------
# Every request above reaches a portless "localhost", where host:port and hostname compare
# equal, so a check loosened to the hostname (or to a substring) stayed green (DEFEAT_SHAPES
# #392c). The dashboard really serves on a port (5005); a page on a SIBLING port of the same
# host (another local service, an agent's own `python -m http.server`) is a different origin.
# Sec-Fetch-Site is omitted so the Origin comparison is the ONLY check that can refuse.

_HOST = "localhost:5005"

_PORTED_ORIGINS = {
    "sibling port": "http://localhost:5006",
    "this hostname without the port": "http://localhost",
    "a prefix of this host:port": "http://localhost:500",
    "this host:port as a prefix": "http://localhost:50055",
    "this host:port inside another host": "http://localhost.5005.evil.example",
    "another hostname on this port": "http://127.0.0.1:5005",
    "userinfo before this host:port": "http://evil@localhost:5005",
    # the scheme varies too (DEFEAT_SHAPES #392d): an https page one component away is
    # refused just the same, so a check that only parses an `http://` prefix goes red
    "https sibling port": "https://localhost:5006",
    "https hostname without the port": "https://localhost",
    "https userinfo before this host:port": "https://evil@localhost:5005",
}


def _drive_ported(route: str, origin: str, sid: str):
    path, kwargs, _ = _ROUTES[route]
    client = app.test_client()
    client.set_cookie(humanproof.COOKIE, sid, domain="localhost")
    return client.post(path, base_url=f"http://{_HOST}",
                       headers={"Origin": origin, humanproof.HEADER: humanproof.token_for(sid)},
                       **kwargs)


@pytest.mark.parametrize("shape", list(_PORTED_ORIGINS))
@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_decide_route_refuses_an_origin_that_is_not_this_host_and_port(route, shape,
                                                                             monkeypatch):
    calls = _sinks(monkeypatch)
    resp = _drive_ported(route, _PORTED_ORIGINS[shape], humanproof.new_session_id())
    assert resp.status_code == 403, (route, shape, resp.status_code)
    assert calls == [], (route, shape, calls)
    assert "cross-origin request" in resp.get_data(as_text=True), (route, shape)


@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_decide_route_accepts_the_origin_naming_this_host_and_port(route, scheme,
                                                                         monkeypatch):
    """Positive control: the same cookie + token with Origin == this host:port decides, so
    each refusal above is the Origin's alone. Under BOTH schemes: behind the TLS-terminating
    proxy (Caddy on the tailnet) the browser's Origin is `https://` while Flask sees plain
    http, so the scheme is deliberately not compared; a check that does, or that only parses
    an `http://` Origin, refuses the real deployment's Approve button."""
    calls = _sinks(monkeypatch)
    resp = _drive_ported(route, f"{scheme}://{_HOST}", humanproof.new_session_id())
    assert resp.status_code == 200, (route, resp.get_data(as_text=True))
    assert calls == [_ROUTES[route][2]]


def test_the_agent_on_a_sibling_port_cannot_approve_over_a_real_socket(repo, server):  # noqa: F811
    """End to end on a real port: a full browser session (page cookie + token) whose Origin
    names a sibling port of the dashboard's own host is refused, and nothing merges."""
    import http.cookiejar
    import re
    from urllib.parse import urlparse
    _seed(repo)
    rid = _request()["request_id"]
    port = urlparse(server).port
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    page = opener.open(f"{server}/override/{rid}", timeout=10).read()
    token = re.search(rb'name="session_token" value="([0-9a-f]{64})"', page).group(1).decode()

    def post(origin: str) -> int:
        req = urllib.request.Request(f"{server}/api/override/{rid}/approve", method="POST",
                                     data=json.dumps({"confirm": True}).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Origin": origin, humanproof.HEADER: token})
        try:
            return opener.open(req, timeout=10).status
        except urllib.error.HTTPError as e:
            return e.code

    with _github() as squash:
        assert post(f"http://127.0.0.1:{port + 1}") == 403
        squash.assert_not_called()
        assert overrides.load(rid)["state"] == overrides.PENDING
        assert post(f"http://127.0.0.1:{port}") == 200   # the same session, its own origin
    squash.assert_called_once()
