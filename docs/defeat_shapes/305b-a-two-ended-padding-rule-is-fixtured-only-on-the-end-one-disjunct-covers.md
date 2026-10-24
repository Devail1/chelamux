## 305b. A two-ended padding rule is fixtured only on the end that one disjunct covers

**Assertion form:** a helper must act when a condition holds at EITHER end of a string, and it
writes that as `a(text) or b(text)`. In CMX-305, `_code` wraps a source line in a Markdown code
span and pads it with a space when the line starts with a backtick or ends with one. Without the
pad, the backtick at that edge merges into the fence and the span breaks. The guard rendered an
issue for the line `x = `a``b``, and asserted the exact span. That line ENDS with a backtick but
does not START with one. Every fixture of this kind sat on the trailing end, so `endswith` alone
decided every one of them.

**Mutation that defeats it:** replace the disjunct that no fixture reaches with `False`:
`pad = " " if text.startswith("`") or text.endswith("`") else ""` becomes
`pad = " " if False or text.endswith("`") else ""`. A line starting with a backtick, such as
`` `cmd` and x ``, now renders as ``` ``cmd` and x`` ```. Markdown reads that as a two-backtick
fence closed early, so the issue body is garbled. The suite stays green because no fixture
starts with a backtick.

**Guard form that survives:** list the four cases of the two-ended rule as a table: leading
only, trailing only, both, and neither (an inner backtick only, which must NOT be padded).
Assert the exact rendered span for each. Then drive one real line through the production
renderer for the end the original fixture skipped. Knock out each disjunct separately and watch
a different row go red.

**Why this is distinct from [[405|shape 405]] and [[62b|shape 62b]]:** 405 is a region extractor
whose start and end are different patterns, and the end pattern was never reached. Here one
symmetric rule is written as two disjuncts that should mirror each other, and one is never
decisive. 62b is a disjunctive *exemption* fixtured where every clause holds at once, so each
clause hides behind the others. Here the fixture holds exactly one clause, and the fault is
which clause it picked.
