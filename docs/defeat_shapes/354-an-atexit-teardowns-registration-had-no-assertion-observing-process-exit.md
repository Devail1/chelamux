## 354. An atexit teardown's registration is the only thing that guards a claimed cleanup, and nothing in the suite ever observes an actual process exit

**Assertion form:** `tests/conftest.py` points `PM2_HOME` at a throwaway directory for the
whole test session (issue #466 — every unmocked `pm2` subprocess this suite spawns must
never reach the operator's real `~/.pm2` God Daemon) and registers a teardown,
`atexit.register(_kill_sandbox_pm2_daemon)`, so that throwaway daemon is killed and its
directory removed when the suite process exits — "so nothing survives the run." The two
tests that existed for this (`tests/test_pm2_home_isolation.py`) both assert things that are
true *while the suite is running*: that `PM2_HOME` resolves away from the real home, and
that a bare `pm2 jlist` subprocess never sees the operator's live `chela-*` service names.
Neither one runs, or waits for, or otherwise observes a process exit — so neither one can
tell the difference between the teardown actually being wired and the teardown never firing
at all.

**Mutation that defeats it:** `atexit.register(_kill_sandbox_pm2_daemon)` →
`atexit.register(lambda: None)`. The registration is a statement with no return value and no
observable side effect anywhere else in the running process — every other test in the suite
imports `conftest` once, reads `SANDBOX_PM2_HOME`/`os.environ["PM2_HOME"]` (both set by
lines that run *before* the `atexit.register` call and are untouched by this mutation), and
never triggers an interpreter shutdown of its own. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green under this mutation (3855 passed) because nothing in the 3855 assertions ever
ran past the point where the mutation's effect would show up: the sandbox pm2 daemon (and
its directory) surviving the run it claims to clean up after, unboundedly, on every single
invocation of the suite.

**Why the obvious fixes don't work:**

- Calling `_kill_sandbox_pm2_daemon()` directly, in-process, from a test: this is the one
  `SANDBOX_PM2_HOME` the whole session shares (it is deliberately session-scoped, not
  per-test, to avoid spawning a fresh pm2 God Daemon on every test that touches pm2 — see
  the comment above `SANDBOX_PM2_HOME`). Calling the teardown mid-suite deletes the directory
  every other test still needs and can leave a later test pointed at a `PM2_HOME` whose
  directory no longer exists.
- Inspecting `atexit`'s registered callbacks directly: the C `atexit` module (Python 3, not
  the old pure-Python one) exposes no public "list what's registered" API. `atexit._ncallbacks()`
  looks like a plausible probe (call count before/after `atexit.unregister(func)`), but
  measured directly: `unregister` removes the function from firing at `_run_exitfuncs()` time
  (confirmed: a probe function registered then unregistered does not run) yet
  `_ncallbacks()`'s count is unaffected by `unregister` — it does not decrement. A test built
  on that count would fail on *correct* code, which is worse than not testing the wiring at
  all: it would force a rework loop to "fix" code that was never broken.
- Calling `atexit._run_exitfuncs()` in-process: this runs *every* atexit callback registered
  in the whole process, including `shutil.rmtree(SANDBOX_CHELA_DIR, ...)` (the other
  session-wide sandbox every test also shares) and the dashboard's
  `config.clear_dashboard_port`. Same problem as calling the teardown directly, at wider
  blast radius.

**Guard form that survives:** don't inspect the registration; reproduce the event it's
supposed to handle. Spawn a **fresh subprocess** that imports `conftest.py` (the exact
module-level code pytest itself executes — no fixtures run merely by importing it) and then
exits normally, letting CPython's real atexit machinery fire in that process. Capture the
sandbox directory path it printed before exiting, and assert it no longer exists once the
subprocess has returned. This is black-box: it never touches `atexit`'s internals, doesn't
care which private API a given CPython version exposes, and needs `pm2` installed for
nothing (`_kill_sandbox_pm2_daemon`'s `shutil.rmtree` call runs unconditionally at the end
regardless of whether the `pm2 kill` half executed). Under the mutation above the directory
survives; under the real registration it's gone — see
`test_the_sandbox_pm2_home_is_removed_when_the_process_that_created_it_exits` in
`tests/test_pm2_home_isolation.py`.

The general shape: whenever a guard's claimed behavior is "runs later, at some lifecycle
event this test process itself never reaches" (atexit, a signal handler, a `finally` a normal
test run never falls through, a scheduled job), a test that only checks the *registration
call was made* (or, worse, only checks state set up *before* that event) proves nothing about
whether the event, when it actually happens, does what's claimed. Drive the real event —
in a subprocess, a fork, or whatever isolation keeps its blast radius off the shared fixtures
the rest of the suite depends on — and observe its effect from the outside.

**Found:** CMX-354 rework round 1 (2026-09-10), PR #469. The judge applied the mutation
above to `tests/conftest.py` in a throwaway checkout; `CHELA_REQUIRE_JS_TESTS=1 uv run
pytest -q` stayed green (3855 passed, 0 failed, 0 error(s)) because neither existing test in
`tests/test_pm2_home_isolation.py` observed a process exit.
