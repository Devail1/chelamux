"""CMX-206: the page `/term/<wid>/` ACTUALLY serves carries the OSC 52 clipboard shim.

tests/term_osc52.test.mjs proves what `_TERM_OSC52_SHIM` does (OSC 52 write → writeText,
a `?` read query ignored, oversize/malformed ignored, a refusal toasted) in jsdom. That
only matters if term_http injects it, so this reads it back out of the RENDERED response
— Flask test client through the real proxy route, upstream ttyd faked — never out of
app.py's source (defeat shape 05).
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
def served_head(monkeypatch) -> str:
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})
    page = b"<!doctype html><html><head><title>ttyd</title></head><body></body></html>"
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTtydResponse(page))
    resp = app_mod.app.test_client().get("/term/@1/")
    assert resp.status_code == 200
    return resp.get_data(as_text=True).split("</head>", 1)[0]


def test_served_pane_carries_the_osc52_shim_once_in_head(served_head):
    shim = app_mod._TERM_OSC52_SHIM
    # Non-empty and wired, so an emptied constant can't satisfy `in`.
    assert "registerOscHandler(52," in shim and "writeText(" in shim
    assert served_head.count(shim) == 1, \
        "term_http no longer serves _TERM_OSC52_SHIM in the ttyd page <head>"
