### Added

- **`chela judge sweep` runs on any git repo, with no chela setup.** No `WORKFLOW.md`, run
  record, daemon, tmux or tracker: the suite is `--test-cmd`, else the workflow's
  `judge.test_cmd`, else detected (pytest / `npm test` / `go test` / `cargo test`); `--json`
  prints the result as JSON. A changed file in a language the sweep cannot mutate (anything
  but Python and `.js`/`.mjs`) is reported as UNKNOWN (exit `3`), never as a pass. Run
  standalone, the sweep does not provision the repo's environment. (CMX-97)
