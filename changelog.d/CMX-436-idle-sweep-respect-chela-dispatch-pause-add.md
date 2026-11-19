### Changed

- **The idle nightly sweep now stays out of a paused queue and the host's busy hours.** It
  never starts while `chela dispatch --pause` holds the queue (until the hold is released or
  its TTL runs out), nor inside `sweep.blocked_hours`: local `"HH:MM-HH:MM"` windows that may
  wrap past midnight, default `["03:00-04:30"]`. A sweep that is already running stops between
  two mutations when either begins, as it does for a waiting agent, and the rest is owed. The
  inbox notice says which: "yielded: dispatch paused" or "yielded: blocked hours 03:00-04:30".
- **The fallback moved to 05:00 and fires only in its own window.** `sweep.fallback_after_hours`
  defaults to 3, so 02:00 + 3 h = 05:00. The fallback fires only within
  `sweep.fallback_window_hours` (default 2, so 05:00–07:00), never later in the day, and a
  fallback time that falls inside a blocked window moves to that window's end.
- **The notice rates throughput only on a real sample.** Below 10 mutations and 10 minutes it
  says "too few to rate" instead of a figure like 22885 mutations/hour.
