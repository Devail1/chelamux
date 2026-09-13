## 365b. A sort key's third-component tie-breaker was proven correct-when-present, not load-bearing, because the fixture's input order already matched its output

**Assertion form:** `test_a_null_created_at_sorts_last` gave the two null-group issues
("no date" = #2, "blank date" = #3) ascending numbers, in the same order they appear in
`gh`'s fake response, and asserted the output kept that order. Shape [[365]] (round 1 of
this same PR) had already fixed the fixture so `created_at` order and `int(number)` order
disagree across the *whole* list — but within the null group specifically, the two
remaining components of the sort key (`created_at is None`, `created_at or ""`) are tied by
construction (both fold to `(True, "")`), so `int(number)` is the *only* thing left to order
them, and nothing in round 1's fix touched whether that ordering coincided with input order.

**Mutation that defeats it:** `(created_at is None, created_at or "", int(number))` →
`(created_at is None, created_at or "", 0)` — replaces the tie-breaker with a constant.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3983 passed, exit 0) because
Python's `sort()` is stable: with all three key components now tied between "no date" and
"blank date", the sort falls back to their position in the input list, which was #2-then-#3
— the same order `int(number)` itself would have produced, and the same order the assertion
checked for. The test could not distinguish "ordered by number" from "ordered by nothing,
list happened to already be right."

**Guard form that survives:** for a tie-breaking component, the fixture must make the
component's ordering and the input list's ordering *disagree* — not just make the sort
key's *other* components disagree (that was 365's fix, and it's a different, necessary but
insufficient, axis). Here: keep "no date" and "blank date" in the same list positions but
give them numbers whose ascending order is reversed from that position (`no date` = 5,
`blank date` = 2) while keeping the asserted output in the *number*-correct order
(`has a date`, `blank date`, `no date`). A real `int(number)` tie-break now produces that
order regardless of input position; a degraded constant tie-break falls back to stable-sort
input order instead and visibly disagrees with the assertion.

**Found:** CMX-365 rework round 2 (2026-09-13), PR #500. The judge's required-mutation-set
verdict named this mutation as surviving unchanged from round 1's fix. Closed by
re-numbering the null-group fixture so ascending-number order runs opposite to input-list
order, and updating the assertion to the number-correct (not input-order-correct) sequence.
