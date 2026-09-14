## 370c. A chained exception's message assertion is blind to whether the chain
exists, and to whether the message is rendered or hard-coded

**Assertion form:** `test_a_readonly_database_missing_a_column_raises_loudly` asserted
`"readonly" in message.lower()` on `SchemaMigrationError`'s own `str()`. This is a
source-constant check: it is satisfied identically whether the message is built by
interpolating the real `sqlite3.OperationalError` (`f"... failed: {e}"`) or by
hard-coding the word "readonly" into the f-string. Nothing observed the exception's
`__cause__` at all, so nothing could tell whether `raise ... from e` was even present.

**Mutation that defeats it:** two independent mutations to `chela/dispatcher.py`'s
`ensure_schema` each survived a full green suite:

- `raise SchemaMigrationError(...) from e` → `raise SchemaMigrationError(...)` (the
  `from e` dropped). `exc_info.value.__cause__` becomes `None` — the chained traceback
  that issue #515 exists to provide is gone — but the message text is unchanged, so the
  substring assertion never notices.
- `f"... failed: {e}"` → `f"... failed: readonly database"` (the interpolation replaced
  with a literal that happens to be true for the one case under test). Every OTHER
  failure this same line can raise for (disk full, `no such table`, a corrupt database)
  now reports "readonly database" regardless of what actually went wrong — the exact
  inscrutable-message bug #515 exists to remove, re-created inside the fix for it. The
  substring check can't distinguish a rendered value from a constant that happens to
  contain the same word.

**Guard form that survives:** for an exception meant to CHAIN a lower-level cause,
assert `isinstance(exc.__cause__, <the underlying exception type>)` directly — this is
the only way to observe that `raise ... from e` (vs. a bare `raise`) is actually present,
since the implicit `__context__` a bare `raise` still sets is invisible to any check that
doesn't read `__cause__` specifically. For the message, assert `str(cause) in message`
— tying the rendered text to the ACTUAL exception instance for this run, not to a word a
human autor decided was always going to be there.

**Found:** PR #519 (CMX-370), round 3 — the judge, applying both mutations above to a
throwaway checkout independently, got `4067 passed, 0 failed, 0 error(s)` both times,
because the readonly test's only message assertion (`"readonly" in message.lower()`) was
a source-constant check that never looked at `__cause__`.

**Round 6 addendum — checking the CAUSE's type is blind to the EXCEPTION's own type:**
`isinstance(cause, sqlite3.OperationalError)` proves the chained *cause* is an
`OperationalError`. It says nothing about `SchemaMigrationError` itself — a class the fix
depends on being a genuinely different type from `sqlite3.OperationalError`, per the class's
own docstring ("`sqlite3.OperationalError` is the SAME exception for duplicate column name
… and attempt to write a readonly database … Raised here instead."). Nothing in the suite
observed that relationship, so:

- **Mutation:** `class SchemaMigrationError(RuntimeError):` → `class
  SchemaMigrationError(sqlite3.OperationalError):`. Every existing assertion — `pytest.raises
  (SchemaMigrationError)`, `isinstance(cause, sqlite3.OperationalError)`, the message checks —
  is satisfied identically, because a subclass instance passes `isinstance` checks for both
  its own type and every ancestor. `4069 passed, 0 failed, 0 error(s)`, unchanged. But every
  `except sqlite3.OperationalError: pass` elsewhere in the codebase (the exact idiom this class
  exists to stop re-swallowing — live one module over at `chela/context.py:68`, filed as issue
  #520) would now silently re-swallow `SchemaMigrationError` too, restoring #515.
- **Guard form that survives:** assert the NEGATIVE relationship directly —
  `assert not issubclass(SchemaMigrationError, sqlite3.OperationalError)` — alongside the
  existing `isinstance(cause, ...)` check on the chained cause. An `isinstance`/`pytest.raises`
  check on an instance can never distinguish "is this type" from "is a subclass of a type it
  must NOT be"; only an explicit `issubclass` assertion on the class itself can.
