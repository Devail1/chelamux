### Fixed

- **A watchdog write no longer locks out a later run's hand-in in the same tick.** The
  dispatcher's reconcile loop left several watchdog writes uncommitted (a parked run's idle
  clock, a window-gone or idle failure, a nudge stamp), so the tick held the database write
  lock while a later run's `chela task-finished` request was applied on its own connection,
  which then failed with "database is locked" and stalled the queue. Each of those writes
  now commits before the loop moves on, with a two-run regression test per write. (CMX-359)
