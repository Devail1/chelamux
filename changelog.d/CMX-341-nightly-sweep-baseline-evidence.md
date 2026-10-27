### Fixed

- **A red nightly-sweep baseline now names its failing tests and keeps its evidence.** The
  notice and `nightly.log` used to say only "1 failed, 11024 passed", and the JUnit report
  went with the checkout. A red baseline now lists each failing test id (the first five, plus
  a count) with the first line of its failure, says what re-running them alone showed, and
  keeps the JUnit report and the output tail in
  `$CHELA_DIR/sweep-nightly/<date>/<project>/` (the newest 14 dates are kept). A failing test
  that passes when re-run alone is reported as `baseline flake: <ids>` and is deselected
  from every mutation run, so the battery still runs that night. The isolated re-run now
  also finds failing test ids in the JUnit report when the output tail no longer holds
  pytest's `FAILED` lines. (CMX-341)
