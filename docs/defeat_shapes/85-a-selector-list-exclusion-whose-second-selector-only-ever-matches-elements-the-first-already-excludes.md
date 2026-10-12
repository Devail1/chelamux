## 85. A selector-list exclusion whose second selector only ever matches elements the first already excludes

**Assertion form:** a DOM filter drops rows by a CSS selector LIST —
`.filter(r => !r.closest('.side-group.collapsed, .side-archived'))` ("skip a collapsed
group's rows and the Archived section's"). The comma is an `or`, but it does not read like
one: it reads as a single selector. The suite tests the first alternative directly (collapse
a folder group, check its rows are skipped) and never renders the second one in a state the
first does not already cover.

**Why the obvious tests don't catch it:** the Archived section is itself a `.side-group`,
and it is COLLAPSED by default — so in its default state it matches BOTH alternatives. And
by default it is not drawn at all (View ▸ Status is Active). The second selector only
matters for a state that takes two deliberate steps to reach (Status ▸ All, then open the
section) and that no fixture took.

**Mutation that defeats it:** drop the second selector — `'.side-group.collapsed,
.side-archived'` → `'.side-group.collapsed'`. Every test stays green: no archived row is
ever on screen while its section is open.

**Guard form that survives:** drive the element into the state where ONLY the second
selector matches it — the Archived section drawn AND open (not `.collapsed`), holding a row
that otherwise qualifies (a hidden *live* window, so it carries `data-agent` like any other
row) — and assert the fixture got there before asserting the outcome (the section is not
collapsed; the row is in it). Then the numbering must stop at the rows outside it.

**Relation to [[62b|shape 62b]]:** the same disjunction-redundancy, in a CSS selector list
instead of a boolean `or`, where it is the UI's DEFAULT state (collapsed, hidden) — not a
hand-built realistic fixture — that satisfies both clauses at once.

**Found:** CMX-85 rework round 1, PR #657. `chela/dashboard/static/js/nav.js` `_altRows()`;
fixed in `tests/sidebar_alt_switch.test.mjs` ("an OPEN Archived section's rows are skipped").
