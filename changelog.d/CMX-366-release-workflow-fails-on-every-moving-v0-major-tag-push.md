### Fixed

- **Moving the `v0` major tag no longer leaves a red Release run.** The Release workflow now
  triggers only on a full `vX.Y.Z` tag (`v[0-9]+.[0-9]+.[0-9]+`) instead of `v*`, so the
  major-tag force-move that follows every release no longer starts it and fails looking for a
  `## [0]` CHANGELOG section. `tests/test_release_workflow.py` evaluates the filter with
  GitHub's documented pattern syntax. (CMX-366)
