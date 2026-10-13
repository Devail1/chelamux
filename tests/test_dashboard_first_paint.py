"""CMX-89: the dashboard's server-rendered FIRST panel is the one JS will select.

With terminals on, main.js switches to the Wall — but only after its whole module graph
has loaded, so a markup that marked Work ``active`` painted Work first and then jumped
(worst on a phone). The served index must already mark the Wall active, and Work only
when there is no Wall to land on.
"""
from __future__ import annotations

from html.parser import HTMLParser

import pytest

from chela import config
from chela.dashboard import app as dash


class _Panels(HTMLParser):
    """``id -> class list`` for every ``.panel`` in the page (parsed, not regexed)."""

    def __init__(self):
        super().__init__()
        self.panels: dict[str, list[str]] = {}

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if "panel" in classes and a.get("id"):
            self.panels[a["id"]] = classes


def _panels(monkeypatch, terminals: bool) -> dict[str, list[str]]:
    monkeypatch.setattr(config, "TERMINALS_ENABLED", terminals)
    p = _Panels()
    p.feed(dash.app.test_client().get("/").get_data(as_text=True))
    return p.panels


def test_terminals_on_first_paint_is_the_wall_not_work(monkeypatch):
    panels = _panels(monkeypatch, True)
    assert "active" in panels["panel-terminals"], "the Wall is not the server-rendered first panel"
    assert "active" not in panels["panel-work"], "Work still paints first, before main.js switches"
    assert [k for k, c in panels.items() if "active" in c] == ["panel-terminals"]


def test_terminals_off_first_paint_is_work(monkeypatch):
    panels = _panels(monkeypatch, False)
    assert "panel-terminals" not in panels
    assert "active" in panels["panel-work"], "with no Wall, nothing in JS ever activates Work"
    assert [k for k, c in panels.items() if "active" in c] == ["panel-work"]


@pytest.mark.parametrize("terminals", [True, False])
def test_rendered_index_has_no_leftover_jinja(monkeypatch, terminals):
    monkeypatch.setattr(config, "TERMINALS_ENABLED", terminals)
    html = dash.app.test_client().get("/").get_data(as_text=True)
    assert "{%" not in html and "%}" not in html
