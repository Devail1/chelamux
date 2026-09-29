## 393b. The claimed layout includes an optional element the fixture never renders

**Assertion form:** the change claims an order and alignment across a row — "shape · title ·
dim subtitle · pill, title and subtitle on one line" — and the browser test measures the
bounding boxes of every element in that order EXCEPT the one that only renders
conditionally. Here the pane subtitle renders only when the agent has an `ai_title`, and the
fixture's agents had none, so the element the one-line rule exists for was never in the DOM.

**Mutation that defeats it:** break the rule that only the missing element exercises —
`.gs-grip, .gs-label { flex-direction: row }` → `column`. With a single child (the title),
row and column lay out identically; the suite measured the same boxes and stayed green.

**Guard form that survives:** make the fixture render the conditional element (give every
agent an `ai_title`), assert it IS rendered (a null box fails, rather than being skipped),
and measure it in place: its x after the title's right edge, its vertical centre within the
row tolerance of the title's. Before trusting a layout guard, list every element the claim
names and check the fixture actually draws each one.

**Related:** shape 3 (a mount that never reaches the off-state) is the same blindness for a
state; this is the same blindness for a DOM element.

**Found:** `tests/browser/fixture.mjs` + `tests/browser/dashboard.test.mjs` CMX-393 (PR #545,
judge round 1).
