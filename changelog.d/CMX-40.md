### Added

- **A judge window shows its detached mutation battery's live progress instead of
  "idle".** While `chela judge run --detach` runs, the judge pane's state pill reads
  `⚖️ testing · 3/6 · 15m` (its status dot reads working), and `chela peek` on the window adds a
  `judge:` line with the KILLED/SURVIVED/INVALID tally and the experiment running now (a held-out one
  is never named). A run whose process died before a verdict reads `⚖️ judge run died — no
  verdict`, never idle. The badge disappears when the run finishes. (CMX-40)
