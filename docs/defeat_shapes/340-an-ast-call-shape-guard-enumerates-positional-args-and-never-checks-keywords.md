## 340. An AST call-shape guard enumerates positional args and never checks keywords

**Assertion form:** `test_all_run_bg_teardowns_route_through_reap` (added to close
[[339|shape 339]]) parses `tests/test_terminals_selfheal.py`'s own source and asserts each of
the six `finally:` bodies is structurally `_reap(proc)` — "one statement, one call, one
argument — nothing else", per the guard's own docstring and shape 339's write-up. The
`is_reap_call` predicate checks `stmt.value.func.id == "_reap"`, `len(stmt.value.args) == 1`,
and `stmt.value.args[0].id == "proc"` — three conditions that together read as "exactly the
call `_reap(proc)`, no more, no less".

**Mutation that defeats it:** change one call site's `finally: _reap(proc)` to
`finally: _reap(proc, term_timeout=0)`. `ast.Call.args` holds only *positional* arguments —
`ast.Call.keywords` is a separate list the predicate never inspects — so `len(args) == 1` and
`args[0].id == "proc"` are both still true and the guard passes. `term_timeout=0` is not a
cosmetic no-op: `_reap` calls `proc.wait(timeout=term_timeout)` and treats any raised
`TimeoutExpired` as "the process didn't exit gracefully" — `timeout=0` collapses that window
to nothing, so the very first fast-poll `wait()` almost always raises, and the supervisor is
SIGKILLed instead of allowed to run its bash `EXIT` trap. That trap is this file's own
documented `cleanup()` — the fix for a real, cited leaked-ttyd/leaked-`webterm_*` regression —
so a mutation the guard was supposed to catch instead silently reintroduces the exact failure
mode issue #436 closed. Full suite (`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q`) stayed green
before and after: 3545 passed, 0 failed.

**Why the "one statement, one call, one argument" framing didn't close this on its own:**
the docstring and shape 339's own write-up describe the invariant in prose as covering the
whole call shape, but the implementation only enumerated the piece of `ast.Call` that Python
puts positional arguments in. `ast.Call` splits a call's arguments into two independent
attributes (`args` for positional, `keywords` for `kw=val` and `**kw`), and a predicate that
walks one without the other is not a partial version of the prose invariant — it is silently
scoped to a narrower one ("one positional argument") that happens to read identically until
someone adds a keyword. This is the AST-guard sibling of #76 (a call *spy* that captures full
kwargs but whose assertion only checks a subset of them): there the data was captured and the
assertion under-used it, here the data (`.keywords`) was never even read.

**Guard form that survives:** add `and not stmt.value.keywords` to `is_reap_call` — one extra
positional-and-keyword-both-empty condition, so `_reap(proc, term_timeout=0)`,
`_reap(proc=proc)`, and `_reap(proc, **extra)` are all rejected the same way a second
positional argument already was. More generally: an AST "this call has exactly N arguments and
nothing else" guard must check `keywords` (and, for a function def rather than a call site,
`vararg`/`kwarg`/`kwonlyargs`) alongside `args` — checking only one half of Python's call
representation is enumerating a subset while writing (and believing) a superset invariant.

**Found:** CMX-339 rework round 2 (2026-09-03), PR #437 — the judge mutated
`test_missing_session_is_created_not_fatal`'s `finally: _reap(proc)` to
`finally: _reap(proc, term_timeout=0)` and the full suite stayed green (3545 passed,
0 failed). Closed by adding `and not stmt.value.keywords` to `is_reap_call` in
`test_all_run_bg_teardowns_route_through_reap` — verified to go red against the exact
mutation above before being committed.
