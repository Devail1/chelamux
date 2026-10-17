"""CMX-167: the page ``/term/<wid>/`` actually serves loads the self-reconnect shim FIRST.

The shim wraps ``window.WebSocket``; it only sees ttyd's socket if it runs before ttyd's own
script, which ttyd 1.7.7 inlines in ``<body>``. So the guard is on the SERVED page (real proxy
route, upstream ttyd faked): the tag is the first thing injected into ``<head>``, it is a plain
synchronous script (no ``defer``/``async``/``module``), and its URL serves the shim's bytes.
The JS behaviour itself is tests/term_reconnect.test.mjs.
"""
from __future__ import annotations

import io
import re
from pathlib import Path

import pytest

import chela.dashboard.app as app_mod

_SHIM = Path(app_mod.__file__).parent / "static" / "term-reconnect.js"
_TTYD_PAGE = (b"<!doctype html><html><head><title>ttyd</title></head>"
              b"<body><script>new WebSocket('ws://x/ws', ['tty'])</script></body></html>")


class _FakeTtydResponse:
    status = 200

    def __init__(self, body: bytes, ctype: str):
        self._body = io.BytesIO(body)
        self.headers = {"Content-Type": ctype}

    def read(self):
        return self._body.read()

    def getheaders(self):
        return list(self.headers.items())


@pytest.fixture
def served(monkeypatch):
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})

    def go(body: bytes = _TTYD_PAGE, ctype: str = "text/html; charset=utf-8"):
        monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                            lambda *a, **k: _FakeTtydResponse(body, ctype))
        resp = app_mod.app.test_client().get("/term/@1/")
        assert resp.status_code == 200
        return resp.get_data(as_text=True)
    return go


def test_the_reconnect_shim_is_the_first_script_in_head(served):
    html = served()
    head = html[:html.index("</head>")]
    first = re.search(r"<script\b[^>]*>", head.split("<title>ttyd</title>", 1)[1])
    assert first is not None
    assert re.fullmatch(r'<script src="/static/v/[^/"]+/term-reconnect\.js">', first.group(0)), first.group(0)
    assert html.index("term-reconnect.js") < html.index("<body>") < html.index("new WebSocket")


def test_the_served_tag_resolves_to_the_shim_bytes(served):
    html = served()
    src = re.search(r'<script src="(/static/v/[^"]+/term-reconnect\.js)">', html).group(1)
    resp = app_mod.app.test_client().get(src)
    assert resp.status_code == 200
    assert resp.get_data() == _SHIM.read_bytes()


def test_non_html_assets_are_not_injected(served):
    body = served(b"console.log(1)", "application/javascript")
    assert "term-reconnect" not in body
