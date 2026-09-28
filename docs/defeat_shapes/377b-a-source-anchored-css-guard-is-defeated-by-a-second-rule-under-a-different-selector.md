## 377b. A source-anchored CSS guard is defeated by a second rule that targets the same element under a different selector

**Assertion form:** three separate guards, each closing a real prior-round defect, each written
the same way — read `style.css` as TEXT and assert a fact about ONE selector's exact rule body:
`tests/dashboard_shell_grid.test.mjs` anchored `^\.canvas\s*\{([^}]*)\}` and asserted the body
never carries `grid-row: 2` or later ([[377|shape 377]]'s own prescribed fix); `tests/wallnav.test.mjs`
test 11b anchored `^\.term-status-dot\.waiting\s*\{` and asserted its `clip-path` is a 3-point
triangle, plus a check that no `.gs-dot.<state>` or `:not(.gs-dot)`-excluded rule exists; and
`.grid-stack { background: transparent; }` had no guard on its padding/margin at all. All three
read as "the shape is pinned," because each one DOES assert something true about the one
selector spelling it names.

**Mutation that defeats it:** append a SECOND rule, under a DIFFERENT selector string, that
targets the same element and re-declares the same property:
1. `.app > .canvas { grid-row: 2; }` — the `.canvas` guard's anchor `^\.canvas\s*\{` never
   matches this line (different selector text entirely), but `.app > .canvas` has higher
   specificity (0,2,0 vs `.canvas` alone's 0,1,0) and wins the cascade in a real browser,
   recreating the exact off-screen-Wall bug shape 377 itself was filed to close.
2. `.gs-head .term-status-dot.waiting { clip-path: none; border-radius: 50%; }` — not
   `.gs-dot.waiting` (one of 11b's two checks), not a `:not(.gs-dot)` exclusion (11b's other
   check), and it never touches the bare `.term-status-dot.waiting` rule 11b-2 reads (that rule
   still has the correct triangle). It just outranks the bare rule inside `.gs-head` on
   specificity (0,3,0 vs 0,2,0), forking the pane header back onto a circle for "needs you" —
   the exact "two shape dialects" defect 11b exists to catch, through a selector shape neither
   of 11b's two checks enumerates.
3. `.grid-stack { background: transparent; padding: 24px; margin: 12px; }` — appended right
   after the existing rule; there was no guard reading this property at all, so this one is the
   simpler "nobody asked the question" case rather than a specificity fork, but it shares the
   same fix.

All three landed on PR #529 round 3 (2026-09-28); `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (4109 passed) with each applied in isolation.

**Why a source-text anchor can't close this:** a regex anchored to one selector's exact text
can only ever answer "what does THIS selector declare" — it has no notion of the CSS cascade
(specificity, then source order) that decides which of several selectors targeting the SAME
element actually wins in a browser. Widening the anchor to also reject `.app > .canvas`,
`.gs-head .term-status-dot.waiting`, etc. only trades one closed hole for a list that can never
be exhaustive — `.app .canvas`, `#canvas`, an attribute qualifier, a `!important`, a later
cascade layer are all different strings reaching the same element, and a text parser has to
enumerate every one by hand to catch the next.

**Guard form that survives:** ask the question a browser actually answers — mount the REAL
stylesheet against the REAL ancestor structure in jsdom (`pretendToBeVisual: true`) and read
`getComputedStyle(el)` for the RESOLVED value, not the source. This was believed to be
impossible in this repo (see `tests/wallnav.test.mjs`'s "the four RENDERED-CSS visual cues have
NO jsdom guard, by design" block, and shape 377's own "Guard form that survives" section, both
of which describe cascade resolution as something jsdom cannot do and route it to manual
live-browser verification instead) — but that caution is correct only for values that resolve
through `var()`/`color-mix()`, which jsdom's CSSOM returns as literal unresolved declaration
text rather than a computed color (confirmed separately by
`tests/dashboard_scale_nav_a11y.test.mjs`'s `--ui-font` test). For LITERAL, non-custom-property
values — `grid-row`, `grid-column`, `clip-path`, `border-radius`, `padding`, `margin` — jsdom's
`getComputedStyle` resolves the real cascade (specificity, then source order) exactly like a
browser, confirmed empirically by mounting each mutation above and reading the LOSING value
back correctly. So: for any placement/shape/spacing fact expressible without a custom property
or `color-mix()`, mount the real stylesheet + a real fixture of the surrounding structure and
assert the RESOLVED value — this closes regardless of which selector shape a future rule uses
to reach the same element, because the browser (and jsdom, for these properties) does the
enumeration for you. Reserve the "no jsdom guard, by design, verify manually" policy for values
that genuinely do route through `var()`/`color-mix()`.

**Found:** CMX-377 round 3 (PR #529), 2026-09-28, judge mutations 1–3. Closed by adding a
cascade-resolved companion test next to each source-only guard:
`tests/dashboard_shell_grid.test.mjs`'s second test (mounts `.app > .sidebar` + `.app > .canvas`
and reads `getComputedStyle(canvas).gridRow`/`.gridColumn`), `tests/wallnav.test.mjs`'s 11b-3
(mounts `.term-status-dot.<state>` under both `.sidebar` and `.gs-head` and compares
`clipPath`/`borderRadius`), and a new full-space-Wall test in
`tests/dashboard_scale_nav_a11y.test.mjs` (mounts the real wall fixture and reads
`getComputedStyle('.grid-stack').padding*`/`margin*`). Each was verified against the judge's own
mutation, re-applied by hand, going red, then restored green.

**See also:** [[377|shape 377]] — this shape is a direct sequel: 377's own "Guard form that
survives" prescribed the source-anchored regex that this round's judge went on to defeat, and
377's "no jsdom guard, by design" precedent (borrowed from `tests/wallnav.test.mjs`) turned out
to be broader than it needed to be for non-custom-property values.
