## 318b. A reset written inline in two SQL writers is tested only in the writer whose test file already had a harness

**Assertion form:** a new column set (`parked_at`, `parked_reason`, `parked_until`,
`parked_by`) must be cleared whenever a run starts a fresh attempt. There are two places that
start one, and each is its own hand-written SQL statement: `_spawn`'s
`INSERT … ON CONFLICT DO UPDATE SET …` (a claim, including a failed→retry re-dispatch of the
same `task_id`) and `_respawn_rework`'s `UPDATE runs SET status='running', …`. The schema
comment names both ("a claim or a rework re-spawn clears all four"). Only the rework path got
a test: `tests/test_dispatcher_rework.py` already had a respawn fixture, so seeding a park on
its row and asserting four NULLs afterwards was three lines. The claim path had no park test
at all. Read next to the comment, the one test looked like it covered "a new attempt clears
the park".

**Mutation that defeats it:** drop the four `parked_*=NULL` assignments from the
`ON CONFLICT` clause only. The rework test still goes through the other statement and stays
green, and no other test re-claims a row that carries a park. A retried run then starts its
new attempt already parked, and the stuck detector skips the new agent from its first tick.

**Why this is distinct from [[07|shape 7]] and [[72|shape 72]]:** both of those are about
several routes into ONE shared function or action. Here no code is shared: the reset is
copied as a literal into two independent statements. So no test of either statement can
reach the other, even in principle, and a "covered by the other path" assumption is wrong by
construction. What decided which copy got tested was where a harness already existed, not
where the risk was.

**Guard form that survives:** when one invariant ("a new attempt is not parked") is written
out by hand in N statements, list the statements (`grep` the column name in every
`UPDATE`/`INSERT … ON CONFLICT`) and drive each one from a row that holds the
non-reset value. Assert the invariant itself (`park.manual(row) is None` on a row that is live
again), and assert each column on its own, so dropping any one of them goes red. If the test
file for one writer has no harness, borrow one from another file instead of skipping that
writer.

**Found:** CMX-318 rework round 3 (2026-10-24), PR #852. The judge removed the `parked_*=NULL`
clause from `_spawn`'s `ON CONFLICT` upsert and the full suite (10649 tests) stayed green.
Closed by `tests/test_park.py::test_a_claim_clears_a_park_left_on_the_row`, which reuses
`tests/test_taskmodal_data.py`'s `_spawn` harness: it claims, parks the live row, fails it,
re-claims it, and asserts the new attempt is not parked.
