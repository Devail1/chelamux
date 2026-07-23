"""Terminal text → PNG renderer for the bridge's ``/screenshot`` command.

:func:`text_to_image` turns a captured tmux pane (optionally carrying ANSI SGR
colour escapes, via ``tmux capture-pane -e``) into a dark-background PNG so an
operator gets a faithful, monospaced snapshot of an agent's terminal in
Telegram instead of reflowed plain text.

The ANSI parser (16-colour, 256-colour cube + grayscale, and 24-bit RGB) is
ported from six-ddc/ccbot's ``src/ccbot/screenshot.py`` (MIT); see the top-level
NOTICE file for upstream attribution.

Pillow does no per-glyph font fallback: a character missing from the font
you hand it renders as ``.notdef`` tofu (``▢``). Claude Code's TUI draws its
tool-use bullet (``⏺``), thinking spinners (``✦``/``✷``/``✨``) and status
markers (``⚙``) from Unicode blocks that JetBrains Mono — chela's bundled
dashboard/web-terminal font — doesn't cover. So each character is rendered
with the first font in a small chain that actually contains its glyph:
JetBrains Mono (primary) → a small Symbola subset (Misc Technical + Misc
Symbols/Dingbats — the blocks those glyphs live in) → Symbols Nerd Font
(already bundled for the web terminal's icon glyphs) → Pillow's built-in
bitmap font as a last resort. See ``chela/dashboard/static/fonts/README.md``
for the font licenses.

Pillow is an optional dependency (the ``[telegram]`` extra installs it); importing
this module without Pillow raises ImportError, and the ``/screenshot`` handler
degrades to a text snapshot in that case. fontTools (also a ``[telegram]``
extra dependency) is used only to read each fallback font's cmap once at
import time — no shaping/layout work happens through it.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont

_FONTS_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "static" / "fonts"

# Ordered glyph-fallback chain: the first font whose cmap contains a given
# character wins. JetBrains Mono (SIL OFL 1.1) is the primary monospaced
# face; Symbola (subset, freeware — see LICENSE-Symbola.txt) and Symbols
# Nerd Font (MIT) are reused/subsetted purely to cover glyphs JetBrains Mono
# lacks, rather than switching the whole render to a different font.
_FONT_PATH = _FONTS_DIR / "JetBrainsMono.ttf"
_FALLBACK_FONT_PATHS = (
    _FONT_PATH,
    _FONTS_DIR / "Symbola-Subset.ttf",
    _FONTS_DIR / "SymbolsNerdFontMono-Regular.ttf",
)

# Basic 16-colour ANSI palette (indices 0-15), tuned to a dark terminal theme.
_ANSI_COLORS: dict[int, tuple[int, int, int]] = {
    0: (0, 0, 0), 1: (205, 49, 49), 2: (13, 188, 121), 3: (229, 229, 16),
    4: (36, 114, 200), 5: (188, 63, 188), 6: (17, 168, 205), 7: (229, 229, 229),
    8: (102, 102, 102), 9: (241, 76, 76), 10: (35, 209, 139), 11: (245, 245, 67),
    12: (59, 142, 234), 13: (214, 112, 214), 14: (41, 184, 219), 15: (255, 255, 255),
}
_DEFAULT_FG = (212, 212, 212)
_DEFAULT_BG = (30, 30, 30)

_ANSI_PATTERN = re.compile(r"\x1b\[([0-9;]*)m")


@dataclass
class _Style:
    """Foreground/background state carried across an ANSI-parsed line."""

    fg: tuple[int, int, int] = _DEFAULT_FG
    bg: tuple[int, int, int] | None = None


@dataclass
class _Segment:
    """A run of text sharing one :class:`_Style`."""

    text: str
    style: _Style


@lru_cache(maxsize=None)
def _font_cmap(path: Path) -> frozenset[int]:
    """The set of Unicode code points ``path`` has glyphs for (empty on failure)."""
    try:
        font = TTFont(str(path), lazy=True, fontNumber=0)
        try:
            return frozenset(font.getBestCmap())
        finally:
            font.close()
    except Exception:
        return frozenset()


@lru_cache(maxsize=None)
def _load_fallback_chain(size: int) -> tuple[tuple[ImageFont.FreeTypeFont, frozenset[int]], ...]:
    """Loaded ``(face, cmap)`` pairs for :data:`_FALLBACK_FONT_PATHS`, in order."""
    chain = []
    for path in _FALLBACK_FONT_PATHS:
        try:
            face = ImageFont.truetype(str(path), size)
        except OSError:
            continue
        chain.append((face, _font_cmap(path)))
    return tuple(chain)


def _load_font(size: int):
    """The primary face at ``size`` (JetBrains Mono), or Pillow's default on failure."""
    chain = _load_fallback_chain(size)
    return chain[0][0] if chain else ImageFont.load_default()


def _face_for_char(chain, default, ch: str):
    """The first fallback-chain face that has a glyph for ``ch`` (else ``default``)."""
    cp = ord(ch)
    for face, cmap in chain:
        if cp in cmap:
            return face
    return default


def _split_by_face(text: str, chain, default) -> list[tuple[str, object]]:
    """Group consecutive characters of ``text`` that resolve to the same face.

    Keeps runs whole when possible so measuring/drawing stays cheap for the
    common case (a whole segment in the primary font).
    """
    if not text:
        return []
    runs: list[tuple[str, object]] = []
    current_face = _face_for_char(chain, default, text[0])
    current_chars = [text[0]]
    for ch in text[1:]:
        face = _face_for_char(chain, default, ch)
        if face is current_face:
            current_chars.append(ch)
        else:
            runs.append(("".join(current_chars), current_face))
            current_face = face
            current_chars = [ch]
    runs.append(("".join(current_chars), current_face))
    return runs


def _approximate_256_color(idx: int) -> tuple[int, int, int]:
    """Map a 256-colour palette index to RGB (cube + grayscale ramp)."""
    if idx < 16:
        return _ANSI_COLORS[idx]
    if idx < 232:
        idx -= 16
        return ((idx // 36) * 51, ((idx % 36) // 6) * 51, (idx % 6) * 51)
    gray = 8 + (idx - 232) * 10
    return (gray, gray, gray)


def _apply_codes(style: _Style, codes: str) -> _Style:
    """Return a new :class:`_Style` after applying an SGR code sequence."""
    new = _Style(fg=style.fg, bg=style.bg)
    parts = [int(c) for c in codes.split(";") if c]
    i = 0
    while i < len(parts):
        code = parts[i]
        if code == 0:
            new = _Style()
        elif 30 <= code <= 37:
            new.fg = _ANSI_COLORS[code - 30]
        elif code == 38:  # extended foreground
            if i + 2 < len(parts) and parts[i + 1] == 5:
                new.fg = _approximate_256_color(parts[i + 2] % 256)
                i += 2
            elif i + 4 < len(parts) and parts[i + 1] == 2:
                new.fg = (parts[i + 2], parts[i + 3], parts[i + 4])
                i += 4
        elif code == 39:
            new.fg = _DEFAULT_FG
        elif 40 <= code <= 47:
            new.bg = _ANSI_COLORS[code - 40]
        elif code == 48:  # extended background
            if i + 2 < len(parts) and parts[i + 1] == 5:
                new.bg = _approximate_256_color(parts[i + 2] % 256)
                i += 2
            elif i + 4 < len(parts) and parts[i + 1] == 2:
                new.bg = (parts[i + 2], parts[i + 3], parts[i + 4])
                i += 4
        elif code == 49:
            new.bg = None
        elif 90 <= code <= 97:
            new.fg = _ANSI_COLORS[code - 90 + 8]
        elif 100 <= code <= 107:
            new.bg = _ANSI_COLORS[code - 100 + 8]
        i += 1
    return new


def _parse_line(line: str, with_ansi: bool) -> list[_Segment]:
    """Split ``line`` into styled segments (one flat segment when ANSI is off)."""
    if not with_ansi:
        return [_Segment(_ANSI_PATTERN.sub("", line), _Style())]
    segments: list[_Segment] = []
    style = _Style()
    pos = 0
    for match in _ANSI_PATTERN.finditer(line):
        before = line[pos:match.start()]
        if before:
            segments.append(_Segment(before, style))
        codes = match.group(1)
        style = _apply_codes(style, codes) if codes else _Style()
        pos = match.end()
    tail = line[pos:]
    if tail:
        segments.append(_Segment(tail, style))
    return segments or [_Segment("", _Style())]


def text_to_image(text: str, *, font_size: int = 24, with_ansi: bool = True) -> bytes:
    """Render ``text`` to a dark-background PNG and return its bytes.

    ``with_ansi`` parses SGR colour escapes (from ``capture-pane -e``); with it
    off (or on text that has none) everything renders in the default fg. Always
    returns non-empty PNG bytes for any input, including the empty string.
    """
    font = _load_font(font_size)
    chain = _load_fallback_chain(font_size)
    lines = text.split("\n")
    line_segments = [_parse_line(line, with_ansi) for line in lines]
    # Split each segment into (run_text, face) runs once, up front, so a
    # segment that mixes e.g. plain text and a spinner glyph draws each run
    # with whichever font in the fallback chain actually has that glyph.
    line_runs = [
        [(seg.style, _split_by_face(seg.text, chain, font)) for seg in segments]
        for segments in line_segments
    ]

    padding = 16
    line_height = int(font_size * 1.4)

    # Measure against a throwaway canvas before sizing the real one.
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    max_width = 0
    for seg_runs in line_runs:
        w = 0
        for _style, runs in seg_runs:
            for run_text, face in runs:
                bbox = probe.textbbox((0, 0), run_text, font=face)
                w += bbox[2] - bbox[0]
        max_width = max(max_width, w)

    img_width = max(1, int(max_width)) + padding * 2
    img_height = line_height * max(1, len(lines)) + padding * 2

    img = Image.new("RGB", (img_width, img_height), _DEFAULT_BG)
    draw = ImageDraw.Draw(img)

    y = padding
    for seg_runs in line_runs:
        x = padding
        for style, runs in seg_runs:
            if style.bg and runs:
                cursor, left, right = x, None, None
                for run_text, face in runs:
                    bbox = draw.textbbox((cursor, y), run_text, font=face)
                    left = bbox[0] if left is None else left
                    right = bbox[2]
                    cursor += bbox[2] - bbox[0]
                draw.rectangle([left, y, right, y + line_height], fill=style.bg)
            for run_text, face in runs:
                draw.text((x, y), run_text, fill=style.fg, font=face)
                bbox = draw.textbbox((0, 0), run_text, font=face)
                x += bbox[2] - bbox[0]
        y += line_height

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
