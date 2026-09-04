## 345d. A name-set-driven AST walk has no yielded-count check, so renaming one of its guarded functions silently drops it out of the loop

**Assertion form:** `_promptness_check_try_bodies()` (added CMX-345 round 1 to close
[[345|shape 345]]) walks this file's own AST and yields `(name, try_body)` for every
top-level function whose name is in the hardcoded `_PROMPTNESS_CHECK_FUNCS` frozenset (two
names: `test_disabled_wall_still_writes_empty_map_and_idles` and
`test_disabled_wall_sigterm_does_not_orphan_the_idle_sleep`).
`test_promptness_checks_are_not_absorbed_into_reap` then does
`for name, body in _promptness_check_try_bodies(): ...` directly — no count is ever taken
of how many sites the walk actually yielded, even though its structural sibling,
`test_all_run_bg_teardowns_route_through_reap` (added the same round-4 fix that introduced
this file's promptness family, [[339|shape 339]]), asserts exactly that count for its own
walk: `assert len(sites) == 6, f"expected 6 ... found {n}"`.

**Mutation that defeats it:** rename either guarded function, leaving its body untouched —
e.g. `test_disabled_wall_sigterm_does_not_orphan_the_idle_sleep` →
`test_disabled_wall_sigterm_does_not_orphan_the_idle_sleep_v2`. `_PROMPTNESS_CHECK_FUNCS`
still names the OLD identifier, so the AST walk's `func.name not in _PROMPTNESS_CHECK_FUNCS`
filter now excludes the renamed function entirely; the walk silently yields 1 site instead
of 2, the `for` loop iterates once, and the whole test still passes — it just stopped
checking one of the two call sites this file's own comment block says the check protects.
`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3743 passed, 0 failed) under the
mutation.

**Why the round-1 fix's own design doesn't catch it:** the loop's membership source
(`_PROMPTNESS_CHECK_FUNCS`) and its consumption (`for name, body in
_promptness_check_try_bodies()`) are two different places in the same file, and neither one
is checked against a THIRD, independent number. A frozenset membership test degrades
gracefully — matching fewer names just yields fewer results — so a coverage-count assertion
is the only thing that turns "quietly checking less" into a failure. This is the same
underlying gap [[339|shape 339 round 1]] closed for `_run_bg_teardown_sites` (bare iteration
with no assertion on how many `finally: _reap(proc)` sites were found) and that [[304|shape
304]] closed for a glob-driven repo sweep (an emptied glob still satisfies `assert not
violations`) — but distinct from both in mechanism: this isn't an emptied walk (0 matches)
or an un-scoped iteration, it's a *partial*, silent drop from 2 sites to 1 caused by two
name-derived views of the same functions drifting out of sync with each other, with no
third source of truth to catch the mismatch. The sibling guard in the very same file
(`test_all_run_bg_teardowns_route_through_reap`) already demonstrates the fix exists and is
cheap — it simply was not applied to the newer, structurally identical loop added right
below it.

**Guard form that survives:** take `len(list(...))` before iterating and assert it equals a
literal count matching the known membership size (`assert len(sites) == 2, f"expected 2
promptness-check call sites ... found {len(sites)} — a guarded function renamed out of sync
with _PROMPTNESS_CHECK_FUNCS silently drops out of this loop instead of failing it"`) —
mirroring the sibling's own `expected 6 ... found {n}` phrasing exactly. The literal must be
independent of `len(_PROMPTNESS_CHECK_FUNCS)` (asserting the walk's yield count against the
size of the very set that drives the walk is circular and can never catch a name drifting
out of both places together) — pin the number a human expects, the way the sibling pins `6`.

**Found:** CMX-345 rework round 2 (2026-09-04), PR #449 — the judge applied the mutation
above to a throwaway checkout; closed by asserting `len(sites) == 2` in
`test_promptness_checks_are_not_absorbed_into_reap` before iterating, verified to go red
against the exact mutation before landing.
