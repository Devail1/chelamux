"""CMX-426: after a deploy, an open dashboard must not keep running OLD cached JS/CSS.

The page loads every asset from ``static/v/<ASSET_VERSION>/…``. These guard the three
halves: every URL the served HTML/JS names carries the version (an unversioned one keeps
the pre-deploy cache alive), the version is stable within one deploy (no reload storm),
and the cache headers match (HTML revalidates, a versioned asset is immutable).
"""
from __future__ import annotations

import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest

from chela.dashboard import app as dash

STATIC = Path(dash.__file__).parent / "static"
JS_DIRS = (STATIC, STATIC / "js", STATIC / "collab")


@pytest.fixture
def client():
    return dash.app.test_client()


def _prefix() -> str:
    return f"static/v/{dash.ASSET_VERSION}/"


class _Refs(HTMLParser):
    """Every URL-bearing attribute in the page, by tag — parsed, not regexed, so attribute
    order (``href`` before ``rel``) or quoting can't hide a tag from the check."""

    def __init__(self):
        super().__init__()
        self.refs: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.refs.append((tag, dict(attrs)))


def _page_refs(client):
    parser = _Refs()
    parser.feed(client.get("/").get_data(as_text=True))
    return parser.refs


def test_every_script_and_stylesheet_in_the_page_is_versioned(client):
    refs = _page_refs(client)
    scripts = [a["src"] for t, a in refs if t == "script" and "src" in a]
    module = [a["src"] for t, a in refs if t == "script" and a.get("type") == "module" and "src" in a]
    styles = [a["href"] for t, a in refs
              if t == "link" and "stylesheet" in (a.get("rel") or "").split()]
    # The page as it ships: gridstack + main.js, gridstack.css + style.css. A count that
    # drops means a tag stopped being recognised — not that it stopped needing a version.
    assert module == [_prefix() + "js/main.js"]
    assert len(scripts) >= 2 and len(styles) >= 2
    assert _prefix() + "style.css" in styles
    for url in scripts + styles:
        assert url.startswith(_prefix()), f"{url!r} is not versioned — it keeps the pre-deploy cache"
    # ANY reference to a static file — icon, image, script, stylesheet — carries the version.
    static_refs = [v for _, a in refs for k, v in a.items()
                   if k in ("src", "href") and v and "static/" in v]
    assert len(static_refs) >= len(scripts) + len(styles)
    for url in static_refs:
        assert url.startswith(_prefix()), f"{url!r} names a static file without the version"
    # The page must tell its JS which version it is, or the reload banner can never compare.
    html = client.get("/").get_data(as_text=True)
    assert f"window.CHELA_ASSET_VERSION = \"{dash.ASSET_VERSION}\"" in html


def test_every_versioned_page_asset_is_served(client):
    """The versioned URL must actually resolve to the file — a version in the path the
    route then can't strip is a page with no JS at all."""
    for tag, a in _page_refs(client):
        url = a.get("src") or (a.get("href") if tag == "link" else None)
        if not url or not url.startswith(_prefix()):
            continue
        r = client.get("/" + url)
        assert r.status_code == 200, f"{url} → {r.status_code}"
        rel = url[len(_prefix()):]
        assert r.get_data() == (STATIC / rel).read_bytes()
        r.close()


def test_ttyd_shims_load_versioned_scripts():
    for shim in (dash._term_upload_shim(), dash._term_presence_shim("@1")):
        srcs = re.findall(r"src=\"([^\"]+)\"", shim)
        assert len(srcs) == 1
        for url in srcs:
            assert url.startswith("/" + _prefix()), f"{url!r} in a ttyd shim is not versioned"
    assert '"/' + _prefix() + 'term-upload.js"' in dash._term_upload_shim()
    assert '"/' + _prefix() + 'collab/presence-shim.js"' in dash._term_presence_shim("@1")


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


def test_version_is_stable_within_a_deploy(client):
    """⭐ The case that must be ACCEPTED: one deploy, many requests, ONE version."""
    a = client.get("/api/version").get_json()["version"]
    b = client.get("/api/version").get_json()["version"]
    assert a == b == dash.ASSET_VERSION
    assert dash.compute_asset_version() == dash.ASSET_VERSION
    assert client.get("/").get_data() == client.get("/").get_data()


@pytest.mark.parametrize("rel", ["js/a.js", "collab/b.mjs", "style.css", "index.html", "img/c.svg"])
def test_version_changes_when_a_served_asset_changes(tmp_path, rel):
    """Every kind of file the browser caches moves the version — a CSS-only or
    template-only deploy is still a deploy the open page must be told about."""
    f = tmp_path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("one\n")
    v1 = dash.compute_asset_version((tmp_path,))
    assert dash.compute_asset_version((tmp_path,)) == v1
    assert re.fullmatch(r"[0-9a-f]{12}", v1)
    f.write_text("two\n")
    assert dash.compute_asset_version((tmp_path,)) != v1


def test_version_changes_when_a_served_asset_is_renamed(tmp_path):
    (tmp_path / "a.js").write_text("x\n")
    v1 = dash.compute_asset_version((tmp_path,))
    (tmp_path / "a.js").rename(tmp_path / "b.js")
    assert dash.compute_asset_version((tmp_path,)) != v1


def test_version_ignores_files_the_browser_never_loads(tmp_path):
    """A Python- or test-only change must NOT ask every open page to reload."""
    (tmp_path / "a.js").write_text("x\n")
    v1 = dash.compute_asset_version((tmp_path,))
    (tmp_path / "notes.py").write_text("print(1)\n")
    assert dash.compute_asset_version((tmp_path,)) == v1


def test_running_version_covers_both_static_and_templates(tmp_path):
    """ASSET_VERSION is the hash of BOTH served roots: a template edit is a deploy too."""
    static = shutil.copytree(STATIC, tmp_path / "static")
    templates = shutil.copytree(Path(dash.__file__).parent / "templates", tmp_path / "templates")
    assert dash.compute_asset_version((static, templates)) == dash.ASSET_VERSION
    (templates / "index.html").write_text((templates / "index.html").read_text() + "<!-- x -->")
    assert dash.compute_asset_version((static, templates)) != dash.ASSET_VERSION


def test_cache_headers(client):
    assert client.get("/").headers["Cache-Control"] == "no-cache"
    r = client.get("/" + _prefix() + "js/main.js")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"
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
