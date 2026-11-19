"""🔐 CMX-392 slice 2: share-request decisions and the card's Merge prove the operator's
BROWSER SESSION, the same proof slice 1 (``tests/test_dashboard_human_proof.py``) puts on
override decisions.

The hole: ``/api/share-requests/<id>/approve`` grants a guest access to a host directory and
``/api/dispatcher/runs/<id>/merge`` runs ``gh pr merge`` itself. Both acted on any POST that
reached loopback, which every dispatched agent does: an agent could merge its own PR, or grant
a guest a mount, with one ``urllib`` call.

Every guard is driven through EVERY route (approve, deny, revoke, merge), each proof removed
on its own while the other two stay valid, on a host WITH a port, under BOTH schemes.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest

from chela import share_requests as sr

flask = pytest.importorskip("flask")

from chela.dashboard import app as dash  # noqa: E402
from chela.dashboard import humanproof  # noqa: E402
from tests.test_share_requests import T0, _filed, allowed, typing_on  # noqa: E402,F401

app = dash.app
HOST = "localhost:5005"


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


def _agent_post(url: str, body: dict) -> int:
    """An agent's raw ``urllib`` POST: JSON, no Origin, no cookie, no token."""
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as e:
        return e.code


def _status(rid: str) -> str:
    return next(r["status"] for r in sr.listing(now=T0) if r["id"] == rid)


# --- the repro: the agent's raw POST is refused and changes nothing ---------------------------

@pytest.mark.parametrize("action,body", [("approve", {"minutes": 60, "rw": True}),
                                         ("deny", {})])
def test_an_agent_urllib_post_cannot_decide_a_share_request(typing_on, allowed, server,  # noqa: F811
                                                            action, body):
    """🔴 REPRO (red on dev): approve/deny over a real socket without the proof ⇒ 403 and the
    request is still pending."""
    rec = _filed(target=allowed)
    assert _agent_post(f"{server}/api/share-requests/{rec['id']}/{action}", body) == 403
    assert _status(rec["id"]) == sr.PENDING


def test_an_agent_urllib_post_cannot_revoke_an_approval(typing_on, allowed, server):  # noqa: F811
    rec = _filed(target=allowed)
    assert sr.approve(rec["id"], by="op", now=T0)[0]
    assert _agent_post(f"{server}/api/share-requests/{rec['id']}/revoke", {}) == 403
    assert _status(rec["id"]) == sr.APPROVED


@pytest.fixture
def mergeable(tmp_path, monkeypatch):
    """A run whose PR would merge for real: CI none, MERGEABLE, every ``subprocess.run`` in
    the dashboard recorded instead of run (``gh pr merge`` among them)."""
    row = {"task_id": "CMX-7", "pr_url": "https://github.com/o/r/pull/77",
           "workflow_path": str(tmp_path / "WORKFLOW.md")}
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda *a, **k: [row])
    monkeypatch.setattr(dash.dispatcher, "_read_pr_checks",
                        lambda *a, **k: SimpleNamespace(state=dash.dispatcher.CI_NONE,
                                                        failing=[], detail="", head_sha=""))
    monkeypatch.setattr(dash, "_pr_mergeable", lambda *a, **k: "MERGEABLE")
    calls: list = []

    def fake_run(argv, **kw):
        calls.append((list(argv), kw.get("cwd")))
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(dash.subprocess, "run", fake_run)
    return SimpleNamespace(calls=calls, cwd=str(tmp_path))


def test_an_agent_urllib_post_cannot_press_the_card_merge(mergeable, server):
    """🔴 REPRO (red on dev): the card's Merge over a real socket without the proof ⇒ 403 and
    no ``gh pr merge`` runs (nor any other command)."""
    assert _agent_post(f"{server}/api/dispatcher/runs/CMX-7/merge", {}) == 403
    assert mergeable.calls == []


def test_the_card_merge_with_the_page_session_still_runs_gh_pr_merge(mergeable):
    """The dashboard's own button: `/` issues the session, the card POSTs its token ⇒ the
    squash merge runs, with its exact argv and cwd."""
    import re
    client = app.test_client()
    page = client.get("/", base_url=f"https://{HOST}")
    token = re.search(rb'"sessionToken":\s*"([0-9a-f]{64})"', page.data).group(1).decode()
    resp = client.post("/api/dispatcher/runs/CMX-7/merge", base_url=f"https://{HOST}",
                       headers={"Origin": f"https://{HOST}", "X-Chela-Session-Token": token})
    assert resp.status_code == 200, resp.get_json()
    assert mergeable.calls[0] == (["gh", "pr", "merge", "77", "--squash"], mergeable.cwd)


# --- EVERY route × EVERY missing or wrong proof ----------------------------------------------
#
# The side effects are recorded instead of performed. Each row removes or corrupts ONE proof
# and keeps the other two valid, so that proof is the only thing that can refuse.

_ROUTES = {
    "share approve": ("/api/share-requests/r-1/approve", ("approve", "r-1")),
    "share deny": ("/api/share-requests/r-1/deny", ("deny", "r-1")),
    "share revoke": ("/api/share-requests/r-1/revoke", ("revoke", "r-1")),
    "card merge": ("/api/dispatcher/runs/CMX-1/merge", ("merge", "CMX-1")),
}


@pytest.fixture
def sink(monkeypatch):
    calls: list = []
    monkeypatch.setattr(sr, "approve", lambda rid, **k: calls.append(("approve", rid)) or (True, "ok"))
    monkeypatch.setattr(sr, "deny", lambda rid, **k: calls.append(("deny", rid)) or (True, "ok"))
    monkeypatch.setattr(sr, "revoke", lambda rid, **k: calls.append(("revoke", rid)) or True)
    monkeypatch.setattr(dash.dispatcher, "list_runs",
                        lambda *a, **k: calls.append(("list_runs", None)) or [{"task_id": "CMX-1"}])
    monkeypatch.setattr(dash, "_merge_one",
                        lambda row: calls.append(("merge", row["task_id"])) or {"ok": True})
    return calls


def _drive(route: str, *, origin, cookie, token, scheme="http", fetch_site=None):
    client = app.test_client()
    if cookie is not None:
        client.set_cookie(humanproof.COOKIE, cookie, domain="localhost")
    headers = {} if origin is None else {"Origin": origin}
    if fetch_site is not None:
        headers["Sec-Fetch-Site"] = fetch_site
    if token is not None:
        headers["X-Chela-Session-Token"] = token
    return client.post(_ROUTES[route][0], base_url=f"{scheme}://{HOST}", headers=headers,
                       json={})


NO_ORIGIN = "no Origin header"
CROSS = "cross-origin request"
CROSS_SITE = "cross-site request"
NO_SESSION = "no dashboard session"
STALE = "missing or stale session token"
OK = f"http://{HOST}"

_SHAPES = {
    "no Origin": (lambda s, t: (None, s, t), NO_ORIGIN),
    "empty Origin": (lambda s, t: ("", s, t), NO_ORIGIN),
    "null Origin": (lambda s, t: ("null", s, t), CROSS),
    "foreign Origin": (lambda s, t: ("http://evil.example", s, t), CROSS),
    "sibling port": (lambda s, t: ("http://localhost:5006", s, t), CROSS),
    "https sibling port": (lambda s, t: ("https://localhost:5006", s, t), CROSS),
    "hostname without the port": (lambda s, t: ("http://localhost", s, t), CROSS),
    "https hostname without the port": (lambda s, t: ("https://localhost", s, t), CROSS),
    "another hostname on this port": (lambda s, t: ("http://127.0.0.1:5005", s, t), CROSS),
    "userinfo before host:port": (lambda s, t: ("https://evil@localhost:5005", s, t), CROSS),
    "cross-site fetch": (lambda s, t: (OK, s, t, "cross-site"), CROSS_SITE),
    "same-site fetch": (lambda s, t: (OK, s, t, "same-site"), CROSS_SITE),
    "no cookie": (lambda s, t: (OK, None, t), NO_SESSION),
    "empty cookie": (lambda s, t: (OK, "", t), NO_SESSION),
    "another session's cookie": (lambda s, t: (OK, s + "x", t), STALE),
    "no token": (lambda s, t: (OK, s, None), STALE),
    "empty token": (lambda s, t: (OK, s, ""), STALE),
    "wrong token": (lambda s, t: (OK, s, "0" * 64), STALE),
    "another session's token": (lambda s, t: (OK, s, humanproof.token_for(s + "x")), STALE),
    "the session id as its own token": (lambda s, t: (OK, s, s), STALE),
}


@pytest.mark.parametrize("shape", list(_SHAPES))
@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_route_refuses_every_missing_or_wrong_proof(route, shape, sink):
    build, reason = _SHAPES[shape]
    sid = humanproof.new_session_id()
    origin, cookie, token, *fetch_site = build(sid, humanproof.token_for(sid))
    resp = _drive(route, origin=origin, cookie=cookie, token=token,
                  fetch_site=fetch_site[0] if fetch_site else None)
    assert resp.status_code == 403, (route, shape, resp.status_code)
    assert sink == [], (route, shape)
    body = resp.get_json()
    assert body["ok"] is False and reason in body["error"], (route, shape, body)


@pytest.mark.parametrize("fetch_site", [None, "same-origin", "none"])
@pytest.mark.parametrize("scheme", ["http", "https"])
@pytest.mark.parametrize("route", list(_ROUTES))
def test_every_route_acts_once_for_the_full_proof(route, scheme, fetch_site, sink):
    """Positive control: the same rows with nothing removed act, under BOTH Origin schemes,
    each against a server that sees either scheme (the TLS-terminating proxy case)."""
    sid = humanproof.new_session_id()
    for served in ("http", "https"):
        resp = _drive(route, origin=f"{scheme}://{HOST}", cookie=sid,
                      token=humanproof.token_for(sid), scheme=served, fetch_site=fetch_site)
        assert resp.status_code == 200, (route, scheme, served, resp.get_json())
    acted = [c for c in sink if c[0] != "list_runs"]
    assert acted == [_ROUTES[route][1]] * 2
