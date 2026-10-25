### Changed

- **`chela task-finished` no longer re-runs its whole battery when the base moved during it.**
  When `origin/<base>` moved while the self-check and sweep ran and merges cleanly, the hand-in
  merges it in, runs only the tests that observe the files the new base commits touched, and
  re-runs the self-check experiments only on PR files those commits also touched. A red test,
  a surviving experiment or a git error still refuses. The judge is told the base was merged
  mid-check. (CMX-334)
