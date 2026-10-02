### Added

- **Work board: a Linear-backed card links to its Linear issue.** Every card for a Linear
  task — queued, running, in review or done — shows its identifier as `CMX-12 ↗`, opening
  the issue in a new tab without triggering the card's own click. The href is exactly the
  `url` the Linear adapter fetched (recorded on the run at claim, in a new
  `runs.tracker_url` column), never built from a workspace slug. Cards from markdown and
  GitHub-issues workflows are unchanged. (CMX-5)
