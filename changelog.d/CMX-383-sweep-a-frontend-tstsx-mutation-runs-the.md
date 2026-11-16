### Changed

- **A dashboard frontend mutation now runs only the tests that can see it.** A mutated file
  under `chela/dashboard/frontend/src/` runs the Vitest specs whose imports reach it (plus any
  spec that reads files with `import.meta.glob`) and the root JS suites that import or name
  it. It no longer runs the Python suite. If that subset stays green, the mutation is checked
  again on every JS suite rather than on the full suite. The full suite's
  committed-bundle check fails on any source change, so it used to count every frontend
  mutation as killed. A frontend mutation used to take about 7.5 minutes; a subset run now
  takes about 45 seconds. (CMX-383)
- **The nightly sweep's inbox notice reports its throughput**: how many mutations it measured,
  in how many hours, and the rate per hour. A regression in test selection now shows up as a
  number. (CMX-383)
