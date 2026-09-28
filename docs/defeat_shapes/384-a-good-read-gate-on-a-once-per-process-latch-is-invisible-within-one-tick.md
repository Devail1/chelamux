## 384. A good-read gate on a once-per-process latch is invisible within one tick — the gated action is a no-op on the bad read, and only the LATCH it spends shows up, one tick later

**Assertion form:** a migration runs once per process (`if cond and not_failed and key not in
_done: migrate(conn, mapping(open_tasks)); _done.add(key)`), and the test drives it on a
single GOOD tick, asserting the rows moved. A second test of the migration helper calls it
directly. Nothing drives a FAILED read.

**Why that doesn't guard the `not_failed` clause:** on a failed read the adapter returns `[]`,
so `mapping([])` is empty and `migrate` is a no-op — dropping the clause changes NOTHING
observable on that tick (the shape-[[46]] half: the gated action is idempotent at rest). What
it does change is the non-idempotent side effect riding with it: the once-per-process latch
gets spent on the empty run. The damage only lands on the NEXT, good tick, which skips the
migration, so the unmigrated row reads as "removed from source" and is reconciled away while
the task is claimed again. A single-tick test — good or failed — cannot see a latch that only
matters across ticks.

**Mutation that defeats it:** delete `not tracker_read_failed and` from the gate (CMX-384,
`chela/dispatcher.py`). Suite green.

**Guard form that survives:** drive the SEQUENCE the latch exists for — tick 1 with a failed
read (assert nothing moved), tick 2 with a good read — and assert the tick-2 outcome (row on
the new id, still `running`, no second claim). Isolate tick 1 from any OTHER path that could
act on it (here `_claim_order` re-reads origin and would claim even on a failed local read),
so the only thing carried into tick 2 is the latch's state.

**Sibling in the same verdict** (already shape [[373b]] / [[310]]): the same PR changed BOTH
arms of `strike_lines` (`[ ]` and `[x]`) to hash the bare title, but tested only the `[ ]`
arm's `"struck"` result; the `[x]` arm's `"already"` result had no assertion of its own.
