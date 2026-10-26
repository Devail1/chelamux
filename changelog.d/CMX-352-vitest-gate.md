### Fixed

- **The frontend gate test no longer runs the whole Vitest suite a second time.**
  `test_each_ci_gate_passes_the_committed_tree[test]` was `vitest run` again, beside
  `test_frontend_vitest_suite`, with a 180 s budget; it timed out twice in CI while xdist
  shared the runner. That case is gone, and the remaining full Vitest run over the fixture
  copy gets the 300 s budget the main suite already had (the suite's measured p95 is 112 s
  on an idle runner). (CMX-352)
