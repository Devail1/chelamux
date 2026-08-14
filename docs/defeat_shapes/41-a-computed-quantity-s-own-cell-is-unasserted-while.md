## 41. A computed quantity's own rendered cell is unasserted while every sibling cell around it in the same row/table is

**Assertion form:** a render function produces several related figures into one table — a
per-group subtotal, per-item lines within the group, and a grand total footer. A test opens
the table and pins the group's NAME cell, the item lines' names and values, and the grand
total — everything *around* the subtotal — but never reads the subtotal cell itself back.
Because the subtotal sits in the same row as an asserted name cell and is computed from the
same items whose own values are asserted, it reads as covered by proximity even though no
assertion has ever touched its own text content.

**Mutation that defeats it:** replace the computed value with a constant in the one cell
never read (`_fmtCost(p.total)` -> `_fmtCost(0)`). Every other assertion in the test
— the group name, the item rows, the grand total — is computed independently of this one
cell and still passes unchanged, so the whole suite stays green while the subtotal itself
renders wrong for every group, every time.

**Guard form that survives:** when a render function produces N related figures, list them
out explicitly and check each one has an assertion reading its own specific cell/node back
— not "a test opened this table and it looked right," but "this exact cell's text equals
this exact expected value." A quantity computed by a `reduce`/`sum` and rendered into a
dedicated cell needs a fixture where that computed value is a *distinguishable* number (not
coincidentally zero or equal to a sibling figure) and an assertion that reads that cell by
its own selector, not the row's or table's aggregate text.

**Found:** CMX-287 rework round 5 (2026-08-14), PR #358 — `cost.js`'s `renderCostTable()`
reduces each project's agents into `total` and renders it into `.cost-project-row`'s own
cost cell. `tests/settings_cost.test.mjs` asserted the project row's name cell
(`.cost-project-row td:first-child`), the agent rows' names, the per-agent `$1.50` figure
and the fleet-total footer's `$3.75` — never the project row's own cost cell. The judge
zeroed the subtotal (`_fmtCost(p.total)` -> `_fmtCost(0)`) in a throwaway checkout and all
3127 tests stayed green, because every other figure in the table is computed and asserted
independently of that one cell. Closed by reading `.cost-project-row td:last-child` for
both projects and pinning their exact expected subtotals.
