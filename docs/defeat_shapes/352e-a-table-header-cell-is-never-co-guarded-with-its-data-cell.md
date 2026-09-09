## 352e. A table header cell is never co-guarded with its data cell — the finder locates cells by label, decoupled entirely from the header row

**Assertion form:** `_renderRunsTable` in `chela/dashboard/static/js/dispatcher.js` emits a
`<thead>` row as one hardcoded string of `<th>` tags and, separately, a `<tbody>` row built
cell-by-cell through `_cell(label, content)`, which stamps each `<td>` with a matching
`data-label` attribute (read by the `@media (max-width:768px)` block to re-flow the table on
mobile). `tests/task_progress_chip.test.mjs`'s DOM assertions locate the new Tasks column
exclusively via `row.querySelector('td[data-label="Tasks"]')` — a query that is fully
satisfied by `_cell('Tasks', ...)` alone and never touches, counts, or reads the `<thead>` at
all. The `<th>Tasks</th>` this PR also added to the header string and the `<td
data-label="Tasks">` `_cell` emits are two independent pieces of markup, written on two
different lines, joined only by a human keeping them in sync by eye — and nothing in the
suite checks that they still are.

**Mutation that defeats it:** drop the header cell this PR added, leaving the body cell
untouched:

```diff
- <th>Status</th><th>Tasks</th><th>Branch</th>
+ <th>Status</th><th>Branch</th>
```

`tests/task_progress_chip.test.mjs`'s existing assertions still find
`td[data-label="Tasks"]` (the query never looked at `<thead>`) and still read a chip out of
it — every one of them passes. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3820
passed, 0 failed, 0 error(s)) with the mutation in place, even though every column in the
real rendered table — Branch, Window, Started, Ended, Attempt, PR, Error — now sits one
position left of the heading it's supposed to be under, for every run in production.

**Guard form that survives:** for a table whose header is a hand-written string and whose
body is built from a per-cell helper carrying its own `data-label`, add one assertion that
ties the two together structurally rather than by eye: collect `thead th` and a body row's
`td`s as parallel arrays, assert their lengths are equal (catches a header cell dropped or
added anywhere), and assert the index of the header whose text matches the label under test
equals the index of the `td` carrying that `data-label` (catches a header cell dropped,
duplicated, or reordered relative to its own cell specifically). Neither half alone is
sufficient — count-equality alone would not catch a header and cell that both exist but have
swapped positions with a neighbour; index-equality alone (without knowing lengths agree)
could still pass by coincidence if the header row silently gained or lost cells symmetrically
on both sides of the one being checked.

**Found:** CMX-352 rework round 3 (2026-09-09), PR #465. The judge's own throwaway-checkout
mutation battery found the dropped `<th>Tasks</th>` survived with 3820 tests green, because
`tests/task_progress_chip.test.mjs` never queried `<thead>`. Closed by a new test asserting
`thead th` count equals the row's `td` count, and that the `Tasks` header's index among
headers equals the `Tasks` cell's index among cells.
