## 303. An SVG icon guard upgraded from wrapper-presence to path-presence still can't tell WHICH icon rendered, or whether its path actually paints anything

**Assertion form:** a guard for a vendored inline-SVG icon (the `_LUCIDE` map / `lucideIcon()`
pattern in `chela/dashboard/static/js/util.js`) is written as `svg.querySelector('path')` —
proving the `<svg>` wrapper got *some* content, closing [shape 302](302-an-icon-lookup-falls-back-to-empty-instead-of-failing.md)'s
"empty `<svg>`" defeat. That single boolean check is then treated as proof the CORRECT icon
rendered, and stays the only thing read off the `<svg>` node at all.

**Mutation that defeats it:** two independent ways:
1. **Swap the icon name at the call site** — `lucideIcon('crown', 12)` → `lucideIcon('minus',
   12)`. Every entry in `_LUCIDE` has at least one `<path>`, so `svg.querySelector('path')`
   is exactly as true for the wrong icon as the right one; a distinctness-free presence check
   ([shape 78](78-a-distinctness-only-assertion-stands-in-for-the-designed-literal.md)'s
   general form) can't tell a crown from a dash. Title text, CSS class, and every other DOM
   attribute the badge's own markup sets are untouched, so nothing else in the test catches it
   either.
2. **Kill the paint, not the geometry** — `lucideIcon`'s `stroke="currentColor"` (these are
   outline icons: `fill="none"`, so the stroke IS the visible ink) → `stroke="none"`. The
   `<path>` node and its exact `d` data are still in the DOM untouched — a literal-path-data
   guard closing mutation 1 above still passes — and no CSS rule changes, so any
   `getComputedStyle` check on the badge's *wrapper* (`display`, `width` — the
   [shape 67](67-a-computed-style-visibility-guard-is-written-for-a.md) recipe) stays green
   too, because `stroke` is an SVG *presentation attribute* on the icon's own element, one
   layer beneath both the DOM-presence check and the wrapper's CSS cascade. The badge renders
   a correctly-shaped, correctly-sized, zero-ink outline: visually identical to the bare
   colour dot the feature was written to replace.

**Why path-presence alone can't close either:** `querySelector('path')` answers one question
— "did the interpolation produce a non-empty child" — and neither "which child" nor "is the
child's own paint attribute intact" is reachable from that answer. Icon identity and icon
paint are two axes a single presence boolean cannot separate; each needs its own literal-value
assertion.

**Guard form that survives:** for any vendored-icon badge whose SPECIFIC identity is part of
the feature (not just "an icon is here somewhere"), assert both, hardcoded independently of
the source map so a corrupted map entry can't satisfy its own test:
- the exact `d` attribute(s) of the rendered `<path>` element(s), e.g.
  `assert.deepEqual([...svg.querySelectorAll('path')].map(p => p.getAttribute('d')),
  [<the icon's own literal path data>])`
- the `<svg>`'s own `stroke` attribute equals the paint-producing value the helper is
  documented to emit (`assert.equal(svg.getAttribute('stroke'), 'currentColor')`), which is
  orthogonal to any CSS-cascade `display`/`width`/`visibility` check on the containing element
  and must be asserted separately from it.

**Found:** CMX-302 rework round 3 (2026-08-17), PR #376. `tests/sidebar.test.mjs`'s
orchestrator-badge test had already closed [shape 302](302-an-icon-lookup-falls-back-to-empty-instead-of-failing.md)'s
round-2 fix (`svg.querySelector('path')`, proving the map entry wasn't empty) — the judge
applied both mutations above to the same guard in the same round, and
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3211 passed) with either one in
isolation. Closed by adding a literal `d`-attribute comparison and a `stroke` attribute
assertion alongside the existing `querySelector('path')` check.

**See also:** [[302|shape 302]] — the earlier rounds on this exact badge/helper
(wrapper-presence, then absent-key, then present-but-empty); this shape is what's left once
all three of those are closed and the guard still can't distinguish *which* icon painted
*what*. [[78|shape 78]] and [[67|shape 67]] are this shape's general forms (distinctness vs.
literal value; enumerated computed-style properties vs. erasure) applied to SVG icon identity
and SVG presentation-attribute paint specifically.
