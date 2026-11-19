### Fixed

- **A sweep or self-check killed mid-mutation no longer hands its mutation to the next
  agent.** A reboot kills the kill guardian too, so the mutated file and its
  `.chela-sweep-recovery.json` used to sit in the worktree until a human noticed. Now every
  entry point that works in a worktree restores first: the dispatcher's spawn and rework
  re-spawn (before the agent's window exists), `task-finished`, `request-push`,
  `judge self-check` / `sweep`, and the nightly sweep. A file that is neither the marker's
  original nor its mutation is never overwritten; the refusal names the three-way state at
  that line, and the run goes to `needs_human`. The marker records the kernel boot id, so a
  pid reused after a reboot never counts as a live sweep. On start, the daemon scans the
  worktree roots for markers with dead owners, restores those it can prove, and posts one
  inbox notice (`sweep_recovery_boot`) listing what it restored and what it left. (CMX-398)
- **Nothing pushes a mutation.** Every applied mutation is journalled (as a git blob id) in
  the worktree's own git dir. `request-push` and `task-finished` refuse while a recovery
  marker exists or any file, on disk or committed in HEAD, is byte-for-byte a recorded
  mutation, naming `file:line`; the daemon checks HEAD again before it runs `git push`.
  (CMX-398)
