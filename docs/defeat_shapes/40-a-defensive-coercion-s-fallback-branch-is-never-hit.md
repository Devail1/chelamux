## 40. A defensive coercion's fallback branch is never hit because every fixture already supplies the well-formed shape it exists to correct

**Assertion form:** a fetch/join function states, in a comment, that its input is
"defensively coerced" against a malformed shape from a flaky or mocked endpoint
(`Array.isArray(ctx) ? ctx : []`), and every test fixture that ever flows through that
function is already the well-formed shape (an array). The full suite is green, and the
coercion reads as covered because the code path around it is heavily exercised — but no
fixture has ever actually been the malformed shape, so the `? ctx : []` half of the ternary
has never once been the branch taken.

**Mutation that defeats it:** delete the coercion (`Array.isArray(ctx) ? ctx : []` ->
`ctx`). Every existing fixture is still an array, so `.map`/`.forEach`/etc. still works
identically for all of them — nothing downstream changes, and the suite stays green. The
only thing that changes is what happens on the one input shape no test ever sends.

**Guard form that survives:** for a stated defensive coercion, add a fixture that is
actually the malformed shape the coercion names (a bare `{}`, `null`, a string) and assert
the specific fallback behavior the code's own comment promises — here, that an un-awaited
async call doesn't throw out of a `.map` and leave the caller's DOM stuck on its
pre-fetch placeholder ("Loading…") behind an unhandled rejection. A coercion with no
fixture that ever takes its fallback branch is unguarded no matter how many tests exercise
the branch it wasn't needed for.

**Found:** CMX-287 rework round 5 (2026-08-14), PR #358 — `cost.js`'s `refreshCost()`
coerces `/api/cost`'s response with `Array.isArray(ctx) ? ctx : []`, stating in its own
comment that the Cost tab "must" survive a bare `{}` from a flaky/mocked endpoint the same
way the Settings modal's other tabs do. Every `/api/cost` fixture in
`tests/settings_cost.test.mjs` (the initial payload and both re-fetch payloads) was an
array; the judge deleted the coercion in a throwaway checkout and all 3127 tests stayed
green. Closed by a new fixture (`costPayload = {}`) asserting the Cost tab falls back to
its empty-state render instead of staying on "Loading…".
