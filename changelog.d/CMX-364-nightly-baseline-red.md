### Fixed

- **The demo-env leak guard no longer fails on a machine without `$LANG`.** Its probe
  `os.environ` returned a sentinel for an allowed name that was unset, so `demo_env`'s
  `os.environ.get("LANG", "C.UTF-8")` saw the sentinel instead of its default and the
  nightly sweep (which runs with no `LANG`) went red. The probe now reads `PATH`/`LANG`
  exactly as `os.environ` does, and the test runs with `LANG` both set and unset. (CMX-364)
