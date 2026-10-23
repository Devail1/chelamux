"""CMX-321: the page ``/term/<wid>/`` actually serves keeps the terminal hidden while it boots.

The hiding only works if it is in place before ttyd's body script opens xterm and paints: the
boot CSS and the preference shim (which adds ``chela-term-booting`` to ``<html>`` and later
removes it) must both be in the served ``<head>``. The frame-by-frame behaviour is
tests/browser/term_boot_reveal.test.mjs; this guards the wiring on the real proxy route
(upstream ttyd faked, as in tests/test_term_reconnect_served.py).
"""
from __future__ import annotations

import io

import pytest

import chela.dashboard.app as app_mod

_TTYD_PAGE = (b"<!doctype html><html><head><title>ttyd</title></head>"
              b"<body><script>window.term = new Terminal()</script></body></html>")


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
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTtydResponse(_TTYD_PAGE, "text/html; charset=utf-8"))
    resp = app_mod.app.test_client().get("/term/@1/")
    assert resp.status_code == 200
    return resp.get_data(as_text=True)


def test_the_boot_css_and_the_shim_that_drives_it_are_in_head(served):
    head = served[:served.index("</head>")]
    css = "html.chela-term-booting .xterm{opacity:0!important}"
    assert css in head
    assert "root.classList.add('chela-term-booting')" in head
    assert "root.classList.remove('chela-term-booting')" in head
    assert served.index("</head>") < served.index("window.term = new Terminal()")


def test_the_fallback_reveal_is_1500ms_after_the_terminal_appears(served):
    assert "if(!seen){seen=true;setTimeout(reveal,1500);}" in served
