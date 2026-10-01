"""CMX-426: after a deploy, an open dashboard must not keep running OLD cached JS/CSS.

The page loads every asset from ``static/v/<ASSET_VERSION>/…``. These guard the three
halves: every URL the served HTML/JS names carries the version (an unversioned one keeps
the pre-deploy cache alive), the version is stable within one deploy (no reload storm),
and the cache headers match (HTML revalidates, a versioned asset is immutable).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from chela.dashboard import app as dash

STATIC = Path(dash.__file__).parent / "static"
JS_DIRS = (STATIC / "js", STATIC / "collab")


@pytest.fixture
def client():
    return dash.app.test_client()


def _prefix() -> str:
    return f"static/v/{dash.ASSET_VERSION}/"


def test_every_script_and_stylesheet_in_the_page_is_versioned(client):
    html = client.get("/").get_data(as_text=True)
    scripts = re.findall(r"<script\b[^>]*\bsrc=\"([^\"]+)\"", html)
    styles = re.findall(r"<link\b[^>]*rel=\"stylesheet\"[^>]*href=\"([^\"]+)\"", html)
    module = re.findall(r"<script\b[^>]*type=\"module\"[^>]*src=\"([^\"]+)\"", html)
    assert module, "no <script type=module> found — the regex no longer matches the page"
    assert len(styles) >= 2 and len(scripts) >= 2
    for url in scripts + styles:
        assert url.startswith(_prefix()), f"{url!r} is not versioned — it keeps the pre-deploy cache"
    # The page must tell its JS which version it is, or the reload banner can never compare.
    assert f"window.CHELA_ASSET_VERSION = \"{dash.ASSET_VERSION}\"" in html


def test_ttyd_shims_load_versioned_scripts():
    for shim in (dash._term_upload_shim(), dash._term_presence_shim("@1")):
        srcs = re.findall(r"src=\"([^\"]+)\"", shim)
        assert srcs
        for url in srcs:
            assert url.startswith("/" + _prefix()), f"{url!r} in a ttyd shim is not versioned"


_STATIC_IMPORT = re.compile(r"^\s*(?:import|export)\b[^;]*?\bfrom\s*['\"]([^'\"]+)['\"]", re.M)
_SIDE_IMPORT = re.compile(r"^\s*import\s*['\"]([^'\"]+)['\"]", re.M)
_DYN_IMPORT = re.compile(r"\bimport\(\s*([^)]*)\)")


def _js_files():
    return [p for d in JS_DIRS for p in sorted(d.glob("*.js"))]


def test_every_module_import_inherits_the_version():
    """A relative specifier resolves against the importing module's versioned URL; an
    absolute ``/static/…`` one would escape it. Dynamic imports of our own modules must go
    through ``staticUrl()`` (util.js), which adds the version."""
    seen = 0
    for p in _js_files():
        src = p.read_text(encoding="utf-8")
        for spec in _STATIC_IMPORT.findall(src) + _SIDE_IMPORT.findall(src):
            seen += 1
            assert spec.startswith(("./", "../")), f"{p.name}: import {spec!r} is not relative"
        for arg in _DYN_IMPORT.findall(src):
            arg = arg.strip()
            if arg.startswith(("'https://", '"https://')):
                continue   # third-party CDN, not ours to version
            seen += 1
            assert arg.startswith("staticUrl("), (
                f"{p.name}: dynamic import({arg}) bypasses staticUrl() — unversioned")
        # No bare absolute static URL in code at all (comments aside; staticUrl's own
        # '/static/' prefix is not a URL to a file).
        code = "\n".join(ln for ln in src.splitlines() if not ln.lstrip().startswith(("//", "*")))
        bare = re.findall(r"['\"]/static/[^'\"]+['\"]", code)
        assert not bare, f"{p.name} names a bare static URL: {bare}"
    assert seen > 50, "the import scan found almost nothing — the regexes no longer match"


def test_static_url_adds_the_version():
    util = (STATIC / "js" / "util.js").read_text(encoding="utf-8")
    body = util.split("function staticUrl(path) {", 1)[1].split("}", 1)[0]
    assert "'v/' + ASSET_VERSION" in body


def test_version_is_stable_within_a_deploy(client):
    """⭐ The case that must be ACCEPTED: one deploy, many requests, ONE version."""
    a = client.get("/api/version").get_json()["version"]
    b = client.get("/api/version").get_json()["version"]
    assert a == b == dash.ASSET_VERSION
    assert dash.compute_asset_version() == dash.ASSET_VERSION
    assert client.get("/").get_data() == client.get("/").get_data()


def test_version_changes_when_a_served_asset_changes(tmp_path):
    (tmp_path / "js").mkdir()
    f = tmp_path / "js" / "a.js"
    f.write_text("export const x = 1;\n")
    v1 = dash.compute_asset_version((tmp_path,))
    assert dash.compute_asset_version((tmp_path,)) == v1
    f.write_text("export const x = 2;\n")
    assert dash.compute_asset_version((tmp_path,)) != v1


def test_cache_headers(client):
    assert client.get("/").headers["Cache-Control"] == "no-cache"
    r = client.get("/" + _prefix() + "js/main.js")
    assert r.status_code == 200
    assert "immutable" in r.headers["Cache-Control"]
    assert b"refresh" in r.get_data()
    r.close()
    old = client.get("/static/v/000000000000/js/main.js")
    assert old.status_code == 200
    assert old.headers["Cache-Control"] == "no-cache"
    old.close()
    # Unversioned URLs keep working.
    plain = client.get("/static/js/main.js")
    assert plain.status_code == 200
    plain.close()
    assert client.get("/api/version").headers["Cache-Control"] == "no-cache"


def test_versioned_route_does_not_escape_static(client):
    r = client.get("/" + _prefix() + "../app.py")
    assert r.status_code == 404
