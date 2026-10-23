### Added

- **`chela park <run> --reason "…" [--until <time|run:ID>]` and `chela unpark <run>`.** An
  agent the orchestrator holds on purpose no longer draws "looks STUCK" notices: the stuck
  detector skips a parked run and sends ONE notice if the park outlives its `--until` or
  `CHELA_PARK_MAX_S` (default 6h). A run waiting for a suite slot counts as parked
  automatically ("waiting for a suite slot (holder: …)"). Parks show in `chela dispatch-runs`
  and as a badge on the Work board's card, with the reason and since when. An agent cannot
  park itself: the command refuses inside the run's own worktree and from any Claude Code
  session that is not the orchestrator. (CMX-318)
