### Fixed

- **`chela ops scan` no longer reports a live suite's test PM2 daemon as leaked.** It
  decided liveness by matching `PM2_HOME` in other processes' `/proc` environ, which an
  xdist worker never shows (conftest sets it after start-up), so a running suite's daemon
  was flagged, killed, and the suite hung to its timeout. A `/tmp/chela-tests-pm2-*` God
  Daemon is now spared while any suite of its run is live: a process in its
  `chela-suite-*` scope, one of the run's worktree in such a scope, or a test run of that
  worktree. Once no suite is live it is reported only on its second scan in a row, and the
  finding says "do not kill while a suite runs". (CMX-353)
