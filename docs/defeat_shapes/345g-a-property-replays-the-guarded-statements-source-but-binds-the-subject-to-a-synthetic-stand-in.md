## 345g. A PROPERTY replay extracts the guarded statements' own SOURCE, but binds the guarded name to a freshly spawned SUBJECT — a real-site statement that changes the real subject's state before the pinned run is invisible to both the extraction and the replay

**Assertion form:** `test_promptness_checks_are_not_absorbed_into_reap`'s PROPERTY block
(rewritten CMX-345 round 1 to close [[345|shape 345]]) extracts the guarded
`proc.terminate(); proc.wait(timeout=<=5)` run's own source out of each guarded function via
`_proc_call_run` + `ast.get_source_segment`, then `compile`/`exec`s that source with
`{"proc": proc}`, where `proc` is a *freshly spawned*, still-alive, SIGTERM-ignoring stub
`subprocess.Popen` created by the PROPERTY block itself — not the `proc` object the guarded
function actually holds at the point those statements run for real.

**Mutation that defeats it:** insert a statement that ends the *real* `proc` outright,
directly above the pinned run, using a shape that names `proc` without being a
`proc.<method>()` Expr:
```diff
-         proc.terminate()
-         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
+         os.kill(proc.pid, 9)
+         proc.terminate()
+         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
```
At the REAL call site this makes the test's own promptness check vacuous: the SIGKILL kills
the supervisor before `proc.terminate()`/`proc.wait()` even run, so `wait()` returns almost
instantly against an already-dead process — the exact pre-fix hang (up to an hour on a
SIGTERM-ignoring child) would no longer be detected there. Yet `_proc_call_run`'s run
boundary only absorbs contiguous bare `proc.<method>(...)` Expr statements, so
`os.kill(proc.pid, 9)` — a call to `os.kill`, not a method call ON `proc` — sits OUTSIDE the
extracted run entirely: it is neither pulled into the `exec`'d segment nor rejected by any
check. The PROPERTY block then `exec`s only `proc.terminate(); proc.wait(timeout=5)` against
its OWN synthetic, still-alive stub — which is unaffected by anything that happened to the
real `proc` — so it still raises `TimeoutExpired` right on schedule and the whole test stays
green. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3743 passed, 0 failed) under
the mutation.

**Why the round-1 fix's own design doesn't catch it:** [[345|shape 345]]'s fix correctly
diagnosed that the PROPERTY block must exercise the guarded call sites' own STATEMENTS rather
than a hand-written stand-in, and `_proc_call_run` + `get_source_segment` delivers that. But
extracting the statements' source text and extracting what those statements actually *operate
on* are two different guarantees — the fix supplies only the first. `exec(code, {"proc": proc})`
rebinds the name `proc` to whatever object the PROPERTY block passes in, so the replay proves
"the pinned pair raises against a child that is alive and SIGTERM-ignoring" as an abstract fact
about those two lines, never as a fact about the state the real call site's `proc` is actually
in when those same two lines execute for real. Anything upstream of the pinned run that leaves
the real supervisor already dead, exited, or rebound is invisible to this design by
construction: it cannot be a `proc.<method>()` Expr (or it would join the run and get replayed,
which is exactly what [[345|shape 345]]'s own fix already defends against for e.g.
`proc.kill()`), so it structurally cannot trip the run-boundary scan, and the replay never
observes the real object at all. This is the mirror image of [[345|shape 345]] one level up:
that shape was "the STATEMENTS replayed are a stand-in for the real site's"; this one is "the
STATEMENTS are real, but the SUBJECT they run against is a stand-in for the real site's".

**Guard form that survives:** reject, ahead of the pinned run, any statement in the same try
body that names the guarded variable (`proc`) outside of a `proc.<method>()` Expr call —
`os.kill(proc.pid, ...)`, a rebinding `proc = ...`, or any other call taking `proc`/`proc.pid`
as an argument all match this shape and all can end or replace the real subject without
routing through a statement the run-boundary scan would see. Existing legitimate references to
`proc` in these functions (`assert proc.poll() is None`, `_wait(..., proc=proc, ...)`) are
`ast.Assert` statements, not bare `ast.Expr` calls, so a check scoped to bare-Expr calls
naming `proc` catches the mutation shape without false-positiving on those.

**Found:** CMX-345 rework round 4 (2026-09-04), PR #449 — the judge applied the mutation above
to a throwaway checkout; closed by adding `_diverts_proc_liveness` and asserting no statement
before the pinned run in `test_promptness_checks_are_not_absorbed_into_reap` trips it, verified
to go red against the exact mutation before landing.
