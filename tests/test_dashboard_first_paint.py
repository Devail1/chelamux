"""CMX-89: what paints first is the view JS selects. Since CMX-248 the server renders no panel:
the body is one root the shell draws into, and the boot selects the first view (the Wall with
terminals on, else Work) in the same task as the mount, so nothing paints between them.
``frontend/src/boot.test.tsx`` and ``tests/dashboard_default_view.test.mjs`` pin that choice;
this file pins that the server sends no panel to paint first, either way.
"""
from __future__ import annotations

import pytest

from chela import config
from chela.dashboard import app as dash


@pytest.mark.parametrize("terminals", [True, False])
def test_the_server_renders_no_panel_only_the_root_and_the_flag(monkeypatch, terminals):
    monkeypatch.setattr(config, "TERMINALS_ENABLED", terminals)
    html = dash.app.test_client().get("/").get_data(as_text=True)
    assert 'class="panel' not in html and html.count('<div id="root"></div>') == 1
    assert f'"terminalsEnabled": {"true" if terminals else "false"}' in html


@pytest.mark.parametrize("terminals", [True, False])
def test_rendered_index_has_no_leftover_jinja(monkeypatch, terminals):
    monkeypatch.setattr(config, "TERMINALS_ENABLED", terminals)
    html = dash.app.test_client().get("/").get_data(as_text=True)
    assert "{%" not in html and "%}" not in html
