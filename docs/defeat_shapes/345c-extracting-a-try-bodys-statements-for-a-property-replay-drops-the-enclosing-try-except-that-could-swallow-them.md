## 345c. Extracting a try body's statements for a PROPERTY replay drops the enclosing try/except that could swallow them

**Assertion form:** `test_promptness_checks_are_not_absorbed_into_reap`'s PROPERTY block
(added CMX-345 round 1 to close [[345|shape 345]]) extracts the guarded `proc.terminate();
proc.wait(timeout=<=5)` run out of each guarded function's own try BODY via
`_proc_call_run`, then `ast.get_source_segment` + `compile`/`exec`s exactly that source
against a synthetic SIGTERM-ignoring child, asserting the exec raises `TimeoutExpired`
promptly. The docstring frames this as replaying "the actual call-site pattern", not a
hand-written stand-in.

**Mutation that defeats it:** wrap the SAME two statements, unchanged, in a new `except
subprocess.TimeoutExpired: pass` on the enclosing try (leaving `finally: _reap(proc)`
untouched):
```diff
-         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
-     finally:
-         _reap(proc)
+         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
+     except subprocess.TimeoutExpired:
+         pass
+     finally:
+         _reap(proc)
```
In the real function this fully defeats the promptness check the docstring says the guarded
functions exist to make: a SIGTERM-ignoring child now makes `proc.wait` raise, the new
`except` swallows it, and the test's own top-level assertion
(`assert proc.poll() is None` / the orphaned-sleep check) never sees a failure — the
regression this file exists to catch (issue from 2026-08-17, `sleep 3600` reparented to
PID 1) is silently absorbed. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3743
passed, 0 failed) under the mutation.

**Why the round-1 fix's own design doesn't catch it:** `_proc_call_run(body)` only ever
looks at `tries[0].body` — the sequence of statements *inside* the try, never the
`ast.Try` node's `handlers` list that wraps them. `ast.get_source_segment` then pulls out
only the two Expr statements it found and `exec`s them bare, with no `try/except` of their
own around the `exec(code, {"proc": proc})` call other than the test's own
`pytest.raises(subprocess.TimeoutExpired)`. So the replay is *more* faithful to the
statements' own text than [[345|345]]'s original stand-in, but strictly less faithful to
their real execution context: the real function's added `except` clause sits between the
`proc.wait()` raise and the caller, and nothing in the extraction step re-creates or even
notices that a handler now exists. This is the mirror image of [[345|345]] itself — there
the stand-in reproduced the mechanism by hand instead of the real statements; here the real
statements are used verbatim, but the surrounding control-flow frame that could intercept
their outcome is discarded in the same extraction step that was supposed to make the check
faithful.

**Guard form that survives:** before yielding a try body for replay, assert the enclosing
`ast.Try` node has no `handlers` at all — `tries[0].handlers` must be empty. A promptness
check's whole point is that a `TimeoutExpired` from the guarded `proc.wait()` must reach the
caller undisturbed; any `except` clause added to that same try, no matter what it does with
the exception, breaks that property before the extraction step ever runs, so the earliest
and most durable place to reject it is the structural check that already walks this file's
AST for the try itself — not by trying to extend the replay to somehow include arbitrary
handler bodies too.

**Found:** CMX-345 rework round 2 (2026-09-04), PR #449 — the judge applied the mutation
above to a throwaway checkout; closed by asserting `not tries[0].handlers` in
`_promptness_check_try_bodies` before yielding a site, verified to go red against the exact
mutation before landing.
