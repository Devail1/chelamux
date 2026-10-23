#!/usr/bin/env python3
"""Write the web terminal's woff2 fonts (CMX-322; see ``chela/dashboard/term_fonts.py``).

Usage: ``uv run --extra dev python scripts/build_term_fonts.py [--force] [<upstream.ttf> ...]``

* Symbols Nerd Font: cut ``SymbolsNerdFontMono-Regular.ttf`` into the ``NERD_SUBSETS``
  blocks, one woff2 each.
* Every other face: each ``<name>.ttf`` given on the command line, or found next to the
  woff2 in ``static/fonts/``, is converted to ``<name>.woff2``, glyphs unchanged. woff2 is a
  lossless container, so the woff2 that is committed is the font's source from then on: to
  update a face, pass the new upstream TTF here under the same name.

Only a woff2 that is missing, or whose TTF is named on the command line, is written:
woff2 output is not byte-stable across fontTools/brotli versions, and new bytes are a new
content-hashed URL, which costs every viewer a re-download. ``--force`` rewrites them all.

Needs fontTools with brotli (``fonttools[woff]``, in the ``dev`` extra).
"""
from __future__ import annotations

import sys
from pathlib import Path

from fontTools import subset
from fontTools.ttLib import TTFont

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from chela.dashboard.term_fonts import FONTS_DIR, NERD_SOURCE, NERD_SUBSETS, TERM_FONTS  # noqa: E402


def _woff2(src: Path, dest: Path, unicodes: str | None = None) -> None:
    font = TTFont(str(src))
    if unicodes is not None:
        opts = subset.Options()
        opts.layout_features = ["*"]
        opts.notdef_outline = True
        sub = subset.Subsetter(opts)
        sub.populate(unicodes=subset.parse_unicodes(unicodes))
        sub.subset(font)
    font.flavor = "woff2"
    font.save(str(dest))
    print(f"{dest.name}: {src.stat().st_size} -> {dest.stat().st_size} bytes")


def main(argv: list[str]) -> int:
    force = "--force" in argv
    given = {Path(a).stem: Path(a) for a in argv if a != "--force"}
    for file, ranges in NERD_SUBSETS:
        if force or not (FONTS_DIR / file).is_file():
            _woff2(FONTS_DIR / NERD_SOURCE, FONTS_DIR / file, ranges.replace("U+", ""))
    for font in TERM_FONTS:
        if font.file in {f for f, _ in NERD_SUBSETS}:
            continue
        stem = Path(font.file).stem
        src = given.get(stem) or FONTS_DIR / f"{stem}.ttf"
        if src.is_file() and (force or stem in given or not (FONTS_DIR / font.file).is_file()):
            _woff2(src, FONTS_DIR / font.file)
        elif not (FONTS_DIR / font.file).is_file():
            print(f"{font.file}: no woff2 and no {stem}.ttf to build it from", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
