### Fixed

- **No suite starts inside `sweep.blocked_hours` (default 03:00-04:30).** The suite-slot
  queue used to ignore the window, so agents' hand-ins, self-checks and sweeps ran into the
  host's nightly cron jobs and the low-memory reaper killed them. Now no slot is granted
  inside the window, for any run including the judge's. A waiting run says "blocked hours
  until 04:30", a running one finishes its current experiment and then gives the slot up,
  and the queue resumes in priority order when the window ends.
- **A run killed while it was only waiting for the slot is re-queued once.** When the window
  is over and memory is back, chela asks that run's own agent to run the same command again.
  A second kill goes to the stopped-runs notice, as before. (CMX-479)
