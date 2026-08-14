## 12. A "stop, don't fall through" rule untested because every fixture has the property everywhere

**Assertion form:** a function is supposed to STOP at the first (or most recent) matching
entry in a sequence and return based on that entry alone — never falling through to consult
an older entry, even when the matching entry itself carries nothing useful. Every test fixture
that exercises the loop gives *every* candidate entry the same shape (all carry the payload,
or only one entry exists at all).

**Mutation that defeats it:** change `return X` (unconditional, first match) to `if <X is
non-empty/present>: return X` — i.e. keep scanning past a matching-but-empty entry instead of
stopping there. Every existing test either has a single relevant entry, or has multiple
entries that all carry the payload, so "stop at the first match" and "keep scanning until you
find a match with the payload" produce identical results on every fixture in the suite.

**Guard form that survives:** construct a fixture where the *most recent* matching entry
deliberately lacks the property (empty/absent), while an *older* one has it — and assert the
function returns the empty/absent result, not the older entry's. This is the one shape a
same-value-everywhere fixture structurally cannot exercise.

**Found:** `chela/dispatcher.py`'s `latest_required_mutations` (CMX-269 rework round 5) — the
function already stopped correctly (`return [...] if isinstance(raw, list) else []` on the
first non-retry entry), but no test had a history where the *latest* substantive verdict
carried no `mutations` while an *earlier* one did, so a fall-through mutation
(`if isinstance(raw, list): return [...]`, otherwise keep looping) stayed green. Fixed by
`test_latest_required_mutations_stops_at_the_latest_verdict_even_when_it_carries_no_findings`.
