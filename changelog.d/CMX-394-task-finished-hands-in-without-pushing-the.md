### Fixed

- **`task-finished` no longer hands in a PR that sits on an older commit.** Its last step
  compares the worktree's HEAD with the branch on origin (read live with `git ls-remote`).
  If they differ and the PR exists, it requests the push itself, and the daemon pushes before
  handing the run in. If no PR exists yet, it refuses with
  `HEAD <sha> not pushed (origin has <sha>): run request-push`. An unreadable HEAD or remote
  refuses too. After every push the daemon reads origin back, so a push that exits 0 but
  leaves origin off the worktree's HEAD is now an error (retried, then escalated) instead of
  a silent "accepted". When the conflict check sends a PR back while the worktree's HEAD is
  ahead of the pushed head it graded, the rework brief and an inbox notice
  (`run_head_unpushed`) say so. (CMX-394)
