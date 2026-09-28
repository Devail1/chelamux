## 377d. A cascade-resolved CSS guard is blind to anything its INPUTS lack: at-rule-wrapped overrides, and selectors qualified on what only the real DOM carries

**Assertion form:** [[377b|shape 377b]]'s fix — mount the real `style.css` in jsdom and read
`getComputedStyle(el)` for the RESOLVED value — applied with two inputs that were not the
browser's: (a) the RAW stylesheet text, and (b) a hand-written minimal fixture (`.app` with an
empty `.canvas`; a bare `<span class="term-status-dot waiting">` under `.gs-head`; a `.grid-stack`
whose items are empty `<div>`s). Each read as "the cascade decides, so any selector spelling is
covered."

**Mutation that defeats it:** an override the cascade guard's inputs cannot see:
1. `@supports (display: grid) { .app > .canvas { grid-row: 2; } }` — jsdom's `getComputedStyle`
   applies only top-level style rules; it PARSES `@supports`/`@media`/`@container` blocks into
   the CSSOM and then ignores every rule inside them. An always-true wrapper hides any override.
2. `.gs-head .term-status-dot[data-wid].waiting { clip-path: none; border-radius: 50%; }` —
   the real pane-header dot (`terminals.js` `paneHead`) carries `data-wid`; the fixture's did not.
3. `#term-stage > .grid-stack:has(.gs-head) { padding: 24px; margin: 12px; }` — every real pane
   has a `.gs-head`; the fixture's panes were empty.

All three: PR #529 round 4, 2026-09-28, suite green (4116 passed).

**Why the cascade alone can't close this:** the cascade resolves correctly over whatever DOM and
rules you give it. A qualifier (`[data-wid]`, `:has(...)`, `:not(:empty)`, an ancestor id) that
the fixture lacks simply doesn't match there — so the fork applies in the browser and nowhere in
the test. And jsdom's at-rule blind spot is not a specificity question at all: the rule is never
a candidate.

**Guard form that survives:** make both inputs the browser's.
- **DOM:** mount the REAL shell (`sliceTemplate` over `templates/index.html`) and render the REAL
  markup through the real code path (`renderTerminals` → `paneHead`, `renderSidebarAgents`, the
  real status tick) — assert a sanity count that the populated structure is really there.
- **Stylesheet:** flatten it for a concrete viewport before mounting —
  `tests/js_helpers/css_viewport.mjs`'s `cssForViewport(css, {width, height})` walks jsdom's own
  CSSOM, unwraps every `@media`/`@supports`/`@container`/`@layer` block that matches the viewport
  in source order, drops the rest, and **throws** on any at-rule or media feature it can't
  evaluate (fail closed — a new wrapper shape must be taught, never slip past).

**Found:** CMX-377 round 4 (PR #529), 2026-09-28, judge mutations 1–3. Closed by
`tests/restyle_real_shell_css.test.mjs` (real shell + real panes + real sidebar rows, CSS
flattened at 1440×900) and by routing the three round-3 fixture guards through
`cssForViewport`. Each judge mutation re-applied → RED → restored.

**See also:** [[377b|shape 377b]] (this is its sequel), [[377c|shape 377c]] (fixture never mounts
the element — the same "the test's DOM is not the browser's DOM" family).
