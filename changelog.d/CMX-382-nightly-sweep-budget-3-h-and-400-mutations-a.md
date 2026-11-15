### Changed

- **The nightly sweep now gets 3 hours and 400 mutations a night.** chelamux's own
  WORKFLOW.md gains a `sweep:` block (`budget_seconds: 10800`, `max_mutations: 400`) that only
  the nightly battery reads; the per-PR sweep in `chela judge sweep` and `task-finished` keeps
  `judge.sweep_budget_seconds`. The battery starts around 02:00 while the machine is idle, and
  one night's backlog of owed mutations had outgrown the old budget. (CMX-382)
