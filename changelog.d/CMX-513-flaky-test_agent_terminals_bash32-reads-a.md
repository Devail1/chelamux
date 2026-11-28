### Fixed

- **Flaky `test_supervisor_follows_windows_and_reaps_on_sigterm`.** When a ttyd was respawned,
  the test could read its pid file while the stub was rewriting it. The wait loop read the empty
  file as pid 0, took that for the new pid and stopped waiting. The next read then raised
  `ValueError` on `int('')`. Every pid read in `tests/test_agent_terminals_bash32.py` now waits
  until the file is non-empty, and the respawn wait needs a non-empty pid that differs from the
  old one. The ttyd stub now pauses between truncating its pid file and writing it, so every run
  hits this race instead of an occasional one. (CMX-513)
