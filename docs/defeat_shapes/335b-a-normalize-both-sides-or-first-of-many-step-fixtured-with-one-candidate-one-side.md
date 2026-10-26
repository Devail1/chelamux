## 335b. A "normalize both sides" or "first of many" step fixtured with one candidate on one side

**Assertion form:** a function compares two inputs after normalizing BOTH (`.strip()` on the
`before` lines and on the `after` lines) and picks the FIRST line that differs. The fixtures
put the whitespace on one side only (`"    return 1"` → `"return 1"`) and change exactly one
line, so the asserted target is right.

**Mutation that defeats it:** drop the normalization on the side the fixture left clean
(`{ln.strip() for ln in after…}` → `{ln for ln in after…}`), or scan the lines in reverse
(`for ln in lines` → `for ln in reversed(lines)`). An un-indented `after` is already
normalized, and with one changed line, first and last are the same line.

**Why it looks covered:** the whitespace-only fallback and the multi-line pick each have a
fixture that reads the exact target back — but each fixture has only one input that could
tell the variants apart, and it sits where the mutation doesn't look.

**Guard form that survives:** for every side that is normalized, a fixture whose whitespace
sits on THAT side and changes the answer (`"return 1\nx = 1"` → `"    return 1\nx = 2"` ⇒
`x = 1`). For a first-of-many pick, a fixture with TWO candidates that differ
(`"a = 1\nb = 2"` → `"a = 0\nb = 0"` ⇒ `a = 1`).

**Found:** CMX-335 rework round 2, PR #890. `chela/shape_sites.py` `shape()`; fixed in
`tests/test_shape_sites.py` (`test_shape_of_a_multi_line_or_duplicate_call_corruption`).
