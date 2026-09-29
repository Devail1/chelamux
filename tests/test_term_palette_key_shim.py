"""CMX-385: the page `/term/<wid>/` ACTUALLY serves carries the palette/settings key shim.

tests/settings_shortcut.test.mjs proves what `_TERM_PALETTE_KEY_SHIM` does (Ctrl+K →
openPalette, Ctrl+, → toggleSettings, Ctrl+V untouched) inside a real jsdom iframe. That
only matters if term_http injects it, so this reads the shim back out of the RENDERED
response — Flask test client through the real proxy route, upstream ttyd faked — never out
of app.py's source (defeat shape 05: a `[:0]` slice at the call site keeps every source
substring intact while the served page loses the shim).
"""
from __future__ import annotations

import io

import pytest

import chela.dashboard.app as app_mod


class _FakeTtydResponse:
    status = 200

    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def read(self):
        return self._body.read()

    def getheaders(self):
        return list(self.headers.items())


@pytest.fixture
def served_html(monkeypatch) -> str:
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})
    page = b"<!doctype html><html><head><title>ttyd</title></head><body></body></html>"
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTtydResponse(page))
    resp = app_mod.app.test_client().get("/term/@1/")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_served_ttyd_page_carries_the_palette_settings_shim_once_in_head(served_html):
    shim = app_mod._TERM_PALETTE_KEY_SHIM
    # Non-empty and the Ctrl+, wire present, so an emptied constant can't satisfy `in`.
    assert "toggleSettings" in shim and "openPalette" in shim
    assert served_html.count(shim) == 1, \
        "term_http no longer serves _TERM_PALETTE_KEY_SHIM in the ttyd page"
    head = served_html.split("</head>", 1)[0]
    assert shim in head, "the palette/settings shim must be injected into <head>"


def test_non_html_assets_pass_through_without_the_shim(monkeypatch):
    """Negative control: the shim is only injected into the HTML doc."""
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})
    fake = _FakeTtydResponse(b"console.log(1)")
    fake.headers = {"Content-Type": "application/javascript"}
    monkeypatch.setattr(app_mod.urllib.request, "urlopen", lambda *a, **k: fake)
    body = app_mod.app.test_client().get("/term/@1/app.js").get_data(as_text=True)
    assert body == "console.log(1)"
