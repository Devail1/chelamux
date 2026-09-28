## 386. A layout guard hands jsdom the geometry as a literal, so the CSS rule that bounds it never feeds the assertion

**Assertion form:** jsdom does no layout, so an "on-screen" guard stubs the geometry the handler
reads — `anchor.getBoundingClientRect = () => ({ left: 64, … })`,
`Object.defineProperty(menu, 'offsetWidth', { value: 232 })` — runs the real positioning code, and
asserts `left + 232 <= 390`. The PR's actual fix for "the menu runs off the phone's right edge" was
a CSS rule, `@media (max-width: 768px) { .launch-menu { max-width: calc(100vw - 16px); } }`.

**Mutation that defeats it:** (PR #538 round 1, 2026-09-29, suite green — 4212 passed)
`.launch-menu { max-width: none; }` — the rule the fix depends on is gone; a long nowrap project
label now sizes the popover past a 390px screen. The guard still passes: `232` was typed into the
test, not resolved from the stylesheet, so no CSS change can reach it. The fab's `left` (`+ 50px`)
was likewise a literal.

**Why it looks right:** the literals are the *correct* values for the code as written (232 IS the
min-width, 64 IS where the fab sits), and the real handler arithmetic runs. But the invariant lives
in the stylesheet, and the fixture is the only path from the stylesheet to the assertion — a
literal severs it. It is also parked on the *short-content* case, where min-width wins and
max-width is never exercised at all.

**Guard form that survives:**
- **Derive every stubbed dimension from the cascade**, mounted at the viewport
  (`cssForViewport`): the anchor's rect from its resolved `left`/`top`/`width`/`height`, the
  popover's width as `clamp(resolved min-width, natural, resolved max-width)` — `none` ⇒ ∞.
- **Evaluate the resolved value, fail closed:** `calc()`/`max()`/`min()` over `px`/`vw`/`env()`;
  anything else throws. (jsdom quirks: it re-serializes `max(a, b)` inside `calc()` as
  `max(a * , * b)`, and drops a bare top-level `max(…, env(…))` to `auto` — handle both
  explicitly, never let `auto` default to 0.)
- **Drive the case the rule exists for:** a natural content width far past the viewport (900px on
  a 390px phone), and assert the resolved width actually got capped (`width < natural`) as well as
  on-screen.
- A real-browser (Playwright) check at 390px remains the ground truth for pixel placement.
