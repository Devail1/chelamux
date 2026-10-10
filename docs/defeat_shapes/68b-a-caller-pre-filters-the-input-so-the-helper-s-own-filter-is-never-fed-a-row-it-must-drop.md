## 68b. A caller pre-filters the input, so the helper's own filter is never fed a row it must drop

**Assertion form:** a pure helper carries an exclusion in its own predicate —

```python
def closed_run_stalls(runs, ready_ids):
    return [... for r in runs
            if r.get("status") == "closed" and not r.get("requeue_pending")
            and r.get("task_id") in ready]
```

— and the negative control for that exclusion ("a requeued run is not a stall") is written
end-to-end through ONE caller: `chela doctor`'s scan. But that scan drops `requeue_pending`
rows *before* it calls the helper. The test is green, reads exactly like the invariant, and
never once hands the helper a row its clause has to reject.

**Mutation that defeats it:** delete the clause from the helper
(`... and not r.get("requeue_pending")` → nothing). The doctor test still passes — the
caller's pre-filter already did the work. A second caller (the dashboard's Work board) that
passes EVERY run straight in now flags a requeued task as "closed run blocks this task", and
no test reaches that caller with a requeued run.

**Why it slips through:** this is the inverse of [[60|shape 60]]. There, a helper's contract
is proven at one call site and a sibling bypasses it. Here, the helper IS called at both
sites, but the tested site makes the helper's clause redundant. A redundant clause is dead
code to that test, so deleting it changes nothing the test can see.

**Guard form that survives:** pin the exclusion at the helper itself (a unit test feeding it
the same row with and without the excluded field), AND drive the negative control through
every caller that does NOT pre-filter. Before you call a negative control done, ask: "on this
path, does anything upstream already remove the row I'm testing?" If something does, the
test is proving the upstream filter, not the clause.

**Found:** `chela/dispatcher.py`'s `closed_run_stalls` (CMX-68, PR #634, round 1).
`test_a_requeued_task_is_not_a_stall` went only through `runtime_truth`'s
`_closed_run_stalls_scan`, which skips `requeue_pending` rows itself. Closed by
`test_closed_run_stalls_skips_a_requeue_pending_run` (the helper, directly) and
`test_the_work_board_never_flags_a_requeued_closed_run` (the caller with no pre-filter). The
same round's other survivor, a close route tested only with `requeue: true`, is
[[363|shape 363]] (only the true case mounted). It was closed by
`test_the_board_close_route_without_requeue_true_is_a_plain_close`.
