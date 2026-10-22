"""CMX-178/CMX-215: the page's one module script is the Vite build's entry, named by the
manifest: the React islands with the legacy ``main.js`` graph bundled behind them. An
unreadable manifest leaves the page with no JS, and says so as an error."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chela.dashboard import app as dash

MANIFEST = Path(dash.__file__).parent / "static" / "app" / "manifest.json"


def test_the_entry_is_the_committed_manifests_file():
    file = json.loads(MANIFEST.read_text())["src/main.tsx"]["file"]
    assert file.startswith("assets/main-") and file.endswith(".js")
    assert dash.app_entry() == dash.APP_ENTRY == f"app/{file}"


@pytest.mark.parametrize("body,why", [
    (None, "unreadable"),                                   # FileNotFoundError (OSError)
    ("{not json", "unreadable"),                            # JSONDecodeError (ValueError)
    (b"\xff\xfe\x00", "unreadable"),                        # UnicodeDecodeError (ValueError)
    ('{"other.tsx": {"file": "x.js"}}', "unreadable"),      # KeyError (LookupError)
    ('["src/main.tsx"]', "unreadable"),                     # list indexed by str: TypeError
    ('{"src/main.tsx": {"file": ""}}', "names no entry"),
    ('{"src/main.tsx": {"file": 7}}', "names no entry"),
])
def test_an_unreadable_manifest_is_an_error_that_says_why(tmp_path, caplog, body, why):
    manifest = tmp_path / "manifest.json"
    if isinstance(body, bytes):
        manifest.write_bytes(body)
    elif body is not None:
        manifest.write_text(body)
    with caplog.at_level("WARNING", logger="dashboard"):
        assert dash.app_entry(manifest) is None
    assert [r.levelname for r in caplog.records] == ["ERROR"]
    assert "dashboard has no JS" in caplog.records[0].getMessage()
    assert why in caplog.records[0].getMessage()
    assert str(manifest) in caplog.records[0].getMessage()


def _page(monkeypatch, entry):
    monkeypatch.setattr(dash, "APP_ENTRY", entry)
    return dash.app.test_client().get("/").get_data(as_text=True)


def test_the_page_loads_the_entry_as_its_one_module_script_and_has_the_overlays_slot(monkeypatch):
    html = _page(monkeypatch, "app/assets/main-Abc123.js")
    v = dash.ASSET_VERSION
    tag = f'<script type="module" src="static/v/{v}/app/assets/main-Abc123.js"></script>'
    assert html.count(tag) == 1 and html.count('type="module"') == 1
    assert "js/main.js" not in html, "the raw, unbundled legacy entry is back"
    # CMX-221: Settings (and the version badge in its foot) draws into the overlays island.
    assert html.count('<div id="overlays-island"></div>') == 1


def test_the_version_badge_is_not_in_the_sidebar(monkeypatch):
    # CMX-291: in the sidebar foot the badge drew over the CPU/RAM/Disk strip. It is in the
    # Settings modal's foot (features/settings, Settings.test.tsx pins it there).
    html = _page(monkeypatch, "app/assets/main-Abc123.js")
    sidebar = html[html.index('<aside class="sidebar"'):html.index("</aside>")]
    assert "version-island" not in sidebar and "version-badge" not in sidebar
    assert 'id="settings-drawer"' not in html, "a static Settings modal is back beside the React one"


def test_without_an_entry_the_page_loads_no_module_script(monkeypatch):
    html = _page(monkeypatch, None)
    assert "static/v/" in html and "/app/" not in html
    assert 'type="module"' not in html and "js/main.js" not in html
