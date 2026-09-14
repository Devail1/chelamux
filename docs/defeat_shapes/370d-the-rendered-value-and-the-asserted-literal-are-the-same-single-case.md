## 370d. The rendered value and the asserted literal are the same single case, so
an interpolation and a constant are indistinguishable

**Assertion form:** `test_a_readonly_database_missing_a_column_raises_loudly` asserted
`"pr_url" in message` on `SchemaMigrationError`'s own `str()`. The suite has exactly one
fixture that reaches this branch (`_legacy_db_missing_pr_url`), and it is missing exactly
one column: `pr_url` — which also happens to be the FIRST entry in `ensure_schema`'s
migration list, so it is the column whose `ALTER TABLE` fires (and fails) first on a
readonly connection every time. The value under test and the literal in the assertion are
therefore the same single case, always — a column name interpolated from `_column` and a
column name hard-coded as the constant `"pr_url"` render byte-identical output for every
input this test ever exercises.

**Mutation that defeats it:** `chela/dispatcher.py`'s `ensure_schema`:

```diff
- f"runs.{_column} is missing and ALTER TABLE failed: {e}"
+ f"runs.pr_url is missing and ALTER TABLE failed: {e}"
```

`CHELA_REQUIRE_JS_TESTS=1 uv run pytest -q` stayed green (4067 passed, 0 failed) with this
applied — the only test reaching the branch could never observe the difference, because
`_column` was always `"pr_url"` on the one fixture that ever ran it.

**Guard form that survives:** don't test the loop's first iteration on the only fixture
that reaches the branch — drop a column that is NOT first in the migration list (here:
`ALTER TABLE runs DROP COLUMN task_number` on an otherwise fully-migrated db) and assert
the error names THAT column, and that it does NOT name the column that is actually
present (`pr_url`). A hard-coded literal can only ever match one specific column; forcing
the failure to land on a *different* one for the same code path makes the literal and the
interpolation diverge, which is the only way to prove the message is rendered rather than
constant.

More generally: when an assertion's expected literal and the fixture's one input value
are the same across the entire suite, that assertion cannot tell "this was computed from
the input" apart from "this was always going to be true regardless of the input" — vary
the input on a second test until the literal would have to be wrong if it weren't
actually rendered.

**Found:** PR #519 (CMX-370), round 4 — the judge applied the mutation above to a
throwaway checkout and it stayed green, because `"pr_url" in message` was satisfied
identically by both the interpolated and the hard-coded version of the string.
