## 395d. A filter's rejected rows are in the fixture, but lack the field the filter protects

**Assertion form:** a filter drops a class of rows before an aggregation reads them, and a test
includes a row of that class to show it is dropped. In CMX-395, `private_metrics` computes
`rounds = [r for r in records if not r.get("stale")]`, and the metrics test includes a stale
record. But that record carried only `state` and `visible`, which were the fields the test
author had in mind. It carried no `consistency`, so the flip-rate sums would come out the same
whether they read `rounds` or `records`. The consistency sampler had the same problem:
`if o.verdict in facts` drops INVALID outcomes, but no first pass in the suite was ever INVALID.

**Mutation that defeats it:** keep the filter and point a second consumer at the unfiltered
list (`for r in rounds` → `for r in records` in the flip sums), or delete the filter clause
(`if o.verdict in facts` → nothing). The rejected row either isn't there, or contributes a
zero to the field that consumer sums, so the result doesn't change. The suite stays green.

**Why this is distinct from [[42]] and [[395b]]:** 42 is a fixture that starts in the right
state. Here the fixture does contain the wrong row, but that row is "wrong" only on the axes
the test author thought about. 395b is about a helper's own non-success return values. Here
the filter is fine, and what goes untested is a consumer's choice of which list to read.

**Guard form that survives:** for each consumer downstream of a filter, give the rejected row
a non-zero value in *that consumer's* field and assert the total leaves it out. A stale record
with `consistency: {"sampled": 5, "flipped": 3}` must not move the flip rate, and an INVALID
first pass must not count as `sampled`.

**Found:** CMX-395 rework round 5 (2026-09-29), PR #547. It survived 4398 tests. It was closed by
`test_metrics_ignore_a_STALE_rounds_consistency_flips` and
`test_an_INVALID_first_pass_does_not_consume_a_consistency_rerun_slot`. The same round had two
more survivors. The private record's `state` hardcoded to clean was shape [[42]]: every record
the suite wrote came from a report that was already clean or never read back. The task-id
sanitiser was shape [[73]]: no fixture task id contained a `/`.
