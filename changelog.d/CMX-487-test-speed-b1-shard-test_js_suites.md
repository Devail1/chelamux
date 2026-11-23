### Changed

- **The JS suites run in four pytest files, so a full test run is shorter.** Every `*.test.mjs`
  used to run from `tests/test_js_suites.py`, and `--dist loadfile` kept all of them on one
  worker, which set the run's wall-clock. They now run from `tests/test_js_suites_{0..3}.py`; a
  hash of the suite's path picks its file, and a test fails if a suite lands in none or in two.
  Run one suite with `pytest tests/ -k <its file name>`. (CMX-487)
