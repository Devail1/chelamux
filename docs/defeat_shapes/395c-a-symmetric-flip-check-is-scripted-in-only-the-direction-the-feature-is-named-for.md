## 395c. A symmetric flip check is scripted in only the direction the feature is named for

**Assertion form:** a check compares two runs and must treat a change in EITHER direction the
same way. In CMX-395 that check is `again.verdict != first.verdict`: a consistency re-run that
turns KILLED into SURVIVED, or SURVIVED into KILLED, is `flaky`. The feature was motivated by
one direction only: a "survived" caused by a stale `.pyc` turns into a needless rework round.
So every flip fixture scripted SURVIVED→KILLED, and every test asserted what that direction
changes, which is that the survivor stops blocking. No fixture ever re-ran a KILLED experiment
and got SURVIVED.

**Mutation that defeats it:** narrow the symmetric check to the one tested direction:
`… and again.verdict != first.verdict:` → `… and again.verdict != first.verdict and
first.verdict == SURVIVED:`. A KILLED→SURVIVED flip is now silently accepted as KILLED. With
no other blocker, the report comes out **clean** about a guard that did not hold on a second
run, and the suite stays green.

**Why this is distinct from [[357]] and [[395b]]:** 357 bounds a numeric interval on one side.
Here the domain is a pair of facts, and the bug is dropping one ordered pair. 395b is about the
helper's *other* return values (INVALID, contamination). Here both values are the success
values, but only one of their two orderings is exercised.

**Guard form that survives:** for any "these two differ" check, script every ordered pair the
feature must treat alike (A→B and B→A) and assert the outcome that the *untested* direction
changes. For KILLED→SURVIVED that means the experiment is flaky and the report is CANNOT VERIFY,
**not clean**, because a report whose only experiment was killed is clean by default.

**Found:** CMX-395 rework round 4 (2026-09-29), PR #547. It survived 4394 tests. It was closed by
`test_a_KILLED_then_SURVIVED_flip_is_flaky_too_and_the_report_is_NOT_clean`. The same round's
second survivor, the run row's `judge_detail` dropping its held-out count, is shape [[79]]:
a third render of a shape guarded in two places, where the only assertion on the row was
no-leak (absence only), and deleting the count also satisfies it. It was closed by asserting the
row's exact `judge_detail` in the end-to-end leak test and in the held-out-only block test.
