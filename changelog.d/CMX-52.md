### Added

- **`chela task-finished --handoff "<note>"` leaves a note for the reviewer.** The agent's
  note (what changed, what to look at first, what it assumed) is stored on the run
  (`runs.handoff`) and shown, sanitised, in the orchestrator's inbox notice and on the
  Dispatch board card. It is informational only and never gates anything. (CMX-52, #653)
