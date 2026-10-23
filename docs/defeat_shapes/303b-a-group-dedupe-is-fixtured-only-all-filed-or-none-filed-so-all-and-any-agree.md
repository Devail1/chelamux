## 303b. A group dedupe is fixtured only all-filed or none-filed, so `all` and `any` agree

**Assertion form:** a batch of keys is skipped when it is "already done" (`if all(k in filed
for k in keys): continue`). The tests run it twice — first with an empty store (files), then
with the store the first run wrote (skips) — and delete the whole store for the negative
control (files again).

**Why that's a gap:** every fixture holds a store that has EVERY key of the group or NONE of
them. On those two inputs `all(...)` and `any(...)` return the same answer, so the quantifier
— the one thing that makes the dedupe per key rather than per group — is unpinned. The sibling
shape is a gate with two conjuncts (`covered and status != CANNOT_VERIFY`) whose negative cases
all fail BEFORE the first conjunct is set: the second conjunct is never what decides.

**Mutation that defeats it:** `if all(k in filed for k in keys):` → `if any(...)`. Suite green
— while in production a NEW surviving mutation on a guard line filed last week for another
category is silently counted "already filed" and never reaches the tracker. Likewise
`... and result["status"] != CANNOT_VERIFY` → `... and True`: a red-baseline night moves the
covered window and its merges are never swept.

**Guard form that survives:** for a group predicate, fixture a PARTIAL store — some keys of the
group present, some not — and assert the branch only the right quantifier takes. For an `and`
gate, give each conjunct a case where every OTHER conjunct is already true (a failure that
happens after `covered` is set: a red baseline, a torn key store), so that conjunct alone
decides.
Related: [[303c|shape 303c]], [[86b|shape 86b]], [[47|shape 47]], [[370b|shape 370b]].

**Found:** CMX-299 rework round 1 (2026-10-21), PR #819, which was closed and re-scoped into
three slices; catalogued by the scheduler slice, CMX-303, which carries the guards. Closed in
`tests/test_sweep_nightly.py` by `test_a_new_survivor_category_on_an_already_filed_guard_is_filed`
and the `red_baseline` / `torn_key_store` rows of `test_only_a_run_that_looked_moves_the_window`.
