## 23b. A second call to the same sync in one pass masks the first

**Assertion form:** a function runs at two points in one pass, for example once before the
gates and once after the work the gates allow. The test drives the whole pass (`tick()`) and
asserts the outcome: the tracker reached In Progress, written once. The assertion is real, and
it does fail when the function itself breaks.

**Mutation that defeats it:** remove the *earlier* call site
(`summary["tracker_transitions"] = _sync_tracker_states(...)` → `= 0`). The fixture's pass also
takes the later call, because it dispatched something, and that call produces the same outcome
one step later. Nothing observes *when* the write landed, so the suite stays green. This is
not [[07|shape 7]], where no fixture reaches the other caller. Here the fixture reaches both,
and the redundancy is what hides the gap.

**Guard form that survives:** drive the pass down a route that reaches **only** the call site
under test. Find the condition that skips the other call and set it. In the CMX-23 case,
the post-dispatch sync runs only `if summary["dispatched"]`, so the fix holds the queue
(`hold.take`) with a run already parked in review. The pre-gate call is then the only thing
that can write In Review. Assert the outcome on that route, and give each call site its own
route.

**Found:** CMX-23 rework round 1 (2026-10-07), PR #607. `tick()` syncs the Linear state before
the dispatch gates (so a held or blocked queue still moves a PR-opened run to In Review) and
again right after a claim (so the claim lands In Progress in the same tick). Every tick-driven
test dispatched, so the post-claim call covered for a missing pre-gate one. Fixed by
`test_the_tick_drives_the_tracker_even_when_dispatch_is_held` and by the tick-driven bounce
test in `tests/test_linear_workflow_states.py`.
