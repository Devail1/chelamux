"""CMX-457: the dashboard installs as a home-screen app, and a ``work`` dashboard looks different.

The manifest and the icons are served by the dashboard; the PNGs are drawn from the chela
mark (``chela.dashboard.pwa``). The work variant is told apart by SHAPE (a corner badge), so
the badge test compares luminance, which a red-weak eye sees, not hue."""
from __future__ import annotations

import json
import re
from pathlib import Path
import struct
import zlib

import pytest

from chela.dashboard import app as dash
from chela.dashboard import pwa

ICON_URLS = ("/apple-touch-icon.png", "/icon-192.png", "/icon-512.png", "/icon-512-maskable.png")


# --- a stdlib PNG reader (Pillow is not a core dependency) ---------------------------------

def png_size(data: bytes) -> tuple[int, int]:
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


def png_pixels(data: bytes) -> list[list[tuple[int, int, int]]]:
    """8-bit RGB rows, any of the five scanline filters."""
    w, h = png_size(data)
    assert data[24:29] == bytes([8, 2, 0, 0, 0]), "expected 8-bit RGB, not interlaced"
    idat, pos = b"", 8
    while pos < len(data):
        (n,) = struct.unpack(">I", data[pos:pos + 4])
        if data[pos + 4:pos + 8] == b"IDAT":
            idat += data[pos + 8:pos + 8 + n]
        pos += 12 + n
    raw, stride, prev, rows = zlib.decompress(idat), w * 3, bytearray(w * 3), []
    for y in range(h):
        f, line = raw[y * (stride + 1)], bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for i in range(stride):
            a = line[i - 3] if i >= 3 else 0
            b, c = prev[i], prev[i - 3] if i >= 3 else 0
            if f == 1:
                line[i] = (line[i] + a) & 255
            elif f == 2:
                line[i] = (line[i] + b) & 255
            elif f == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif f == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                line[i] = (line[i] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append([tuple(line[i:i + 3]) for i in range(0, stride, 3)])
        prev = line
    return rows


def luma(px) -> float:
    return 0.299 * px[0] + 0.587 * px[1] + 0.114 * px[2]


def region(rows, x0, y0, x1, y1):
    return [rows[y][x] for y in range(y0, y1) for x in range(x0, x1)]


# --- fixtures ------------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch):
    """No PWA variable set; returns a setter."""
    monkeypatch.delenv("CHELA_DASHBOARD_ICON_VARIANT", raising=False)
    monkeypatch.delenv("CHELA_DASHBOARD_APP_NAME", raising=False)

    def set_(**kv):
        for k, v in kv.items():
            monkeypatch.setenv(k, v)
    return set_


@pytest.fixture
def client():
    return dash.app.test_client()


def head(client) -> str:
    r = client.get("/")
    assert r.status_code == 200
    html = r.get_data(as_text=True)
    return html[:html.index("</head>")]


def fetch(client):
    """Everything a phone reads to install the dashboard."""
    m = client.get("/manifest.webmanifest")
    return {"manifest": m.get_data(), "head": head(client),
            **{u: client.get(u).get_data() for u in ICON_URLS}}


# --- acceptance ----------------------------------------------------------------------------

def test_the_manifest_is_served_with_its_mime_and_every_icon_it_lists_is_a_png_of_its_size(env, client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.mimetype == "application/manifest+json"
    m = json.loads(r.get_data())
    assert m["name"] == "chela – Mission Control"
    assert m["short_name"] == "chela"
    assert m["display"] == "standalone"
    assert m["start_url"] == "./"
    assert m["theme_color"] == "#1b1f24"
    assert m["background_color"] == "#1b1f24"
    assert [(i["sizes"], i["purpose"]) for i in m["icons"]] == [
        ("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")]
    for icon in m["icons"]:
        got = client.get("/" + icon["src"])       # relative to /manifest.webmanifest
        assert got.status_code == 200, icon
        assert got.mimetype == icon["type"] == "image/png"
        w, h = png_size(got.get_data())
        assert f"{w}x{h}" == icon["sizes"]


def test_the_head_links_the_apple_touch_icon_the_manifest_and_the_theme_color(env, client):
    h = head(client)
    assert re.search(r'<link rel="apple-touch-icon" sizes="180x180" href="apple-touch-icon.png">', h)
    assert '<link rel="manifest" href="manifest.webmanifest" crossorigin="use-credentials">' in h
    assert '<meta name="theme-color" content="#1b1f24">' in h
    assert '<meta name="apple-mobile-web-app-capable" content="yes">' in h
    assert '<meta name="apple-mobile-web-app-title" content="chela">' in h
    assert "<title>chela – Mission Control</title>" in h
    touch = client.get("/apple-touch-icon.png")
    assert touch.mimetype == "image/png"
    assert png_size(touch.get_data()) == (180, 180)


def test_the_work_variant_changes_the_manifest_title_and_icons(env, client):
    default = fetch(client)
    env(CHELA_DASHBOARD_ICON_VARIANT="work", CHELA_DASHBOARD_APP_NAME="chela work")
    work = fetch(client)
    m = json.loads(work["manifest"])
    assert m["name"] == "chela work – Mission Control"
    assert m["short_name"] == "chela work"
    assert m["theme_color"] == "#5c4100"
    assert m["display"] == "standalone"
    h = work["head"]
    assert "<title>chela work – Mission Control</title>" in h
    assert '<meta name="apple-mobile-web-app-title" content="chela work">' in h
    assert '<meta name="theme-color" content="#5c4100">' in h
    for url in ICON_URLS:
        assert work[url] != default[url], url


# The badge's square, as a share of the side: the bottom-right corner of the mark's box,
# which is 80% of the icon (56% on the maskable one, to stay in its safe zone).
BADGE = {"/apple-touch-icon.png": (0.66, 0.84), "/icon-192.png": (0.66, 0.84),
         "/icon-512.png": (0.66, 0.84), "/icon-512-maskable.png": (0.62, 0.74)}


@pytest.mark.parametrize("url", ICON_URLS)
def test_the_work_icon_is_the_same_mark_plus_a_badge_in_its_bottom_right_corner(env, client, url):
    d = png_pixels(client.get(url).get_data())
    env(CHELA_DASHBOARD_ICON_VARIANT="work")
    w = png_pixels(client.get(url).get_data())
    n = len(d)
    # The badge region differs in LUMINANCE (shape, not hue): a red-weak eye sees it.
    lo, hi = (int(f * n) for f in BADGE[url])
    dd, ww = region(d, lo, lo, hi, hi), region(w, lo, lo, hi, hi)
    diff = sum(abs(luma(a) - luma(b)) for a, b in zip(dd, ww)) / len(dd)
    assert diff > 60, diff
    # ...and the rest is the same mark: the top half is byte-for-byte the default's.
    assert w[:n // 2] == d[:n // 2]


def test_without_the_env_the_output_is_byte_identical_to_the_explicit_default(env, client):
    unset = fetch(client)
    env(CHELA_DASHBOARD_ICON_VARIANT="default", CHELA_DASHBOARD_APP_NAME="chela")
    explicit = fetch(client)
    env(CHELA_DASHBOARD_ICON_VARIANT="", CHELA_DASHBOARD_APP_NAME="   ")
    blank = fetch(client)

    def stable(f):   # the page's per-request session token is not configuration
        return {**f, "head": re.sub(r'"sessionToken": "[^"]*"', "", f["head"])}
    assert stable(unset) == stable(explicit) == stable(blank)


def test_the_maskable_work_icon_keeps_its_badge_inside_the_safe_zone(env):
    """A maskable icon may be cropped to a circle of radius 0.4 × side: outside it, only tile."""
    rows = png_pixels(pwa.icon_png("work", 512, "maskable"))
    c, r = 256, 0.4 * 512
    outside = {rows[y][x] for y in range(512) for x in range(512)
               if (x + 0.5 - c) ** 2 + (y + 0.5 - c) ** 2 > r * r}
    assert outside == {(0x1B, 0x1F, 0x24)}


def test_the_default_icon_draws_the_mark_on_an_opaque_tile(env):
    rows = png_pixels(pwa.icon_png("default", 180, "any"))
    assert rows[0][0] == rows[179][179] == (0x1B, 0x1F, 0x24)
    marked = sum(1 for row in rows for px in row if px == (0xF0, 0xF3, 0xF6))
    assert marked > 0.05 * 180 * 180


# --- configuration -------------------------------------------------------------------------

@pytest.mark.parametrize("variant_env, name_env, variant, name", [
    (None, None, "default", "chela"),
    ("", "", "default", "chela"),
    ("default", None, "default", "chela"),
    ("work", None, "work", "chela"),
    ("  WORK ", "  chela work ", "work", "chela work"),
    (None, "ops", "default", "ops"),
])
def test_settings_come_from_the_env(env, variant_env, name_env, variant, name):
    if variant_env is not None:
        env(CHELA_DASHBOARD_ICON_VARIANT=variant_env)
    if name_env is not None:
        env(CHELA_DASHBOARD_APP_NAME=name_env)
    s = pwa.settings()
    assert (s.variant, s.app_name) == (variant, name)


@pytest.mark.parametrize("url", ["/", "/manifest.webmanifest", "/icon-192.png"])
def test_an_unknown_variant_fails_closed(env, client, url):
    """A typo must not make a work dashboard look like the personal one."""
    env(CHELA_DASHBOARD_ICON_VARIANT="wrok")
    r = client.get(url)
    assert r.status_code == 503
    assert "CHELA_DASHBOARD_ICON_VARIANT='wrok'" in r.get_data(as_text=True)


def test_the_icon_drawer_refuses_an_unknown_variant():
    with pytest.raises(pwa.ConfigError):
        pwa.icon_png("personal", 192, "any")


def test_the_app_name_is_escaped_in_the_head_and_kept_verbatim_in_the_manifest(env, client):
    env(CHELA_DASHBOARD_APP_NAME='<b>"x')
    h = head(client)
    assert "<b>" not in h
    assert 'content="&lt;b&gt;&#34;x"' in h
    assert json.loads(client.get("/manifest.webmanifest").get_data())["short_name"] == '<b>"x'


def test_an_icon_revalidates_by_etag(env, client):
    first = client.get("/icon-192.png")
    assert first.headers["Cache-Control"] == "no-cache"
    again = client.get("/icon-192.png", headers={"If-None-Match": first.headers["ETag"]})
    assert again.status_code == 304


def test_an_unlisted_icon_name_is_not_served(client):
    assert client.get("/icon-64.png").status_code == 404


# --- the path reader and the rasterizer ----------------------------------------------------

def test_a_path_of_lines_reads_as_one_closed_polygon():
    assert pwa.parse_path("M0 0 L10 0 L10 10 L0 10 Z") == [[(0, 0), (10, 0), (10, 10), (0, 10)]]
    assert pwa.parse_path("M0 0 10 0 10 10 Z") == [[(0, 0), (10, 0), (10, 10)]]


def test_a_cubic_ends_on_its_end_point():
    (poly,) = pwa.parse_path("M0 0 C0 10 10 10 10 0 Z")
    assert poly[0] == (0, 0)
    assert poly[-1] == (10, 0)
    assert len(poly) == 11


@pytest.mark.parametrize("d", ["M0 0 Q1 1 2 2 Z", "M0 0 c1 1 2 2 3 3 Z", "M0 0 C1 1 2 2 Z", "M0 0 L1 Z",
                               "L1 1"])
def test_a_path_command_it_cannot_draw_raises(d):
    with pytest.raises(ValueError):
        pwa.parse_path(d)


def test_coverage_fills_even_odd_with_a_hole():
    outer = [(2.0, 2.0), (18.0, 2.0), (18.0, 18.0), (2.0, 18.0)]
    inner = [(6.0, 6.0), (14.0, 6.0), (14.0, 14.0), (6.0, 14.0)]
    g = pwa.coverage([outer, inner], 20)
    assert g[3][3] == pytest.approx(1.0)
    assert g[10][10] == pytest.approx(0.0)
    assert g[0][0] == pytest.approx(0.0)


def test_coverage_is_fractional_on_an_edge():
    g = pwa.coverage([[(0.0, 0.0), (2.5, 0.0), (2.5, 4.0), (0.0, 4.0)]], 4)
    assert g[1][:4] == pytest.approx([1.0, 1.0, 0.5, 0.0])


def test_the_browser_fixture_renders_the_same_head_as_the_default_settings(env, client):
    """tests/browser/fixture.mjs renders the template by regex; its PWA values must be what an
    unconfigured dashboard renders, or the browser suites test a page production never serves."""
    fixture = (Path(__file__).parent / "browser" / "fixture.mjs").read_text()
    h = head(client)
    for value in ("chela – Mission Control", "chela", "#1b1f24"):
        assert f"'{value}')" in fixture
    assert "<title>chela – Mission Control</title>" in h
    assert 'content="chela"' in h and 'content="#1b1f24"' in h


def test_a_move_without_a_close_keeps_the_polygon_before_it():
    assert pwa.parse_path("M0 0 L4 0 L4 4 M10 10 L14 10 L14 14 Z") == [
        [(0, 0), (4, 0), (4, 4)], [(10, 10), (14, 10), (14, 14)]]


def test_a_path_that_never_closes_still_draws():
    assert pwa.parse_path("M0 0 L4 0 L4 4") == [[(0, 0), (4, 0), (4, 4)]]


@pytest.mark.parametrize("svg", [
    '<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0 L1 0 L1 1 Z"/></svg>',
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><rect width="1"/></svg>',
])
def test_a_mark_without_a_viewbox_or_a_path_is_refused(monkeypatch, tmp_path, svg):
    f = tmp_path / "mark.svg"
    f.write_text(svg)
    monkeypatch.setattr(pwa, "MARK_SVG", f)
    pwa.mark.cache_clear()
    try:
        with pytest.raises(ValueError, match="no viewBox or no path"):
            pwa.mark()
    finally:
        pwa.mark.cache_clear()


def test_coverage_of_a_shape_off_the_canvas_is_zero_not_negative():
    g = pwa.coverage([[(-5.0, 0.0), (-0.5, 0.0), (-0.5, 4.0), (-5.0, 4.0)],
                      [(4.5, 0.0), (9.0, 0.0), (9.0, 4.0), (4.5, 4.0)]], 4)
    assert g[1] == pytest.approx([0.0, 0.0, 0.0, 0.0])


def test_coverage_of_a_span_inside_one_pixel_is_its_width():
    g = pwa.coverage([[(1.25, 0.0), (1.75, 0.0), (1.75, 4.0), (1.25, 4.0)]], 4)
    assert g[2] == pytest.approx([0.0, 0.5, 0.0, 0.0])
