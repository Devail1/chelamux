## 335. A "pick the changed line" step fixtured only where the changed line is also the first

**Assertion form:** a function takes a multi-line `before`/`after` pair and picks ONE line to
work on — the first line of `before` that `after` no longer has — with the first line as the
fallback. Every fixture is a single-line pair (`"return 1"` → `"return 0"`), so the asserted
target is right.

**Mutation that defeats it:** drop the "changed" filter — `if ln not in gone` → `if True`, so
the step always returns the first line. On a one-line `before` the first line IS the changed
line, and the fallback returns it too, so the filter, the fallback and the mutation all give
the same answer.

**Why it looks covered:** the target is asserted exactly, and the multi-line path runs in other
tests (a held-out `before` spanning two lines) — but those tests never read back the target that
line produced.

**Guard form that survives:** fixture a `before` whose FIRST line is unchanged and whose LATER
line is changed (`"x = 1\nreturn 1"` → `"x = 1\nreturn 0"` ⇒ `return 1`). Pin the fallback
separately, with a pair that differs only in whitespace, so no line counts as changed.

**Found:** CMX-335 rework round 1, PR #890. `chela/shape_sites.py` `shape()`; fixed in
`tests/test_shape_sites.py` (`test_shape_of_a_multi_line_or_duplicate_call_corruption`).
