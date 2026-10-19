"""CMX-178: the page loads the React islands' built entry, named by the Vite manifest, before
the legacy ``main.js``; an unreadable manifest drops the islands and nothing else."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from chela.dashboard import app as dash

MANIFEST = Path(dash.__file__).parent / "static" / "app" / "manifest.json"


def test_the_entry_is_the_committed_manifests_file():
    file = json.loads(MANIFEST.read_text())["src/main.tsx"]["file"]
    assert file.startswith("assets/main-") and file.endswith(".js")
    assert dash.island_entry() == dash.ISLAND_ENTRY == f"app/{file}"


@pytest.mark.parametrize("body,why", [
    (None, "unreadable"),                                   # FileNotFoundError (OSError)
    ("{not json", "unreadable"),                            # JSONDecodeError (ValueError)
    (b"\xff\xfe\x00", "unreadable"),                        # UnicodeDecodeError (ValueError)
    ('{"other.tsx": {"file": "x.js"}}', "unreadable"),      # KeyError (LookupError)
    ('["src/main.tsx"]', "unreadable"),                     # list indexed by str: TypeError
    ('{"src/main.tsx": {"file": ""}}', "names no entry"),
    ('{"src/main.tsx": {"file": 7}}', "names no entry"),
])
def test_an_unreadable_manifest_drops_the_islands_and_says_why(tmp_path, caplog, body, why):
    manifest = tmp_path / "manifest.json"
    if isinstance(body, bytes):
        manifest.write_bytes(body)
    elif body is not None:
        manifest.write_text(body)
    with caplog.at_level("WARNING", logger="dashboard"):
        assert dash.island_entry(manifest) is None
    assert [r.getMessage() for r in caplog.records] and why in caplog.records[0].getMessage()
    assert str(manifest) in caplog.records[0].getMessage()


def _page(monkeypatch, entry):
    monkeypatch.setattr(dash, "ISLAND_ENTRY", entry)
    return dash.app.test_client().get("/").get_data(as_text=True)


def test_the_page_loads_the_entry_before_main_js_and_has_the_version_slot(monkeypatch):
    html = _page(monkeypatch, "app/assets/main-Abc123.js")
    v = dash.ASSET_VERSION
    tag = f'<script type="module" src="static/v/{v}/app/assets/main-Abc123.js"></script>'
    main = f'<script type="module" src="static/v/{v}/js/main.js"></script>'
    assert html.count(tag) == 1 and html.index(tag) < html.index(main)
    assert html.count('<span id="version-island"></span>') == 1


def test_without_an_entry_the_page_loads_no_island_script_and_still_loads_main_js(monkeypatch):
    html = _page(monkeypatch, None)
    assert "static/v/" in html and "/app/" not in html
    assert f'<script type="module" src="static/v/{dash.ASSET_VERSION}/js/main.js"></script>' in html
