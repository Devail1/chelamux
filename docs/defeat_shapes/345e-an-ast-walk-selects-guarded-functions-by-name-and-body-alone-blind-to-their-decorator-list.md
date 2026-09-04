## 345e. An AST walk selects guarded functions by name and reads only `func.body`, blind to `func.decorator_list` — a decorator that disarms the site entirely leaves every guard satisfied

**Assertion form:** `_promptness_check_try_bodies()` (added CMX-345 round 1 to close
[[345|shape 345]]) walks this file's own AST, matches functions by name against
`_PROMPTNESS_CHECK_FUNCS`, and yields `(name, try_body)` from `func.body` for
`test_promptness_checks_are_not_absorbed_into_reap` to extract-and-replay. `_reap_order_assertion`
and `test_all_run_bg_teardowns_route_through_reap`'s `_run_bg_teardown_sites` do the same:
select a function by name, then read only `func.body`. None of the three ever looks at
`func.decorator_list`.

**Mutation that defeats it:** invert the existing, legitimate
`@pytest.mark.skipif(shutil.which("pgrep") is None, reason="pgrep not installed")` on
`test_disabled_wall_sigterm_does_not_orphan_the_idle_sleep` to an unconditional
`@pytest.mark.skipif(True, reason="pgrep not installed")`:
```diff
- @pytest.mark.skipif(shutil.which("pgrep") is None, reason="pgrep not installed")
+ @pytest.mark.skipif(True, reason="pgrep not installed")
```
The function's body — the thing every AST walk in this file actually inspects — is byte-identical.
pytest now skips the test unconditionally: no SIGTERM is ever sent to a real supervisor, no
orphaned-sleep assertion runs. Yet `_promptness_check_try_bodies()` still finds the function by
name, `len(sites) == 2` (itself added to close [[345d|shape 345d]]) still holds, and the PROPERTY
block still extracts the function's try-body statements and `exec`s them against a synthetic stub
— proving the pattern works in a replay while the real, decorated test that pattern supposedly
guards never runs at all. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3742 passed
instead of 3743 — one fewer collected — 0 failed) under the mutation.

**Why the round-1 and round-2 fixes' own design doesn't catch it:** [[345d|shape 345d]] closed the
gap where a *rename* silently dropped a site out of the name-set walk, by asserting the yielded
count. But a `skipif(True, ...)` decorator doesn't rename anything or change `func.body` — it
disarms the site through a THIRD attribute of the same `ast.FunctionDef` node that no walk in this
file reads. A guard that selects functions by name and then trusts `func.body` to reflect what
actually executes is implicitly assuming the function is reachable at all; nothing enforces that
assumption. This is the mirror image of [[345d|shape 345d]] (drop from the *membership set* side)
entered from the opposite side: the membership and the body are both intact, and the function is
simply never invoked by the test runner.

**Guard form that survives:** before yielding a matched function, inspect `func.decorator_list`
for a `skip` decorator, or a `skipif` decorator whose condition argument is an `ast.Constant`
(a literal `True`/`False`) rather than a runtime-computed expression like
`shutil.which("pgrep") is None` — the legitimate case must stay an `ast.Compare`/`ast.Call`, not a
bare constant. Fail loudly if any guarded site carries one, since a guard replaying a site's own
source can never observe whether that source is actually reachable.

**Found:** CMX-345 rework round 3 (2026-09-04), PR #449 — the judge applied the mutation above to
a throwaway checkout; closed by adding `_decorator_forces_unconditional_skip` and asserting against
it in `_promptness_check_try_bodies` before yielding, verified to go red against the exact mutation
before landing.
