## 40c. A new state is asserted in its source vocabulary, but not through the translation table a second surface reads

**Assertion form:** a model function gains a new state (`tileState()` returns
`cls: 'testing'` / `'died'` for a running / dead judge battery). The test asserts the
surface that prints the model's class VERBATIM — the pane's state pill wears
`gs-state-testing` — and a sibling surface with its own mapping (the sidebar row). A third
surface, the pane-header status dot and the taskbar chip, does not print the model's class:
it pushes it through a small translation table (`_TILE_CLS_TO_DOT`, model class → the dot's
four-word vocabulary `working/waiting/idle/done`) and falls back to `'idle'` for any key it
does not know. Nothing reads the dot. The new rows of that table are therefore asserted by
nothing — and because the fallback IS the old behaviour, deleting or neutering them is
invisible.

**Mutation that defeats it:** map the new states back onto the fallback
(`testing: 'working', died: 'waiting' };` → `testing: 'idle', died: 'idle' };`). The pill
still says `⚖️ testing · 3/6 · 15m` in `gs-state-testing`, the sidebar row still says it, and
the dot beside them reads idle — exactly the defect the feature exists to remove.

**Guard form that survives:** for every surface that renders the state, assert the CLASS that
surface's CSS colours by, read off the rendered element, not the label text and not the
model's own class. Collect every element of that surface for the window (pane header dot
AND, by minimizing a pane, its taskbar chip dot) and assert each carries exactly one state
word and that it is the expected one (`['working']`, never `['idle']`), with a negative-control
window whose dot does read `idle`, so the assertion is known to be able to tell them apart.
Cover both new states — one row of the table pinned leaves the other free.

**Found:** CMX-40 rework round 3 (2026-10-09), judge verdict on PR #622.
`tests/judge_battery_badge.test.mjs` asserted the pill's `gs-state-testing` and the sidebar
row's `working`/`waiting`, but no `.term-status-dot[data-status-for]`; the mutation above
stayed green on the full suite (6383 passed). Closed by adding header-dot and taskbar-chip
dot assertions for the running, dead and no-battery windows.
