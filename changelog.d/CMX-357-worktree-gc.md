### Added

- **A daily worktree GC.** A worktree whose run closed without removing it, or whose run row
  was pruned or requeued, used to stay on disk for good (26 worktrees, 1.9 GB, 21 of them
  orphaned). The daemon now runs `chela ops worktrees --gc` once a day. It removes each
  worktree under a workflow's `workspace.root` that no run owns, or whose run has been `done`
  or `closed` for at least 3 days, and keeps the branch. A worktree with uncommitted changes is
  first saved to `$CHELA_DIR/worktree-salvage/<name>-<date>.tar.gz` (the diff plus its untracked
  files, without venvs, `node_modules` or caches). A worktree with a live run or a process
  running in it is never touched, and neither is one the pass cannot read. `chela ops
  worktrees` reports the count and size of orphaned worktrees without removing anything.
  (CMX-357)
