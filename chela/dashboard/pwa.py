"""The dashboard as an installable web app: home-screen icons and the web manifest (CMX-457).

iOS ignores an SVG for "Add to Home Screen" (it saves a page screenshot instead) and Android
wants a manifest with PNG icons, so the page head links an ``apple-touch-icon`` (180×180) and
``manifest.webmanifest`` (192, 512 and a maskable 512).

The PNGs are drawn here, from the chela mark in ``static/img/favicon.svg`` itself, in pure
Python (Pillow is not a core dependency): the path is flattened to polygons, filled
even-odd with anti-aliasing, and encoded with ``zlib``. Each icon is drawn once per process.

**Two variants, both env-only** (like the identity settings: ``~/.chela/config.json`` is a
file the dashboard itself may write, so it must not be able to change what the dashboard
claims to be):

* ``CHELA_DASHBOARD_ICON_VARIANT``: ``default`` or ``work``. The ``work`` icon is the same
  mark plus a corner badge (Lucide's briefcase on a rounded square). Told apart by SHAPE,
  not hue alone: a red-weak eye must see two different icons. Its accent and
  ``theme_color`` differ on top of that. An unknown value fails CLOSED (:class:`ConfigError`):
  a typo must not make a work dashboard look like a personal one.
* ``CHELA_DASHBOARD_APP_NAME``: the label (default ``chela``): the manifest's
  ``short_name``, the iOS home-screen title, and the page title's first word.

Nothing org-specific ships here: only the generic ``work`` variant. The org sets the name.
"""
from __future__ import annotations

import math
import os
import re
import struct
import threading
import zlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

VARIANT_ENV = "CHELA_DASHBOARD_ICON_VARIANT"
APP_NAME_ENV = "CHELA_DASHBOARD_APP_NAME"

DEFAULT, WORK = "default", "work"
VARIANTS = (DEFAULT, WORK)
DEFAULT_APP_NAME = "chela"

MARK_SVG = Path(__file__).parent / "static" / "img" / "favicon.svg"

# The tile and the mark are the same in both variants: the badge is what tells them apart.
TILE = "#1b1f24"
MARK = "#f0f3f6"
# variant -> (theme_color, badge accent)
THEMES = {
    DEFAULT: ("#1b1f24", None),
    WORK: ("#5c4100", "#f2b134"),
}

# (file name, size, purpose): the manifest lists all but the apple-touch icon.
APPLE_TOUCH = ("apple-touch-icon.png", 180, "any")
MANIFEST_ICONS = (
    ("icon-192.png", 192, "any"),
    ("icon-512.png", 512, "any"),
    ("icon-512-maskable.png", 512, "maskable"),
)
ICONS = {name: (size, purpose) for name, size, purpose in (APPLE_TOUCH, *MANIFEST_ICONS)}

# The mark's box as a share of the icon's side. A maskable icon may be cropped to a circle of
# radius 0.4 × side, so everything (badge included) sits inside that.
_BOX = {"any": 0.8, "maskable": 0.56}
_SUPERSAMPLE = 4


class ConfigError(ValueError):
    """A PWA setting is set but unusable."""


@dataclass(frozen=True)
class Settings:
    variant: str
    app_name: str

    @property
    def theme_color(self) -> str:
        return THEMES[self.variant][0]

    @property
    def title(self) -> str:
        return f"{self.app_name} – Mission Control"


def settings() -> Settings:
    """The configured variant and app name, from the environment only."""
    raw = (os.environ.get(VARIANT_ENV) or "").strip().lower()
    variant = raw or DEFAULT
    if variant not in VARIANTS:
        raise ConfigError(f"{VARIANT_ENV}={raw!r} is not one of {', '.join(VARIANTS)}")
    name = (os.environ.get(APP_NAME_ENV) or "").strip() or DEFAULT_APP_NAME
    return Settings(variant, name)


def manifest(s: Settings) -> dict:
    """The web manifest. URLs are relative to the manifest's own, so a dashboard behind a path
    prefix still resolves them."""
    return {
        "name": s.title,
        "short_name": s.app_name,
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "theme_color": s.theme_color,
        "background_color": TILE,
        "icons": [{"src": name, "sizes": f"{size}x{size}", "type": "image/png",
                   "purpose": purpose} for name, size, purpose in MANIFEST_ICONS],
    }


# --- the mark -----------------------------------------------------------------------------

Poly = list[tuple[float, float]]
_NUM = r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"


def _cubic(p0, p1, p2, p3, n: int = 10) -> Poly:
    out = []
    for i in range(1, n + 1):
        t = i / n
        u = 1 - t
        out.append((u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
                    u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1]))
    return out


def parse_path(d: str) -> list[Poly]:
    """An absolute ``M``/``L``/``C``/``Z`` path as closed polygons (curves flattened). Any
    other command raises: a mark redrawn with one must be handled, not half-drawn."""
    tokens = re.findall(rf"[A-Za-z]|{_NUM}", d)
    polys: list[Poly] = []
    cur: Poly = []
    i, cmd = 0, None
    while i < len(tokens):
        if tokens[i].isalpha():
            cmd = tokens[i]
            i += 1
            if cmd == "Z":
                if cur:
                    polys.append(cur)
                cur = []
                continue
        nums = []
        while i < len(tokens) and not tokens[i].isalpha():
            nums.append(float(tokens[i]))
            i += 1
        if cmd == "M" and len(nums) >= 2:
            if cur:
                polys.append(cur)
            cur = [(nums[0], nums[1])]
            nums = nums[2:]
            cmd = "L"           # extra pairs after M are line-tos
        if cmd == "L" and len(nums) % 2 == 0 and cur:
            cur.extend(zip(nums[::2], nums[1::2]))
        elif cmd == "C" and nums and len(nums) % 6 == 0 and cur:
            for k in range(0, len(nums), 6):
                cur.extend(_cubic(cur[-1], nums[k:k + 2], nums[k + 2:k + 4], nums[k + 4:k + 6]))
        else:
            raise ValueError(f"unsupported path command {cmd!r} {nums[:6]}")
    if cur:
        polys.append(cur)
    return polys


@lru_cache(maxsize=1)
def mark() -> tuple[tuple[float, float, float], list[Poly]]:
    """``((min_x, min_y, side), polygons)``: the favicon's viewBox and its path, translated."""
    svg = MARK_SVG.read_text()
    vb_m = re.search(r'viewBox="([^"]+)"', svg)
    path_m = re.search(r'<path\b[^>]*\sd="([^"]+)"', svg)
    if not vb_m or not path_m:
        raise ValueError(f"{MARK_SVG} has no viewBox or no path")
    vb = [float(v) for v in vb_m.group(1).split()]
    path = path_m.group(1)
    tx, ty = 0.0, 0.0
    t = re.search(r'transform="translate\(([^,\s]+)[,\s]+([^)\s]+)\)"', svg)
    if t:
        tx, ty = float(t.group(1)), float(t.group(2))
    polys = [[(x + tx, y + ty) for x, y in p] for p in parse_path(path)]
    return (vb[0], vb[1], max(vb[2], vb[3])), polys


# --- drawing ------------------------------------------------------------------------------

def _rounded_rect(x0: float, y0: float, x1: float, y1: float, r: float) -> Poly:
    r = max(0.0, min(r, (x1 - x0) / 2, (y1 - y0) / 2))
    out: Poly = []
    for cx, cy, a0 in ((x1 - r, y0 + r, -90), (x1 - r, y1 - r, 0),
                       (x0 + r, y1 - r, 90), (x0 + r, y0 + r, 180)):
        for k in range(9):
            a = math.radians(a0 + k * 90 / 8)
            out.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return out


def coverage(polys: list[Poly], size: int) -> list[list[float]]:
    """Per-pixel coverage (0..1) of ``polys`` filled even-odd, on a ``size``-square grid:
    ``_SUPERSAMPLE`` scanlines per pixel row, exact horizontal span coverage."""
    rows: list[list[tuple]] = [[] for _ in range(size)]
    for p in polys:
        for (xa, ya), (xb, yb) in zip(p, p[1:] + p[:1]):
            lo, hi = min(ya, yb), max(ya, yb)
            for r in range(max(0, int(lo)), min(size, int(math.ceil(hi)))):
                rows[r].append((xa, ya, xb, yb))
    w = 1 / _SUPERSAMPLE
    grid = []
    for r in range(size):
        acc = [0.0] * size
        for j in range(_SUPERSAMPLE):
            y = r + (j + 0.5) * w
            xs = sorted(xa + (y - ya) * (xb - xa) / (yb - ya) for xa, ya, xb, yb in rows[r]
                        if (ya <= y < yb) or (yb <= y < ya))
            for a, b in zip(xs[::2], xs[1::2]):
                a, b = max(a, 0.0), min(b, float(size))
                if b <= a:
                    continue
                ia, ib = int(a), int(b)
                if ia == ib:
                    acc[ia] += (b - a) * w
                    continue
                acc[ia] += (ia + 1 - a) * w
                for k in range(ia + 1, ib):
                    acc[k] += w
                if ib < size:
                    acc[ib] += (b - ib) * w
        grid.append(acc)
    return grid


def _rgb(hexcolor: str) -> tuple[int, int, int]:
    return int(hexcolor[1:3], 16), int(hexcolor[3:5], 16), int(hexcolor[5:7], 16)


def _paint(img: list[list[list[float]]], cov: list[list[float]], color: str) -> None:
    c = _rgb(color)
    for row, crow in zip(img, cov):
        for x, a in enumerate(crow):
            if a > 0:
                a = min(a, 1.0)
                px = row[x]
                for k in range(3):
                    px[k] = px[k] * (1 - a) + c[k] * a


def _briefcase(x0: float, y0: float, side: float) -> list[list[Poly]]:
    """Lucide ``briefcase`` (24-unit grid, stroke 2) as filled shapes, each even-odd:
    ``<path d="M16 20V4a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16"/>`` and
    ``<rect width="20" height="14" x="2" y="6" rx="2"/>``."""
    def u(v: float) -> float:
        return v * side / 24

    def rr(a, b, c, d, r):
        return _rounded_rect(x0 + u(a), y0 + u(b), x0 + u(c), y0 + u(d), u(r))
    body = [rr(1, 5, 23, 21, 3), rr(3, 7, 21, 19, 1)]
    handle = [rr(7, 1, 17, 20, 3), rr(9, 3, 15, 20, 1)]
    return [body, handle]


def badge_box(size: int, purpose: str) -> tuple[float, float, float]:
    """``(x0, y0, side)`` of the work badge: the mark box's bottom-right corner."""
    box = _BOX[purpose] * size
    off = (size - box) / 2
    side = 0.38 * box
    return off + box - side, off + box - side, side


def _encode_png(img: list[list[list[float]]]) -> bytes:
    size = len(img)
    raw = bytearray()
    for row in img:
        raw.append(0)
        for px in row:
            raw.extend(max(0, min(255, int(round(v)))) for v in px)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b""))


_RENDER_LOCK = threading.Lock()


def icon_png(variant: str, size: int, purpose: str) -> bytes:
    """The icon as PNG bytes: an opaque tile (iOS fills transparency with black), the mark
    centred, and for ``work`` the badge cut into its bottom-right corner. Drawn once per
    process; the lock keeps a burst of first requests (a phone asks for several icons at
    once) from each drawing the same icon in parallel."""
    if variant not in VARIANTS:
        raise ConfigError(f"unknown icon variant {variant!r}")
    with _RENDER_LOCK:
        return _draw(variant, size, purpose)


@lru_cache(maxsize=None)
def _draw(variant: str, size: int, purpose: str) -> bytes:
    (vx, vy, vside), polys = mark()
    box = _BOX[purpose] * size
    off = (size - box) / 2
    k = box / vside
    placed = [[(off + (x - vx) * k, off + (y - vy) * k) for x, y in p] for p in polys]
    tile = list(_rgb(TILE))
    img = [[list(map(float, tile)) for _ in range(size)] for _ in range(size)]
    _paint(img, coverage(placed, size), MARK)
    accent = THEMES[variant][1]
    if accent:
        bx, by, bs = badge_box(size, purpose)
        ring = 0.07 * bs
        _paint(img, coverage([_rounded_rect(bx - ring, by - ring, bx + bs + ring, by + bs + ring,
                                            0.22 * bs + ring)], size), TILE)
        _paint(img, coverage([_rounded_rect(bx, by, bx + bs, by + bs, 0.22 * bs)], size), accent)
        pad = 0.16 * bs
        for shape in _briefcase(bx + pad, by + pad, bs - 2 * pad):
            _paint(img, coverage(shape, size), TILE)
    return _encode_png(img)
