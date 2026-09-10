## 359b. An `except` clause's fallback value is proven REACHED but never proven EQUAL to the docstring's promise

**Assertion form:** a function's docstring promises a specific sentinel for one failure mode
— "`None` when ... the timestamp is unparseable" — and the code matches it:
`except ValueError: return None`. The test suite for the function's neighboring failure modes
is thorough: one test proves `None` when there is no matching record at all (the predicate
never matches), another proves a record that *looks* like a match but carries a malformed
field of the wrong *type* is rejected by the predicate before parsing is even attempted
(`isinstance(o.get("timestamp"), str)` guards `.replace()`, which would otherwise raise
`AttributeError` on a non-string). Both are real, adjacent failure modes, and both are
guarded. But neither test ever constructs the one input that actually reaches the `except
ValueError:` line itself: a record that passes every predicate check — real record, right
type, non-blank content — whose `timestamp` is a syntactically valid *string* that
`datetime.fromisoformat` still cannot parse (`"not-a-timestamp"`, `""`, a truncated
ISO string). Nothing in the suite ever calls the function with that shape, so the `except`
clause's *body* — which value it actually returns — is never observed by any assertion.

**Mutation that defeats it:** change what the `except` clause returns:
`except ValueError: return None` → `except ValueError: return 0.0`. Every existing test
still passes, because every existing test's input is caught by an earlier `if not rec: return
None` or by the predicate rejecting the record before parsing — none of them ever reach the
`except` body, so the value inside it is free to be anything and the suite cannot tell the
difference. The consequence is not cosmetic: this function's `0.0` and `None` feed a `>`
comparison in a caller (`last_assistant_activity_at(...) > last_user_activity_at(...)`) that
treats `None` as "can't compare, so not done" but treats `0.0` as a real, comparable floor
below every real epoch timestamp — so the caller's behavior for "one malformed record" and
"the human definitely spoke" diverge silently, in the direction that makes the malformed case
look *more* done, not less.

**Why this is distinct from [[351|shape 351]]:** 351 is about an `except` clause's *tuple*
being narrowed to a type still covered by a *different, wider* test — the exception class
under test still gets caught, just via a different code path than intended. Here every
existing test's input never triggers the `except` block AT ALL; the gap isn't which exception
types are caught, it's that the one case documented to land in this specific `except` body
has no fixture constructing it.

**Guard form that survives:** for every documented failure-mode sentinel in a docstring,
write the ONE test whose input is the minimal, real case that reaches exactly that `except`
(or `if`) body — not a case that's caught earlier by a cheaper check, and not a case that
skips the code path by failing a predicate first. Concretely: a record shaped exactly like a
valid real user turn (right `type`, not a sidechain/meta, string content) whose `timestamp`
field is itself a non-empty string that `datetime.fromisoformat` raises `ValueError` on —
`{"timestamp": "not-a-timestamp", ...}` — asserting the function's return is `None`, not just
"falsy" or "not a crash."

**Found:** `chela/transcripts.py`'s `last_user_activity_at` (CMX-359, PR #485, judge round 2)
— `except ValueError: return None` (already correct on this branch, matching the docstring)
had no test constructing an unparseable-but-string timestamp; round 1 had added siblings for
"no real user turn" and "non-string timestamp" but not this one. Closed by adding
`test_last_user_activity_at_is_none_when_the_newest_timestamp_is_unparseable` to
`tests/test_transcripts.py`.

**See also:** [[351|shape 351]] — the narrowed-exception-tuple version of "the except clause
looks tested but isn't"; this is the untested-fallback-value version of the same lesson.
