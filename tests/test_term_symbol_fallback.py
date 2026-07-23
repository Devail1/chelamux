"""CMX-159: the dashboard web terminal's `⏺` (U+23FA), `❌` (U+274C), `✅`
(U+2705) and spinner glyphs (`✦` `✷` `✨` `⚙`) rendered as tofu (`▢`) even
after CMX-155's atlas fix, because none of `_TERM_FONTS` (chela/dashboard/
app.py) actually contain those glyphs — an xterm texture-atlas rebuild can't
rasterize a glyph no stacked font contains. CMX-156 already bundled a Symbola
subset with exactly this coverage for the /screenshot PNG renderer, but never
wired it into the web terminal's font stack. This checks both halves: the
font is actually served (@font-face in `_TERM_FONT_CSS`) AND actually reachable
in the fallback chain the shim hands xterm.js (`_TERM_FONT_PREF_SHIM`'s `fam`).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from chela.dashboard.app import _TERM_FONT_CSS, _TERM_FONT_PREF_SHIM, _TERM_FONTS

_FONTS_DIR = Path(__file__).resolve().parent.parent / "chela" / "dashboard" / "static" / "fonts"

# The exact glyphs the dispatch brief named as tofu on the wall panes.
_REQUIRED_GLYPHS = "⏺❌✅✦✷✨⚙"


def test_symbola_fallback_font_face_is_served():
    assert any(
        family == "Symbola Fallback" and filename == "Symbola-Subset.ttf"
        for family, filename, _variable, _weight in _TERM_FONTS
    ), "Symbola-Subset.ttf must be registered in _TERM_FONTS so it's actually @font-face'd"
    assert "Symbola Fallback" in _TERM_FONT_CSS
    assert "Symbola-Subset.ttf" in _TERM_FONT_CSS


def test_symbola_subset_file_actually_contains_the_reported_glyphs():
    fonttools = pytest.importorskip("fontTools.ttLib")
    font = fonttools.TTFont(str(_FONTS_DIR / "Symbola-Subset.ttf"))
    cmap = font.getBestCmap()
    missing = [ch for ch in _REQUIRED_GLYPHS if ord(ch) not in cmap]
    assert not missing, f"Symbola-Subset.ttf is missing glyphs: {missing!r}"


def test_shim_fallback_chain_includes_symbola_before_monospace():
    js = _TERM_FONT_PREF_SHIM
    assert "'Symbola Fallback'" in js
    # Must sit ahead of the final bare `monospace` in the fam chain — a family
    # listed but never reached (e.g. only in the preload list) leaves the
    # browser falling through to system monospace exactly as before the fix.
    fam_marker = "var fam="
    fam_start = js.index(fam_marker)
    fam_end = js.index(";", fam_start)
    fam_expr = js[fam_start:fam_end]
    assert "Symbola Fallback" in fam_expr
    assert fam_expr.index("Symbola Fallback") < fam_expr.index("monospace")


def test_no_regression_guard_against_symbola_missing_from_the_fallback_chain():
    """Prove the previous test isn't decoration: strip the Symbola tier back out
    of the `fam` chain (simulating a revert to the pre-CMX-159 stack) and
    confirm the guard actually goes RED."""
    js = _TERM_FONT_PREF_SHIM
    corrupted = js.replace(
        "var fam=\"'\"+lat+\"','Symbols Nerd Font','Symbola Fallback','\"+heb+\"',monospace\";",
        "var fam=\"'\"+lat+\"','Symbols Nerd Font','\"+heb+\"',monospace\";",
    )
    assert corrupted != js, "corruption did not apply — fam expression text drifted"

    fam_start = corrupted.index("var fam=")
    fam_end = corrupted.index(";", fam_start)
    assert "Symbola Fallback" not in corrupted[fam_start:fam_end], (
        "corrupting the fam chain should drop Symbola Fallback — "
        "meaning the real guard doesn't actually test this"
    )
