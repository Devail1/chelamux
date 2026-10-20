## 282. A per-key budget's reset on a new key is never reached, because the test stops at the key change

**Assertion form:** a counter is a budget per key (re-runs per head sha), and the code that
starts a new key zeroes it. The test drives the first key until its budget is spent, then
switches the key (`fake.sha = "newpush1"`) and asserts one thing about the new key (it is not
judged), and stops. CMX-282's `ci_flake_reruns` is zeroed by CMX-255's first re-run of a new
head; the test never let the new head go red a second time.

**Mutation that defeats it:** drop the reset — `"ci_rerun_note=?, ci_rerun_at=?,
ci_flake_reruns=0 WHERE task_id=?"` → `"ci_rerun_note=?, ci_rerun_at=? WHERE task_id=?"`. The
new head inherits the spent counter, so its first registered-only red skips straight to
`ci_known_flake` with no re-run at all. Green, because nothing after the key change reads the
counter.

**Why it looks covered:** the budget's EXHAUSTION is tested (two re-runs, then the judge), and
the key change is tested (the new head is not judged). The reset sits between the two and is
consumed only by the new key's NEXT red, which no test produces. It is shape 65c (a counter
across a reset, only one cycle driven) for a counter that must be cleared rather than kept.

**Guard form that survives:** spend the first key's budget, switch the key, then spend the
second key's budget too, asserting the counter right after the switch (`== 0`) and the full
re-run count on the second key (`== FLAKE_RERUNS` re-runs before it proceeds).

**Found:** CMX-282 rework round 1, PR #797. `chela/dispatcher.py` CMX-255 re-run write;
fixed in `tests/test_ci_known_flake.py::test_a_new_head_gets_its_own_known_flake_reruns`.
