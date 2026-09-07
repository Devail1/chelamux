## 346. A branch-selecting condition's narrowed clause only changes an unasserted output marker

**Assertion form:** an `if`/`elif`/`else` chain picks between two output-rendering
branches on a compound condition, where the two branches produce *almost* the same text —
one wraps it in a decoration (a `✅` prefix, a status glyph, a log level) the other omits:

```python
elif result.behind_before == 0 and not result.restarted:
    print(headline)
else:
    print(f"✅ {headline}")
```

The guard's own test drives a state that is meant to land in the `else` arm (`behind_before
== 0` **and** `restarted` non-empty) and asserts the shared, substantive part of the output —
the headline text itself (`"restarted stale service(s): chela-dashboard" in out`). It never
asserts the one thing that actually distinguishes the two arms: the `✅` marker only `else`
prints.

**Mutation that defeats it:** drop the second clause from the `elif`'s compound condition
(`and not result.restarted` removed), so the branch test now matches on `behind_before == 0`
alone:

```diff
-     elif result.behind_before == 0 and not result.restarted:
+     elif result.behind_before == 0:
```

For the exact state the test drives (`behind_before == 0`, `restarted` non-empty), this
reroutes execution from `else` into the `elif` — the *wrong* arm — but `headline` is computed
identically upstream of the branch, so `print(headline)` renders the same substantive text the
mutant `print(f"✅ {headline}")` would have. Every assertion that only reads for that text
still holds; the marker's absence goes unnoticed.

**Guard form that survives:** assert the marker itself, not just the prose it decorates —
`assert "✅" in out` (or, more precisely, that the marker is glued to the start of this exact
headline, if the same glyph can legitimately appear elsewhere in the output). A dropped
clause that reroutes to the unmarked branch then changes the one thing being checked.

**Why this is distinct from [[301|shape 301]] and [[322|shape 322]]:** 301 is a prose guard
whose asserted substrings never overlap the span the fix actually touches at all. 322 is a
formatted message with several interpolated *argument* slots, only some of which are read
back. This shape is neither missing overlap nor an unread argument slot — the two branches'
literal text is byte-for-bit identical except for a static, non-interpolated marker
*attached by the branch choice itself*; the guard reads the (identical) computed content and
never reads the (differing) branch-chosen wrapper around it.

**Found:** CMX-346 rework round 1, PR #452. `chela/main.py`'s `cmd_update` routes a
restart-only "nothing to pull, but restarted stale services" catch-up through the same
`elif`/`else` pair that has always separated a genuine no-op (`print(headline)`, no marker)
from a real action (`print(f"✅ {headline}")`, marked) — CMX-346 widened what counts as "a
real action" to include a bare restart, by narrowing the `elif`'s condition with the added
`and not result.restarted` clause. `test_update_cli_reports_a_restart_only_catch_up_when_nothing_behind`
asserted the headline text but not the marker; the judge dropped the clause in a throwaway
checkout and `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (3749 passed). Closed by
adding `assert "✅" in out` alongside the existing text assertion.
