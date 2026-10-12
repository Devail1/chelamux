## 85b. A cap whose every fixture is already under it

**Assertion form:** a list is cut to its first `N` items — `.slice(0, 9)`, "only the
first nine rows get an Alt+N number" — and the guard asserts the items that DO pass:
"rows 1..k are numbered 1..k, in order". Every fixture draws `k < N` items (here five).
The cap is in the code, named in the test file's header, and never reached by any input.

**Mutation that defeats it:** widen or delete the cap — `.slice(0, 9)` → `.slice(0, 99)`
(or drop the `.slice` entirely). With five rows, "the first nine" and "the first
ninety-nine" are the same five rows, so every assertion stays green while a tenth row
would now show a `10` that no key can ever press.

**Why it looks covered:** the numbering assertion is exact (`deepEqual` over every row),
so it reads as pinning the whole output. It does — for the input it was given. The cap
only acts on items past `N`, and no fixture has any. This is the mirror of
[[15|shape 15]]: there a one-item list hides a cut that is too SHORT; here an under-cap
list hides a cut that is too LONG (or missing).

**Guard form that survives:** fixture MORE than `N` items (`N + 2`: one past the cap
proves it bites, the second proves it is not an off-by-one at the boundary) and assert
both sides — the first `N` carry `1..N`, and every item past `N` carries nothing. Assert
the fixture actually drew `N + 2` items first, or a fixture that silently collapses
(a group folded, a row filtered) re-opens the same hole.

**Found:** CMX-85 rework round 2, PR #657. `chela/dashboard/static/js/nav.js` `_altRows()`;
fixed in `tests/sidebar_alt_switch.test.mjs` ("only the first NINE visible rows are
numbered — rows 10 and 11 carry nothing").
