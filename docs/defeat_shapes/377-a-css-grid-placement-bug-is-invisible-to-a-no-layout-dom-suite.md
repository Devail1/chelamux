## 377. A CSS grid-placement bug is invisible to a no-layout DOM suite — 4000+ green jsdom tests, blank viewport in a real browser

**Assertion form:** the sidebar+canvas shell's tests (`tests/sidebar.test.mjs`, `tests/wallnav.test.mjs`,
`tests/dashboard_scale_nav_a11y.test.mjs`, …) all build the real DOM in jsdom and assert on it: node
presence, class names, `textContent`, and — where a numeric CSS property is at stake — the declared
value read back out of the CSS *source* via a small regex helper (`_ruleBody`/`_prop`). Every one of
these passed on the branch that shipped `.canvas { grid-row: 2; ... }` (a leftover from the removed
topbar row) inside a two-column `.app { grid-template-columns: 264px 1fr; }` with no
`grid-template-rows` declared at all.

**Why that doesn't work here:** jsdom implements the DOM and CSSOM (parsing, the cascade,
`getComputedStyle` for *declared* values) but not layout — there is no engine underneath computing
actual box positions, so nothing in this suite can observe where `.canvas` ends up on screen. The bug
was a *placement* fact, not a declared-property fact: `grid-row: 2` on an element in a grid whose own
`.app` rule declares only one implicit row auto-places that element at column 1 (nothing else claims
column 2), stacking it *below* the sidebar instead of beside it. Every jsdom assertion that exists
(node exists, has the right class, the right declared `padding`/`font-size`/…) is unaffected — the
`.canvas` element is still there, still has its class, still cascades whatever properties a test
happens to read. Only opening the actual rendered page in a real browser and reading
`getComputedStyle(...).gridRowStart` / the element's bounding box surfaced it (PR #529 round 2): the
Wall panel rendered off-screen at y≈900px, and the entire right side of the 1440×900 viewport was
blank.

**Mutation that defeats it:** any placement/positioning property whose *effect* depends on the
surrounding grid/flex context, not just its own declared value — `grid-row`/`grid-column` here, but
the same shape applies to `order`, `float`, `position` + `top`/`left` interacting with a containing
block, or `z-index` stacking context bugs. A test that only re-reads the property back out of source
or the cascade cannot tell "declared X" from "declared X, and X actually places the element where
the layout requires" — the two are the same thing only if you already know the surrounding grid's
shape, which a source-only assertion doesn't encode.

**Guard form that survives:** don't try to make jsdom do layout it structurally cannot do. Instead,
assert the *placement contract* directly in the CSS source, anchored to the specific top-level rule
(not a nested `@media` override with a legitimately different value) — e.g. "no rule places `.canvas`
on `grid-row: 2` or later" and "`.canvas` explicitly claims `grid-column: 2`" — so a regression has to
change the *source text* this test reads, not just something a layout engine would need to catch. This
is weaker than a real layout assertion (it can't catch every possible grid misconfiguration, only the
specific placement contract it names), but it closes the exact defect class jsdom's DOM-only suite is
structurally blind to, which a purely behavioural (node-presence/class/textContent) test never will.
`tests/dashboard_shell_grid.test.mjs` is this guard for `.canvas`/`.sidebar`; verified by re-applying
the original `grid-row: 2` mutation by hand and confirming it goes red, then restoring and confirming
green.

**Found:** CMX-377 round 2 (PR #529), 2026-09-28. The round-1 branch (`23394a0`) passed its full
4000+-test jsdom suite (including the sidebar/wallnav/scale-nav-a11y guards this task's own brief
required to stay green) while `.canvas`'s leftover `grid-row: 2` — dead weight from the topbar-row
removal earlier in the same task — silently broke the entire layout in a real browser. Caught only by
a human/reviewer actually serving the dashboard and opening it in Chrome, not by any test in the
suite.
