"""CMX-158: the collab-relay shared-terminal viewer (chela/collab-relay/public/
index.html) is a THIRD render surface for Claude Code TUI glyphs, distinct from
the dashboard's xterm atlas (CMX-155) and the /screenshot PNG (CMX-156). It
sets xterm.js's `fontFamily` to `JetBrains Mono, Menlo, Consolas, monospace`
but bundles no fonts at all, so viewers without JetBrains Mono installed fell
back to whatever the OS had — usually missing the tool-marker bullet `⏺`,
`❌`/`✅`, and thinking-spinner glyphs, which came out as tofu (`▢`).

The fix reuses the dashboard's existing fallback tier (Symbola subset +
Symbols Nerd Font) via `@font-face`, served from the relay's own static
assets (a separately-deployed Cloudflare Worker, hence its own copy rather
than a cross-origin reference), and appends both faces to the terminal's
`fontFamily` stack.
"""
from __future__ import annotations

from pathlib import Path

import pytest

fontTools_ttLib = pytest.importorskip("fontTools.ttLib")
TTFont = fontTools_ttLib.TTFont

_ROOT = Path(__file__).resolve().parent.parent
_PUBLIC = _ROOT / "chela" / "collab-relay" / "public"
_INDEX_HTML = _PUBLIC / "index.html"

# The exact glyphs Anthony reported tofu-ing: the tool-use marker, status
# marks, and thinking spinners — all outside JetBrains Mono's coverage.
_TUI_GLYPHS = "⏺❌✅✦✷✨"


def _html() -> str:
    return _INDEX_HTML.read_text()


def test_fallback_font_files_are_actually_bundled():
    assert (_PUBLIC / "fonts" / "Symbola-Subset.ttf").is_file()
    assert (_PUBLIC / "fonts" / "SymbolsNerdFontMono-Regular.ttf").is_file()


def test_bundled_symbola_subset_covers_the_reported_tui_glyphs():
    # Guards the fixture assumption: if this ever stops covering the glyphs
    # the viewer needs, the font-face wiring below is pointless.
    font = TTFont(str(_PUBLIC / "fonts" / "Symbola-Subset.ttf"))
    cmap = font.getBestCmap()
    missing = [ch for ch in _TUI_GLYPHS if ord(ch) not in cmap]
    assert not missing, f"Symbola-Subset.ttf is missing glyphs: {missing!r}"


def test_index_html_declares_font_face_for_both_fallback_fonts():
    html = _html()
    assert "/fonts/Symbola-Subset.ttf" in html
    assert "/fonts/SymbolsNerdFontMono-Regular.ttf" in html
    assert "@font-face" in html


def test_terminal_font_family_includes_the_fallback_chain():
    html = _html()
    assert "fontFamily:" in html
    # Must sit in the actual xterm.js Terminal() constructor options, not just
    # appear somewhere unrelated in the file.
    start = html.index("new Terminal({")
    end = html.index("});", start)
    ctor = html[start:end]
    assert "Symbola" in ctor, "Terminal() fontFamily is missing the Symbola fallback"
    assert "Symbols Nerd Font" in ctor, "Terminal() fontFamily is missing the Nerd Font fallback"


def test_no_regression_guard_against_the_diagnosed_bug():
    """Directly reproduces CMX-158: strip the fallback faces from the
    Terminal() fontFamily (simulating a revert to the old JetBrains-Mono-only
    stack) and confirm the guard above actually goes RED — proving it isn't
    decoration."""
    html = _html()
    start = html.index("new Terminal({")
    end = html.index("});", start)
    ctor = html[start:end]
    assert "Symbola" in ctor and "Symbols Nerd Font" in ctor

    corrupted = html.replace(
        "fontFamily: \"JetBrains Mono, Menlo, Consolas, 'Symbola', 'Symbols Nerd Font', monospace\"",
        "fontFamily: 'JetBrains Mono, Menlo, Consolas, monospace'",
    )
    assert corrupted != html, "corruption did not apply — fontFamily text drifted"

    c_start = corrupted.index("new Terminal({")
    c_end = corrupted.index("});", c_start)
    c_ctor = corrupted[c_start:c_end]
    assert "Symbola" not in c_ctor and "Symbols Nerd Font" not in c_ctor, (
        "corrupting the fontFamily stack should reproduce the missing-fallback "
        "bug — the guard doesn't actually test what it claims"
    )
