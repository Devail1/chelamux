## 345h. An AST predicate enumerates ONE spelling of a construct the language offers several of — a keyword arg instead of a positional one, a nested block instead of a top-level statement, an Assign instead of an Expr

**Assertion form:** three of this file's meta-guards each recognize a single syntactic
shape for the construct they're checking, rather than the construct itself:
- `_decorator_forces_unconditional_skip` (added round 3 to close [[345e|shape 345e]]) reads
  the literal condition of `@pytest.mark.skipif(...)` only off `dec.args[0]` — the
  positional form.
- `_diverts_control_flow` (added round 4 to close [[345f|shape 345f]]) is applied only to
  `func.body[:index]`, i.e. the flat, top-level statement list — it classifies each
  `stmt` itself, never anything nested one block inside it.
- The "its try body calls `_reap()`" check in `test_promptness_checks_are_not_absorbed_into_reap`,
  and `_diverts_proc_liveness` (both added round 4 to close [[345g|shape 345g]]), both key off
  `isinstance(stmt, ast.Expr)` — a bare statement-level call — never a call embedded inside
  another statement's value.

**Mutation that defeats it:** three independent mutations, one per guard above, each entering
through the spelling the guard doesn't enumerate:
```diff
- @pytest.mark.skipif(shutil.which("pgrep") is None, reason="pgrep not installed")
+ @pytest.mark.skipif(condition=True, reason="pgrep not installed")
```
pytest's own `evaluate_skip_marks` reads `mark.kwargs['condition']` when the keyword form is
used, so this disables the site exactly as `skipif(True, ...)` does, but `dec.args` is empty —
the positional-only check never sees it.
```diff
-     assert fake.mock_calls == [
+     if True:
+         return
+
+     assert fake.mock_calls == [
```
The `return` is real and unconditional (nothing after it in `test_reap_propagates_if_the_child_survives_sigkill_too`
runs), but it sits inside an `ast.If`, not at the top level of `func.body` — the flat scan
never descends into it.
```diff
-         proc.terminate()
-         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
+         proc = (_reap(proc), subprocess.Popen([sys.executable, "-c", ""]))[1]
+         proc.terminate()
+         proc.wait(timeout=5)  # pre-fix: hung on `sleep 3600`, i.e. up to an hour
```
`_reap(proc)` here absorbs the exact SIGTERM-promptness check this test exists to make, and the
same assignment rebinds `proc` to a freshly spawned, already-exiting stand-in — but the call and
the rebind both live inside an `ast.Assign`'s value/targets, not a bare `ast.Expr`, so neither the
`_reap()`-absorption check nor `_diverts_proc_liveness` sees it. All three mutations left
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` green (3742 or 3743 passed, 0 failed) with the
corruption in place.

**Why the earlier fixes' own design doesn't catch it:** each prior fix (rounds 3 and 4) correctly
identified a construct that needed checking and wrote a predicate for the ONE spelling of it the
fix's own reproduction used — `skipif(True, ...)`, a top-level `return`, `os.kill(proc.pid, 9)` as
a bare `Expr`. None of those fixes were wrong about the construct; each was incomplete about the
construct's *grammar*. Python routinely offers more than one way to write the same semantic thing
— a keyword argument alongside a positional one, a statement nested one block deep alongside a
top-level one, an expression's value tucked inside an assignment alongside a bare expression
statement — and a predicate written against one recollected reproduction, rather than the
language's actual grammar for that construct, silently excludes every other spelling.

**Guard form that survives:** stop enumerating statement shapes and either (a) walk the whole
subtree the check needs to cover (`ast.walk(func)` / `any(isinstance(n, ast.Return) for n in
ast.walk(stmt))` instead of a flat `func.body` scan; `for n in ast.walk(assign)` looking for a
`_reap`/`proc` reference instead of restricting to `ast.Expr`), or (b) also check every keyword
form pytest itself accepts for the same argument (`dec.args[0]` OR `kw.value for kw in
dec.keywords if kw.arg == "condition"`), or (c) observe the RUNTIME fact instead of the source
form entirely.

**Found:** CMX-345 rework round 5 (2026-09-04), PR #449 — the judge applied all three mutations
above to a throwaway checkout; closed by broadening `_decorator_forces_unconditional_skip` to also
read the `condition=` keyword, rewriting `_diverts_control_flow` to walk each preceding statement's
whole subtree rather than classify it directly, and broadening both the `_reap()`-absorption check
and `_diverts_proc_liveness` to walk each statement's subtree (and recognize an `Assign` that
targets or references `proc`) rather than match only a top-level `ast.Expr` — verified to go red
against all three mutations before landing.
