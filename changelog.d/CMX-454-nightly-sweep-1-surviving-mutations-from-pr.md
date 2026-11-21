### Changed

- **A test now pins the sidebar's archive-record guard in the custom-group reconcile.**
  `reconcileAssign` skips a null hole in the archive list instead of reading through it; a
  test with a hole ahead of a live record goes red if the guard's `&&` becomes `||` (the
  nightly mutation sweep found no test could tell them apart). (CMX-454)
