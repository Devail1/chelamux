### Fixed

- **A wall pane's font-atlas walk now runs to completion even when ttyd's preferences
  arrive mid-walk.** To evict a texture atlas baked from a fallback font, the terminal
  shim sets the font size to a neighbouring "decoy" value for two frames and then sets it
  back. ttyd's `SET_PREFERENCES` message arrives on the WebSocket at no fixed time, and it
  sets the font size too. In about 1 boot in 10 it arrived before xterm had rendered the
  decoy. The walk still marked itself done, so stale glyphs could stay. The walk now
  checks in each of its two frames that the decoy is still set, and runs again if it is
  not (up to 3 times, all before the pane is revealed). A second apply that arrives during
  a walk now waits for it to finish instead of starting a walk of its own. This also fixes
  `tests/browser/term_boot_reveal.test.mjs`, which failed intermittently ("no hidden frame
  at the decoy size"). (CMX-375)
