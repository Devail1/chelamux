# Miriam Mono CLM — license & attribution

`MiriamMonoCLM-Book.ttf` and `MiriamMonoCLM-Bold.ttf` in this directory are part
of the **Culmus** Hebrew font project and are bundled as the Hebrew fallback for
the web terminal — the Latin/Nerd fonts carry no Hebrew, so without them the
browser falls back per-device to a mismatched `monospace` for Hebrew runs. Miriam
Mono is the only freely-licensed *monospace* Hebrew font, so it aligns uniformly
on the terminal's fixed grid where proportional Hebrew fonts do not.

- **Font:** Miriam Mono CLM (Culmus project)
- **Copyright:**
  - 2010 Yoram Gnat (yoramg@shenkar.ac.il)
  - 2002–2024 Maxim Iorsh (iorsh@math.technion.ac.il)
  - 1999 (URW)++ Design & Development — Latin glyphs, digits and punctuation are
    part of the Nimbus Mono L font family
- **Upstream:** https://culmus.sourceforge.io/ · https://sourceforge.net/projects/culmus/
- **License:** GNU General Public License, version 2 (**GPL-2**, *without* the
  Culmus font exception). Full text in [`GPL-2.txt`](./GPL-2.txt).

## Note on this repository's license

chelamux itself is MIT-licensed. These font files are an **independent,
separately-licensed work** merely aggregated into the repository and served
verbatim as a web asset — bundling them does **not** place the chelamux source
under the GPL. The GPL-2 applies only to the Miriam Mono CLM font files
themselves. To drop the copyleft dependency, replace these two `.ttf` files with
an OFL Hebrew font (e.g. Noto Sans Hebrew) and update the `@font-face` `src` in
`chela/dashboard/app.py` (`_TERM_FONT_CSS`) — at the cost of proportional (non-
monospace) Hebrew that clamps less evenly into the terminal grid.
