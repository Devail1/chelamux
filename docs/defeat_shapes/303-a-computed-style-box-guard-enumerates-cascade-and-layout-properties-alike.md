## 303. A property is assumed to need real layout to observe, by what it's named rather than by testing it — nearly leaving a real hole unguarded

**Assertion form:** a guard proves an element's box hasn't regrown (or collapsed) by
reading a growing list of individual `getComputedStyle` properties off it — `display`,
`width`, `padding`, `min-width`/`max-width`, `visibility`/`opacity` — one more name each
time a rework round finds the next hole a prior round's list didn't cover. When the next
hole is a box-model property that sits OUTSIDE the border box (`margin`, which
`box-sizing: border-box` cannot clamp the way it clamps `padding`), the natural
conclusion is that it belongs with the OTHER things outside this harness's reach —
`transform`, `clip-path`, off-screen positioning — which this repo has independently and
correctly established need a real layout engine jsdom does not have (CMX-273's
`docs/SPIKE_WALL_FILLS_STAGE.md`, CMX-298's `tests/kanban_flatten.test.mjs` "NOT GUARDED"
section). That conclusion is reached by what the property is CALLED and CONCEPTUALLY
does (moves a box, "sounds like" layout) — not by actually running it through
`getComputedStyle` in jsdom and reading back what comes out.

**Mutation that defeats it:** `.ar-role.orchestrator { margin: 0 80px; }`
— under the row's real flex layout an 80px side margin makes the badge consume ~178px of
the row and squeezes the adjacent session name into its ellipsis, the exact reported bug
CMX-302 exists to fix, while `getComputedStyle(badge)` in jsdom keeps reporting
`width`/`min-width`/`max-width` as `18px` and `paddingLeft`/`paddingRight` as `0px` — none
of the five already-enumerated properties account for it. Found live on CMX-302, PR #376,
round 6 (2026-08-17) — `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green
(3211 passed) with the mutation applied, alongside two genuine CASCADE mutations
(`visibility: hidden`, `opacity: 0`, see shape [[67|67]]) from the same round. The
near-miss: round 7's own human-authored rework brief, reasoning from "margin sits outside
the border box" by analogy to the genuinely-unobservable `transform`/`clip-path`/
off-screen-positioning trio, prescribed leaving it as NOT GUARDED and verified only by a
one-time browser capture — which would have left the exact mutation above defeating the
suite on every future regression, forever, with no CI signal.

**Why the analogy doesn't hold:** jsdom's `getComputedStyle` resolves the winning CASCADE
declaration for a literal, non-percentage value on ANY property — it does not distinguish
"box-model property that visually affects layout" from "property jsdom happens to be able
to read." Tested directly against a real jsdom instance and this repo's real style.css:
`getComputedStyle(el).marginLeft`/`.marginRight` for `margin: 0 80px` read back `'80px'`,
exactly the same declared-value echo `width`/`padding`/`min-width`/`max-width` already
relied on above it in the same file. So do `transform` (`getComputedStyle(el).transform`
reads back `'scale(0)'`), `clip-path` (`'inset(50%)'`), and `position`/`left`
(`'absolute'`/`'-9999px'`) — every one of them is a plain CASCADE read in jsdom. What
jsdom genuinely cannot resolve is RESOLVED/composited GEOMETRY:
`getBoundingClientRect`/`offsetWidth` are always zero, and percentage/`vw`/flex-distributed
widths never resolve against a real parent (the actual CMX-273/CMX-298 finding, confirmed,
not overturned) — a narrower boundary than "any property that sounds like layout."

**Guard form that survives:** before writing a NOT GUARDED note for a property, or citing
a "needs real layout" precedent as the reason not to assert it, actually run
`getComputedStyle` on it in jsdom against the real stylesheet — a two-line
`node -e "..."` check — rather than reasoning from the property's name or its category in
the CSS spec. If it comes back as the literal declared value, guard it exactly like every
other box-model property already asserted (see `tests/sidebar_role_badge_css.test.mjs`'s
round 6/8 `margin` test, added after this was actually checked). Reserve NOT GUARDED, and
the CMX-273/CMX-298 "verify by capture instead" escape hatch, for the properties (or the
combined OUTCOME — does the box occupy N real screen pixels, at this position) that fail
that same two-line check: `getBoundingClientRect`, `offsetWidth`, and anything whose
declared value is a percentage/`vw`/flex-distributed share of a parent jsdom never lays
out.

**Found:** `tests/sidebar_role_badge_css.test.mjs` (CMX-302, PR #376, round 6-8,
2026-08-17). The `margin` mutation above is now guarded directly (`marginLeft`/
`marginRight` assertions on both the orchestrator and dispatched badges); the file's own
"NOT GUARDED" section was corrected in the same round to state the real CASCADE-vs-LAYOUT
boundary rather than the wider, unverified one.
