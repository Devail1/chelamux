## 370. A "does not raise" guard satisfied by two different mechanisms

**Assertion form:** the guards for a schema migration's read-only-and-current-schema fast
path, its benign-race swallow branch, and its escalation's error message all asserted only
`pytest.raises(...)` or plain "did not raise" — never what the migration function actually
DID.

**Mutation that defeats it:** three independent mutations to `chela/dispatcher.py`'s
`ensure_schema` each survived a full green suite: (a) the discriminator itself deleted —
`existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}` replaced with
`existing_columns: set[str] = set()`; (b) the swallow branch dead-coded —
`if "duplicate column name" in str(e).lower():` turned into `if False and ...`; (c) the
escalation's message blanked — `raise SchemaMigrationError(f"...")` turned into
`raise SchemaMigrationError("")`. (a) and (b) survive together for a subtle reason: on
SQLite, `ALTER TABLE ... ADD COLUMN <existing>` on a READ-ONLY connection raises
`duplicate column name`, not `readonly database` — the duplicate-column check fires BEFORE
the write-permission check. So with the `PRAGMA table_info` pre-check deleted, the
readonly-and-current-schema path still lands in the `duplicate column name` → `continue`
branch and still returns normally — "no exception" is satisfied whether the pre-check skips
the ALTER outright, or the ALTER fires and gets rescued by the fallback message check. A
"does not raise" assertion cannot tell which mechanism ran, so removing either one alone
stays green. (c) survives for the ordinary reason: nothing read the exception's own message.

**Guard form that survives:** wrap the connection in a thin recording proxy — an object whose
`execute()` appends every SQL statement to a list before delegating to the real connection —
and assert on the STATEMENTS the function issued, not merely on whether it raised. "No ALTER
TABLE was ever attempted" (the fast path) and "an ALTER TABLE was attempted and its
OperationalError was swallowed" (the race path) become two independently observable facts
instead of collapsing into the same "returned normally" outcome. For an exception that is
supposed to explain itself, assert the message text directly (the missing column's name, and
the underlying sqlite reason) — not just that raising happened at all.

**Found:** PR #519 (CMX-370) — the judge, applying each of the three mutations above to a
throwaway checkout independently, got `4064 passed, 0 failed, 0 error(s)` every time, because
`tests/test_ensure_schema_readonly.py` asserted only `pytest.raises`/no-raise and never which
mechanism produced that outcome.
