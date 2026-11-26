### Fixed

- **Tests now pin the daemon's session-name restore step and the dispatcher's restore record.**
  The nightly mutation sweep showed that nothing noticed if the daemon stopped logging which
  window names and queued titles it put back after a reboot. That step is now a small function,
  `restore_session_names`, with tests for its log line, its silence when nothing was restored,
  its swallowed failure and its place in the tick. A new test also checks that a launch which
  restored a leftover mutation records one `sweep_mutation_restored` event. (CMX-501)
