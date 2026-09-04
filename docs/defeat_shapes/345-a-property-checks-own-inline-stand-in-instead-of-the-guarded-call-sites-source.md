## 345. A PROPERTY check's own inline stand-in reproduces the guarded mechanism by hand instead of exercising the guarded call sites' actual source

**Assertion form:** `test_promptness_checks_are_not_absorbed_into_reap`'s docstring says the
AST checks above it "only prove the SHAPE is still there, not that the shape does anything",
and that the block below "drives the actual PROPERTY this check exists for" — that a bare
`proc.terminate(); proc.wait(timeout=<=5)` pattern, pinned by CMX-339 round 4 in the try
bodies of `test_disabled_wall_still_writes_empty_map_and_idles` and
`test_disabled_wall_sigterm_does_not_orphan_the_idle_sleep`, must actually detect a
SIGTERM-ignoring child, and detect it PROMPTLY (not by falling through to `_reap`'s SIGKILL
fallback, which would mask the exact promptness regression the pattern exists to catch).

**Mutation that defeats it:** widen either guarded call site's pinned pattern by inserting a
`proc.kill()` immediately before its `proc.terminate(); proc.wait(timeout=5)`:
```diff
-         proc.terminate()
-         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
+         proc.kill()
+         proc.terminate()
+         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
```
This reintroduces exactly the harm CMX-339 round 4 closed: SIGKILL-first means the wait no
longer fails fast on a SIGTERM-ignoring child (a bare `wait()` after `kill()` succeeds almost
immediately, so a real promptness regression at this call site — the process taking up to an
hour to answer SIGTERM — would go undetected). `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`
stayed green (3743 passed, 0 failed) under the mutation.

**Why the PROPERTY block's own design doesn't catch it:** the block writes its own
`ignore_term_promptness.py`, spawns its own `subprocess.Popen`, and asserts on its own local
`proc` variable — it never reads, imports, calls, or otherwise reaches either guarded
function. It therefore proves a property of CPython's `subprocess.Popen.wait(timeout=...)` in
isolation — true regardless of whether the real call sites still use the pinned pattern — not
a property of the two call sites it is written to protect. This is the same family as #18 (a
stub pins that the mechanism was invoked, not what ran) and #50 (a renderer proven against
hand-called arguments the real caller never passes): in all three, the check exercises a
hand-built stand-in instead of the actual production code path, so a mutation to the real
call site is invisible to it.

**Guard form that survives:** extract the guarded statements' own source — not a rewritten
equivalent — out of each guarded function's AST, and `exec` THOSE statements against the
synthetic SIGTERM-ignoring child. `_proc_call_run(body)` finds the maximal contiguous run of
bare `proc.<method>(...)` Expr statements at the tail of the try body; because the run
boundary is structural (any Expr call on `proc`, not just the two pinned method names), a
mutation that squeezes an extra `proc.kill()` in next to the pinned pair stays *inside* the
extracted run and gets executed along with it. `ast.get_source_segment` pulls the exact
source text of that run, `compile`/`exec` runs it with `proc` bound to the stub — so the
property check now observes the real call site's current behavior, not a hand-written
substitute for it.

**Found:** CMX-345 rework round 1 (2026-09-04), PR #449 — the judge applied the mutation
above to a throwaway checkout; closed by rewriting `test_promptness_checks_are_not_absorbed_into_reap`'s
PROPERTY block to extract and `exec` each guarded function's own terminate/wait statements via
`_proc_call_run`, verified to go red against the exact mutation before landing.
