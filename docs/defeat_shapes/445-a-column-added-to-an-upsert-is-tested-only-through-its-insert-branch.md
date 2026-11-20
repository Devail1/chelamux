## 445. A column added to an upsert is tested only through its INSERT branch

**Assertion form:** a new column is threaded into an upsert:
`INSERT INTO runs (…, priority) VALUES (…, ?) ON CONFLICT(task_id) DO UPDATE SET …,
priority=excluded.priority`. The test claims a fresh task and reads the column back off the
row (`resolve_run(...)["priority"] == priority`), parametrized over a few values. It reads as
"the claim records the tracker's priority".

**Mutation that defeats it:** drop the new column from the `DO UPDATE SET` list only —
`priority=excluded.priority, tracker_edge=NULL` → `tracker_edge=NULL`. A run that is claimed
AGAIN (a re-queue, a reclaim after the issue was re-prioritised Medium → Urgent) keeps the
first claim's value, and queues at the stale priority.

**Why it looks covered:** an upsert is two writers in one statement, and a fresh row only ever
reaches the first. The column list and the `VALUES` tuple are both exercised, so the column
looks wired end to end; the `DO UPDATE` list is a separate, hand-maintained enumeration that
no fresh-row fixture can see. It is [[88|shape 88]]'s cousin: there the second write was
tested only from a row already holding the reset value; here it is never reached at all.

**Guard form that survives:** claim the same task twice with DIFFERENT values for the new
column (including a value → NULL transition), and assert the row carries the second one after
each claim. Any column added to an upsert's insert list gets a test that reaches its conflict
branch with a value that differs from the first write.

**Found:** CMX-445 rework round 1, PR #963 (a held-out survivor; this was found by the rework's
own hand-mutation pass over the diff). `chela/dispatcher.py` `_spawn()`'s `runs.priority`;
fixed in `tests/test_risk_level.py`
(`test_a_re_claim_takes_the_trackers_current_priority`). The same pass also strengthened, in
`tests/test_suite_slot_priority.py`: the doctor's "a run waiting for the slot is never past
its wall" conjunct, previously fixtured only where the subtracted queue time already put it
under the wall ([[337|shape 337]]); a clock-ahead clamp whose fixture had no queued total to
eat into; the 60 s announcement boundary; the yield's re-wait dropping its `on_wait` hook
(the fixture's hook was `None`, so `None` and the hook coincided — [[342|shape 342]]); the
yield re-queueing at its ORIGINAL claim time, whose end-to-end fixture let the competing
equal arrive only AFTER the yield, so "claim time" and "now" ordered it identically (also
shape 342); a rank bound with no fixture above the fallback value it bounds to; and the
record-write failure path leaking its slot fd.
