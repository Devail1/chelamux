## 307c. The fix pins the launch, but not the sibling call that must reach the same place

**Assertion form:** [[307b|shape 307b]]'s fix. The probe and the real `systemd-run` both get
one launch env (the suite's `env` plus the operator's bus vars), and tests assert that whole
env for each of them.

**Mutation that defeats it:** in `bounded()`'s `finally`, `_reap(unit, {**env, **added})` →
`_reap(unit, env)`. The cleanup `systemctl --user kill` now runs under the bare suite env,
which has no user bus, so it can never reach the scope it is meant to kill. The suite stays
green (10105 passed). `test_reap_pins_its_argv_and_never_raises` calls `_reap` directly with
no env, and the end-to-end tests use a fake `systemd-run` and never look at the reap.

**Why it looks covered:** the invariant "every call into the user's systemd gets the
operator's bus" was fixed and pinned at the two call sites the bug report named. The third
call site, the cleanup, shares the invariant but sits in a `finally`. No test reads what it
was handed.

**Guard form that survives:** list every call that must reach the same external endpoint
(probe, launch, cleanup) and pin the env each one receives, with the same differing-source
fixture as 307b. A fix to one site of an invariant is not done until each sibling site has
its own assertion.

**Found:** CMX-307 judge round 3, PR #839. Closed in `tests/test_suite_guard.py` by
`test_bounded_reaps_over_the_same_bus_it_launched_on`.
