## 68c. A rework pins only the clause the verdict named, and leaves its sibling clauses unfed

**Assertion form:** round 1 of [[68b|shape 68b]] found that `closed_run_stalls`'s
`requeue_pending` clause was never handed a row it had to drop, because doctor's scan
pre-filters. The rework did exactly what the verdict asked. It added a unit test for that one
clause, plus a board-side negative control with a requeued run:

```python
def closed_run_stalls(runs, ready_ids):
    return [... for r in runs
            if r.get("status") == "closed" and not r.get("requeue_pending")
            and r.get("task_id") in ready]
```

The predicate has three clauses, and the same pre-filter in doctor
(`status == "closed" and not requeue_pending`) makes **two** of them redundant on the doctor
path. Only the named one got a test.

**Mutation that defeats it:** `r.get("status") == "closed"` → `r.get("status") != "merged"`.
Doctor still passes, because its pre-filter keeps only closed rows. The board test only seeds a
closed run, so a status filter that lets every status through changes nothing it can see. On the
real board, every active, in-review, done or failed run on a Todo task is now flagged
"closed run blocks this task".

**Why it slips through:** a verdict reports the **one** mutation that survived, not every one
that would. Its finding is about a *shape*: a caller makes the helper's filter dead code. A fix
aimed only at the named clause closes that instance and leaves every sibling clause on the same
dead path open. The next round finds the next clause, one round at a time.

**Guard form that survives:** when a verdict says "this clause is never exercised because a
caller pre-filters", list **every** clause that pre-filter makes redundant, and pin each one at
the helper (one row per clause, differing only in that field, with a positive control in the
same test). Then drive one end-to-end negative control through the caller that does not
pre-filter, seeded with a row that violates **each** clause. For a status clause, parametrize
over every status the system can produce (`NOT_CLAIMABLE`, `failed`, …), not just the one or
two that come to mind.

**Found:** `chela/dispatcher.py`'s `closed_run_stalls` (CMX-68, PR #634, round 2). Closed by
`test_closed_run_stalls_flags_only_a_closed_run` (parametrized over every run status) and
`test_the_work_board_flags_only_the_closed_run_never_an_in_flight_or_finished_one` (an active,
in-review, done and failed run, each on a ready task, beside one real stall). The same round
also pinned the third clause, READY and not merely open
(`test_a_closed_task_open_but_not_ready_is_not_a_stall`, a Backlog issue).
