### Added

- **`chela slots` lists the suite queue, and a long wait is reported.** It shows who holds a
  suite slot and who waits for one, in queue order, with rank and wait time.
  `chela dispatch-runs` shows the rank next to "waiting for a suite slot". An Urgent or High
  task waiting more than 30 minutes (`CHELA_SLOT_WAIT_ALERT_S`), or any waiter past 2 hours
  (`CHELA_SLOT_WAIT_ANY_ALERT_S`), gets one `run_slot_wait` inbox notice naming the holder and
  the queue, at most once an hour. (CMX-494)

### Fixed

- **A standalone `chela judge sweep` or `judge self-check` queues at its issue's priority.**
  Only `chela task-finished` did; the others queued as "no priority", so an Urgent task waited
  4 hours behind a High one. Everything that runs a suite in a dispatched worktree now takes
  its rank from the run's row, judge rounds queue among themselves by their task's priority,
  and raising an issue's priority re-ranks a run that is already waiting. (CMX-494)
