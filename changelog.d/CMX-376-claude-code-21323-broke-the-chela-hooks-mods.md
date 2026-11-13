### Fixed

- **The chela-hooks Mod's band test passes on Claude Code 2.1.323 again.** In 2.1.323 the
  band above the prompt became one row per plugin. A test's own `ui.render` hook now
  draws a row of its own, and the Mod's `next(e)` resolves to the engine's own drawing
  (`{ type: "engine" }`). The Mod still draws that node first and its "needs you" line
  under it, so nothing changed for users; only the test's way of reading the band was
  stale. The test now reads the Mod's own row and checks that the engine's node is kept
  exactly once, with the line after it. `tests/test_mods_spike.py` pins the engine version
  the Mod's suite last passed on (`ENGINE_PASSED_ON`). A red run now names both that
  version and the installed one, so the next engine drift diagnoses itself. (CMX-376)
