### Fixed

- **A sweep, self-check or hand-in now holds its suite slot from start to finish.** With
  `suite_slots: 1`, three agents' batteries still ran interleaved: each took the slot for
  one mutation's suite run and handed it to the next battery, so all three were mid-run when
  the low-memory reaper killed them. `chela judge sweep`, `chela judge self-check`, a judge
  round and all of `chela task-finished`'s checks now take ONE slot for the whole battery,
  and each suite run inside re-enters it. A second agent waits for the first battery to
  finish and names the holder while it waits. Batteries killed mid-run are named in ONE
  inbox notice (`suite_runs_stopped`) once the slot is free and memory is back. (CMX-393)
