"""CMX-322: ttyd's client (~720 kB of xterm.js + app) is inline in its HTML page, which
the pane proxy fetched uncompressed and served no-cache, so every pane open downloaded it
again. term_http now lifts it out to /term-bundle/<digest>.js (gzipped, immutable) and
gzips what is left of the page."""
from __future__ import annotations

import gzip
import hashlib
import io

import pytest

import chela.dashboard.app as app_mod

_BIG = "var x=1;" * 20_000                      # 160 kB: ttyd-client sized
_SMALL = "window.small=1;"
_PAGE = ("<!doctype html><html><head><title>ttyd</title>"
         f"<script>{_SMALL}</script></head><body>"
         f'<script type="text/javascript">{_BIG}</script></body></html>')
_DIGEST = hashlib.sha256(_BIG.encode()).hexdigest()[:16]


class _FakeTtydResponse:
    status = 200

    def __init__(self, body: bytes, ctype: str = "text/html; charset=utf-8"):
        self._body = io.BytesIO(body)
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._body.read()

    def getheaders(self):
        return list(self.headers.items())


@pytest.fixture(autouse=True)
def _fresh_bundles(monkeypatch):
    monkeypatch.setattr(app_mod, "_TTYD_BUNDLES", {})


@pytest.fixture
def ttyd(monkeypatch):
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})

    def serve(body: str, ctype: str = "text/html; charset=utf-8"):
        monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                            lambda *a, **k: _FakeTtydResponse(body.encode(), ctype))
    return serve


# --- _externalize_ttyd_bundle ---------------------------------------------------------

def test_the_big_inline_script_is_lifted_to_a_hashed_url():
    out = app_mod._externalize_ttyd_bundle(_PAGE)
    assert _BIG not in out
    assert f'<script src="/term-bundle/{_DIGEST}.js"></script></body>' in out
    js, gz = app_mod._TTYD_BUNDLES[_DIGEST]
    assert js == _BIG.encode()
    assert gzip.decompress(gz) == js


def test_small_inline_scripts_and_src_scripts_stay_where_they_are():
    page = (f"<head><script>{_SMALL}</script>"
            '<script src="/x.js"></script></head>')
    assert app_mod._externalize_ttyd_bundle(page) == page
    assert app_mod._TTYD_BUNDLES == {}


def test_the_threshold_is_64_kib():
    at = "a" * (64 * 1024)
    below = "a" * (64 * 1024 - 1)
    assert "/term-bundle/" in app_mod._externalize_ttyd_bundle(f"<script>{at}</script>")
    assert app_mod._externalize_ttyd_bundle(f"<script>{below}</script>") == f"<script>{below}</script>"


def test_at_most_four_ttyd_builds_are_remembered_oldest_dropped_first():
    digests = []
    for i in range(5):
        js = f"var build={i};" + "x" * (64 * 1024)
        app_mod._externalize_ttyd_bundle(f"<script>{js}</script>")
        digests.append(hashlib.sha256(js.encode()).hexdigest()[:16])
    assert list(app_mod._TTYD_BUNDLES) == digests[1:]


def test_the_same_bundle_twice_is_stored_once():
    app_mod._externalize_ttyd_bundle(_PAGE)
    app_mod._externalize_ttyd_bundle(_PAGE)
    assert list(app_mod._TTYD_BUNDLES) == [_DIGEST]


# --- /term-bundle/<digest>.js ---------------------------------------------------------

def test_the_bundle_is_served_gzipped_and_immutable(ttyd):
    ttyd(_PAGE)
    client = app_mod.app.test_client()
    client.get("/term/@1/")
    resp = client.get(f"/term-bundle/{_DIGEST}.js", headers={"Accept-Encoding": "gzip, br"})
    assert resp.status_code == 200
    assert resp.headers["Content-Encoding"] == "gzip"
    assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert resp.headers["Vary"] == "Accept-Encoding"
    assert resp.mimetype == "text/javascript"
    assert gzip.decompress(resp.data) == _BIG.encode()


def test_the_bundle_is_served_plain_to_a_client_without_gzip(ttyd):
    ttyd(_PAGE)
    client = app_mod.app.test_client()
    client.get("/term/@1/")
    resp = client.get(f"/term-bundle/{_DIGEST}.js")
    assert resp.status_code == 200
    assert "Content-Encoding" not in resp.headers
    assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert resp.data == _BIG.encode()


def test_an_unknown_bundle_is_404(ttyd):
    ttyd(_PAGE)     # never the real loopback: a live ttyd may listen on 7681
    resp = app_mod.app.test_client().get("/term-bundle/0123456789abcdef.js")
    assert resp.status_code == 404


def test_the_bundle_route_is_gated_on_terminals(monkeypatch):
    app_mod._TTYD_BUNDLES[_DIGEST] = (b"js", gzip.compress(b"js"))
    monkeypatch.setattr(app_mod.config, "TERMINALS_ENABLED", False)
    assert app_mod.app.test_client().get(f"/term-bundle/{_DIGEST}.js").status_code == 404


# --- the pane page --------------------------------------------------------------------

def test_the_pane_page_is_gzipped_for_a_gzip_client(ttyd):
    ttyd(_PAGE)
    resp = app_mod.app.test_client().get("/term/@1/", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers["Content-Encoding"] == "gzip"
    assert resp.headers["Vary"] == "Accept-Encoding"
    html = gzip.decompress(resp.data).decode()
    assert f'<script src="/term-bundle/{_DIGEST}.js"></script>' in html
    assert _BIG not in html
    assert "chelaApplyTermPrefs" in html          # the shims are still injected
    assert len(resp.data) < 64 * 1024


def test_the_pane_page_is_plain_for_a_client_without_gzip(ttyd):
    ttyd(_PAGE)
    resp = app_mod.app.test_client().get("/term/@1/")
    assert "Content-Encoding" not in resp.headers
    html = resp.get_data(as_text=True)
    assert f'<script src="/term-bundle/{_DIGEST}.js"></script>' in html
    assert "chelaApplyTermPrefs" in html


def test_a_non_html_passthrough_is_untouched(ttyd):
    ttyd('{"token": ""}', "application/json")
    resp = app_mod.app.test_client().get("/term/@1/token", headers={"Accept-Encoding": "gzip"})
    assert "Content-Encoding" not in resp.headers
    assert resp.get_data(as_text=True) == '{"token": ""}'


# --- review follow-ups ----------------------------------------------------------------

@pytest.mark.parametrize("attrs", [
    ' type="module"', ' type="application/json"', ' nonce="abc"', " defer",
    ' type="text/javascript" async',
])
def test_a_script_that_is_not_plain_classic_is_never_lifted(attrs):
    page = f"<script{attrs}>{_BIG}</script>"
    assert app_mod._externalize_ttyd_bundle(page) == page
    assert app_mod._TTYD_BUNDLES == {}


@pytest.mark.parametrize("attrs", ["", ' type="text/javascript"', " type='text/javascript'",
                                   " TYPE=text/javascript"])
def test_a_plain_classic_script_is_lifted(attrs):
    out = app_mod._externalize_ttyd_bundle(f"<script{attrs}>{_BIG}</script>")
    assert out == f'<script src="/term-bundle/{_DIGEST}.js"></script>'


def test_gzip_refused_with_q0_is_not_sent(ttyd):
    ttyd(_PAGE)
    client = app_mod.app.test_client()
    resp = client.get("/term/@1/", headers={"Accept-Encoding": "gzip;q=0, identity"})
    assert "Content-Encoding" not in resp.headers
    assert resp.headers["Vary"] == "Accept-Encoding"
    resp = client.get(f"/term-bundle/{_DIGEST}.js", headers={"Accept-Encoding": "gzip;q=0"})
    assert "Content-Encoding" not in resp.headers
    assert resp.data == _BIG.encode()


def test_an_unseen_digest_is_recovered_from_a_live_ttyd(monkeypatch):
    """The dashboard restarted between serving a page and its script request."""
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681, "@2": 7682})
    asked = []

    def urlopen(url, timeout):
        asked.append((url, timeout))
        if ":7681/" in url:
            raise ConnectionRefusedError("dead ttyd")
        return _FakeTtydResponse(_PAGE.encode())
    monkeypatch.setattr(app_mod.urllib.request, "urlopen", urlopen)
    resp = app_mod.app.test_client().get(f"/term-bundle/{_DIGEST}.js")
    assert resp.status_code == 200
    assert resp.data == _BIG.encode()
    assert asked == [("http://127.0.0.1:7681/term/@1/", 5), ("http://127.0.0.1:7682/term/@2/", 5)]


def test_a_digest_no_live_ttyd_serves_is_404(monkeypatch):
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda url, timeout: _FakeTtydResponse(_PAGE.encode()))
    resp = app_mod.app.test_client().get("/term-bundle/0123456789abcdef.js")
    assert resp.status_code == 404
    assert list(app_mod._TTYD_BUNDLES) == [_DIGEST]     # the live build was registered
