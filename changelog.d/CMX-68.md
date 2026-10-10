### Added

- **A closed run that silently blocks a READY task is now flagged, with a one-click way
  out.** `chela doctor` reports `dispatch.closed_run_stalls`: a task in its tracker's ready
  state whose run is `closed` (no requeue pending) is never claimed again, so doctor names it,
  says "closed run blocks this task: requeue or refile", and gives the `--requeue` command. An
  unreadable tracker reads CANNOT VERIFY, never "no stalls". On the Work board, the task's
  card carries the same flag, computed over all of the workflow's runs and not only the 10
  most recent. Its **Requeue** button (or **Close & requeue** on a run still in flight) runs
  `chela close --requeue` through the new `POST /api/dispatcher/runs/<id>/close`. (CMX-68)
