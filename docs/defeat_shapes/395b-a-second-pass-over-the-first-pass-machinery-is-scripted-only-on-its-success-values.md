## 395b. A second pass over the first pass's machinery is scripted only with that machinery's success values

**Assertion form:** a feature re-runs part of an earlier pass through the SAME helper — here
CMX-395's consistency check, which re-applies a sample of mutation experiments through
`_apply_experiments` and compares each second verdict with the first. The helper can return
more than the two values the feature is about. It has a third verdict (`INVALID`: the suite
timed out or stopped running) and a failure channel (`contamination`: the file could not be
restored). The tests script the re-run's suite with exit codes only, `0` and `1`, so the
second pass only ever produces `KILLED`/`SURVIVED` and always restores cleanly. Every test is
honest about the flip it checks: SURVIVED→KILLED is flaky, SURVIVED→SURVIVED blocks.

**Mutation that defeats it:** anything that treats the helper's other outputs as if they were
success values:

- `if again.verdict in facts and again.verdict != first.verdict:` →
  `if again.verdict != first.verdict:`. A SURVIVED→INVALID re-run now counts as a flip, so
  a stable survivor is marked flaky and stops blocking. That is exactly the weakening the
  feature was forbidden to introduce, and no fixture ever produces an INVALID re-run.
- `if contamination:` → `if False and contamination:` on the re-run's return. The report
  keeps its blocking verdict about a worktree that still carries a mutation, and no fixture
  makes the re-run's restore fail.

**Why this is distinct from [[379]] and [[373b]]:** 379 is a tri-state arm that the first
walk never populates. Here the first pass's INVALID and contamination arms are already
tested, in their own tests. The gap is that the SECOND caller inherits them, and its tests
were written against the only two values the new feature is named after. 373b is a new error
branch next to a tested sibling; here the error branch is old, and its new consumer is the
one that went unscripted.

**Guard form that survives:** when a feature calls an existing helper again, list every value
and channel that helper can return: each verdict, each error string, each exception. Then
script the second call to produce each one at least once. The minimum is a SURVIVED→INVALID
re-run that must still block and must not be counted as flipped, and a re-run whose restore
raises and must produce CANNOT VERIFY. Fail only the second pass (key the failure on the
call count), so that the test proves the re-run's handling and not the first pass's.

**Found:** CMX-395 rework round 3 (2026-09-29), PR #547. Both mutations survived 4377 tests.
They were closed by `test_an_INVALID_rerun_is_NOT_a_flip_and_the_stable_survivor_still_BLOCKS`
(the re-run's `SuiteResult(ok=False)`) and
`test_a_consistency_rerun_that_cannot_RESTORE_its_file_is_CANNOT_VERIFY` (a `Path.write_text`
that raises only after the third suite call).
