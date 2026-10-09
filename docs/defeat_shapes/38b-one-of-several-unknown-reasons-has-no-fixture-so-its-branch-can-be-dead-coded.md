## 38b. One of several "unknown" reasons has no fixture, so its branch can be dead-coded

**Assertion form:** `usage.limits()` classifies each 5h/7d limit into a reading or one of
three UNKNOWN reasons — no sample, a stale sample (> 30 min old), or a sample whose
`resets_at` has already passed (its % belongs to the previous window). The PR's own
acceptance list named only "stale or missing ⇒ unknown, not 0%", so the suite had a missing
fixture and a stale fixture — and every fixture that produced a reading put `resets_at` in
the future. The third reason was written, documented in the docstring, and never driven.

**Mutation that defeats it:** `elif b[2] is not None and b[2] <= now:` →
`elif False and …`. The reset branch is now dead; a fresh sample whose window just reset
falls through to the reading branch and shows the OLD window's 97% as current. Green,
because no fixture has a fresh sample with a past `resets_at` — the only input that reaches
that branch.

Found in the same round, already catalogued: the `> BROKEN_MIN_TOKENS` conjunct of
`is_cache_broken` replaced by `True` survived because every "not flagged" fixture failed a
*different* conjunct (shape 348), and `usage.report(names)` → `usage.report({})` survived
because the only HTTP test stubbed the window-name map to `{}` (shape 7b).

**Guard form that survives:** enumerate the reasons a value is UNKNOWN from the code, not
from the ticket's acceptance list, and give each its own fixture that is valid on every
OTHER axis (here: a fresh 5-minute-old sample, real %, `resets_at` one minute ago) — then
assert the value is `None` and the reason names *that* cause. Pin the boundary too
(`resets_at == now` is unknown) and keep a sibling limit in the same fixture that must stay
a reading, so a mutation that blanks everything goes red as well.

**Found:** CMX-38 rework round 1 (2026-10-09), PR #618. `chela/usage.py`,
`tests/test_usage.py`.
