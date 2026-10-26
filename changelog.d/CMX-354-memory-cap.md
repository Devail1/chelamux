### Added

- **An agent's own pytest run is memory-capped per test process.** A suite started by
  `chela task-finished` or the judge runs in a memory-capped `chela-suite-*` scope; an
  agent's own `pytest` did not, and one runaway xdist worker reached 11 GB and got other
  agents' suites reaped. `tests/conftest.py` now sets a soft `RLIMIT_AS` of 3 GiB on each
  process that runs tests, unless a `chela-suite-*` or `memcap` cgroup already caps it, so
  a runaway test fails with `MemoryError` in its own worker. Subprocesses (node, vitest,
  Chromium) are exempt. `CHELA_TEST_MEMORY_CAP=6G` raises it; `0` turns it off. The
  standing rules now say never to run a full suite yourself. (CMX-354)
