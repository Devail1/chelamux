## 396. A two-granularity filter is fixtured only where both granularities select the same rows, so the coarse path is dead to the suite

**Assertion form:** a search filter matches at two granularities. First, a coarse check asks
whether the whole container matches (a Settings section's heading, help text or
`data-keywords`). If it does, every row shows. If it doesn't, a fine check tests each labelled
row's own label and keywords (`nav.js` `_applySettingsSearch`, CMX-396). The tests type
"remote" and "mobile" and assert that the Remote Control row shows, that unrelated rows hide,
and that a live count appears.

**Why the obvious tests don't catch it:** the section that contains Remote Control has
**exactly one** labelled row, and that row matches the query by its own label or keywords
too. For that fixture, "show the whole section" and "show the matching rows" produce the same
DOM, and `Math.max(rows.length, 1)` gives the same count as `rowHits.length`: 1 = 1. So the
coarse path can be switched off, blinded to its three text sources (heading, `.s-desc`,
section `data-keywords`), or made to miscount, and every assertion still passes. The one
section it exists for, Remote access, has **no** labelled rows and is reachable only through
the coarse path, but no test ever searched for it. A related gap had the same cause: every
query the tests typed kept the Save row's section on the coarse path, so the "unlabelled rows
never hide" filter was never exercised.

**Mutation that defeats it:** `if (_sMatches(_sSectionText(sec), terms))` → `if (false && …)`.
Dropping `sec.dataset.keywords` or the `.s-desc` text from `_sSectionText`.
`hits += Math.max(rows.length, 1)` → `hits += 1`. All four stay green.

**Guard form that survives:** pick fixtures where the two granularities **disagree**:
- a query that matches **only** at the container level, on a container with **zero**
  labelled rows. Add one query per text source ("vpn" is section-keyword-only and "termius"
  is help-text-only on Remote access), and use setup asserts to prove each term appears
  nowhere else.
- a container-level match on a section with **N > 1** labelled rows ("terminal font" → 3
  rows), asserting that the count is exactly N.
- a **row-level** match inside a section that also holds an unlabelled row (a Timing knob next
  to Save), asserting that the unlabelled row stays visible.

Related: [[362c|shape 362c]] (only one element of a multi-flag scan is exercised) and
[[342|shape 342]] (two quantities coincide in every fixture).

**Found:** CMX-396 rework round 1 (2026-09-29), PR #548. The judge found 4 surviving
mutations of this shape, along with a second caller that was never exercised (Dispatch
rendering late) and a touch-screen focus branch that was never exercised.
