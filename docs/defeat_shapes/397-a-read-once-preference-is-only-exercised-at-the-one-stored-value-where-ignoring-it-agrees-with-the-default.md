## 397. A read-once preference is only exercised at the one stored value where ignoring it agrees with the default

**Assertion form:** a module reads a stored user choice (`localStorage`) the FIRST time it
builds a control, and resolves it through a pure helper: `open = !resolve(stored, default)`.
The pure helper has its own table test (stored `'0'`/`'1'`/`null` × default on/off, all
correct). The DOM suite boots the real module once, with NO stored value, and asserts the
control starts in the default state.

**Why that doesn't guard the wiring:** the helper's table proves `resolve` honours a stored
value; nothing proves the call site passes it. And because the module latches the value
once per process, the one DOM suite can only ever observe ONE stored value — the fixture
picked `null`, which is exactly the value where passing `stored` and passing a literal
`null` give the same answer. Every later assertion in that file (toggling, remembering the
choice via `setItem`) runs AFTER the latch, so none of them re-reads storage either.

**Mutation that defeats it:** `resolve(stored, focus)` → `resolve(null, focus)` at the call
site. The helper table stays green (the helper is untouched); the DOM suite stays green (its
fixture's stored value already is `null`).

**Guard form that survives:** boot the real module in its OWN process with a stored value
that CONTRADICTS the default (here: Focus on ⇒ default collapsed, stored `'0'` ⇒ expanded)
and assert the loaded state follows the stored value. Share the boot code in a non-`*.test`
helper module so the second process can't drift from the first. Related:
[[303|shape 303]] (an argument value that coincides with the hardcode) and [[329|shape 329]]
(a helper tested, its call site not) — this one adds the once-per-process latch, which is why
simply adding a second `test()` to the existing file cannot close it.

**Found:** CMX-397 rework round 1 (2026-09-29), PR #549 — the collapsible Wall layout
toolbar's `pc_wall_grid_collapsed` read in `terminals.js` `_buildGridPicker`.
