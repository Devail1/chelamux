## 260b. A loop variable handed back to a periodic call is never handed back, because the cadence gate admits one call per test

**Assertion form:** the daemon starts the ops scan every 30 min and keeps the returned child
in a loop variable, `ops_child = ops.spawn(ops_child)`. Passing that child back is the whole
"never doubled" guarantee: `spawn()` only declines to start a second scan when it is handed
one that is still running. The wiring test ran TWO real `cmd_run` ticks with a spy on
`ops.spawn` and asserted `spawned == [None]`, which reads as "once per interval". The unit
test of `spawn()` itself proved a running child passed in is returned unchanged.

**Why that doesn't guard the wiring:** the second tick falls inside the 30-min interval, so
the spy sees exactly one call, and the first call's argument is `None` whatever the loop
passes. The variable that carries the child between passes is never read by any call a test
can observe. The `spawn()` unit test proves the helper honours the argument, not that the loop
supplies it.

**Mutation that defeats it:** `ops_child = ops.spawn(ops_child)` → `ops_child = ops.spawn(None)`
(`chela/main.py`, CMX-260). Suite green. In production every 30-min tick starts a fresh scan
even while the last one is still running.

**Guard form that survives:** make the gate due on every tick (here `ops.INTERVAL_S = 0`),
have the spy return a DISTINCT value per call, and assert the full argument sequence
(`[None, "child-1", "child-2"]`). Two calls is the minimum. A third makes sure the variable is
re-assigned each pass, not just set once.

**Found:** CMX-260, PR #764, rework round 3. Closed by the INTERVAL_S=0 block in
`tests/test_ops_scan.py::test_the_daemon_spawns_the_scan_once_per_interval_and_survives_its_crash`.

**See also:** [[83|shape 83]] covers state a function records for its own next call, which is
never consumed by a second call. This is the loop-level version, where the test can't make a
second call because of the interval.
