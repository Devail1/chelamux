### Changed

- **One-command install.** The README and docs/GETTING_STARTED.md now install with
  `uv tool install "chelamux[dashboard] @ git+https://github.com/Devail1/chelamux"`, then
  start tmux, the daemon and the wall: four commands, with the wall up about 15s after the
  first on a clean Ubuntu box. `gh`, Telegram, Linear and pm2 are follow-ups, not
  prerequisites. (CMX-92)
- **`chela doctor` separates required from optional checks.** Telegram and Linear read
  "not configured (optional)" when absent, an error from either no longer fails the run,
  and a clean run ends with `required checks: all pass`. (CMX-92)

### Added

- **`scripts/check_fresh_install.sh`** runs the README install block verbatim in a clean
  Ubuntu container (docker or podman, as your own uid, a stub `claude`), times it, and
  checks the dashboard answers and `chela doctor` passes. The test suite runs it wherever
  docker or podman answers; without one it exits 77 and the test reports SKIPPED. (CMX-92)
