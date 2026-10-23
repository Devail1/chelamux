### Fixed

- **`test_a_detached_judge_survives_the_daemon_being_killed` no longer flakes in CI.** It
  waited for the pid file to exist, but `write_text` creates the file empty before it writes,
  so on a slow runner the kill landed in that gap and the test read `/proc//stat`
  (= `/proc/stat`) and failed with `IndexError`. The pid is now published by rename. The
  other process tests in `tests/test_ticklock.py` now wait on what they can observe instead
  of fixed gaps and tight deadlines: the stand-in daemon ends its tick when the test says so,
  and a "survives the kill" check samples the process for a second instead of looking once.
  (CMX-314)
