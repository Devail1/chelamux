### Fixed

- **Tests now pin what CMX-388's dispatch-off-by-choice code says and returns.** The exact
  dispatcher and reconciliation detail text with `CHELA_DISPATCH=off`, the "IGNORED"
  note for configured workflows, the `1 workflow` / `2 workflows` count, and the sidebar's
  folder key for a missing row each have a test. The nightly sweep found that no test
  failed when any of them was broken. Tests only, no behaviour change. (CMX-463)
