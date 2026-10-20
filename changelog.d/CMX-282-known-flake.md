### Added

- **Known-flake quarantine.** `tests/flaky.toml` registers flaky tests, each with a tracker
  issue and an expiry date. When a PR's CI is red only on registered tests, the dispatcher
  re-runs the failed jobs up to 2 more times, after CMX-255's one re-run. If it is still red
  only on those tests, the PR is not sent back for rework: the run goes to the judge, records
  `ci_known_flake`, and the inbox notice names the issue. The flaky test still runs and
  reports. The registry is read from the orchestrator's own checkout, so a PR cannot
  quarantine its own red. `chela merge` still needs green CI, so a known-flake PR is judged
  but must be re-run green (or overridden) before it merges. A CI test fails when an entry is
  expired, malformed, or its issue is Done, Canceled or missing. `chela ops scan`'s
  `repeat_flake` rule names a "quarantine candidate" when the same test flakes on 2 or more
  PRs within 48 hours. (CMX-282, #797)
