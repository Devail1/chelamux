### Changed

- **README cut from 811 lines to ~150** — a front door, not a manual: what chela is, install,
  a five-step quickstart, and a map into the docs site. Everything it used to carry in full
  now lives at [chela.pages.dev/docs](https://chela.pages.dev/docs).

### Added

- **The decisions inbox is documented on the site at last.** "The orchestration loop" —
  one of chela's three stated pillars — appeared **nowhere** on chela.pages.dev: not in
  `docs.html`, not on the landing page, which pitched schedule + dispatch + wall only. It is
  now a first-class docs section (`#orchestration`) and a fourth feature card on the landing
  page, alongside new sections for **agent rooms** (`#rooms`), **agent autonomy and the
  resource-isolation gap** (`#autonomy`), and **how it works** (`#internals`, including the
  HTTP API and why chela does not depend on a structured agent protocol).
- `docs/README.md` — an index separating the user-facing references from the internal design
  records, so the directory stops reading as one undifferentiated pile.

### Fixed

- **The landing site said "MIT licensed" in both footers.** chela has been **AGPL v3** since
  after 0.3.0 (`LICENSE`, and the README badge). The public site had been stating the wrong
  licence to every visitor.
