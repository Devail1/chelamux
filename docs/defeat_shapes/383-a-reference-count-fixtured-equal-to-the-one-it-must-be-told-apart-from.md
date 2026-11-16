## 383. A reference count fixtured equal to the one it must be told apart from

**Assertion form:** a verdict compares a run against a *reference* — here the judge's
collapse check, "a red run that ran far fewer tests than its baseline was taken down, not
tripped". The code picks between two references: the full suite's baseline (~12k tests) and
the selected ring's own expected count. The guard drives the path and asserts the verdict, but
every fixture uses one result (`4 passed`) as the full baseline, the subset, AND the ring.

**Mutation that defeats it:** pass the wrong reference — `_subset_baseline(ring)` → `baseline`.
With 4 == 4 == 4, "4 of the ring's 4" and "4 of the full suite's 4" are the same comparison,
so KILLED stays KILLED. In production every frontend ring kill (4 of 12k) would read as a
collapsed suite and become INVALID.

**Why it looks covered:** the test exercises the exact line and asserts an exact verdict for
both a red and a green ring. Only the *size* of the reference matters, and no fixture makes the
two sizes differ. It is a cousin of [[85b|shape 85b]]: the input never reaches the region where
the two choices disagree.

**Guard form that survives:** fixture the references at realistic, *different* sizes (a 12k full
baseline, a 4-test ring), assert that difference first, and pin both sides of the threshold: a
ring that ran all its tests is KILLED, and a ring that ran 1 of its 4 is INVALID. The wrong
reference turns the first INVALID; no reference at all turns the second KILLED.

A wiring twin, from the same round: a run copies `report.per_hour` into its result, and only the
renderer was tested, from a hand-built dict. Nothing ran `run()` and compared the rendered rate
to the report's, so `per_hour=None` survived. Wrap the real producer, give it a distinctive
value, and assert that value comes out the far end (result, payload and inbox line).

**Found:** CMX-383 rework round 1, PR #933. `chela/judge.py` `_measure` (ring confirmation) and
`chela/sweep_nightly.py` (`per_hour` into the notice). Fixed in
`tests/test_judge_select_frontend.py` (`FULL` baseline;
`test_ring_and_subset_are_adjudicated_against_their_own_counts_not_the_full_suites`) and
`tests/test_sweep_nightly.py`
(`test_a_swept_night_carries_the_batterys_measured_rate_into_its_notice`).
