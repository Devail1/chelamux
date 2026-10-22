## 307. A capability probe runs under a different env than the launch it vouches for

**Assertion form:** a wrapper only engages when a probe says it can: `_scope_works()` runs
`systemd-run --user --scope -- true` and, on success, `bounded()` wraps the suite in a
memory-capped scope. The probe is tested (argv pinned, every failure fails closed), the
wrapper's argv is tested, and a real-systemd test drives `judge.run_suite` end to end. Each
is green, so "the suite runs under its ceiling wherever the probe passes" reads as covered.

**Mutation that defeats it:** none needed. The probe inherited the caller's `os.environ`;
the launch ran under the suite's env, which `_suite_env` builds by DROPPING every var
`chela.env` set. On a daemon started under `env -i`, `XDG_RUNTIME_DIR` exists only because
`chela.env` set it, so the probe found the user bus and the launch did not (`Failed to
connect to bus: No medium found`). Every judged suite came back CANNOT VERIFY. The agent's
own self-check passed because its shell exports `XDG_RUNTIME_DIR` for real, and so did every
test: each ran in a shell where both envs agreed, which is the one case the bug cannot show.

**Why it looks covered:** the probe is a real launch, not a `which`, so it feels like proof.
But it proves the launch works only under the env the probe ran in. A test environment where
the two envs coincide makes the gap invisible, the same way [[93|shape 93]]'s override is
invisible where the default resolves to the same path.

**Guard form that survives:** build ONE launch env (the suite's env plus what the wrapper
needs) and pass it to both the probe and the launch. Then test with the two envs made to
differ the way production makes them differ: strip the variable through the real stripping
path (`config.ENV_FILE_VARS` → `_suite_env`), put a fake `systemd-run` on `PATH` that fails
exactly as the real one does without the bus and logs the env of every call, and assert that
the probe AND the launch both saw the bus while the suite itself still saw its stripped env.
Keep a real-systemd test with the same stripping, so the wrapper is checked against systemd
itself where one exists.

**Found:** CMX-307 judge round 1, PR #839. Fixed in `chela/suite_guard.py` (`_scope_works(env)`,
`bounded(test_cmd, env)`, `_bus_added`) and `chela/judge.py` (`run_suite` builds the suite env
before the scope); guarded by the `bus_stripped` tests in `tests/test_suite_guard.py`.
