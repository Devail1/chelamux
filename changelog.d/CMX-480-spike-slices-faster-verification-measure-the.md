### Added

- **Where verification time goes: `docs/spikes/TEST_SPEED.md`.** It measures one full suite
  (14,647 tests in 641 s; peak anonymous memory 3.39 GB), the box's free memory on a normal
  day, and today's judge round and hand-in timings. It also proposes the follow-up slices,
  each with its expected saving:
  - split `tests/test_js_suites.py`, which sets the suite's wall-clock on its own;
  - share one green baseline per tree;
  - select tests for shell-script mutations and by line coverage;
  - admit a second suite slot only when memory allows.

  (CMX-480)
