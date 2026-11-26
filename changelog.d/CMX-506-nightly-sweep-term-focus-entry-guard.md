### Fixed

- **The Wall's focus guard is now tested outside a browser too.** `term-focus.js` patches
  `focus()` only inside a framed pane, and only once per page. Nothing checked those two early
  returns where Chromium is not installed, so the nightly mutation sweep could remove them
  unnoticed. A Node test now runs the real file against a stand-in window and fails if either
  return is removed. Test-only; no behaviour change. (CMX-506)
