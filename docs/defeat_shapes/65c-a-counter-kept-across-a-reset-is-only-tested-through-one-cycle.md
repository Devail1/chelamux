## 65c. A counter kept across a reset is only tested through ONE cycle

**Assertion form:** a row is partly reset at some lifecycle edge, and a keep-list says which
columns survive. One of the kept columns is a counter whose value only matters to the NEXT
cycle. CMX-65's requeued claim resets every column of the closed attempt except
`_REQUEUE_KEEP`, which includes `requeue_count`. That counter is what names the attempt after
the next requeue (`-r3`). Every requeue test ran exactly one cycle: requeue, claim, then assert
`-r2`. The first claim reads the counter *before* the reset runs, so its branch is right
whatever the reset does to the counter afterwards.

**Mutation that defeats it:** `_REQUEUE_KEEP | {...}` → `(_REQUEUE_KEEP - {"requeue_count"}) | {...}`.
The claim zeroes the counter after it has already been read. The one-cycle test still sees
`-r2` and stays green. In production a second requeue counts from 0 again, so the third
attempt takes `-r2`, which is the branch and the kept worktree of the attempt that was just
closed. That is the exact reuse CMX-65 forbids.

**Why the existing test doesn't catch it:** a value that is reset *after* it is consumed in
the same cycle can only be observed by the next consumer. A test that stops after one cycle
asserts on that cycle's output, which was computed from the pre-reset value. It is the
time-axis twin of shape 15 (a list tested with exactly the length the bug can't distort).

**Guard form that survives:** drive the lifecycle twice and assert on the second cycle's
output (`-r3`, worktree `CMX-33-r3`, and the `-r2` worktree untouched). Also assert the
counter's value on the row right after the first reset (`requeue_count == 1`), so a failure
points at the reset and not at the naming.

**Found:** `chela/dispatcher.py` `_spawn` + `_REQUEUE_KEEP` (CMX-65, PR #627, round 2).
