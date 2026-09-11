## 360. A guard refuses a state a sibling step already forecloses earlier, the same tick

**Assertion form:** the rework loop (3b, `chela/dispatcher.py`) selected every
`changes_requested` row and, before handing it to `_respawn_rework`, checked
`row["pr_state"] == "merged"` and skipped the row if so — "refuse at the source", written as
a belt-and-suspenders companion to the merge-reconcile step (1) that runs earlier in the same
tick. The test built to pin it, `test_a_merged_rework_row_is_never_handed_to_respawn`, seeded
a row with `status="changes_requested", pr_state="merged"` and asserted `attach.call_count ==
0` — which passed, and looked like proof the 3b check was doing its job.

**Mutation that defeats it:** `if row["pr_state"] == "merged":` → `if False and
row["pr_state"] == "merged":`. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3946
passed) with the guard fully disabled. The seeded row never reaches 3b's `WHERE
status='changes_requested'` query at all: phase 0 refreshes `pr_state` before step 1 runs,
and step 1's merge-reconcile (`RECONCILE_MERGE_STATUSES` includes `changes_requested`)
selects on that same `status IN (...)` set, sees `pr_state == "merged"`, and flips the row to
`done` — committed, same connection — before step 3b's query ever executes, later in the same
tick. The test's own inline comment said as much ("this is the reconcile path proving it, not
the refusal path") but read the redundancy as harmless rather than as proof the second check
could never fire.

**Why this is not shape 337 (a guarded condition proven only where a sibling already
rejects):** shape 337's guard COULD be pinned properly with a better fixture — a case existed
where the tested gate and the untested gate would disagree, it just wasn't in the suite yet.
Here no such case exists: every dispatcher tick that can make a `changes_requested` row's
`pr_state` read `merged` runs step 1 before step 3b, unconditionally, on the same connection,
scoped to the same workflow. There is no ordering, no concurrency interleaving, and no
fixture contortion that lands a `changes_requested` + `pr_state='merged'` row in front of
3b's query — step 1 has already closed it out from under 3b by construction. Writing a
"reachable" fixture for this guard means faking a state the dispatcher itself cannot
produce: seeding the row directly into that shape and skipping `tick()`'s own step 1, which
proves the fixture can reach the code, not that production ever can.

**Guard form that survives:** don't write one — delete the dead check instead. When a
judge's required-mutation-set survivor turns out to guard an unreachable branch, the fix is
not a stronger fixture; it is proving reachability first. Trace the exact tick sequence that
would have to occur for the guarded state to exist at that code point: same connection, same
tick, in call order, from the top of `tick()`. If a step upstream already excludes that state
unconditionally (not "usually", not "under most configs" — literally always, for every row
that WHERE clause can select), the downstream check is dead weight: it adds a comment
claiming a race it can't actually catch, a maintenance surface pointing at a mutation nobody
can reproduce, and a test whose passing tells you nothing about the guard's own logic (it
tells you about the sibling step's). Prefer removing the intermediate value/branch over
adding a test that pins a state the system cannot produce — a fixture contorted to reach it
pins a fiction and misleads the next reader worse than no test at all.

**Found:** CMX-360 rework round 1 (2026-09-11), PR #493. Confirmed by tracing `tick()`'s call
order (phase 0 pr_state refresh → step 1 merge-reconcile, scoped to `wf.path`, same
connection → … → step 3b rework spawn, same scope, same connection, later in the same tick)
and independently verified in a scratch checkout: removing `"running"` from
`RECONCILE_MERGE_STATUSES_WITH_RUNNING` (the genuinely-guarded half, kept as-is) turns the
suite red; deleting the entire 3b `pr_state == "merged"` block does not change a single
test's outcome.
Fixed by deleting the check (`chela/dispatcher.py`) and its two spawn-side tests
(`test_a_merged_rework_row_is_never_handed_to_respawn`,
`test_an_open_rework_row_still_spawns_ARM_TWO_OF_THE_GUARD`) from
`tests/test_dispatcher_merged_running_reconcile.py` rather than adding a fixture for a state
`tick()` cannot produce.

**See also:** [[337|shape 337]] — same family (redundant coverage looks like a working
guard), but there a reachable disagreement case exists and the fix is a better fixture; here
none exists and the fix is deletion.
