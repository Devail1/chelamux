## 41b. A helper returns a gating flag, and the renderer is free to ignore it

**Assertion form:** a pure model helper (`prChip(pr)` in `wallmodel.js`) returns both the
text to show AND a boolean that decides whether a claim may be made around it
(`{label, flag, dim, …}` — `flag` is "this PR is confirmed open, so it earns the ⚑").
The helper is tested exhaustively: `flag` is true for open, false for merged, closed,
draft, unknown and missing. The renderer reads it:
`el.textContent = (chip.flag ? '⚑ ' : '') + chip.label`.

**Mutation that defeats it:** stop consulting the flag at the call site
(`el.textContent = '⚑ ' + chip.label`). The helper still computes `flag` correctly and
every one of its tests stays green; the page now draws "⚑ #613 merged" on a dead PR,
which is the exact defect the change was written to fix. Suite green.

**Why it is not shape 436:** in 436 the call site reverts to an old *label* expression,
and the helper's output disappears from the page. Here the helper's label is still
rendered, verbatim and correct; only the *decision* the helper handed over is dropped.
A DOM test that reads the label back on an open PR (the fixture's usual case) still
passes, because on an open PR the flag is true and the mutation is a no-op. The only
fixture that can tell the two apart is one where the flag is FALSE.

**Guard form that survives:** drive the real renderer (`termTick` → `_applyWallTileFrame`)
through every state the flag distinguishes, including the false ones, and read back the
rendered element's exact text, plus each other field the helper hands over (dim class,
tooltip, and the sibling action bar the same predicate gates). Assert both directions:
the ⚑ appears for an open, non-draft PR and is absent for every other state.

**Found:** CMX-41 rework round 1 (PR #620). Fixed by `tests/wallnav.test.mjs`
("CMX-41: the rendered .gs-pr chip and Review bar …").
