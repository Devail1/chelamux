### Changed

- **The public landing page, docs page and README now name the project chelamux, at
  chelamux.com.** Page titles, Open Graph and Twitter meta, canonical links, the nav
  wordmark and the hero copy say chelamux; the command-line tool is still `chela`, and
  absolute `chela.pages.dev` links now point to `https://chelamux.com`. The package name,
  CLI entry point, `~/.chela` and `CHELA_*` settings are unchanged. (CMX-361)
- **The landing site serves a `robots.txt` and a `sitemap.xml`** for chelamux.com, so
  `/robots.txt` no longer falls back to the home page. (CMX-361)
- **The GitHub Action's display name is now "chelamux judge"** (was "chela judge sweep"). The
  `uses: Devail1/chelamux@v0` reference, inputs and outputs are unchanged. (CMX-361)
