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


# Claude Code's TUI draws these from Unicode blocks JetBrains Mono (the
# primary font) doesn't cover — the exact glyphs that rendered as tofu (`▢`)
# in the posted /screenshot PNG before the fallback chain was added.
_TUI_GLYPHS = "⏺✦✷✨⚙"


def test_primary_font_really_lacks_the_tui_glyphs():
    # Guards the fixture assumption below: if JetBrains Mono ever gains these
    # glyphs upstream, the fallback chain is still correct but no longer the
    # only thing making them render.
    chain = screenshot._load_fallback_chain(24)
    _primary_face, primary_cmap = chain[0]
    for glyph in _TUI_GLYPHS:
        assert ord(glyph) not in primary_cmap, glyph


def test_fallback_chain_covers_every_tui_glyph():
    chain = screenshot._load_fallback_chain(24)
    for glyph in _TUI_GLYPHS:
        assert any(ord(glyph) in cmap for _face, cmap in chain), glyph


def test_face_for_char_picks_a_fallback_face_for_tui_glyphs():
    chain = screenshot._load_fallback_chain(24)
    primary_face = chain[0][0]
    for glyph in _TUI_GLYPHS:
        face = screenshot._face_for_char(chain, primary_face, glyph)
        assert face is not primary_face, glyph


def test_split_by_face_breaks_out_a_fallback_run():
    chain = screenshot._load_fallback_chain(24)
    primary_face = chain[0][0]
    runs = screenshot._split_by_face("⏺ Bash", chain, primary_face)
    assert [text for text, _face in runs] == ["⏺", " Bash"]
    assert runs[0][1] is not primary_face
    assert runs[1][1] is primary_face


def test_renders_tui_marker_glyphs_to_png():
    png = screenshot.text_to_image("\n".join(_TUI_GLYPHS))
    assert png.startswith(_PNG_MAGIC)
