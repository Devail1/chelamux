"""CMX-493: the page ``/term/<wid>/`` serves loads the focus guard before ttyd's own script.

The guard wraps ``HTMLElement.prototype.focus``; it must be in place before ttyd's body script
opens its socket and focuses its terminal. The behaviour is tests/browser/wall_focus_kept.test.mjs.
"""
from __future__ import annotations

import re
from pathlib import Path

import chela.dashboard.app as app_mod
from tests.test_term_reconnect_served import served  # noqa: F401  (the fixture)

_SHIM = Path(app_mod.__file__).parent / "static" / "term-focus.js"


def test_the_focus_guard_is_a_plain_script_in_head_and_its_url_serves_it(served):  # noqa: F811
    html = served()
    tag = re.search(r'<script src="(/static/v/[^/"]+/term-focus\.js)"></script>', html)
    assert tag, "term_http does not inject the focus guard"
    assert tag.start() < html.index("</head>") < html.index("new WebSocket")
    resp = app_mod.app.test_client().get(tag.group(1))
    assert resp.status_code == 200
    assert resp.get_data() == _SHIM.read_bytes()
