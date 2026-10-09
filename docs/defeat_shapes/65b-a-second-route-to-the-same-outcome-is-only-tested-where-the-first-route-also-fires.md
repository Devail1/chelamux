## 65b. A second route to the same outcome is only tested where the first route also fires

**Assertion form:** CMX-65's requeue makes a closed task claimable by TWO routes at once.
`close_run(..., requeue=True)` moves the Linear issue back to Todo (so the tracker's own
`claimable` READY filter lets it through), and the claim loop also hands the requeued ids
to `_claim_order` as retry ids (`_requeued_run_ids`), so the task is claimed whatever state
the issue is in. The only end-to-end test (`test_requeue_claims_a_fresh_attempt_on_a_new_branch_and_worktree`)
used a fake tracker on which the Todo move always succeeds. Both routes fired on every run,
so the test proved that *one of them* works, never that the second one is wired at all.

The same PR had the mirror image at the claim's own merged-PR guard. `close_run` already
refuses to requeue a merged run, so the claim loop's "a requeued row whose PR merged out of
band is never re-run" `continue` was only ever reached by a fixture that the earlier stage
had already stopped (shape 7c, from the other side).

**Mutation that defeats it:** `| _requeued_run_ids(conn, str(wf.path)))` → `| frozenset())`.
The requeue still lands and the Todo move still makes the issue READY, so the tick still
claims it and the suite stays green. In production, a Linear call that fails, or an issue a
human or an integration moves while the requeue is pending, leaves the task stuck in exactly
the silent stall CMX-65 was filed to remove. The second form: `continue` → `pass` on the
claim's merged guard, which no fixture ever reaches.

**Why the existing test doesn't catch it:** when two independent mechanisms both produce the
asserted outcome, a test where both are healthy cannot tell you which one did the work, and
deleting either one leaves it green. This differs from 337, where two ANDed *rejecting*
gates are exercised together. Here two ORed *enabling* routes are, and the redundancy is the
point of the design, not an accident of the fixture.

**Guard form that survives:** for each route, add a test that disables the OTHER route and
asserts the outcome still happens. Here that means stubbing `_tracker_requeue` to a no-op so
the issue stays In Review, then asserting that the next tick still claims `-r2`. For the
later-stage guard, build the state the earlier stage can't prevent: requeue first, then flip
`pr_state='merged'` on the row (a merge by hand on GitHub), and assert no spawn and no `-r2`
worktree.

**Found:** `chela/dispatcher.py` claim loop + `_requeued_run_ids` (CMX-65, PR #627, round 1).
