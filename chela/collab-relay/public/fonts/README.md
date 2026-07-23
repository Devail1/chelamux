# Bundled glyph-fallback fonts

Served via `@font-face` in `public/index.html` so the shared-terminal viewer
renders Claude Code TUI glyphs on **any** browser, regardless of what's
installed locally — the relay has no server-side rendering step to fall back
on, so the fix has to live in the font stack itself.

These are the same two fallback tiers used by the chelamux dashboard's
`/screenshot` PNG renderer (see `../../dashboard/static/fonts/README.md`),
copied here verbatim because this directory is deployed independently (a
separate Cloudflare Worker's static assets, not shared hosting with the
dashboard).

| Font | File | License | Covers |
|------|------|---------|--------|
| Symbola (subset) | `Symbola-Subset.ttf` | Freeware (`LICENSE-Symbola.txt`) | © George Douros — subset to U+2300-23FF, U+2600-27BF: the tool-use bullet `⏺`, status marks `❌`/`✅`, thinking spinners `✦`/`✷`/`✨` |
| Symbols Nerd Font | `SymbolsNerdFontMono-Regular.ttf` | MIT | Nerd Fonts — https://github.com/ryanoasis/nerd-fonts (PUA icons) |

Neither is the primary monospace face (that's `JetBrains Mono, Menlo, Consolas`
in the viewer's `fontFamily`, left to the OS/browser to resolve) — both sit
after it in the fallback chain purely to catch glyphs the primary face lacks.
