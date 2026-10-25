### Fixed

- **A run whose task leaves the tracker while its PR is still open is no longer marked done.**
  Linear's GitHub integration closes an issue when any merged PR names it, so a task could
  leave the tracker while its own PR sat open; the dispatcher read that as done, killed the
  window, and nothing could bring the run back. Now the run keeps its state, its card shows
  "issue left the tracker's ready states; PR still open", and it finishes only when the PR
  merges or closes. `chela reopen` also accepts a `done` run on a linear tracker whose PR is
  still open: there is no struck line to check there, so the PR state decides. (CMX-308)
