"""🪪 CMX-385 — attribute dashboard actions to the user a trusted proxy names.

* tailscale mode: ``Tailscale-User-Login`` from a LOOPBACK peer is the ``by``; the same
  header from any other peer is refused (403) and logged — and is attribution, not authority;
* cloudflare-access mode: ``Cf-Access-Jwt-Assertion`` is VERIFIED (signature, aud, iss, exp)
  with locally generated keys — no network; a missing/invalid token is 401;
* off mode (the default): exactly today's behaviour;
* every mutating request records ``dashboard.action`` with its ``by``.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from chela import config, contract, dispatcher, event_log, overrides
from chela.dashboard import identity
from tests import test_pending_approvals as pa

pytest.importorskip("flask")

WHO = "op@example.com"
TEAM = "myteam.cloudflareaccess.com"
AUD = "a" * 64
LOOP = {"REMOTE_ADDR": "127.0.0.1"}
TAILNET = {"REMOTE_ADDR": "100.64.0.5"}


def _events(kind: str) -> list[dict]:
    return event_log.read(types=[kind])["events"]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """test_pending_approvals' isolation, plus the identity setting OFF unless a test sets it."""
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    for env in (config.ACTOR_ENV, "CLAUDECODE", overrides.TTL_ENV, "TELEGRAM_BOT_TOKEN",
                "TELEGRAM_CHAT_ID", config.DASHBOARD_URL_ENV,
                identity.MODE_ENV, identity.CF_TEAM_ENV, identity.CF_AUD_ENV):
        monkeypatch.delenv(env, raising=False)
    monkeypatch.setattr(contract.notify, "enabled", lambda: False)
    monkeypatch.setattr(overrides.notify, "enabled", lambda: False)


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "WORKFLOW.md").write_text("# wf\n")
    return tmp_path


@pytest.fixture
def client():
    from chela.dashboard.app import app
    return app.test_client()


@pytest.fixture
def tailscale(monkeypatch):
    monkeypatch.setenv(identity.MODE_ENV, "tailscale")


# --- the pieces --------------------------------------------------------------------------

@pytest.mark.parametrize("addr, loop", [
    ("127.0.0.1", True), ("127.5.5.5", True), ("::1", True), ("::ffff:127.0.0.1", True),
    ("100.64.0.5", False), ("10.0.0.1", False), ("::ffff:10.0.0.1", False),
    ("fe80::1%eth0", False), ("0.0.0.0", False), ("", False), (None, False),
    ("localhost", False), ("127.0.0.1.evil", False),
])
def test_is_loopback(addr, loop):
    assert identity.is_loopback(addr) is loop


@pytest.mark.parametrize("raw, want", [
    (None, "off"), ("", "off"), ("off", "off"), ("none", "off"), ("false", "off"),
    ("tailscale", "tailscale"), (" Tailscale ", "tailscale"),
    ("cloudflare-access", "cloudflare-access"), ("CLOUDFLARE-ACCESS", "cloudflare-access"),
])
def test_mode(monkeypatch, raw, want):
    if raw is not None:
        monkeypatch.setenv("CHELA_DASHBOARD_IDENTITY_HEADER", raw)
    assert identity.mode() == want


@pytest.mark.parametrize("raw", ["tailscal", "cloudflare", "on", "true"])
def test_an_unknown_mode_is_a_config_error_not_off(monkeypatch, raw):
    monkeypatch.setenv("CHELA_DASHBOARD_IDENTITY_HEADER", raw)
    with pytest.raises(identity.ConfigError):
        identity.mode()


def test_cloudflare_settings(monkeypatch):
    monkeypatch.setenv("CHELA_DASHBOARD_CF_TEAM_DOMAIN", f"https://{TEAM}/")
    monkeypatch.setenv("CHELA_DASHBOARD_CF_AUD", AUD)
    assert identity.cloudflare_settings() == (TEAM, AUD)
    monkeypatch.setenv("CHELA_DASHBOARD_CF_TEAM_DOMAIN", f"{TEAM}/evil")
    with pytest.raises(identity.ConfigError):
        identity.cloudflare_settings()
    monkeypatch.setenv("CHELA_DASHBOARD_CF_TEAM_DOMAIN", TEAM)
    monkeypatch.delenv("CHELA_DASHBOARD_CF_AUD")
    with pytest.raises(identity.ConfigError, match="AUD"):
        identity.cloudflare_settings()
    monkeypatch.setenv("CHELA_DASHBOARD_CF_AUD", AUD)
    monkeypatch.setenv("CHELA_DASHBOARD_CF_TEAM_DOMAIN", "  ")
    with pytest.raises(identity.ConfigError, match="team domain"):
        identity.cloudflare_settings()


# --- tailscale mode ----------------------------------------------------------------------

def test_tailscale_loopback_header_attributes_the_approval(repo, client, tailscale, monkeypatch):
    """🔴 GUARD (acceptance): the approve event, the override record and the notice all
    carry the proxy's login — "approved by op@… via dashboard"."""
    notices: list[str] = []
    from chela import inbox
    monkeypatch.setattr(inbox, "enqueue", lambda kind, summary, payload: notices.append(summary) or True)
    pa._seed(repo)
    rid = pa._request()
    with pa._github():
        resp = client.post(f"/api/override/{rid}/approve", json={"confirm": True},
                           headers={"Tailscale-User-Login": WHO}, environ_base=LOOP)
    assert resp.status_code == 200 and resp.get_json()["state"] == overrides.MERGED
    assert overrides.load(rid)["decided_by"] == WHO
    (action,) = _events("dashboard.action")
    assert action["payload"]["by"] == WHO
    assert action["payload"]["endpoint"] == "api_override_decide"
    assert action["payload"]["verified"] is False and action["payload"]["mode"] == "tailscale"
    assert any(f"approved by {WHO} via dashboard" in n for n in notices), notices
    assert _events("dashboard.identity_refused") == []


def test_tailscale_header_from_a_non_loopback_peer_is_refused(repo, client, tailscale, monkeypatch):
    """🔴 GUARD (negative control for the loopback check): a spoofed header from the
    network is refused before the route runs, and the attempt is logged."""
    calls: list = []
    monkeypatch.setattr(overrides, "decide", lambda *a, **k: calls.append(a) or {"ok": True})
    resp = client.post("/api/override/override-x/approve", json={"confirm": True},
                       headers={"Tailscale-User-Login": WHO}, environ_base=TAILNET)
    assert resp.status_code == 403
    assert calls == []
    (ev,) = _events("dashboard.identity_refused")
    assert ev["payload"]["claimed"] == WHO and ev["payload"]["peer"] == "100.64.0.5"
    assert ev["payload"]["status"] == 403
    assert _events("dashboard.action") == []


def test_tailscale_without_the_header_is_unattributed_not_refused(client, tailscale, monkeypatch):
    seen: list = []
    monkeypatch.setattr(overrides, "decide",
                        lambda rid, approve, by, channel="terminal": seen.append(by)
                        or {"ok": True, "state": overrides.DENIED})
    resp = client.post("/api/override/override-x/deny", json={}, environ_base=TAILNET)
    assert resp.status_code == 200
    assert seen == ["dashboard"]
    (action,) = _events("dashboard.action")
    assert action["payload"]["by"] is None and "by unattributed" in action["summary"]


@pytest.mark.parametrize("bad", ["", "   ", "op\x1b[31m@example.com", "x" * 257])
def test_tailscale_malformed_header_is_refused(client, tailscale, bad):
    resp = client.get("/api/whoami", headers={"Tailscale-User-Login": bad}, environ_base=LOOP)
    assert resp.status_code == 403


def test_tailscale_whoami_and_name(client, tailscale):
    r = client.get("/api/whoami", headers={"Tailscale-User-Login": WHO,
                                           "Tailscale-User-Name": "Op Erator"}, environ_base=LOOP)
    assert r.get_json() == {"mode": "tailscale", "login": WHO, "name": "Op Erator",
                            "verified": False}


def test_share_request_decisions_carry_the_login(client, tailscale, monkeypatch):
    from chela import share_requests
    seen: list = []
    monkeypatch.setattr(share_requests, "deny", lambda rid, by: seen.append(by) or (True, "denied"))
    r = client.post("/api/share-requests/r1/deny", json={},
                    headers={"Tailscale-User-Login": WHO}, environ_base=LOOP)
    assert r.status_code == 200 and seen == [WHO]
    # unattributed keeps the peer-address fallback
    client.post("/api/share-requests/r1/deny", json={}, environ_base=TAILNET)
    assert seen[-1] == "dashboard@100.64.0.5"


def test_layout_write_records_its_by_and_a_failed_or_read_request_does_not(client, tailscale, monkeypatch):
    from chela import sidebar_layout
    monkeypatch.setattr(sidebar_layout, "update", lambda body: {"order": []})
    h = {"Tailscale-User-Login": WHO}
    assert client.post("/api/sidebar/layout", json={"order": []}, headers=h, environ_base=LOOP).status_code == 200
    assert client.post("/api/sidebar/layout", data="x", headers=h, environ_base=LOOP).status_code == 400
    assert client.get("/api/whoami", headers=h, environ_base=LOOP).status_code == 200
    (action,) = _events("dashboard.action")
    assert (action["payload"]["by"], action["payload"]["method"], action["payload"]["path"],
            action["payload"]["status"]) == (WHO, "POST", "/api/sidebar/layout", 200)


def test_machine_endpoints_are_not_recorded_as_actions(client, tailscale):
    client.post("/hooks/Nope", json={}, environ_base=LOOP)
    client.post("/api/inbox/denial", json={}, environ_base=LOOP)
    assert _events("dashboard.action") == []


def test_the_page_boot_names_the_identity(client, tailscale):
    html = client.get("/", headers={"Tailscale-User-Login": WHO}, environ_base=LOOP).get_data(as_text=True)
    assert f'"login": "{WHO}"' in html
    html = client.get("/", environ_base=LOOP).get_data(as_text=True)
    assert '"identity": null' in html


# --- off mode: exactly today's -----------------------------------------------------------

def test_off_mode_is_todays_behaviour(client, monkeypatch):
    """🔴 GUARD: no refusal, no new events, and the legacy ``dashboard:<who>`` spelling."""
    seen: list = []
    monkeypatch.setattr(overrides, "decide",
                        lambda rid, approve, by, channel="terminal": seen.append(by)
                        or {"ok": True, "state": overrides.DENIED})
    for env in (LOOP, TAILNET):
        r = client.post("/api/override/override-x/deny", json={},
                        headers={"Tailscale-User-Login": WHO}, environ_base=env)
        assert r.status_code == 200
    assert seen == [f"dashboard:{WHO}"] * 2
    assert client.post("/api/override/override-x/deny", json={}).status_code == 200
    assert seen[-1] == "dashboard"
    assert _events("dashboard.action") == [] and _events("dashboard.identity_refused") == []
    assert client.get("/api/whoami", headers={"Tailscale-User-Login": WHO}).get_json() == {
        "mode": "off", "login": None, "name": None, "verified": False}


def test_a_misconfigured_mode_fails_closed(client, monkeypatch):
    monkeypatch.setenv("CHELA_DASHBOARD_IDENTITY_HEADER", "tailscal")
    assert client.get("/api/whoami", environ_base=LOOP).status_code == 503
    monkeypatch.setenv("CHELA_DASHBOARD_IDENTITY_HEADER", "cloudflare-access")
    assert client.get("/api/whoami", environ_base=LOOP).status_code == 503   # no team/aud


# --- cloudflare-access mode --------------------------------------------------------------

def _rsa():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


KEY = _rsa()
OTHER = _rsa()


@pytest.fixture
def cloudflare(monkeypatch):
    monkeypatch.setenv("CHELA_DASHBOARD_IDENTITY_HEADER", "cloudflare-access")
    monkeypatch.setenv("CHELA_DASHBOARD_CF_TEAM_DOMAIN", TEAM)
    monkeypatch.setenv("CHELA_DASHBOARD_CF_AUD", AUD)
    urls: list[str] = []

    def client_for(url):
        urls.append(url)
        return SimpleNamespace(get_signing_key_from_jwt=lambda tok: SimpleNamespace(key=KEY.public_key()))
    monkeypatch.setattr(identity, "_jwks_client", client_for)
    return urls


def _token(key=KEY, alg="RS256", **over) -> str:
    now = int(time.time())
    claims = {"email": WHO, "aud": [AUD], "iss": f"https://{TEAM}", "iat": now - 5,
              "exp": now + 300, "sub": "u1"}
    claims.update(over)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm=alg, headers={"kid": "k1"})


def _cf(client, token, env=LOOP, path="/api/whoami"):
    h = {"Cf-Access-Jwt-Assertion": token} if token is not None else {}
    return client.get(path, headers=h, environ_base=env)


def test_cloudflare_valid_jwt_is_accepted_and_verified(client, cloudflare):
    r = _cf(client, _token())
    assert r.status_code == 200
    assert r.get_json() == {"mode": "cloudflare-access", "login": WHO, "name": None, "verified": True}
    assert cloudflare == [f"https://{TEAM}/cdn-cgi/access/certs"]


def test_cloudflare_forged_jwt_is_refused(client, cloudflare):
    """🔴 GUARD (negative control for the signature check): a token signed by another key."""
    r = _cf(client, _token(key=OTHER))
    assert r.status_code == 401 and "did not verify" in r.get_json()["error"]
    assert _events("dashboard.identity_refused")[0]["payload"]["status"] == 401


@pytest.mark.parametrize("over", [
    {"exp": int(time.time()) - 60},             # expired
    {"aud": ["b" * 64]},                         # another application
    {"iss": "https://evil.cloudflareaccess.com"},
    {"exp": None},                               # no expiry at all
    {"iat": None},
])
def test_cloudflare_bad_claims_are_refused(client, cloudflare, over):
    assert _cf(client, _token(**over)).status_code == 401


def test_cloudflare_alg_none_and_hs256_are_refused(client, cloudflare):
    now = int(time.time())
    claims = {"email": WHO, "aud": [AUD], "iss": f"https://{TEAM}", "iat": now, "exp": now + 60}
    assert _cf(client, jwt.encode(claims, None, algorithm="none")).status_code == 401
    assert _cf(client, jwt.encode(claims, "s" * 32, algorithm="HS256")).status_code == 401


def test_cloudflare_key_fetch_failure_is_refused(client, cloudflare, monkeypatch):
    def boom(url):
        raise RuntimeError("no network")
    monkeypatch.setattr(identity, "_jwks_client", boom)
    assert _cf(client, _token()).status_code == 401


def test_cloudflare_missing_jwt_is_401_and_the_plain_email_header_is_never_read(client, cloudflare):
    assert _cf(client, None).status_code == 401
    r = client.get("/api/whoami", headers={"Cf-Access-Authenticated-User-Email": WHO}, environ_base=LOOP)
    assert r.status_code == 401
    r = client.get("/api/whoami", headers={"Cf-Access-Jwt-Assertion": _token(),
                                           "Cf-Access-Authenticated-User-Email": "boss@example.com"},
                   environ_base=LOOP)
    assert r.get_json()["login"] == WHO


def test_cloudflare_tailscale_header_is_not_an_identity(client, cloudflare):
    r = client.get("/api/whoami", headers={"Tailscale-User-Login": WHO}, environ_base=LOOP)
    assert r.status_code == 401


def test_cloudflare_jwt_from_a_non_loopback_peer_is_refused(client, cloudflare):
    assert _cf(client, _token(), env=TAILNET).status_code == 403


def test_cloudflare_service_token_names_its_common_name(client, cloudflare):
    r = _cf(client, _token(email=None, common_name="svc.access"))
    assert r.get_json()["login"] == "svc.access"
    assert _cf(client, _token(email=None)).status_code == 401


def test_cloudflare_machine_endpoints_skip_the_jwt_from_loopback_only(client, cloudflare):
    assert client.post("/hooks/Nope", json={}, environ_base=LOOP).status_code != 401
    assert client.post("/hooks/Nope", json={}, environ_base=TAILNET).status_code == 401
    # a token a machine route DOES carry is still verified
    r = client.post("/hooks/Nope", json={}, headers={"Cf-Access-Jwt-Assertion": _token(key=OTHER)},
                    environ_base=LOOP)
    assert r.status_code == 401


def test_cloudflare_approval_is_attributed_to_the_verified_login(client, cloudflare, monkeypatch):
    seen: list = []
    monkeypatch.setattr(overrides, "decide",
                        lambda rid, approve, by, channel="terminal": seen.append((by, channel))
                        or {"ok": True, "state": overrides.DENIED})
    r = client.post("/api/override/override-x/deny", json={},
                    headers={"Cf-Access-Jwt-Assertion": _token()}, environ_base=LOOP)
    assert r.status_code == 200 and seen == [(WHO, "dashboard")]
    (action,) = _events("dashboard.action")
    assert action["payload"]["by"] == WHO and action["payload"]["verified"] is True
