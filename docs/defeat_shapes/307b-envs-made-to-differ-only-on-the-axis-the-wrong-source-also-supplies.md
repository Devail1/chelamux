## 307b. Two envs made to differ only on the axis the wrong source also supplies

**Assertion form:** [[307|shape 307]]'s fix. One launch env (`{**env, **_bus_added(env)}`)
goes to both the scope probe and the real `systemd-run`, and the `bus_stripped` tests make
the suite env and the operator env differ the way production does: the suite env lacks
`XDG_RUNTIME_DIR` / `DBUS_SESSION_BUS_ADDRESS`, and a fake `systemd-run` logs the bus vars of
every call. The tests assert that the probe and the launch both reached the bus.

**Mutation that defeats it:** `launch = {**env, **_bus_added(env)}` → `launch =
dict(os.environ)`, which puts the probe back on the operator's env, the bug 307 fixed. The
suite stays green (10104 passed). The only axis the fixture moves is the bus vars, and
`os.environ` carries exactly those. The fake also logs only those, so a probe under the
wrong env looks the same as a probe under the right one. The suite env and `os.environ`
differ in nothing else the test reads.

**Why it looks covered:** the fixture does make the two envs differ, and in the way the bug
report described. But "the probe got the bus" is true under both the correct launch env
and the operator's env. The assertion cannot tell the two sources apart.

**Guard form that survives:** give each source a variable that only it has, and assert the
probe's whole env. A suite env with its own `PATH`, plus an operator-only var set in
`os.environ`, plus a suite `XDG_RUNTIME_DIR` that the operator's must override. Then the
expected probe env is `{suite PATH, operator bus vars}`, with no operator-only var. Both
`dict(os.environ)` and the bare `env` fail it. This is [[391|shape 391]] with an env
instead of a config source: nothing in the fixture held the two sources apart on an axis
the assertion read.

**Found:** CMX-307 judge round 2, PR #839. Closed in `tests/test_suite_guard.py` by
`test_the_probe_runs_under_the_suite_env_plus_the_bus_not_os_environ`.
