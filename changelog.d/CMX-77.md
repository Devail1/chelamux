### Changed

- **CI now tests Python 3.11 and 3.13, and no longer 3.12.** 3.11 is the supported floor
  (`requires-python = ">=3.11"`). 3.13 is what a live install runs today, and CI had never
  tested it. The job count stays at two. `requires-python` and the documented ≥ 3.11 minimum
  are unchanged. (CMX-77)
