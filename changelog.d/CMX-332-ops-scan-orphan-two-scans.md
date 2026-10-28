### Fixed

- `chela ops scan` no longer reports a short-lived test child that was re-parented to init
  for a moment (four false "orphaned (ppid 1)" notices between 2026-10-24 and 10-27, each
  process gone a minute later). An orphan is now a finding only when the same process (pid
  and start time) is still alive with ppid 1 on the next scan. An orphan with no named owner
  is also never one while it shares a `chela-suite-*` scope with a live process of that
  suite and is within the suite timeout. A real leak, like a test fixture left running for
  two days, is still reported once. (CMX-332)
