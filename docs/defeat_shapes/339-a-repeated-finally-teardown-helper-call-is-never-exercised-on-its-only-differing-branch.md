## 339. A repeated `finally:` teardown helper call is never exercised on the one branch that distinguishes it from a naive inline replacement

**Assertion form:** a shared teardown helper (`_reap(proc)`: SIGTERM, bounded wait, escalate
to SIGKILL, wait again) replaces a hand-rolled `proc.terminate(); proc.wait(timeout=10)` at
six independent `finally:` call sites across one test file, closing issue #436 ("a supervisor
slow to exit after SIGTERM fails the TEST cleaning up after it, not the code under test").
Every one of the six looks identical on the page — `finally: _reap(proc)` — and the helper
itself is unit-tested directly (`test_reap_survives_a_sigterm_ignoring_child`,
`test_reap_propagates_if_the_child_survives_sigkill_too`).

**Mutation that defeats it:** revert exactly ONE of the six call sites back to the pre-fix
`proc.terminate(); proc.wait(timeout=10)`. `_reap` and the inline pair are behaviorally
IDENTICAL under the condition every one of these six tests actually produces — a supervisor
that exits promptly once SIGTERM'd — because `_reap`'s only extra behavior (the SIGKILL
escalation) fires solely on the `TimeoutExpired` branch, which none of the six real-process
teardowns ever reaches. So a call site degraded to the naive form passes every test in the
file exactly as before: 3514 passed, 0 failed.

**Why the helper's own unit tests don't close this:** `test_reap_survives_a_sigterm_ignoring_child`
proves `_reap` itself escalates correctly — against a purpose-built stub that ignores SIGTERM.
It says nothing about whether any of the six OTHER call sites still routes through `_reap` at
all, because none of those six ever puts a slow-to-exit process through its own teardown; they
only exercise the fast-exit path, on which `_reap` and the bypass are extensionally the same
function. This is the same family as DEFEAT_SHAPES #60 (a shared helper's contract proven at
one call site, not all) and #330 (a call site's argument never independently observed), but
distinct from both: here every call site LOOKS identical and IS textually correct at the time
of writing — the gap isn't a call site quietly diverging in shape, it's that NONE of the
call sites, including the "reference" one, ever exercises the branch that would tell the
helper and its naive replacement apart. A code reviewer scanning six matching `finally:
_reap(proc)` lines has no way to see that this is true from the diff alone.

**Guard form that survives:** don't try to make each of the six real-process tests exercise
the SIGKILL branch (that would mean deliberately hanging six different supervisor scripts,
which is what `test_reap_survives_a_sigterm_ignoring_child` already does once, generically).
Instead, close the WIRING gap directly and structurally: parse the test file's own source
with `ast` and assert every `finally:` block that tears down a `proc = _run_bg(...)` process
is *exactly* `_reap(proc)` — one statement, one call, one argument — nothing else. This is a
static, structural check on the test file itself (not a "source-constant vs. rendered-value"
check on production code — see #67/#70's caution about those), and it fails the moment any of
the six call sites stops being a bare `_reap(proc)` call, independent of whether the mutated
site's supervisor happens to exit promptly during the run that would otherwise mask it.

**Found:** CMX-339 rework round 1 (2026-09-03), PR #437 — the judge mutated
`test_missing_session_is_created_not_fatal`'s `finally: _reap(proc)` back to
`finally: proc.terminate(); proc.wait(timeout=10)` and the full suite
(`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`) stayed green (3514 passed, 0 failed). Closed by
adding `test_all_run_bg_teardowns_route_through_reap`, which walks `tests/test_terminals_selfheal.py`'s
own AST, finds all six `proc = _run_bg(...)` teardown sites, and asserts each `finally:` body
is structurally `_reap(proc)` and nothing else — verified to go red against the exact mutation
above before being committed.

**Round 2 addendum — the round-1 guard's own "nothing else" check enumerated positional args
and never checked keywords:** `is_reap_call` (the predicate the round-1 guard above added)
checked `stmt.value.func.id == "_reap"`, `len(stmt.value.args) == 1`, and
`stmt.value.args[0].id == "proc"` — three conditions that read as "exactly the call
`_reap(proc)`, no more, no less", but `ast.Call.args` holds only *positional* arguments;
`ast.Call.keywords` is a separate list the predicate never inspected. **Mutation that defeats
it:** change one call site's `finally: _reap(proc)` to `finally: _reap(proc, term_timeout=0)`.
`len(args) == 1` and `args[0].id == "proc"` are both still true, so the guard passed —
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3545 passed, 0 failed) — even though
`term_timeout=0` is not a cosmetic no-op: `_reap` calls `proc.wait(timeout=term_timeout)` and
treats any raised `TimeoutExpired` as "didn't exit gracefully", so `timeout=0` collapses that
window to nothing and SIGKILLs the supervisor before its bash `EXIT` trap (this file's own
documented `cleanup()` / leaked-ttyd / leaked-`webterm_*` fix) can run — the exact failure mode
issue #436 closed, reintroduced through the guard meant to catch exactly that. This is the
AST-guard sibling of #76 (a call *spy* that captures full kwargs but whose assertion only
checks a subset of them): there the data was captured and under-used; here `.keywords` was
never even read. **Guard form that survives:** add `and not stmt.value.keywords` to
`is_reap_call` — one extra positional-and-keyword-both-empty condition. More generally: an AST
"this call has exactly N arguments and nothing else" guard must check `keywords` alongside
`args` — checking only one half of Python's call representation is enumerating a subset while
believing a superset invariant. Found CMX-339 rework round 2 (2026-09-03), PR #437 — the judge
mutated the same call site to `_reap(proc, term_timeout=0)`; closed by adding
`and not stmt.value.keywords`, verified to go red against the exact mutation before landing.

**Round 3 addendum — round 2 closed the CALL-SITE representation; the identical harm moves
to the DEFAULTS and to the terminate/kill ORDER, neither of which the AST guard reads and
neither of which the helper's own unit tests observe:** the AST guard added in round 1/2 only
parses the six `finally:` call sites — it has no opinion on what `_reap`'s signature actually
defaults to, or on the order of statements inside `_reap`'s own body. Both `_reap` unit tests
(`test_reap_survives_a_sigterm_ignoring_child`, `test_reap_propagates_if_the_child_survives_sigkill_too`)
pass `term_timeout` and `kill_timeout` explicitly on every call, so neither test ever observes
the *default* value those six bare `_reap(proc)` call sites actually get at runtime. And the
propagation test pinned `terminate`/`kill` with three independent
`fake.X.assert_called_once()` / `call_count` checks — none of which observe relative order,
because all three stay true under ANY permutation of the four calls. **Mutations that defeat
it:** (1) `def _reap(proc, term_timeout=10, ...)` → `term_timeout=0` — collapses the graceful
window at all six call sites identically to the round-2 mutation, just moved from the call
site to the signature; (2) swap the body to `proc.kill()` first, `proc.terminate()` as the
"escalation" — every `assert_called_once`/`call_count` in the propagation test still passes;
(3) `def _reap(proc, ..., kill_timeout=5)` → `kill_timeout=0` — the un-caught final
`proc.wait(timeout=kill_timeout)` now raises `TimeoutExpired` out of `finally:` on the exact
hang path #436 exists to absorb. All three: `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed
green (3545 passed, 0 failed) under each individually. **Why the AST guard's own design
doesn't extend to these:** it was deliberately written to check *representation at the call
site* ("is this textually `_reap(proc)`, no more, no less") — the right instrument for the
round-1/2 shape, but defaults and body-internal ordering aren't call-site text at all; they
live in the callee's signature and implementation, which no source-derived check of the
*caller* can see. **Guard form that survives:** stop enumerating representations of the call
and pin the callee's *effective* contract directly, once: `inspect.signature(_reap).parameters['term_timeout'].default`
(and `kill_timeout`'s) asserted `>=` a floor large enough to preserve the graceful window and
the post-SIGKILL wait, plus a single `fake.mock_calls == [call.terminate(), call.wait(...),
call.kill(), call.wait(...)]` equality in place of the three independent
`assert_called_once`/`call_count` checks — one assertion that pins method, arguments AND order
together, so no permutation of the same four calls can satisfy it. Found CMX-339 rework round 3
(2026-09-03), PR #437 — the judge applied all three mutations above to a throwaway checkout;
closed by adding `test_reap_defaults_cannot_collapse_either_wait_window` (the signature-default
floor check) and replacing the propagation test's three assertions with the single
`mock_calls` sequence, both verified to go red against each mutation in turn before landing.

**Round 4 addendum — rounds 1-3 all asked "does every teardown still route THROUGH
`_reap`?"; the harm that survived was the mirror image, something that must NOT be `_reap`
being routed through it anyway:** `_reap`'s own docstring draws a must-never boundary —
tests that assert SIGTERM promptness as their actual behavior-under-test call `proc.wait()`
directly for that, in the try BODY, above `finally: _reap(proc)`, because `_reap` is
finally:-only cleanup and "not a replacement for them." Nothing in the suite observed that
boundary: `_run_bg_teardown_sites` (the round-1/2/3 guard) deliberately yields only
`tries[0].finalbody` — it has no opinion on the try body at all. **Mutation that defeats
it:** replace `test_disabled_wall_still_writes_empty_map_and_idles`'s direct
`proc.terminate(); proc.wait(timeout=5)` promptness pair (in the try body) with a second,
redundant `_reap(proc)` call, leaving the `finally: _reap(proc)` untouched:
```diff
-         proc.terminate()
-         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
-     finally:
-         _reap(proc)
+         _reap(proc)
+     finally:
+         _reap(proc)
```
The finalbody is still exactly `_reap(proc)`, so the AST wiring guard passes unchanged; the
whole documented regression this test exists to catch — the disabled-wall idle loop
sleeping in the FOREGROUND, so `pm2 stop` hangs for up to an hour, gets SIGKILLed, and the
bash EXIT trap never runs (no `cleanup()`, leaked ttyds, leaked `webterm_*` mirror
sessions) — becomes undetectable: a supervisor that regressed to hanging the full hour
would now be `_reap`'d (SIGTERM, wait 10s, SIGKILL, wait again) and the test would PASS,
because the 5s bound that was the ONLY thing asserting promptness is gone.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3546 passed, 0 failed) under the
mutation. **Why the round-1/2/3 guard's own design doesn't extend to this:** it was
deliberately written to check the `finally:` body only, because that is where the
issue-#436 wiring question lives; a promptness check living in the try BODY, ABOVE the
`finally:`, is a different AST node entirely, and a source-derived check that never visits
`tries[0].body` has no way to notice it changed. **Guard form that survives:** don't extend
`_run_bg_teardown_sites` (it's answering a different question) — walk the same file's AST a
second way, scoped to just the two functions whose try body contains a documented
promptness check (`test_disabled_wall_still_writes_empty_map_and_idles` and
`test_disabled_wall_sigterm_does_not_orphan_the_idle_sleep`), and assert their try body
still contains a direct `proc.terminate()` and a direct `proc.wait(timeout=<=5)`, and does
NOT contain a call to `_reap`. Found CMX-339 rework round 4 (2026-09-03), PR #437 — the
judge applied the mutation above to a throwaway checkout; closed by adding
`test_promptness_checks_are_not_absorbed_into_reap`, verified to go red against the exact
mutation before landing.

**Round 5 addendum — round 4's guard pinned three SHAPES over an unordered statement list,
not the ADJACENCY that makes the pair a detector; and the propagation test's `mock_calls`
equality had its argument half defeated by a fixture that parks both timeouts on the same
value:** two independent survivals in one round, both instances of the same underlying
lesson — a guard that enumerates that certain shapes exist somewhere in a body, or that
compares two things which happen to hold equal values, is weaker than it reads.

*Experiment 1 — `test_promptness_checks_are_not_absorbed_into_reap`.* The round-4 guard
checked three things over `tries[0].body` as an unordered set: no top-level `_reap(...)`
call, a `proc.terminate()` Expr exists, a `proc.wait(timeout=<=5)` Expr exists. None of
those is "terminate and wait are adjacent, with nothing else able to run between them."
**Mutation that defeats it:**
```diff
-         proc.terminate()
-         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
+         proc.terminate()
+         proc.kill()
+         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
```
All three round-4 shapes stay true — `terminate()` is still there, `wait(timeout=5)` is
still there, no `_reap(...)` appears — but the 5s bound now measures nothing: a supervisor
regressed to a FOREGROUND `sleep 3600` (bash defers traps until the foreground child exits —
the exact pm2-hang / leaked-ttyd / leaked-`webterm_*` regression this test exists for) dies
INSTANTLY from the inserted SIGKILL, the wait returns at once, and the test passes even
though the promptness property it claims to check (SIGTERM alone is what kills the
supervisor within 5s) no longer holds. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed
green (3547 passed, 0 failed) under the mutation. **Guard form that survives:** find the
`proc.terminate()` statement's own index in the try body and assert the *very next*
statement is a direct `proc.wait(timeout=<=5)` — an index check, not a new instrument, since
the AST walk already holds the statement list in order.

*Experiment 2 — `test_reap_propagates_if_the_child_survives_sigkill_too`.* The round-3
addendum's `mock_calls` equality was the right instrument (method, arguments AND order in
one assertion, closing the round-2 permutation gap), but the fixture that exercises it calls
`_reap(fake, term_timeout=1, kill_timeout=1)` — both timeouts parked on the SAME literal.
**Mutation that defeats it:**
```diff
     proc.kill()
-    proc.wait(timeout=kill_timeout)
+    proc.wait(timeout=term_timeout)
```
The expected `[call.terminate(), call.wait(timeout=1), call.kill(), call.wait(timeout=1)]`
is satisfied whichever of the two identically-valued parameters the final wait actually
reads, so routing the post-SIGKILL wait through `term_timeout` instead of `kill_timeout`
passes unchanged — silently making `kill_timeout` a dead argument at every one of the six
call sites, and making the round-3 `test_reap_defaults_cannot_collapse_either_wait_window`
default-floor assertion on `kill_timeout` vacuous (it would keep asserting a floor on a
parameter nothing reads). `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3547
passed, 0 failed) under the mutation. **Why an equality assertion doesn't catch this on its
own:** `mock_calls == [...]` pins order and values correctly, but only once the fixture
feeding it has already destroyed the one distinction the assertion exists to make — a
correct instrument mounted on an indistinguishable fixture proves nothing about the
argument it was meant to isolate (same family as DEFEAT_SHAPES #02, a fixture parked on a
default value). **Guard form that survives:** give `term_timeout` and `kill_timeout`
different values in the fixture (`term_timeout=1, kill_timeout=2`) and expect the matching
distinct values in the `mock_calls` sequence — one-character fixture change, no new
instrument needed. Found CMX-339 rework round 5 (2026-09-03), PR #437 — the judge applied
both mutations above to a throwaway checkout; closed by making `test_promptness_checks_are_not_absorbed_into_reap`
assert adjacency (terminate's index, then the immediately-following statement) instead of
unordered existence, and by giving `test_reap_propagates_if_the_child_survives_sigkill_too`'s
fixture two distinct timeout values — both verified to go red against the exact mutations
before landing.
