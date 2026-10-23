# Bundled terminal fonts — attribution & licenses

Most fonts here are bundled and served (via `@font-face` in `app.py`
`_TERM_FONT_CSS`, declared in `term_fonts.py`) so the web terminal renders
correctly on **any** viewer, regardless of what's installed locally. They power
the **Settings › Terminal font** picker (English/Latin face × Hebrew face ×
size). Only the *selected* faces are actually downloaded by the browser.

**Formats (CMX-322).** The web terminal loads only **woff2**, at content-hashed
URLs served `immutable`. woff2 is a lossless container of the same font, so the
licences below apply to it unchanged. `scripts/build_term_fonts.py` writes every
woff2 (converting a TTF passed to it, and cutting the Symbols Nerd Font blocks).
Four TTFs stay because non-browser code reads them, never because the terminal
does: `JetBrainsMono.ttf`, `MiriamMonoCLM-Book.ttf`, `Symbola-Subset.ttf` and
`SymbolsNerdFontMono-Regular.ttf` (the Telegram `/screenshot` renderer, and
`chela doctor`'s glyph-coverage check for Symbola).

Each font is an **independent, separately-licensed work** merely aggregated into
this repository and served verbatim. Bundling them does **not** place the
chelamux source (AGPLv3) under their licenses. All are permissive **OFL-1.1** except
Miriam Mono CLM (**GPL-2**), which is the only freely-licensed *monospace* Hebrew
font — see its note below.

## Dashboard chrome (not the terminal picker)

`Geist-Variable.woff2` and the Heebo face below it are declared via their own
`@font-face` rules directly in `style.css` (`--ui-font`), not through `app.py`'s
`_TERM_FONT_CSS` — that system only injects into the ttyd iframe. `--ui-font`
is the sans face for the sidebar, pane headers/footers, modals and Work
(CMX-377's "calm desktop app" restyle); `--font` (the picker above) stays for
terminals and code.

| Font | Files | License | Copyright / source |
|------|-------|---------|--------------------|
| Geist | `Geist-Variable.woff2` | OFL-1.1 (`OFL-Geist.txt`) | © 2024 The Geist Project Authors — https://github.com/vercel/geist-font |
| Heebo | `Heebo.woff2` (already vendored above, re-declared for the dashboard page) | OFL-1.1 (`OFL-Heebo.txt`) | © The Heebo Project Authors (Oded Ezer) |

## Icons

| Font | Files | License | Copyright / source |
|------|-------|---------|--------------------|
| Symbols Nerd Font | `SymbolsNerdFont-Powerline.woff2`, `-FileIcons.woff2`, `-FontAwesome.woff2`, `-UI.woff2` (web terminal); `SymbolsNerdFontMono-Regular.ttf` (the full font: `/screenshot`, and the blocks' source) | MIT | Nerd Fonts — https://github.com/ryanoasis/nerd-fonts |

The web terminal's Nerd Font is cut into four blocks (`term_fonts.py`
`NERD_SUBSETS`), each an `@font-face` with its own `unicode-range`, so a pane
downloads a block only when one of its glyphs is on screen: Powerline + Powerline
Extra + Pomicons + IEC power (a few kB), Seti-UI + Devicons, Font Awesome + its
extension + Font Logos, and Codicons + Octicons + Weather. **Material Design Icons
(U+F0001-F1AF0) are left out**: 6,896 of the font's 10,397 glyphs, used by none of
the TUIs the wall runs.

## Coverage fallback (not in the web terminal font picker)

Served via `@font-face` (so xterm.js's per-glyph fallback can reach it) but not
a selectable Settings option — it exists purely to fill the gap neither the
picker's Latin nor Hebrew faces nor Symbols Nerd Font cover. CMX-159: without
it, the web terminal's TUI glyphs (`⏺` `❌` `✅` `✦` `✷` `✨` `⚙`) fell through
the whole family stack to the browser's system `monospace` and came out as
tofu (`▢`) — an xterm texture-atlas rebuild (CMX-155) can't rasterize a glyph
no stacked font actually contains. Same subset `chela/telegram/screenshot.py`'s
`/screenshot` PNG renderer uses as a glyph-coverage fallback (CMX-156).

| Font | Files | License | Copyright / source |
|------|-------|---------|--------------------|
| Symbola (subset) | `Symbola-Subset.woff2` (web terminal), `Symbola-Subset.ttf` (`/screenshot`, `chela doctor`) | Freeware (`LICENSE-Symbola.txt`) | © George Douros — subset to U+2300-23FF, U+2600-27BF (the TUI spinner/tool-marker glyphs JetBrains Mono and Symbols Nerd Font both lack: `⏺` `❌` `✅` `✦` `✷` `✨` `⚙`) |

## English / monospace (Latin)

| Font | Files | License | Copyright / source |
|------|-------|---------|--------------------|
| JetBrains Mono | `JetBrainsMono.woff2` (+ `.ttf` for `/screenshot`) | OFL-1.1 (`OFL-JetBrainsMono.txt`) | © 2020 The JetBrains Mono Project Authors |
| Fira Code | `FiraCode.woff2` | OFL-1.1 (`OFL-FiraCode.txt`) | © The Fira Code Project Authors |
| IBM Plex Mono | `IBMPlexMono-Regular.woff2`, `-Bold.woff2` | OFL-1.1 (`OFL-IBMPlexMono.txt`) | © IBM Corp. (Mike Abbink, Bold Monday) |
| Source Code Pro | `SourceCodePro.woff2` | OFL-1.1 (`OFL-SourceCodePro.txt`) | © Adobe (Paul D. Hunt) |
| Cascadia Code | `CascadiaCode.woff2` | OFL-1.1 (`OFL-CascadiaCode.txt`) | © Microsoft Corporation |

## Hebrew

| Font | Files | License | Copyright / source |
|------|-------|---------|--------------------|
| Miriam Mono CLM | `MiriamMonoCLM-Book.woff2`, `-Bold.woff2` (+ `-Book.ttf` for `/screenshot`) | **GPL-2** (`GPL-2.txt`) | Culmus — © Maxim Iorsh, Yoram Gnat, (URW)++ |
| Noto Sans Hebrew | `NotoSansHebrew.woff2` | OFL-1.1 (`OFL-NotoSansHebrew.txt`) | © The Noto Project Authors |
| Heebo | `Heebo.woff2` | OFL-1.1 (`OFL-Heebo.txt`) | © The Heebo Project Authors (Oded Ezer) |
| Assistant | `Assistant.woff2` | OFL-1.1 (`OFL-Assistant.txt`) | © The Assistant Project Authors |
| Rubik | `Rubik.woff2` | OFL-1.1 (`OFL-Rubik.txt`) | © The Rubik Project Authors |
| Frank Ruhl Libre | `FrankRuhlLibre.woff2` | OFL-1.1 (`OFL-FrankRuhlLibre.txt`) | © The Frank Ruhl Libre Project Authors |
| David Libre | `DavidLibre-Regular.woff2`, `-Bold.woff2` | OFL-1.1 (`OFL-DavidLibre.txt`) | © The David Libre Project Authors |

## Note on Miriam Mono CLM (GPL-2)

Miriam Mono CLM is under the GNU GPL v2 **without** the Culmus font exception.
It is included because it is the only freely-licensed *monospace* Hebrew font, so
it is the one Hebrew option that aligns perfectly on the terminal's fixed grid.
It remains a separate GPL-2 work; the chelamux codebase stays MIT. To ship a
fully OFL-only build, drop `MiriamMonoCLM-*` + `GPL-2.txt`, remove the
`miriam` option from the picker (`term_fonts.py` `TERM_FONTS`, `app.py` `_TERM_FONT_PREF_SHIM`
and `nav.js` `TERM_FONT_LABELS`), and pick an OFL Hebrew font as the default.
