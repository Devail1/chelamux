### Added

- **chelamux is on PyPI: `uvx chelamux dashboard` runs the wall with nothing to clone.** Each
  release tag now builds the sdist and wheel, smoke-tests the wheel and publishes it with
  Trusted Publishing (no API token). The package gains a `chelamux` command next to `chela`,
  so `uvx chelamux …` needs no `--from`. CI builds and runs the wheel on every PR, and a test
  checks the wheel carries the dashboard bundle, the ttyd shims, the templates, the plugin's
  merge-gate script and the statusLine hook. (CMX-363)

### Changed

- **The dashboard's packages are core dependencies.** Flask, flask-sock and cryptography
  install with chelamux itself, since a bare `uvx chelamux` cannot name an extra. The
  `dashboard` extra still exists (empty), so `chelamux[dashboard]` install lines keep
  working. `chela install-statusline` now finds the hook inside an installed wheel too, not
  only in a checkout. (CMX-363)
