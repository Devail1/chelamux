"""CMX-201: the page `/term/<wid>/` ACTUALLY serves carries the right-click paste shim.

tests/term_contextmenu.test.mjs proves what `_TERM_CONTEXTMENU_SHIM` does (right-click →
no native menu, paste through the Ctrl/Cmd+V shim's function) in jsdom. That only matters
if term_http injects it, so this reads the shims back out of the RENDERED response — Flask
test client through the real proxy route, upstream ttyd faked — never out of app.py's
source (defeat shape 05).
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


def test_served_pane_carries_the_contextmenu_shim_once_in_head(served_head):
    shim = app_mod._TERM_CONTEXTMENU_SHIM
    # Non-empty and wired, so an emptied constant can't satisfy `in`.
    assert "'contextmenu'" in shim and "preventDefault()" in shim
    assert "__chelaPasteClipboard" in shim and "(pointer: fine)" in shim
    assert served_head.count(shim) == 1, \
        "term_http no longer serves _TERM_CONTEXTMENU_SHIM in the ttyd page <head>"


def test_served_pane_carries_the_paste_function_the_shim_calls(served_head):
    """The right-click shim pastes only through the Ctrl/Cmd+V shim's exported
    function; serving one without the other would make right-click a silent no-op."""
    key = app_mod._TERM_PASTE_KEY_SHIM
    assert "window.__chelaPasteClipboard=" in key
    assert served_head.count(key) == 1
