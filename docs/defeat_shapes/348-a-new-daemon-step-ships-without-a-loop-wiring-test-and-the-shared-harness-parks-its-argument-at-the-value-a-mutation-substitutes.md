## 348. A new daemon-loop step ships without a loop wiring test, and the shared harness parks its argument at the value a mutation substitutes

**Assertion form:** `keeper_schedule.Scheduler.tick(workflows)` was covered by unit tests of the
seam itself: hold, in-flight, activity check, state file, spawn argv. Every sibling step in
`cmd_run`'s loop (the nightly sweep, the ops scan, maintenance, doctor) also has a test that
drives one real pass of the loop and checks that the call was made. The keeper step had none.

**Mutation that defeats it:** `keeper_sched.tick(DISPATCH_WORKFLOWS)` → `keeper_sched.tick([])`.
The daemon stops scheduling keeper runs for every workflow, and every seam test stays green
because none of them run `cmd_run`.

**Trap waiting for the obvious fix:** the shared one-tick harness
(`tests/test_context.py::_run_one_daemon_tick`) sets `main.DISPATCH_WORKFLOWS = []` to keep the
dispatcher quiet. A wiring test that reuses it unchanged and asserts
`tick_arg == main.DISPATCH_WORKFLOWS` passes under the mutation, because `[] == []`. This is
[[26c|entry 26c]] at the daemon level: the harness stub equals the substituted value.

**Guard form that survives:** after the harness runs, point `DISPATCH_WORKFLOWS` at a NON-empty
list (and stub `dispatcher.poll_interval` to `inf` so the dispatch loop skips those paths).
Swap `keeper_schedule.Scheduler` for a recorder and assert exactly one tick whose argument
`is` that list and equals it.

**Related:** [[358|entry 358]] (a new family member skips the per-member wiring assertion its
siblings have), [[26c|entry 26c]] (the harness stub equals the mutated value).

**Found:** CMX-348 round 1, PR #900. Closed by
`tests/test_keeper_schedule.py::test_the_daemon_loop_ticks_the_keeper_schedule_with_the_dispatch_workflows`.
