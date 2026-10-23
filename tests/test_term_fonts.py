"""CMX-322: the web terminal's fonts are woff2, the icon font is split into small
unicode-range blocks, and every font URL is content-hashed and served immutable — so a
pane never re-downloads or revalidates a font (chela/dashboard/term_fonts.py).

Measured before this change: the icon font was a 2,333,488-byte TTF served
``Cache-Control: no-cache``, and the font shim waited for it on every pane open.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

import chela.dashboard.app as app_mod
from chela.dashboard import term_fonts
from chela.dashboard.term_fonts import FONTS_DIR, NERD_SOURCE, NERD_SUBSETS, TERM_FONTS, TermFont

# The size of the TTF every pane used to download (2026-10-23), as a literal.
_OLD_NERD_TTF_BYTES = 2_333_488

# The TUI glyphs a pane must render, and the face each must come from: Claude Code's
# tool marker / status glyphs, Powerline arrows and a few common Nerd icons.
_GLYPHS = {
    chr(0x23FA): "Symbola Fallback",      # Claude Code's tool marker
    chr(0x2705): "Symbola Fallback",      # check mark
    chr(0x2726): "Symbola Fallback",      # spinner star
    chr(0xE0B0): "JetBrains Mono",        # Powerline right arrow (JetBrains Mono has the basic set)
    chr(0xE0A0): "JetBrains Mono",        # Powerline branch
    chr(0xE0B4): "Symbols Nerd Font",     # Powerline Extra: right half-circle
    chr(0xE0C0): "Symbols Nerd Font",     # Powerline Extra: flame
    chr(0xE702): "Symbols Nerd Font",     # Devicons: git
    chr(0xF015): "Symbols Nerd Font",     # Font Awesome: home
    chr(0xEA60): "Symbols Nerd Font",     # Codicons
    chr(0xF418): "Symbols Nerd Font",     # Octicons: git-branch
}


def _ttfont():
    ttlib = pytest.importorskip("fontTools.ttLib")
    pytest.importorskip("brotli")   # fontTools reads woff2 only with brotli (fonttools[woff])
    return ttlib.TTFont


def _ranges(spec: str) -> list[tuple[int, int]]:
    out = []
    for part in spec.split(","):
        m = re.fullmatch(r"U\+([0-9A-F]+)(?:-([0-9A-F]+))?", part.strip())
        assert m, f"not a unicode-range: {part!r}"
        lo = int(m.group(1), 16)
        out.append((lo, int(m.group(2), 16) if m.group(2) else lo))
    return out


def _in(cp: int, spec: str | None) -> bool:
    return spec is None or any(lo <= cp <= hi for lo, hi in _ranges(spec))


# --- the manifest ---------------------------------------------------------------------

def test_every_face_is_a_woff2_file_on_disk():
    for f in TERM_FONTS:
        assert f.file.endswith(".woff2"), f
        assert (FONTS_DIR / f.file).is_file(), f"{f.file} is declared but not on disk"


def test_the_nerd_font_is_woff2_blocks_much_smaller_than_the_old_ttf():
    nerd = [f for f in TERM_FONTS if f.family == "Symbols Nerd Font"]
    assert [f.file for f in nerd] == [
        "SymbolsNerdFont-Powerline.woff2", "SymbolsNerdFont-FileIcons.woff2",
        "SymbolsNerdFont-FontAwesome.woff2", "SymbolsNerdFont-UI.woff2",
    ]
    sizes = {f.file: (FONTS_DIR / f.file).stat().st_size for f in nerd}
    # Every block is a unicode-range face, so a pane downloads only the blocks it shows.
    assert all(f.unicode_range for f in nerd)
    # The block every prompt / status line needs is tiny.
    assert sizes["SymbolsNerdFont-Powerline.woff2"] < 16 * 1024, sizes
    # All of them together are a quarter of the old TTF at most; any one, an eighth.
    assert sum(sizes.values()) < _OLD_NERD_TTF_BYTES // 4, sizes
    assert max(sizes.values()) < _OLD_NERD_TTF_BYTES // 8, sizes


def test_the_font_css_names_no_ttf_and_never_blocks():
    css = app_mod._TERM_FONT_CSS
    assert ".ttf" not in css
    assert "truetype" not in css
    assert "font-display:block" not in css
    assert css.count("@font-face") == len(TERM_FONTS)
    assert css.count("font-display:swap") == len(TERM_FONTS)
    for f in TERM_FONTS:
        assert f"url('/static/fonts/v/{term_fonts.FONT_DIGESTS[f.file]}/{f.file}')" in css, f


# --- unicode-range vs. the fonts' real cmaps ------------------------------------------

def test_each_face_declares_a_range_covering_every_glyph_it_has():
    """A codepoint the font has but its unicode-range leaves out is unreachable: the
    browser skips the face for it and draws tofu."""
    TTFont = _ttfont()
    for f in TERM_FONTS:
        if f.unicode_range is None:
            continue
        cmap = TTFont(str(FONTS_DIR / f.file)).getBestCmap()
        outside = [hex(cp) for cp in cmap if cp > 0x20 and not _in(cp, f.unicode_range)]
        assert not outside, f"{f.file}: {outside[:10]}"


def test_each_nerd_block_kept_every_glyph_its_range_names():
    TTFont = _ttfont()
    source = TTFont(str(FONTS_DIR / NERD_SOURCE)).getBestCmap()
    for file, spec in NERD_SUBSETS:
        cmap = TTFont(str(FONTS_DIR / file)).getBestCmap()
        lost = [hex(cp) for cp in source if _in(cp, spec) and cp not in cmap]
        assert not lost, f"{file} dropped {lost[:10]}"


@pytest.mark.parametrize("glyph,family", list(_GLYPHS.items()))
def test_tui_glyph_resolves_to_a_face_that_has_it(glyph, family):
    """Walk the shim's family stack the way a browser does: the first face whose
    unicode-range admits the codepoint AND whose cmap has it draws it."""
    TTFont = _ttfont()
    # The default picker faces, in the shim's `fam` order.
    stack = ["JetBrains Mono", "Symbols Nerd Font", "Symbola Fallback", "Miriam Mono CLM"]
    assert ("var fam=\"'\"+lat+\"','Symbols Nerd Font','Symbola Fallback','\"+heb+\"',monospace\";"
            in app_mod._TERM_FONT_PREF_SHIM)
    cp = ord(glyph)
    for fam in stack:
        for f in (f for f in TERM_FONTS if f.family == fam):
            if _in(cp, f.unicode_range) and cp in TTFont(str(FONTS_DIR / f.file)).getBestCmap():
                assert fam == family, f"{glyph!r} drawn by {fam} ({f.file}), expected {family}"
                return
    pytest.fail(f"{glyph!r} (U+{cp:04X}) is in no face of the terminal's stack: tofu")


# --- URLs and caching -----------------------------------------------------------------

def test_font_digests_hash_each_woff2(tmp_path):
    (tmp_path / "A.woff2").write_bytes(b"alpha")
    (tmp_path / "B.ttf").write_bytes(b"not served")
    assert term_fonts.font_digests(tmp_path) == {
        "A.woff2": hashlib.sha256(b"alpha").hexdigest()[:12],
    }


def test_font_url_is_content_hashed():
    assert term_fonts.font_url("A.woff2", {"A.woff2": "0123456789ab"}) == \
        "/static/fonts/v/0123456789ab/A.woff2"
    assert term_fonts.font_url("Gone.woff2", {}) == "/static/fonts/v/0/Gone.woff2"


@pytest.mark.parametrize("font,expected", [
    (TermFont("Icons", "I.woff2", None, None, "U+E000-E0FF"),
     "@font-face{font-family:'Icons';unicode-range:U+E000-E0FF;"
     "src:url('/static/fonts/v/d1/I.woff2') format('woff2');font-display:swap}"),
    (TermFont("Var", "V.woff2", True, None),
     "@font-face{font-family:'Var';font-weight:100 900;"
     "src:url('/static/fonts/v/d2/V.woff2') format('woff2');font-display:swap}"),
    (TermFont("Static", "S.woff2", False, "bold"),
     "@font-face{font-family:'Static';font-weight:bold;"
     "src:url('/static/fonts/v/d3/S.woff2') format('woff2');font-display:swap}"),
])
def test_font_face_css(font, expected):
    digests = {"I.woff2": "d1", "V.woff2": "d2", "S.woff2": "d3"}
    assert term_fonts.font_face_css(font, digests) == expected


def test_a_font_at_its_current_digest_is_immutable():
    client = app_mod.app.test_client()
    for file in ("SymbolsNerdFont-Powerline.woff2", "JetBrainsMono.woff2"):
        resp = client.get(term_fonts.font_url(file))
        assert resp.status_code == 200
        assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"
        assert resp.headers["Content-Type"] == "font/woff2"
        assert resp.data == (FONTS_DIR / file).read_bytes()


def test_a_font_at_a_stale_digest_is_served_but_not_cached():
    resp = app_mod.app.test_client().get("/static/fonts/v/000000000000/JetBrainsMono.woff2")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "no-cache"


def test_a_missing_font_is_404():
    resp = app_mod.app.test_client().get("/static/fonts/v/0/Nope.woff2")
    assert resp.status_code == 404


def test_the_digest_in_the_url_is_the_hash_of_the_served_bytes():
    file = "MiriamMonoCLM-Book.woff2"
    data = (Path(FONTS_DIR) / file).read_bytes()
    assert term_fonts.FONT_DIGESTS[file] == hashlib.sha256(data).hexdigest()[:12]
