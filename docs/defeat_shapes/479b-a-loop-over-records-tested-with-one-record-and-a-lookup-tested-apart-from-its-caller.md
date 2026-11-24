## 479b. A loop over records is tested with one record; a lookup is tested apart from its caller

**Guard claimed:** `requeue_reaped_waiters` skips a record that must not be re-queued and
handles the others; the slot obeys the blocked hours of the worktree it runs in.

**What the guard actually asserted:** every skip reason had a test, and every test had exactly
**one** record, so `continue`, `break` and `return` were the same thing. `blocked_windows` was
tested against many `WORKFLOW.md` files and `slot()` against many clocks, but every `slot()`
test ran in a directory with no `WORKFLOW.md`: the default window, whatever path was passed.

**Mutation that defeats it:**

```diff
  if suite_guard.requeued_recently(cwd):
-     continue
+     break
```

```diff
- windows = blocked_windows(cwd)
+ windows = blocked_windows(d)
```

**Why it looks covered:** each branch is executed and each helper's table is complete. What is
missing is the second element of the loop and the non-default value across the call boundary.
The same gap hid an accumulator (`+=` vs `=`: one window per wait) and a prune that kept
"everything in date" (asserted only that the old entry went).

**Guard form that survives:** for every skip, put the skipped record FIRST and a good one
after it, and assert the good one was handled. For every lookup, drive the caller with a value
that differs from the default in both directions (blocked where the default is free, free where
the default is blocked). For every accumulator, go round twice; for every prune, assert what
stays.

**Found:** CMX-479 rework round 2, PR #1007 (audit after an undisclosed held-out survivor).
Closed by `test_one_record_that_is_not_re_queued_stops_no_other`,
`test_the_slot_itself_obeys_its_own_worktrees_windows`,
`test_two_windows_in_one_wait_are_both_excused_and_nothing_else_is` and
`test_mark_requeued_keeps_every_entry_still_in_date` in `tests/test_suite_blocked_hours.py`.
