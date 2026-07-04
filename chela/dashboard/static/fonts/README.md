# Bundled terminal fonts — attribution & licenses

These fonts are bundled and served (via `@font-face` in `app.py`
`_TERM_FONT_CSS`) so the web terminal renders correctly on **any** viewer,
regardless of what's installed locally. They power the **Settings › Terminal
font** picker (English/Latin face × Hebrew face × size). Only the *selected*
faces are actually downloaded by the browser.

Each font is an **independent, separately-licensed work** merely aggregated into
this repository and served verbatim. Bundling them does **not** place the
chelamux source (MIT) under their licenses. All are permissive **OFL-1.1** except
Miriam Mono CLM (**GPL-2**), which is the only freely-licensed *monospace* Hebrew
font — see its note below.

## Icons

| Font | Files | License | Copyright / source |
|------|-------|---------|--------------------|
| Symbols Nerd Font | `SymbolsNerdFontMono-Regular.ttf` | MIT | Nerd Fonts — https://github.com/ryanoasis/nerd-fonts |

## English / monospace (Latin)

| Font | Files | License | Copyright / source |
|------|-------|---------|--------------------|
| JetBrains Mono | `JetBrainsMono.ttf` | OFL-1.1 (`OFL-JetBrainsMono.txt`) | © 2020 The JetBrains Mono Project Authors |
| Fira Code | `FiraCode.ttf` | OFL-1.1 (`OFL-FiraCode.txt`) | © The Fira Code Project Authors |
| IBM Plex Mono | `IBMPlexMono-Regular.ttf`, `-Bold.ttf` | OFL-1.1 (`OFL-IBMPlexMono.txt`) | © IBM Corp. (Mike Abbink, Bold Monday) |
| Source Code Pro | `SourceCodePro.ttf` | OFL-1.1 (`OFL-SourceCodePro.txt`) | © Adobe (Paul D. Hunt) |
| Cascadia Code | `CascadiaCode.ttf` | OFL-1.1 (`OFL-CascadiaCode.txt`) | © Microsoft Corporation |

## Hebrew

| Font | Files | License | Copyright / source |
|------|-------|---------|--------------------|
| Miriam Mono CLM | `MiriamMonoCLM-Book.ttf`, `-Bold.ttf` | **GPL-2** (`GPL-2.txt`) | Culmus — © Maxim Iorsh, Yoram Gnat, (URW)++ |
| Noto Sans Hebrew | `NotoSansHebrew.ttf` | OFL-1.1 (`OFL-NotoSansHebrew.txt`) | © The Noto Project Authors |
| Heebo | `Heebo.ttf` | OFL-1.1 (`OFL-Heebo.txt`) | © The Heebo Project Authors (Oded Ezer) |
| Assistant | `Assistant.ttf` | OFL-1.1 (`OFL-Assistant.txt`) | © The Assistant Project Authors |
| Rubik | `Rubik.ttf` | OFL-1.1 (`OFL-Rubik.txt`) | © The Rubik Project Authors |
| Frank Ruhl Libre | `FrankRuhlLibre.ttf` | OFL-1.1 (`OFL-FrankRuhlLibre.txt`) | © The Frank Ruhl Libre Project Authors |
| David Libre | `DavidLibre-Regular.ttf`, `-Bold.ttf` | OFL-1.1 (`OFL-DavidLibre.txt`) | © The David Libre Project Authors |

## Note on Miriam Mono CLM (GPL-2)

Miriam Mono CLM is under the GNU GPL v2 **without** the Culmus font exception.
It is included because it is the only freely-licensed *monospace* Hebrew font, so
it is the one Hebrew option that aligns perfectly on the terminal's fixed grid.
It remains a separate GPL-2 work; the chelamux codebase stays MIT. To ship a
fully OFL-only build, drop `MiriamMonoCLM-*.ttf` + `GPL-2.txt`, remove the
`miriam` option from the picker (`app.py` `_TERM_FONTS` / `_TERM_FONT_PREF_SHIM`
and `nav.js` `TERM_FONT_LABELS`), and pick an OFL Hebrew font as the default.
