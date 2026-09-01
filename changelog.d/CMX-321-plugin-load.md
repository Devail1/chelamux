### Fixed

- **`chela doctor`, `chela update` and `chela plugin` now catch a vanished plugin
  marketplace as a hard load failure, not a manifest-drift warning.** `claude plugin list`
  can report `✘ failed to load — Marketplace <x> not found` while the installed manifest is
  byte-identical to what chela renders — Claude Code resolves a plugin through its
  marketplace at load time, and a gone marketplace is a load failure no manifest comparison
  can ever see. All three commands now read Claude Code's own `known_marketplaces.json`,
  confirm (never guess) when an installed copy's marketplace has vanished from it, and
  report that ahead of (and instead of) any staleness comparison, distinctly worded from
  "STALE INSTALL" since only `claude plugin marketplace add` — not `chela update` — fixes
  it. (CMX-321, #409)
