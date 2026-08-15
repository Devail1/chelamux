## 13. A per-item loop over a required SET tested with a set of exactly one

**Assertion form:** a function is supposed to check EVERY item in a list against some
condition and collect the ones that fail — but every fixture that drives it hands it a
list of length one.

**Mutation that defeats it:** change `continue` (skip this item, keep checking the rest of
the list) to `break` (stop checking entirely the moment one item passes). On a one-item
list these are identical — there is nothing left to check either way — so the suite stays
green. The concrete failure is asymmetric and worse than it looks: with two required items,
the agent re-tests the EASY one and the loop stops there, silently excusing the hard one it
never re-tested — which is exactly the "tested something easier instead of the case that
beat it" recurrence this checking function exists to catch, reached through the plural door
instead of the singular one every test exercises.

**Guard form that survives:** construct a fixture with at least TWO items in the required
set — one resubmitted (satisfied), one not — and assert the result names only the one that
was not resubmitted. A same-cardinality-everywhere fixture (every test uses length one)
structurally cannot tell `continue` from `break`.

**Found:** `chela/dispatcher.py`'s `_missing_required_mutations` (CMX-269 rework round 6) —
every fixture in `tests/test_dispatcher_task_finished.py` built its required set via
`_review_history_with_required(mutation)`, which always wraps exactly one mutation. Fixed by
`test_verify_self_check_flags_only_the_required_mutation_that_was_not_resubmitted`, which
hands the function two required mutations and asserts only the unsubmitted one is flagged.
