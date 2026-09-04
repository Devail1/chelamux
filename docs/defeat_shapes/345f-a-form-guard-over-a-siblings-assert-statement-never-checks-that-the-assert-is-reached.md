## 345f. A form-guard that reads a sibling's `assert` statement off the AST never checks that the assert is actually reached

**Assertion form:** `_reap_order_assertion()` (added CMX-345 round 1 to close [[345b|shape 345b]])
walks this file's AST, finds `test_reap_propagates_if_the_child_survives_sigkill_too`, collects its
top-level `ast.Assert` statements, asserts there is exactly one, and returns it so
`test_reap_call_sequence_equality_is_order_sensitive` can check its shape (a bare `==`, `fake.mock_calls`
on the left, a bare list literal on the right). Nothing in `_reap_order_assertion` checks *where*
in `func.body` that assert sits relative to any other top-level statement.

**Mutation that defeats it:** dead-code the sibling's pinned order assertion with a bare `return`
placed directly above it:
```diff
-     with pytest.raises(subprocess.TimeoutExpired):
-         _reap(fake, term_timeout=1, kill_timeout=1)
- 
-     assert fake.mock_calls == [
+     with pytest.raises(subprocess.TimeoutExpired):
+         _reap(fake, term_timeout=1, kill_timeout=1)
+ 
+     return
+ 
+     assert fake.mock_calls == [
```
The ORDER claim is no longer asserted at test time — the function returns before Python ever
reaches the `assert` — yet `func.body` still contains exactly one `ast.Assert` with the exact same
`==`/`fake.mock_calls`/list-literal shape `_reap_order_assertion` and its consumer inspect. Both
`len(asserts) == 1` and every shape check downstream hold unchanged.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3743 passed, 0 failed) under the
mutation — `test_reap_propagates_if_the_child_survives_sigkill_too` itself still passes too,
because `pytest.raises(subprocess.TimeoutExpired)` is satisfied before the `return` is ever hit.

**Why the round-1 fix's own design doesn't catch it:** [[345b|shape 345b]]'s fix moved the guard
from re-proving a stdlib property to reading the sibling's own assert statement off the AST — the
right direction, since it now depends on the sibling's actual current form rather than an
isolated example. But "read the right node" and "check the node is live" are two different
guarantees; `_reap_order_assertion` supplies only the first. An `ast.Assert` node's `test` field
describes its *shape* whether or not control flow ever arrives at it — reachability is a property
of the surrounding statements, not of the node itself, so a predicate over `stmt.test` alone is
structurally blind to anything that precedes it in the body. This is the same family as
[[345e|shape 345e]] (a guard trusts a piece of a `FunctionDef` to reflect what executes, without
checking a different part of the same node that controls whether it does) but at statement
granularity within one function's body rather than at the decorator level.

**Guard form that survives:** `_reap_order_assertion` already computes `func.body` and the pinned
assert's position in it (`func.body.index(assert_stmt)`); take everything before that index and
assert none of it is an `ast.Return` or `ast.Raise` — either would make the assert unreachable
while leaving its AST shape untouched. (A guard on `x` inside a function must also check that
nothing standing between the function's entry and `x` unconditionally exits first, or `x`'s shape
proves nothing about what actually runs.)

**Found:** CMX-345 rework round 3 (2026-09-04), PR #449 — the judge applied the mutation above to
a throwaway checkout; closed by asserting no `Return`/`Raise` precedes the pinned assert in
`_reap_order_assertion`, verified to go red against the exact mutation before landing.
