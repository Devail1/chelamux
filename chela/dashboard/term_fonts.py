"""The web terminal's bundled fonts: what each ttyd page declares, and how it is served (CMX-322).

A pane is its own iframe (``/term/<wid>/``), so whatever a pane's fonts cost, every pane
pays it. Measured 2026-10-23: the icon font was a 2.3 MB uncompressed TTF, served
``no-cache`` (one revalidation round trip per font per pane), and the font shim waited for
all of it before the final font apply. Three rules follow from that:

* **woff2, and the icons split by block.** Every face ships as woff2. Symbols Nerd Font is
  cut into a few blocks (:data:`NERD_SUBSETS`), each its own ``@font-face`` with a
  ``unicode-range``: a browser downloads a block only once a glyph from it is on screen, so
  a Claude Code pane fetches the few-kB Powerline block, or none. Material Design Icons
  (U+F0001-F1AF0, 6,896 glyphs, two thirds of the font) is two blocks of its own, so the
  prompts and file browsers that use `nf-md-*` icons still get them, and nothing else pays.
* **Content-hashed URLs.** :func:`font_url` names a font by a hash of its bytes, and the
  dashboard serves that URL ``immutable``: a pane never revalidates a font.
* **Icons never block.** Every face is ``font-display:swap``; the font shim waits only for
  the Latin and Hebrew faces, and re-renders when an icon block lands later.

The TTFs that stay in ``static/fonts/`` (JetBrains Mono, Miriam Mono CLM Book, the Symbola
subset and the full Symbols Nerd Font) are not served to the terminal: the Telegram
``/screenshot`` renderer (Pillow) and ``chela doctor``'s glyph-coverage fact read them.
``scripts/build_term_fonts.py`` writes every woff2 here from its source.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import NamedTuple

FONTS_DIR = Path(__file__).parent / "static" / "fonts"

# The full icon font the Nerd blocks below are cut from (also /screenshot's icon tier).
NERD_SOURCE = "SymbolsNerdFontMono-Regular.ttf"

# Symbols Nerd Font, one woff2 per block: (file, unicode-range). The ranges are the font's
# own blocks (https://github.com/ryanoasis/nerd-fonts/wiki/Glyph-Sets-and-Code-Points).
NERD_SUBSETS = (
    # Powerline + Powerline Extra, Pomicons, IEC power symbols and the few BMP symbols:
    # what prompts and status lines draw. A few kB.
    ("SymbolsNerdFont-Powerline.woff2",
     "U+23FB-23FE,U+2630,U+2665,U+26A1,U+276C-2771,U+2B58,U+E000-E00A,U+E0A0-E0D7"),
    # Seti-UI + Devicons: file-type icons (file trees, lazygit, eza --icons).
    ("SymbolsNerdFont-FileIcons.woff2", "U+E5FA-E6B7,U+E700-E8EF"),
    # Font Awesome, Font Awesome Extension, Font Logos.
    ("SymbolsNerdFont-FontAwesome.woff2", "U+E200-E2A9,U+ED00-F2FF,U+F300-F381"),
    # Codicons, Octicons, Weather.
    ("SymbolsNerdFont-UI.woff2", "U+E300-E3E3,U+EA60-EC1E,U+F400-F533"),
    # Material Design Icons (`nf-md-*`: starship, eza, yazi), in two halves.
    ("SymbolsNerdFont-MaterialA.woff2", "U+F0001-F0D78"),
    ("SymbolsNerdFont-MaterialB.woff2", "U+F0D79-F1AF0"),
)


class TermFont(NamedTuple):
    family: str
    file: str
    # True: a variable font (one face, weights 100-900). False: one static weight, named by
    # ``weight``. None: an icon/coverage face with no weight axis.
    variable: bool | None
    weight: str | None
    # The CSS unicode-range, or None for every codepoint the font has.
    unicode_range: str | None = None


# Every face the ttyd page declares. The Settings > Terminal font picker chooses one Latin
# face and one Hebrew face (app.py `_TERM_FONT_PREF_SHIM`); an unused @font-face never
# downloads. See static/fonts/README.md for each font's licence.
TERM_FONTS = (
    # Icons, by block (see NERD_SUBSETS).
    *(TermFont("Symbols Nerd Font", f, None, None, r) for f, r in NERD_SUBSETS),
    # Coverage-only fallback (not in the picker): CMX-159. The TUI glyphs (`⏺` `❌` `✅`
    # `✦` `✷` `✨` `⚙`) that neither JetBrains Mono nor Symbols Nerd Font contain. Its range
    # is the subset's own (U+2300-23FF, U+2600-27BF).
    TermFont("Symbola Fallback", "Symbola-Subset.woff2", None, None, "U+2300-23FF,U+2600-27BF"),
    # English / Latin monospace (picker: English face)
    TermFont("JetBrains Mono",  "JetBrainsMono.woff2",       True,  None),
    TermFont("Fira Code",       "FiraCode.woff2",            True,  None),
    TermFont("IBM Plex Mono",   "IBMPlexMono-Regular.woff2", False, "normal"),
    TermFont("IBM Plex Mono",   "IBMPlexMono-Bold.woff2",    False, "bold"),
    TermFont("Source Code Pro", "SourceCodePro.woff2",       True,  None),
    TermFont("Cascadia Code",   "CascadiaCode.woff2",        True,  None),
    # Hebrew (picker: Hebrew face). Miriam is the only monospace one.
    TermFont("Miriam Mono CLM", "MiriamMonoCLM-Book.woff2",  False, "normal"),
    TermFont("Miriam Mono CLM", "MiriamMonoCLM-Bold.woff2",  False, "bold"),
    TermFont("Noto Sans Hebrew", "NotoSansHebrew.woff2",     True,  None),
    TermFont("Heebo",           "Heebo.woff2",               True,  None),
    TermFont("Assistant",       "Assistant.woff2",           True,  None),
    TermFont("Rubik",           "Rubik.woff2",               True,  None),
    TermFont("Frank Ruhl Libre", "FrankRuhlLibre.woff2",     True,  None),
    TermFont("David Libre",     "DavidLibre-Regular.woff2",  False, "normal"),
    TermFont("David Libre",     "DavidLibre-Bold.woff2",     False, "bold"),
)


def font_digests(fonts_dir: Path = FONTS_DIR) -> dict[str, str]:
    """{file: short sha256 of its bytes} for every woff2 in ``fonts_dir``, read once at import:
    a font only changes with a deploy, which restarts the dashboard."""
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()[:12]
            for p in sorted(fonts_dir.glob("*.woff2"))}


FONT_DIGESTS = font_digests()


def font_url(file: str, digests: dict[str, str] = FONT_DIGESTS) -> str:
    """``/static/fonts/v/<digest>/<file>``: the URL changes when the bytes do, so the
    dashboard can serve it ``immutable``. A file with no digest (missing on disk) gets the
    placeholder ``0``, which is never served immutable."""
    return f"/static/fonts/v/{digests.get(file, '0')}/{file}"


def font_face_css(font: TermFont, digests: dict[str, str] = FONT_DIGESTS) -> str:
    """One ``@font-face`` rule. Every face is ``font-display:swap``: text paints at once in
    whatever face is ready, and nothing waits on an icon block."""
    if font.variable is None:
        wgt = ""
    elif font.variable:
        wgt = "font-weight:100 900;"
    else:
        wgt = "font-weight:%s;" % font.weight
    rng = "unicode-range:%s;" % font.unicode_range if font.unicode_range else ""
    return ("@font-face{font-family:'%s';%s%s"
            "src:url('%s') format('woff2');font-display:swap}"
            % (font.family, wgt, rng, font_url(font.file, digests)))
