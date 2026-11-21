### Fixed

- **The nightly sweep keeps up with the merge rate.** It owed every mutation it did not reach,
  so on busy days the backlog grew every night (620 owed, then 1,007) and the oldest PRs were
  never measured. A night now samples at most `sweep.per_pr_sample` mutations per merged PR
  (default 20), spread across mutation kinds. Each PR gets its first sample before any PR gets
  its second. The total is capped at what the budget fits at the measured median time per
  mutation, with at least one per PR. The unsampled rest is dropped and counted, not owed, and
  an older owed backlog is trimmed to the same per-PR share. (CMX-455)
- **No more empty starts.** A sweep started by the daemon no longer starts with less budget
  left than one mutation needs: the measured median baseline plus one mutation, or 5 minutes
  before anything has been measured. That run is recorded as `budget_spent`, ends the day,
  and sends no notice. (CMX-455)
- **The nightly notice is short.** It now says "N dropped (line gone)" as a count, with each
  line written to the log, and stays under 400 characters. A CANNOT VERIFY notice keeps its
  full reason. (CMX-455)
