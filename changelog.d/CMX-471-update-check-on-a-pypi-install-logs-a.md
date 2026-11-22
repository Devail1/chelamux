### Fixed

- **The update check on a PyPI install no longer logs a `NotAGitCheckout` traceback.** chela
  now detects how it was installed: a git checkout, `uv tool`, `uvx` or pip into a venv. A
  non-git install compares its version with PyPI's latest (cached for an hour) and the daemon
  logs one INFO line per check. `/api/update/status` reports `install`, `latest` and
  `behind`. The update banner and Settings > Update name the matching command
  (`uv tool upgrade chelamux`, or "uvx always runs the latest; restart the services") instead
  of offering "Update & restart". `chela doctor` shows the install kind and whether a newer
  version exists, and `chela update` on a package install says how to upgrade it. (CMX-471)
