### Added

- **`chela task-finished` and the nightly sweep are now timed per phase, the way a judge
  round is.** Each hand-in attempt prints one `⏱️ hand-in` line at its end and appends a
  record to `$CHELA_DIR/hand-in-timings/<task>.jsonl`. The record has the time and slot wait
  of lint, suite, self-check (its baseline, flake re-run and experiments), sweep (its
  baseline, flake re-run and mutations) and re-merge, plus the hand-in's total slot wait.
  Refused attempts are recorded too, because they held the suite slot as well. Each nightly
  sweep appends the same kind of record to `$CHELA_DIR/sweep-nightly/timings.jsonl`.
  `chela judge timings --hand-in` and `--nightly` print the median, p90 and max table for
  these records. For hand-ins the table also has `task_total`, the sum of all of one task's
  attempts. This only measures: nothing a hand-in, sweep or judge runs or decides has
  changed. (CMX-397)
