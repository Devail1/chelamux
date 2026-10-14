### Added

- **The judge's mutation sweep runs on any repo, with no chela setup.** `chela judge sweep
  --base <ref>` needs no `WORKFLOW.md`, run record, daemon, tmux or tracker: the suite is
  `--test-cmd`, else the workflow's `judge.test_cmd`, else detected (pytest / `npm test` /
  `go test` / `cargo test`), and the base defaults to `origin/HEAD`. `--json` prints the
  result as JSON. Exit codes: `0` clean, `1` survivors, `2` cannot verify, `3` unknown — a
  changed file in a language the sweep cannot mutate (anything but Python and `.js`/`.mjs`)
  is reported by name and never counts as a pass. Run standalone, the sweep no longer
  provisions the repo's environment (`uv sync` / `pnpm install`). (CMX-97)
- **A GitHub Action for it** (`action.yml`, `uses: Devail1/chelamux@<ref>`): on a pull
  request it runs the sweep against the base, keeps one PR comment up to date with the
  survivors, writes the step summary and `verdict` / `survivors` outputs, and fails the job.
  `GITHUB_TOKEN` is the only credential it uses. (CMX-97)

### Fixed

- **A killed sweep no longer leaves a mutation in the tree.** SIGINT now restores the file
  like SIGTERM/SIGHUP already did, and while mutations run a guardian process in its own
  session waits for the sweep to exit and restores the file from the recovery marker if the
  sweep was `kill -9`ed. Before, a SIGKILL left the mutation on disk until the next sweep.
  (CMX-97)
