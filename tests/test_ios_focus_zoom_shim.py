"""CMX-409: the page `/term/<wid>/` ACTUALLY serves carries static/term-touch.css.

tests/ios_focus_zoom.test.mjs proves term-touch.css gives xterm's hidden helper
textarea >= 16px on a coarse pointer (so tapping a terminal no longer zooms iOS
Safari). That only matters if term_http injects it, so this reads the rule back out
of the RENDERED response — Flask test client through the real proxy route, upstream
ttyd faked — and compares it to the file on disk, never to app.py's source.
"""
from __future__ import annotations

import io
from pathlib import Path

import chela.dashboard.app as app_mod

TOUCH_CSS = (Path(app_mod.__file__).parent / "static" / "term-touch.css").read_text(encoding="utf-8")


class _FakeTtydResponse:
    status = 200

    def __init__(self, body: bytes):
        self._body = io.BytesIO(body)
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def read(self):
        return self._body.read()

    def getheaders(self):
        return list(self.headers.items())


def test_served_ttyd_page_carries_the_touch_font_css_in_head(monkeypatch):
    monkeypatch.setattr(app_mod, "_require_terminals", lambda: None)
    monkeypatch.setattr(app_mod, "_terminals_port_map", lambda: {"@1": 7681})
    page = b"<!doctype html><html><head><title>ttyd</title></head><body></body></html>"
    monkeypatch.setattr(app_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeTtydResponse(page))
    resp = app_mod.app.test_client().get("/term/@1/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    # The file must be the real rule, so an emptied file can't satisfy `in`.
    assert "pointer: coarse" in TOUCH_CSS and "font-size: 16px" in TOUCH_CSS
    head = html.split("</head>", 1)[0]
    assert head.count(TOUCH_CSS) == 1, \
        "term_http no longer serves static/term-touch.css in the ttyd page <head>"
