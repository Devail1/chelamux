"""🔐 CMX-447 (CMX-392 follow-up): the routes that change what agents may do, or act as the
operator, prove the operator's BROWSER SESSION, the same proof override decisions, share-request
decisions and the card's Merge carry (``tests/test_dashboard_human_proof_share_merge.py``).

The hole: every one of these acted on any request that reached loopback, which every dispatched
agent does. An agent could loosen the permission mode of every future dispatch
(``/api/config``), pull and restart every chela service (``/api/update/apply``), put a prompt on
a timer (``/api/schedules``), write to a repo or the tracker as the operator (``/api/dispatcher/
init``, ``/backlog/promote``, ``/linear/issue``), or share a terminal unsandboxed after reading the
window name the confirm asks for (``/api/term/<wid>/share``, ``/share-mode``).

Each route's first side effect is replaced by a recorder that answers 418, so a refusal is a 403
with an empty record and a pass is a 418 with exactly one entry: the route reached its work.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

flask = pytest.importorskip("flask")

from werkzeug.exceptions import abort  # noqa: E402

from chela.dashboard import app as dash  # noqa: E402
from chela.dashboard import humanproof  # noqa: E402
from tests._browser_session import as_browser  # noqa: E402
from tests.test_dashboard_human_proof_share_merge import _SHAPES, HOST  # noqa: E402

app = dash.app

# route → (method, path, the first side effect the recorder stands in for)
_ROUTES = {
    "config": ("POST", "/api/config", "body"),
    "config dispatch": ("POST", "/api/config/dispatch", "body"),
    "config concurrency": ("POST", "/api/config/concurrency", "body"),
    "config timing": ("POST", "/api/config/timing", "body"),
    "update apply": ("POST", "/api/update/apply", "update"),
    "schedule add": ("POST", "/api/schedules", "body"),
    "schedule toggle": ("PATCH", "/api/schedules/7", "body"),
    "schedule delete": ("DELETE", "/api/schedules/7", "remove_task"),
    "backlog promote": ("POST", "/api/dispatcher/backlog/promote", "body"),
    "dispatcher init": ("POST", "/api/dispatcher/init", "body"),
    "linear issue": ("POST", "/api/dispatcher/linear/issue", "body"),
    "term share": ("POST", "/api/term/@9/share", "terminals"),
    "term share mode": ("POST", "/api/term/@9/share-mode", "terminals"),
}


@pytest.fixture
def sink(monkeypatch):
    """Record each route's first act instead of performing it, then stop it with a 418."""
    calls: list = []

    def rec(what):
        def stub(*a, **k):
            calls.append(what)
            abort(418)
        return stub
    monkeypatch.setattr(flask.Request, "get_json", rec("body"))
    monkeypatch.setattr(dash.update, "commits_behind", rec("update"))
    monkeypatch.setattr(dash.scheduler, "remove_task", rec("remove_task"))
    monkeypatch.setattr(dash, "_require_terminals", rec("terminals"))
    return calls


def _drive(client, route: str, *, scheme="http", **headers):
    method, path, _ = _ROUTES[route]
    return client.open(path, method=method, base_url=f"{scheme}://{HOST}", headers=headers,
                       data=b"{}", content_type="application/json")


def _client(origin, cookie, token, fetch_site=None):
    client = app.test_client()
    if cookie is not None:
        client.set_cookie(humanproof.COOKIE, cookie, domain="localhost")
    headers = {} if origin is None else {"Origin": origin}
    if fetch_site is not None:
        headers["Sec-Fetch-Site"] = fetch_site
    if token is not None:
        headers["X-Chela-Session-Token"] = token
    return client, headers


# --- EVERY route × EVERY missing or wrong proof ----------------------------------------------

@pytest.mark.parametrize("shape", list(_SHAPES))
@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_route_refuses_every_missing_or_wrong_proof(route, shape, sink):
    """Each row removes or corrupts ONE proof and keeps the other two valid."""
    build, reason = _SHAPES[shape]
    sid = humanproof.new_session_id()
    origin, cookie, token, *fetch_site = build(sid, humanproof.token_for(sid))
    client, headers = _client(origin, cookie, token, fetch_site[0] if fetch_site else None)
    resp = _drive(client, route, **headers)
    assert resp.status_code == 403, (route, shape, resp.status_code)
    assert sink == [], (route, shape)
    body = resp.get_json()
    if route == "linear issue" and fetch_site == ["cross-site"]:
        # Refused one check earlier: a cross-site fetch reads as a share guest there.
        reason = "Share guests cannot create tasks."
    assert body["ok"] is False and reason in body["error"], (route, shape, body)


@pytest.mark.parametrize("fetch_site", [None, "same-origin", "none"])
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_route_reaches_its_work_with_the_full_proof(route, scheme, fetch_site, sink):
    """Positive control: nothing removed ⇒ the route acts, under BOTH Origin schemes, against a
    server that sees either scheme (the TLS-terminating proxy case)."""
    sid = humanproof.new_session_id()
    for served in ("http", "https"):
        client, headers = _client(f"{scheme}://{HOST}", sid, humanproof.token_for(sid), fetch_site)
        resp = _drive(client, route, scheme=served, **headers)
        assert resp.status_code == 418, (route, scheme, served, resp.status_code)
    assert sink == [_ROUTES[route][2]] * 2


@pytest.mark.parametrize("path", ["/api/config", "/api/config/dispatch",
                                  "/api/config/concurrency", "/api/config/timing"])
def test_reading_the_settings_needs_no_proof(path):
    """Only the WRITE is the operator's act: a GET of the same route stays readable, so the
    Settings tabs (and `curl` from a terminal) can still show what is set."""
    resp = app.test_client().get(path)
    assert resp.status_code == 200, (path, resp.status_code)
    assert "error" not in (resp.get_json() or {})


# --- the repro over a real socket, and the real effects --------------------------------------

@pytest.fixture
def server():
    """The REAL dashboard app behind a real HTTP socket on loopback: what an agent reaches."""
    import threading

    from werkzeug.serving import make_server
    srv = make_server("127.0.0.1", 0, app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    t.join(timeout=5)


@pytest.mark.parametrize("route", list(_ROUTES))
def test_an_agent_urllib_request_is_refused_on_every_route(route, sink, server):
    """🔴 REPRO (red on dev): an agent's raw ``urllib`` call (JSON, no Origin, no cookie, no
    token) over a real socket ⇒ 403, and the route never reaches its work."""
    method, path, _ = _ROUTES[route]
    req = urllib.request.Request(server + path, data=b"{}", method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
    except urllib.error.HTTPError as e:
        status = e.code
    assert status == 403, route
    assert sink == [], route


def test_an_agent_cannot_loosen_the_permission_mode_but_the_page_can(monkeypatch, tmp_path):
    """The motivating case, unstubbed: the stored permission mode is untouched by a POST without
    the proof, and set by the same POST from the page's session."""
    from chela import userconfig
    monkeypatch.setattr(userconfig, "_PATH", tmp_path / "config.json")
    body = json.dumps({"agent_permission_mode": "bypassPermissions"})
    resp = app.test_client().post("/api/config", data=body, content_type="application/json")
    assert resp.status_code == 403
    assert userconfig.get("agent_permission_mode") is None
    resp = as_browser(app.test_client()).post("/api/config", data=body,
                                              content_type="application/json")
    assert resp.status_code == 200, resp.get_json()
    assert userconfig.get("agent_permission_mode") == "bypassPermissions"
