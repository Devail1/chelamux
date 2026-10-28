### Added

- **Every judge round records where its time went.** Each round in the private judge
  record (`$CHELA_DIR/judge-heldout/<task>.jsonl`) now has a `timings` object: wall-clock
  for setup (worktree checkout and the judge agent, up to `chela judge run`), worktree
  rebuild, provisioning, the baseline suite, each visible and held-out experiment (with
  its selected test count and whether it was confirmed on the full suite), the
  consistency re-runs and the whole round. Every phase also reports how much of it was
  spent waiting for a suite slot, and the round records how many agents and judges were
  running when it started. `chela judge show <run>` prints the breakdown under each
  round; rounds recorded before this change print "no timings recorded".
  `chela judge timings [--since 7d] [--json]` gives the median, p90 and max per phase
  across every task, biggest phase first. Nothing about what the judge runs or decides
  has changed. (CMX-196)
