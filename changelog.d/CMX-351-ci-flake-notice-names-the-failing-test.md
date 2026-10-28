### Fixed

- **The "CI flake, re-run green" notice names the test that flaked.** It used to name the
  first log line that said "failed" ("re-register failed nope", "Test Files 1 failed 1"),
  because pytest's `FAILED <nodeid>` line can carry a long assertion message that pushes the
  line out of the log tail chela read. The test id is now read from the whole failing log:
  pytest's `FAILED`/`ERROR` lines, or Vitest's `FAIL <spec> > <suite> > <test>` header (which
  wins when a JS suite ran under pytest). When no line names a test, the notice says "failing
  test not identified" next to the failing job. Flakes are recorded under that id, and
  `chela ops scan` now counts repeat Vitest flakers as well as pytest ones. (CMX-351)
