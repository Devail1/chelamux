"""CMX-213: the served page title uses an en dash, which the copy rule allows (it bans only U+2014)."""
from __future__ import annotations

import re

from chela.dashboard import app as dash


def test_served_title_is_exact():
    html = dash.app.test_client().get("/").get_data(as_text=True)
    assert re.findall(r"<title>(.*?)</title>", html) == ["chela – Mission Control"]
