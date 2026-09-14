"""Issue #520 — `context.ensure_schema` carries the SAME #515 sentinel collision
`dispatcher.ensure_schema` had before CMX-370 (#519): ``sqlite3.OperationalError``
is raised for BOTH ``duplicate column name: …`` (benign — another connection
already added it) and ``attempt to write a readonly database`` (the migration did
NOT happen — e.g. a sandboxed agent with ``~/.chela`` denied, #502). Swallowing
both meant the second case silently skipped the migration, resurfacing later as
an inscrutable ``no such column``.

This mirrors ``tests/test_ensure_schema_readonly.py`` (CMX-370) — same guard
shapes, ported to ``context_snapshots``. CMX-370 took 8 judge rounds to land
guards that couldn't be defeated by mutation; see docs/defeat_shapes/370*.md for
the catalogued mutations reused as negative controls below:

docs/defeat_shapes/370-*.md: on SQLite, ``ALTER TABLE ... ADD COLUMN <existing>``
on a READ-ONLY connection raises ``duplicate column name``, not ``readonly
database`` — the duplicate-column check fires BEFORE the write-permission check.
That means "ensure_schema did not raise" is satisfied by TWO different
mechanisms: the ``PRAGMA table_info`` pre-check skipping the ALTER outright, or
the ALTER firing and getting rescued by the "duplicate column name" fallback.
Every guard below therefore asserts on the STATEMENTS ``ensure_schema`` actually
issued (via ``_RecordingConn``), not merely on whether it raised.

docs/defeat_shapes/370c-*.md / 370d-*.md: a chained exception's message
assertion is blind to whether the chain exists (``raise ... from e`` dropped) and
to whether the message is rendered or hard-coded. Assert ``__cause__`` directly,
and vary which column is missing so a hard-coded literal can't pass.
"""
from __future__ import annotations

import sqlite3

import pytest

from chela import context, dispatcher


class _RecordingConn:
    """Wraps a real ``sqlite3.Connection``, appending every SQL statement passed
    to ``execute`` to ``self.statements`` before delegating — lets a test assert
    on what ``ensure_schema`` actually DID, not just on whether it raised.

    ``fake_empty_pragma``, when set, makes any ``PRAGMA table_info`` query
    return an empty result instead of the real one — modelling the benign race
    from issue #515/#520: another connection's own ALTER lands between this
    connection's ``table_info`` read and its write, so every column this
    connection thinks is missing is actually already there by the time its own
    ALTER runs.

    ``raise_on_alter``, when set, is raised instead of delegating any
    ``ALTER TABLE`` — models an ``OperationalError`` that is neither the
    benign duplicate-column race nor the readonly-database fixture (#521:
    ``database is locked``, disk full, a malformed database image, …).
    """

    def __init__(self, conn, fake_empty_pragma: bool = False, raise_on_alter: Exception | None = None):
        self._conn = conn
        self.statements: list[str] = []
        self._fake_empty_pragma = fake_empty_pragma
        self._raise_on_alter = raise_on_alter

    def execute(self, sql, *args, **kwargs):
        self.statements.append(sql)
        if self._fake_empty_pragma and sql.strip().upper().startswith("PRAGMA TABLE_INFO"):
            return iter(())
        if self._raise_on_alter is not None and sql.strip().upper().startswith("ALTER TABLE"):
            raise self._raise_on_alter
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _legacy_db_missing_every_migrated_column(path):
    """A pre-migration ``context_snapshots`` table — missing every column
    ``ensure_schema`` migrates in (``model`` first)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE context_snapshots (id INTEGER PRIMARY KEY, agent TEXT NOT NULL, "
        "ts TEXT NOT NULL, used_k REAL, total_k REAL, used_pct REAL, messages_k REAL, "
        "messages_pct REAL, free_k REAL, free_pct REAL, rate_limit_pct REAL)"
    )
    conn.commit()
    conn.close()


def test_a_readonly_database_missing_a_column_raises_loudly(tmp_path):
    """The genuine failure: a column is missing AND the ALTER TABLE cannot run
    because the connection cannot write. This must not look like success — and
    the failure must SAY which column and why."""
    db = tmp_path / "scheduler.db"
    _legacy_db_missing_every_migrated_column(db)

    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        with pytest.raises(dispatcher.SchemaMigrationError) as exc_info:
            context.ensure_schema(ro)
    finally:
        ro.close()

    message = str(exc_info.value)
    assert "model" in message, f"error must name the missing column: {message!r}"
    assert "readonly" in message.lower(), (
        f"error must carry the underlying sqlite reason: {message!r}"
    )
    # docs/defeat_shapes/370c-*.md: assert the exception is actually CHAINED to
    # the underlying sqlite3.OperationalError (dropping `from e` leaves
    # __cause__ None), and that the message renders THAT error's own text (not
    # a constant that merely happens to be true for this one case).
    cause = exc_info.value.__cause__
    assert isinstance(cause, sqlite3.OperationalError), (
        f"the sqlite error must be chained via `raise ... from e`: {cause!r}"
    )
    assert str(cause) in message, (
        f"the error must render the sqlite reason, not a constant: {message!r}"
    )
    # Reused SchemaMigrationError must stay a DIFFERENT type from
    # sqlite3.OperationalError, or every `except sqlite3.OperationalError: pass`
    # elsewhere would re-swallow it and silently restore #515/#520.
    assert not issubclass(dispatcher.SchemaMigrationError, sqlite3.OperationalError), (
        "SchemaMigrationError must not be a subclass of sqlite3.OperationalError"
    )


def test_a_readonly_database_missing_a_later_column_names_that_column(tmp_path):
    """docs/defeat_shapes/370d-*.md: the fixture above is missing ``model``, the
    FIRST column in ``ensure_schema``'s migration list, so ``"model" in message``
    would be satisfied identically by an interpolated OR a hard-coded literal.
    Drop a column that is NOT first and assert the error names THAT column
    instead — a hard-coded ``"model"`` can never satisfy this."""
    db = tmp_path / "scheduler.db"
    conn = sqlite3.connect(str(db))
    context.ensure_schema(conn)  # migrate to current schema (every column present)
    conn.execute("ALTER TABLE context_snapshots DROP COLUMN session_name")
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        with pytest.raises(dispatcher.SchemaMigrationError) as exc_info:
            context.ensure_schema(ro)
    finally:
        ro.close()

    message = str(exc_info.value)
    assert "session_name" in message, f"error must name the actually-missing column: {message!r}"
    assert "model" not in message, (
        f"model IS present in this db — it must never appear in the error: {message!r}"
    )


def test_a_writable_database_with_every_column_already_present_stays_silent(tmp_path):
    """MUST BE ACCEPTED — an already-migrated, writable database migrates
    silently and successfully, exactly as before."""
    db = tmp_path / "scheduler.db"
    conn = sqlite3.connect(str(db))
    context.ensure_schema(conn)   # first open: creates + migrates everything
    context.ensure_schema(conn)   # second open: every column already present
    conn.close()


def test_a_writable_database_missing_a_column_still_migrates(tmp_path):
    """MUST BE ACCEPTED — a genuinely missing column on a WRITABLE connection is
    still added, same as always."""
    db = tmp_path / "scheduler.db"
    _legacy_db_missing_every_migrated_column(db)

    conn = sqlite3.connect(str(db))
    wrapped = _RecordingConn(conn)
    context.ensure_schema(wrapped)

    assert any(
        "ALTER TABLE context_snapshots ADD COLUMN model" in s for s in wrapped.statements
    ), "the missing column must actually be migrated via ALTER TABLE"

    cols = {row[1] for row in conn.execute("PRAGMA table_info(context_snapshots)")}
    conn.close()
    assert "model" in cols


def test_a_readonly_connection_on_a_current_schema_attempts_no_ddl(tmp_path):
    """⭐⭐ MUST BE ACCEPTED — the sandboxed-agent path (#502): a read-only
    connection whose schema is ALREADY CURRENT opens and returns normally,
    having attempted NO ``ALTER TABLE`` at all.

    Negative control (docs/defeat_shapes/370-*.md): revert the PRAGMA-derived
    ``existing_columns`` to an empty set — every column looks missing, every
    ALTER fires (and gets rescued by the duplicate-column fallback), no
    exception is raised, but this assertion goes RED."""
    db = tmp_path / "scheduler.db"
    conn = sqlite3.connect(str(db))
    context.ensure_schema(conn)  # migrate to current schema
    conn.close()

    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        wrapped = _RecordingConn(ro)
        context.ensure_schema(wrapped)  # must not raise
        assert not any("ALTER TABLE" in s.upper() for s in wrapped.statements), (
            "schema was already current — no ALTER should have been attempted, "
            f"got: {wrapped.statements!r}"
        )
    finally:
        ro.close()


def test_a_duplicate_column_race_is_swallowed_for_every_column_not_just_the_first(tmp_path):
    """The benign race (#515/#520): another connection's own ALTER lands
    between this connection's ``PRAGMA table_info`` read and its write, so
    every ALTER this connection attempts hits 'duplicate column name' on an
    already-current column. That must be swallowed for THAT column only —
    migration must keep going for every column after it, not abandon the loop
    at the first race (docs/defeat_shapes/370b-*.md: an ``any(...)`` check over
    the loop's statements is satisfied by the FIRST column alone, hiding an
    early ``break``)."""
    db = tmp_path / "scheduler.db"
    conn = sqlite3.connect(str(db))
    context.ensure_schema(conn)  # fully migrate for real

    racing = _RecordingConn(conn, fake_empty_pragma=True)
    context.ensure_schema(racing)  # must not raise

    assert any("ALTER TABLE" in s.upper() for s in racing.statements), (
        "the faked-empty PRAGMA must have driven ensure_schema to attempt DDL "
        "for this assertion to mean anything"
    )
    # `session_name` is the LAST column in the migration list: it can only
    # appear here if the loop kept migrating past the FIRST race (`model`)
    # instead of abandoning the rest.
    assert any(
        "ALTER TABLE context_snapshots ADD COLUMN session_name" in s for s in racing.statements
    ), (
        "losing a race on an early column must not abandon migrating the rest "
        f"— got only: {racing.statements!r}"
    )
    conn.close()


def test_a_third_kind_of_operational_error_on_alter_table_escalates(tmp_path):
    """#521 / docs/defeat_shapes/351-*.md: the benign-race check must be a
    WHITELIST (only ``"duplicate column name"`` is swallowed), never a
    BLACKLIST (e.g. "swallow anything that isn't 'readonly'"). ``ALTER TABLE``
    can raise ``sqlite3.OperationalError`` for reasons that are neither the
    benign race NOR the readonly-database fixture — ``database is locked``,
    disk full, a malformed database image, etc. Those must ESCALATE as
    ``SchemaMigrationError``, exactly like the readonly case, never be
    swallowed as if they were the race.

    A blacklist (``"readonly" not in str(e).lower()``) passes every OTHER
    guard in this file — neither existing fixture drives the ``except`` with
    anything but ``duplicate column name`` or ``readonly database`` — which is
    exactly why this needs its own test rather than relying on the others."""
    db = tmp_path / "scheduler.db"
    _legacy_db_missing_every_migrated_column(db)

    conn = sqlite3.connect(str(db))
    try:
        injecting = _RecordingConn(
            conn, raise_on_alter=sqlite3.OperationalError("database is locked")
        )
        with pytest.raises(dispatcher.SchemaMigrationError) as exc_info:
            context.ensure_schema(injecting)
    finally:
        conn.close()

    message = str(exc_info.value)
    assert "model" in message, f"error must name the missing column: {message!r}"
    assert "database is locked" in message, (
        f"error must carry the underlying sqlite reason: {message!r}"
    )
    cause = exc_info.value.__cause__
    assert isinstance(cause, sqlite3.OperationalError), (
        f"the sqlite error must be chained via `raise ... from e`: {cause!r}"
    )
