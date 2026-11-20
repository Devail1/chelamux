### Fixed

- **A flaky ticklock test no longer fails on a busy runner.**
  `test_a_tick_that_outlives_the_bound_is_refused_and_holds_nothing` failed on a loaded CI
  runner, but not because of its time bound. Its stand-in daemon rewrote the run-row file in
  place while the test polled that file, so the test could read a half-written mix of the old
  and new rows (`JSONDecodeError: Extra data`). The stand-in now writes the row atomically
  (a temp file, then `os.replace`). The test's assertions are unchanged. (CMX-448)
