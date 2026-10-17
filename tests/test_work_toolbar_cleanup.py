"""CMX-207: the Work board toolbar lost its batch "Merge all mergeable" button (and the
``/api/dispatcher/merge-all`` route behind it) and the inline "Seed a starter ..." hint.
Per-card Merge and the ``+ Init a repo`` button stay.
"""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import pytest

from chela.dashboard import app as dash

INDEX_HTML = Path(dash.__file__).parent / "templates" / "index.html"


@pytest.fixture
def client():
    return dash.app.test_client()


def test_merge_all_route_is_gone(client):
    resp = client.post("/api/dispatcher/merge-all", json={})
    assert resp.status_code == 404
    assert not any(r.rule == "/api/dispatcher/merge-all" for r in dash.app.url_map.iter_rules())


def test_per_card_merge_route_still_answers(client):
    # Control: the single-card route is still wired. An unknown run is the ROUTE's own
    # JSON 404, not Flask's "no such URL" page.
    with patch.object(dash.dispatcher, "list_runs", return_value=[]):
        resp = client.post("/api/dispatcher/runs/CMX-0/merge", json={})
    assert resp.status_code == 404
    assert resp.get_json() == {"ok": False, "error": "run CMX-0 not found"}


def test_toolbar_has_no_seed_hint_line_but_keeps_init_button():
    html = INDEX_HTML.read_text()
    assert not re.search(r'<span class="work-toolbar-hint">\s*Seed a starter', html)
    init = re.search(r'<button[^>]*onclick="chela\.openInitRepo\(\)"[^>]*>\+ Init a repo</button>', html)
    assert init, "the + Init a repo button must stay"
    assert 'title="Seed a starter WORKFLOW.md + TODO.md into a repo"' in init.group(0)
