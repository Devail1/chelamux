"""CMX-178/CMX-215: the page's one module script is the Vite build's entry, named by the
manifest, and (CMX-248) the whole page is the one React root it renders into ``#root``, with
the stylesheet the entry imports linked from the manifest too. An unreadable manifest leaves
the page with no JS, or no stylesheet, and says so as an error."""

from __future__ import annotations

import json
import re
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


def _page(monkeypatch, entry, css="app/assets/main-Css456.css"):
    monkeypatch.setattr(dash, "APP_ENTRY", entry)
    monkeypatch.setattr(dash, "APP_CSS", css)
    return dash.app.test_client().get("/").get_data(as_text=True)


def test_the_page_loads_the_entry_as_its_one_module_script_into_one_root(monkeypatch):
    html = _page(monkeypatch, "app/assets/main-Abc123.js")
    v = dash.ASSET_VERSION
    tag = f'<script type="module" src="static/v/{v}/app/assets/main-Abc123.js"></script>'
    assert html.count(tag) == 1 and html.count('type="module"') == 1
    assert "js/main.js" not in html, "the raw, unbundled legacy entry is back"
    # CMX-248: the body is the boot block, the pre-paint restore and the one root; the shell
    # (features/shell <App>) draws the rest, so no markup of its own, no inline handler.
    assert html.count('<div id="root"></div>') == 1
    body = html[html.index("<body>"):]
    assert "<aside" not in body and 'class="app"' not in body and "island" not in body
    assert "onclick=" not in html and "chela." not in body


def test_the_page_links_the_entrys_stylesheet_in_its_head(monkeypatch):
    html = _page(monkeypatch, "app/assets/main-Abc123.js")
    head = html[:html.index("</head>")]
    v = dash.ASSET_VERSION
    assert head.count(f'<link rel="stylesheet" href="static/v/{v}/app/assets/main-Css456.css">') == 1
    assert not re.search(r'href="[^"]*style\.css"', html), "style.css is linked raw beside the bundle's copy of it"
    assert _page(monkeypatch, "app/assets/main-Abc123.js", None).count('rel="stylesheet"') == 1   # gridstack's only


def test_the_stylesheet_is_the_committed_manifests_entry_css():
    css = json.loads(MANIFEST.read_text())["src/main.tsx"]["css"]
    assert len(css) == 1 and css[0].startswith("assets/main-") and css[0].endswith(".css")
    assert dash.app_css() == dash.APP_CSS == f"app/{css[0]}"


@pytest.mark.parametrize("body,why", [
    (None, "unreadable"),                                        # FileNotFoundError (OSError)
    ("{not json", "unreadable"),                                 # JSONDecodeError (ValueError)
    (b"\xff\xfe\x00", "unreadable"),                             # UnicodeDecodeError (ValueError)
    ('{"src/main.tsx": {"file": "x.js"}}', "unreadable"),        # no css: KeyError (LookupError)
    ('["src/main.tsx"]', "unreadable"),                          # list indexed by str: TypeError
    ('{"src/main.tsx": {"css": []}}', "not one file"),
    ('{"src/main.tsx": {"css": ["a.css", "b.css"]}}', "not one file"),
    ('{"src/main.tsx": {"css": "a.css"}}', "not one file"),
    ('{"src/main.tsx": {"css": [""]}}', "not one file"),
    ('{"src/main.tsx": {"css": [7]}}', "not one file"),
])
def test_an_unreadable_stylesheet_entry_is_an_error_that_says_why(tmp_path, caplog, body, why):
    manifest = tmp_path / "manifest.json"
    if isinstance(body, bytes):
        manifest.write_bytes(body)
    elif body is not None:
        manifest.write_text(body)
    with caplog.at_level("WARNING", logger="dashboard"):
        assert dash.app_css(manifest) is None
    assert [r.levelname for r in caplog.records] == ["ERROR"]
    assert "dashboard has no stylesheet" in caplog.records[0].getMessage()
    assert why in caplog.records[0].getMessage()
    assert str(manifest) in caplog.records[0].getMessage()


def test_one_css_file_is_read_as_its_static_path(tmp_path):
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"src/main.tsx": {"file": "x.js", "css": ["assets/main-Q.css"]}}')
    assert dash.app_css(manifest) == "app/assets/main-Q.css"


def test_without_an_entry_the_page_loads_no_module_script(monkeypatch):
    html = _page(monkeypatch, None, None)
    assert "static/v/" in html and "/app/" not in html
    assert 'type="module"' not in html and "js/main.js" not in html
