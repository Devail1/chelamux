### Changed

- **The nightly sweep now starts when the queue is idle, not at a fixed hour.** With
  `sweep.mode: idle` (the new default) the daemon starts it once no suite slot is held or
  waited for, memory is above one suite's peak, and that has lasted `sweep.quiet_seconds`
  (default 10 min). While it runs, it checks between mutations whether an agent is waiting
  for the suite slot. If one is, the sweep finishes the current mutation, hands the slot over
  and ends as "yielded". What it didn't reach is owed to the next run, which may come the same
  day, up to `sweep.budget_seconds` in total. If the box was never idle by `nightly_at` +
  `sweep.fallback_after_hours` (04:00 by default), it runs once on a 30-min fallback budget,
  still yielding. The inbox notice says when and why it started, who it yielded to and how
  many minutes it used. `sweep.mode: fixed` keeps the old clock. (CMX-400)
