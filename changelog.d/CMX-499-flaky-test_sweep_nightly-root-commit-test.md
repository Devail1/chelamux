### Fixed

- **The nightly sweep's root-commit test no longer flakes.** git reads a small
  `--since=@N` as a fuzzy date, not epoch seconds: `@0` means now, so a test that passed
  `since=0` missed its own commit whenever the clock ticked over. The tests now pass a real
  timestamp, and `merged_commits` refuses a `since` below 100000000 so it can't recur.
  Production was not affected: the nightly derives `since` from the current time. (CMX-499)
