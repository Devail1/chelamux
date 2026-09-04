## 345b. A guard written FOR a sibling test's assertion form re-proves a stdlib property instead of observing that assertion

**Assertion form:** `test_reap_propagates_if_the_child_survives_sigkill_too` pins the SIGTERM/
SIGKILL escalation as one `mock_calls == [...]` equality specifically so no permutation of
the four calls can satisfy it (CMX-339 round 3). `test_reap_call_sequence_equality_is_order_sensitive`
was added as its docstring says, "GUARD for the ORDER claim" of that assertion — but it built
its own fresh `MagicMock`, called the same four methods on it in a different order, and
asserted that differently-ordered `mock_calls` list does not equal a hand-retyped copy of the
expected order.

**Mutation that defeats it:** widen the sibling's own comparison from a bare `==` to an
order-insensitive `sorted(..., key=repr) == sorted(..., key=repr)`:
```diff
-     assert fake.mock_calls == [
+     assert sorted(fake.mock_calls, key=repr) == sorted([
          call.terminate(),
          call.wait(timeout=1),
          call.kill(),
          call.wait(timeout=1),
-     ], "must SIGTERM, wait, then SIGKILL, wait again — in that order, not any permutation"
+     ], key=repr), "must SIGTERM, wait, then SIGKILL, wait again — in that order, not any permutation"
```
This is the exact regression the sibling's own docstring warns about — the order claim
becoming false while still reading as an equality check — yet
`test_reap_call_sequence_equality_is_order_sensitive` kept passing, and
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3743 passed, 0 failed) under the
mutation.

**Why the guard's own design doesn't catch it:** the guard imports nothing from `chela`,
calls neither `_reap` nor the sibling test, and re-declares (rather than shares) the pinned
sequence. What it actually asserted was that `unittest.mock._CallList.__eq__` is
order-sensitive — a property of the standard library, true no matter what the sibling's own
assertion looks like. A guard written to be "for" a specific assertion elsewhere in the file
has to actually observe that assertion's current form; building an isolated example that
merely illustrates the property in the abstract proves the property can hold, not that the
one place it's supposed to matter still relies on it. This is the sibling of #339's own
family of gaps (a helper's contract proven once, in isolation, while a specific call site
that needs it is never checked) but distinct in shape: there a *production* call site went
unobserved; here a *test* assertion's own textual form is the thing that needed observing,
and nothing pointed back at it.

**Guard form that survives:** stop re-proving the stdlib property and instead parse this
file's own AST (the same technique CMX-339 round 4 already uses for the try/finally wiring
checks) to find the sibling's one `assert` statement, then assert its *shape* directly: a
single `==` comparison (`ast.Eq`, not `sorted(...) == sorted(...)` or any other wrapping)
whose LHS is the bare `fake.mock_calls` attribute (not a `sorted(fake.mock_calls, ...)` call)
and whose RHS is a bare list literal (not a `sorted([...], ...)` call). Wrapping either side
in `sorted(...)` — or any other call — changes the LHS/RHS node type from `ast.Attribute`/
`ast.List` to `ast.Call`, which trips this guard directly, because it is reading the sibling's
actual current assertion rather than a property that holds independently of it.

**Found:** CMX-345 rework round 1 (2026-09-04), PR #449 — the judge applied the mutation
above to a throwaway checkout; closed by rewriting `test_reap_call_sequence_equality_is_order_sensitive`
to parse the sibling's own assert statement off this file's AST and check its LHS/RHS/operator
shape directly, verified to go red against the exact mutation before landing.
