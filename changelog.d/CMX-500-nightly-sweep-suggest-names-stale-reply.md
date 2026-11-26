### Fixed

- **The "Suggest names" dialog's guards are now tested.** If you flip the dialog's Include
  toggle while the first read is still pending, that read's late answer or failure must be
  ignored, and editing one row must change only that row. No test checked either, so the nightly
  mutation sweep could remove them unnoticed. New tests fail if either guard is removed.
  Test-only; no behaviour change. (CMX-500)
