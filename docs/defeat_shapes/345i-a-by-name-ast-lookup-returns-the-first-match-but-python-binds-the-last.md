## 345i. A by-name AST lookup `return`s on the FIRST `ast.walk` match, but Python (and pytest collection) bind the LAST definition of a shadowed name

**Assertion form:** `_reap_order_assertion()` (added round 4 to close [[345f|shape 345f]])
walks this file's AST looking for a `FunctionDef` named
`test_reap_propagates_if_the_child_survives_sigkill_too`, and `return`s the first match it
finds — the same by-name-then-return-on-first-hit shape used throughout this file's other
meta-guards (`_run_bg_teardown_sites`, `_promptness_check_try_bodies`).

**Mutation that defeats it:** append a second definition of the same name to the end of the
file, shadowing the original — intact — one:
```diff
      ], "must SIGTERM, wait, then SIGKILL, wait again — in that order, not any permutation"
+
+
+ def test_reap_propagates_if_the_child_survives_sigkill_too():
+     fake = MagicMock()
+     fake.wait = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="stub", timeout=1))
+     with pytest.raises(subprocess.TimeoutExpired):
+         _reap(fake, term_timeout=1, kill_timeout=1)
```
Python binds whichever `def` of a given name executes last at module-load time, and pytest
collects functions off the live module object — so this second definition is the one that
actually runs, and it makes no claim at all about call ORDER (no `mock_calls == [...]`
equality, just "raises eventually"). `ast.walk`, by contrast, yields nodes in source order and
this guard returns on the first hit, so it inspects the FIRST (dead, shadowed) definition —
which still has the perfect `==` form, a single assert, and nothing diverting control flow
ahead of it. Every check the guard makes passes against a function nothing ever calls, while
the function pytest actually runs and reports as passing never asserts the order at all.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3743 passed, 0 failed — same total as
the unmutated baseline) under the mutation: pytest collects one function object per name, so the
shadowing duplicate silently REPLACES a collected test rather than adding one, and nothing in
this file (or in the suite's own collection count) would show a shrunk source-to-collected ratio
either way, so that signal went unobserved too.

**Why the round-4 fix's own design doesn't catch it:** [[345f|shape 345f]]'s fix correctly
diagnosed that the pinned assert could be dead-coded by a preceding return/raise/skip, and wrote
a reachability check for exactly that. But it never questioned whether the FUNCTION the check
locates is the one that runs at all — a by-name `ast.walk` lookup implicitly assumes the name is
unique, and nothing in this file (or in `_run_bg_teardown_sites` / `_promptness_check_try_bodies`,
which share the same shape) enforces that assumption. `_promptness_check_try_bodies` happens to
survive this exact mutation shape only by accident, not by design: it is a generator with no
`return`, so a shadowing duplicate of one of ITS names would yield a THIRD site and its sibling
`len(sites) == 2` count (added to close [[345d|shape 345d]]) would catch the extra yield — but
`_reap_order_assertion` returns eagerly on the first match, so no such count ever runs.

**Guard form that survives:** collect ALL `FunctionDef` nodes matching the target name (a list
comprehension over `ast.walk`, not a `return` inside the loop) and assert there is exactly one
before indexing into it — the same "assert the count, don't just trust the first yield" pattern
[[345d|shape 345d]] already established for a *set membership* drop, applied here to a *name
lookup* returning the wrong (shadowed) element instead of silently dropping one.

**Found:** CMX-345 rework round 5 (2026-09-04), PR #449 — the judge applied the mutation above to
a throwaway checkout; closed by rewriting `_reap_order_assertion` to collect every matching
`FunctionDef` and assert `len(matches) == 1` before using it, verified to go red against the exact
mutation before landing.
