## 40d. One fixture per verdict reaches only one of the call sites that emit it

**Assertion form:** a guard that "every verdict is counted" builds a fixture with one item of
EACH verdict value (KILLED, SURVIVED, INVALID) and asserts the exact tally. It looks complete
because the verdict vocabulary is fully covered. But the code emits the same verdict from
SEVERAL call sites — here `_apply_experiments` records INVALID both for an experiment that
fails to parse (malformed) and for one whose file escapes the worktree — and the one INVALID
fixture item (`file="../outside.py"`) reaches only the second. The first call site's
recording is never executed by any test.

**Mutation that defeats it:** route the unreached call site around the counting hook
(`_record(Outcome(...malformed...))` → `outcomes.append(Outcome(...))`) — the verdict still
lands in the report, it just never reaches the live tally, and the fixture's lone INVALID
still counts from the other site.

**Guard form that survives:** enumerate the fixture by EMITTING CALL SITE, not by verdict
value: one item per `_record(...)` (here add a malformed item, `{"guard": …, "file": …}` with
no diff, beside the path escape), assert the tally progression step by step, and assert the
invariant the issue actually states — the final tally sums to the number of experiments
processed (`killed + survived + invalid == total`) — so ANY uncounted item, from any site,
breaks it.

**Found:** CMX-40 rework round 3 (2026-10-09), judge verdict on PR #622. The mutation above
stayed green on the full suite (6382 passed). Closed by adding a malformed experiment to
`test_judge_run_writes_the_tally_the_label_and_the_window` and asserting the tally sums to
`total`.
