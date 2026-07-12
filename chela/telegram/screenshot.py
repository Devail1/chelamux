"""Terminal text → PNG renderer for the bridge's ``/screenshot`` command.

:func:`text_to_image` turns a captured tmux pane (optionally carrying ANSI SGR
colour escapes, via ``tmux capture-pane -e``) into a dark-background PNG so an
operator gets a faithful, monospaced snapshot of an agent's terminal in
Telegram instead of reflowed plain text.

The ANSI parser (16-colour, 256-colour cube + grayscale, and 24-bit RGB) is
ported from six-ddc/ccbot's ``src/ccbot/screenshot.py`` (MIT); see the top-level
NOTICE file for upstream attribution. We keep only a single font tier: chela
already bundles **JetBrains Mono** (SIL OFL 1.1) for the dashboard/web-terminal,
so the renderer reuses that one file and falls back to Pillow's built-in bitmap
font if it can't be loaded — ccbot's extra CJK/symbol tiers (Noto CJK, Symbola)
are intentionally dropped to avoid shipping more font binaries.

Pillow is an optional dependency (the ``[telegram]`` extra installs it); importing
this module without Pillow raises ImportError, and the ``/screenshot`` handler
degrades to a text snapshot in that case.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# Reuse the JetBrains Mono already bundled for the dashboard (SIL OFL 1.1 —
# license at chela/dashboard/static/fonts/OFL-JetBrainsMono.txt) rather than
# duplicating a font binary inside this package.
_FONT_PATH = (
    Path(__file__).resolve().parent.parent
    / "dashboard" / "static" / "fonts" / "JetBrainsMono.ttf"
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


def _load_font(size: int):
    """The bundled JetBrains Mono at ``size``, or Pillow's default on failure."""
    try:
        return ImageFont.truetype(str(_FONT_PATH), size)
    except OSError:
        return ImageFont.load_default()


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
    lines = text.split("\n")
    line_segments = [_parse_line(line, with_ansi) for line in lines]

    padding = 16
    line_height = int(font_size * 1.4)

    # Measure against a throwaway canvas before sizing the real one.
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    max_width = 0
    for segments in line_segments:
        w = 0
        for seg in segments:
            bbox = probe.textbbox((0, 0), seg.text, font=font)
            w += bbox[2] - bbox[0]
        max_width = max(max_width, w)

    img_width = max(1, int(max_width)) + padding * 2
    img_height = line_height * max(1, len(lines)) + padding * 2

    img = Image.new("RGB", (img_width, img_height), _DEFAULT_BG)
    draw = ImageDraw.Draw(img)

    y = padding
    for segments in line_segments:
        x = padding
        for seg in segments:
            if seg.style.bg:
                bbox = draw.textbbox((x, y), seg.text, font=font)
                draw.rectangle([bbox[0], y, bbox[2], y + line_height], fill=seg.style.bg)
            draw.text((x, y), seg.text, fill=seg.style.fg, font=font)
            bbox = draw.textbbox((0, 0), seg.text, font=font)
            x += bbox[2] - bbox[0]
        y += line_height

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
