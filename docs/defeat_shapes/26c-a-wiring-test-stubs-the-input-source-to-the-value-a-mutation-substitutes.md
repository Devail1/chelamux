## 26c. A wiring test stubs the input source to the very value a mutation substitutes

**Assertion form:** a daemon-level test proves a new step is wired into the tick — `inbox.tick`
really calls `stall_alerts.evaluate(store, statuses, runs, live_heads, now)` — by driving the
tick and asserting a push. To keep the tick quiet, the harness stubs every input source it does
not care about to its empty value: `dispatcher.list_runs → []`, no live heads, the real clock.

**Mutation that defeats it:** replace one argument at the call site with that same empty value
(`runs` → `[]`). The stub already makes the argument `[]`, so the substitution changes nothing
the test can observe. Every assertion that rides on the OTHER inputs (the held queue, the
orchestrator's status) still passes. The evaluator's own unit tests feed it runs directly, so
they never see the call site either. The suite stayed green (6148 passed) while the daemon
stopped feeding runs to the clean-unmerged alert at all.

**Guard form that survives:** for each argument the call site forwards, drive the tick with
that source returning a NON-empty value that the push depends on, and assert the push. Here
that means a dispatcher reporting a judge-clean run (fires once past the threshold, on the
tick's own clock), a live GitHub head that disagrees with the row's cached head in BOTH
directions (fires only via the live read; silent when the live head moved), and a clock the
test sets, crossed 1s before and at the threshold. An argument whose source is only ever stubbed
empty is not wired as far as the suite knows.

**Related:** [[02|entry 2]] (fixture parked on a default value) — this is the same idea at
the wiring level, where the "default" is the stub the harness installs for convenience.
[[07|entry 7]] (two callers, one guarded) is the reverse case: here one call site has several
inputs, and only some of them are guarded.

**Found:** `tests/test_stall_alerts.py` `wired` fixture (CMX-26, round 4, PR #610). Closed by
the `wired_runs` fixture and the `test_tick_feeds_*` / `test_tick_never_alerts_clean_*` tests.
