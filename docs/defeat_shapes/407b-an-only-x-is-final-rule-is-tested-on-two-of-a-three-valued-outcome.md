## 407b. An "only X is final" rule is tested on two of a three-valued outcome

**Assertion form:** code short-circuits on ONE value of an enum and does more work for the
rest — "a KILLED subset is final; anything else is confirmed on the full suite". The tests
feel exhaustive because they cover both sides of the story the author had in mind: a KILLED
case (asserted *not* re-run) and a SURVIVED case (asserted re-run). But the enum has a third
value — INVALID, a subset that collapsed rather than tripped a guard — and no fixture ever
produces it.

**Mutation that defeats it:** rewrite the condition from the value that is final to the value
that is not: `if verdict == KILLED` → `if verdict != SURVIVED`. On KILLED and SURVIVED the two
agree exactly; they differ only on INVALID, which is never measured. Green across 4,609 tests
(CMX-407 round 2).

**Guard form that survives:** enumerate the enum, not the story. Write one fixture per value
— here a module-level `raise` that takes the selected test down at collection, which is the
cheapest real way to make a subset INVALID — and assert the branch on each. Better still, put
all of them in ONE battery and assert the invariant as a count: every non-final value costs
exactly one confirmation run, so `calls.count(full) == number of non-KILLED experiments`.

**Found:** CMX-407 round 2 (2026-10-01), PR #558 — `chela/judge.py::_measure`. Closed by
`tests/test_judge_select.py::test_a_subset_that_COLLAPSED_is_re_run_on_the_full_suite_never_final`
and `test_every_measured_verdict_but_KILLED_is_confirmed_on_the_full_suite`.

**Related:** the same round's other survivors are known shapes. `total_seconds = None` at the
`judge_run` call site survived because the header was only tested from a hand-built `Report`
— shape [50](50-a-renderer-is-proven-against-hand-called-arguments-the.md). The dirty-worktree
cache guard and relative-import resolution survived because every fixture was a clean tree
with absolute imports — [407](407-an-environment-gated-branch-is-never-reached-because-no-fixture-has-the-environment.md)'s
"no fixture has the environment", one level down.
