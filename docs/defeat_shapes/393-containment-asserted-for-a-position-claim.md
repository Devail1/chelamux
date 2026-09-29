## 393. A position claim ("at the container's right end") is guarded by containment ("inside the container")

**Assertion form:** the change moves a control into a container AND claims a position inside
it — "the inbox sits at the sidebar head's right end". The browser test proves the control is
a DOM descendant of the container, that its box is drawn inside the container's box, and that
it shares the row with a sibling. Every one of those is true wherever in the container the
control lands.

**Mutation that defeats it:** remove the rule that places it — `.sidebar-head-actions {
margin-left: auto }` → `margin-left: 0`. The inbox now sits right after the wordmark, in the
middle of the head: still inside it, still on the toggle's row, still hit-testable. Green.

**Guard form that survives:** measure the position the claim names, against the container's
CONTENT edge (its box minus padding): the control's right edge within ~2px of it, the
expected siblings to its left in order, and a minimum gap from the neighbour it would slide
up against if the pushing rule went. Containment stays as a separate, weaker assertion.

**Found:** `tests/browser/dashboard.test.mjs` CMX-393 (PR #545, judge round 1).
