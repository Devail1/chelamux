## 205. A periodic retry is tested only by calling it with an injected clock, so the daemon tick that runs it is unpinned

**Assertion form:** a new periodic job (`overrides.retry_approved(now=None)`, "retry an
approved override at most once a minute") is meant to run on every pass of the daemon loop
(`cmd_run`). Its tests are thorough about the job itself: they call
`retry_approved(now=last + 59.9)` and `retry_approved(now=last + 60.001)` to pin the
interval, and drive a fake `gh` through UNKNOWN → MERGEABLE to pin the merge. Every one of
them calls the function **directly**, handing in its own clock. Nothing drives the loop.

**Mutation that defeats it:** comment the production call out —
`overrides.retry_approved()` → `pass  # overrides.retry_approved()` in `cmd_run`. The suite
stays green (9108 passed): the job is still correct, it is just never run, so an approval
deferred on GitHub's `UNKNOWN` waits forever for a retry nobody makes. The injected `now=`
makes this easy to miss: the tests visibly exercise "time passing", which reads like the
scheduling is covered, but the clock they advance is their own, not the daemon's.

**Why it is distinct from shape 330:** 330's leaf is stubbed away at every call site; here
the job is never stubbed and its behaviour is fully pinned. The gap is only the
**scheduler**: the one call site that turns a correct function into a recurring one.

**Guard form that survives:** drive ONE real pass of `cmd_run` with the shared harness
(`tests/test_context.py::_run_one_daemon_tick`), replace the job with a recorder, and assert
it was called, alongside its sibling on the same line (`sweep`) so the ORDER is pinned too:
`assert calls == ["sweep", "retry"]`. Commenting either call out turns it red.

**Found:** CMX-205 judge round 1 (2026-10-18), PR #757. Closed by
`tests/test_merge_override.py::test_the_daemon_tick_retries_an_approved_request`.
