"""PNG renderer for the bridge's /screenshot — exercised with no live Telegram.

These lock in that :func:`chela.telegram.screenshot.text_to_image` always emits
non-empty PNG bytes (verified by the PNG magic number) for plain text, ANSI-
coloured text, and the empty string, and that ANSI escapes never leak into the
rendered output as literal characters. Pillow is an optional ([telegram]) extra,
so the module is skipped when it isn't installed.
"""
from __future__ import annotations

import pytest

screenshot = pytest.importorskip("chela.telegram.screenshot")

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_renders_plain_text_to_png():
    png = screenshot.text_to_image("hello agent\nsecond line")
    assert png.startswith(_PNG_MAGIC)
    assert len(png) > len(_PNG_MAGIC)


def test_renders_ansi_coloured_text_to_png():
    # A red word then reset — the SGR codes must be parsed, not drawn literally.
    png = screenshot.text_to_image("\x1b[31mred\x1b[0m normal", with_ansi=True)
    assert png.startswith(_PNG_MAGIC)


def test_empty_input_still_returns_a_png():
    png = screenshot.text_to_image("")
    assert png.startswith(_PNG_MAGIC)


def test_parse_line_strips_ansi_when_disabled():
    # with_ansi=False must drop the escape codes entirely (no stray glyphs).
    segments = screenshot._parse_line("\x1b[31mred\x1b[0m", with_ansi=False)
    assert "".join(seg.text for seg in segments) == "red"


def test_parse_line_splits_styled_runs_when_enabled():
    segments = screenshot._parse_line("\x1b[31mred\x1b[0m plain", with_ansi=True)
    # The visible text round-trips and the coloured run carries a non-default fg.
    assert "".join(seg.text for seg in segments) == "red plain"
    assert segments[0].text == "red"
    assert segments[0].style.fg == screenshot._ANSI_COLORS[1]
