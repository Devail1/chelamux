## 260. A narrowing conjunct is never fed the input it exists to tell apart

**Assertion form:** a condition is built from a broad test narrowed by a second one —
`prev is not None and prev.poll() is None` ("a previous scan that is STILL RUNNING"),
`rules_done - errors` ("rules that finished WITHOUT raising"). The tests feed the broad case
(no previous child / a rule never reached) and the case where both halves agree (a running
child / a clean rule), and assert the outcome. The narrowing half is also often already proven
at a DIFFERENT consumer of the same derived value, which makes it look covered.

**Why that's a gap:** the narrowing half only changes the outcome on the input where the two
halves disagree — a child that EXISTS but has FINISHED; a rule that FINISHED but RAISED. No
fixture builds that input at this consumer, so the narrowing half can be dropped (or the
derived set swapped back to the un-narrowed one) and every assertion still holds. Coverage at
a sibling consumer does not transfer: `record()` used `clean` twice — to keep dedupe entries
and to keep CPU samples — and only the first was ever driven with a rule that raised.

**Mutation that defeats it:** `if prev is not None and prev.poll() is None:` →
`if prev is not None:` (the daemon then never scans again after its first scan exits);
`if "hung_test" in clean else` → `if "hung_test" in result["rules_done"] else` (a hung_test
that raised overwrites the CPU baseline it never measured). Suite green.

**Guard form that survives:** for every `A and B` / `X - Y`, add the row where A holds and B
does not (a finished child: `poll() == 0` ⇒ a NEW Popen is started and returned; a rule in
`rules_done` AND in `errors` ⇒ the previous state is kept) next to the rows that already pass
— and do it at EACH consumer of a derived value, not once per value. A parametrize over
(raised / never reached / clean) with the expected outcome spelled out reads the whole truth
table in one place.
Related: [[86b|shape 86b]], [[373b|shape 373b]].

**Found:** CMX-260 rework round 2 (2026-10-19), PR #764. Closed in `tests/test_ops_scan.py` by
`test_spawn_is_detached_niced_and_not_doubled` (a finished child is replaced) and
`test_cpu_samples_are_resampled_only_by_a_clean_hung_test`.
