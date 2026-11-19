### Changed

- **The fallback nightly sweep keeps the suite slot until it has measured a floor.** On a day
  the queue is never idle, the fallback used to yield to every waiting agent after each
  mutation, so it measured almost nothing (11-19: 1 of 620). It now keeps the slot until it has
  measured `sweep.fallback_floor_mutations` (default 10) or held it
  `sweep.fallback_floor_seconds` (default 900) past its first mutation, whichever comes first,
  and then yields as before. A dispatch pause or blocked hours still stop it at once. The notice
  names the agents that waited.
- **The nightly notice tracks the owed backlog day over day.** It says how the owed count moved
  since the day before, and says so when it has grown 3 days running.
