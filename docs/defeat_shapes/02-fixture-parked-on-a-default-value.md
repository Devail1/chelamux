## 2. Fixture parked on a default value

**Assertion form:** the guard checks a value while the fixture sits at whatever default it
was initialized to — a value that never crosses the threshold the guarded code reacts to.

**Mutation that defeats it:** delete or invert the threshold-crossing logic entirely. Nothing
in the test ever drives the fixture across the boundary, so the branch that got deleted was
never exercised in the first place — the suite can't tell the difference.

**Guard form that survives:** move the fixture's value **across** the exact threshold the code
is supposed to react to, and assert the reaction (a class added, a message changed) — not just
that the value round-trips at its resting default.

**Found:** `tests/wallnav.test.mjs` test 15c (CMX-230 round 7) — test 15 only ever polled at
`used_pct: 42` (`ctxLevel` stays `'ok'`, no severity class at all). Wrapping the
`ctxChip.className = …` assignment in `if (false)` left the chip painted with whatever class
it started with, and nothing in the suite noticed because nothing polled past 80%. 15c polls
at `used_pct: 85` — across the real `ctx-danger` threshold — and reads the class back off the
node.
