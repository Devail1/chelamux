## 337. A multi-column compare-and-swap `WHERE` clause is proven by moving only one of its columns between the read and the write

**Assertion form:** `acknowledge_blocked_race`'s write is documented as "CAS on
`judge_state`/`judge_sha` together" — a concurrent judge re-run landing between the caller's
read and this function's write must not have its fresh verdict silently stamped as
acknowledged, whether that re-run moved the sha, moved the state, or moved both. The write
itself is a single SQL statement ANDing both columns into one `WHERE` clause:
`WHERE task_id=? AND judge_state=? AND judge_sha IS ?`. The only concurrency test in the file
constructs a stale read that differs from the real row on `judge_sha` alone — the stale copy's
`judge_state` and the real row's `judge_state` are identical throughout. That fixture can only
tell "the write checked *something* that changed" apart from "the write checked nothing at
all"; it cannot tell "the write checked both columns" apart from "the write checked `judge_sha`
alone and never touched `judge_state`" — both produce the exact same refusal on this fixture,
because `judge_sha` alone already differs.

**Mutation that defeats it:** widen the `judge_state` clause into a tautology so it stops
constraining anything, while leaving the `judge_sha` clause untouched:

```diff
- "WHERE task_id=? AND judge_state=? AND judge_sha IS ?",
+ "WHERE task_id=? AND (judge_state=? OR 1) AND judge_sha IS ?",
```

The existing sha-only concurrency test still passes under this mutation — its stale sha still
disagrees with the real row's sha, so the (now sha-only) `WHERE` clause still refuses,
byte-identically to the unmutated version. `CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed
green (3526 passed, 0 failed, 0 error(s)) with this mutation applied to a throwaway checkout of
the PR's head — nothing in the suite ever gave `judge_state` a chance to be the ONLY column
that had moved.

**Guard form that survives:** for an N-column CAS/optimistic-concurrency `WHERE` clause,
one fixture per column is not enough if every fixture also lets a different column disagree —
each column needs its own fixture where it is the ONLY column that disagrees between the
stale read and the real row, with every other column held identical. Closed here by
`test_acknowledge_is_scoped_to_the_current_judge_state_not_only_sha`: the real row and the
stale read share the exact same `judge_sha` ("same-sha"), and only `judge_state` differs (a
fresh judge re-run resolved the SAME commit to a DIFFERENT verdict, the concurrency window the
docstring itself describes) — a `WHERE` clause that dropped or tautologized the `judge_state`
comparison now has nothing left to refuse on, and the acknowledgement wrongly succeeds.

**Why this is distinct from [[55|shape 55]]:** shape 55 is a two-clause *entry* gate
(`if a is not None and b is not None:`) where clause 1 is always true in every fixture and
clause 2 is the one that varies, but never paired with a downstream signal that would make its
absence observable. This shape is a SQL `WHERE` clause used for optimistic concurrency, where
*both* columns are meant to vary independently as the CAS's discriminators — the gap isn't a
missing downstream signal, it's that every fixture happened to move the same one column
(`judge_sha`) and never isolated the other (`judge_state`) as the sole point of disagreement.

**Found:** `chela/dispatcher.py::acknowledge_blocked_race` (CMX-336, PR #431, rework round 2).
`tests/test_dispatcher_blocked_race_ack.py::test_acknowledge_is_scoped_to_the_current_judge_sha`
covered the `judge_sha` column but shared its `judge_state` value between the stale read and
the real row throughout, so a `judge_state=? OR 1` mutation left the whole suite green. Closed
by `test_acknowledge_is_scoped_to_the_current_judge_state_not_only_sha`, which holds `judge_sha`
identical between the stale read and the real row and varies only `judge_state`.

**See also:** [[336|shape 336]] — a companion gap found on the same PR, same function, same
round: an explicit argument or payload field proven only by facts true regardless of whether
the real value actually flowed through. That shape is about a *value* silently being replaced
by a fallback or a hardcoded literal; this shape is about a *predicate* silently losing one of
its conjuncts while every fixture's other conjunct still does the work of failing the test.
