"""Issue #515 — ``ensure_schema`` must tell "column already exists" apart from
"attempt to write a readonly database".

Both raise the SAME ``sqlite3.OperationalError``. Swallowing both meant the first
schema migration to land after the #502 sandbox boundary ships would be silently
skipped for every sandboxed agent (no write access to ``~/.chela``), resurfacing
later as an inscrutable ``no such column`` instead of a loud failure at the point
the migration was actually attempted.

docs/defeat_shapes/370-*.md: on SQLite, ``ALTER TABLE ... ADD COLUMN <existing>``
on a READ-ONLY connection raises ``duplicate column name``, not ``readonly
database`` — the duplicate-column check fires BEFORE the write-permission check.
That means "ensure_schema did not raise" is satisfied by TWO different
mechanisms: the ``PRAGMA table_info`` pre-check skipping the ALTER outright, or
the ALTER firing and getting rescued by the "duplicate column name" fallback.
Every guard below therefore asserts on the STATEMENTS ``ensure_schema`` actually
issued (via ``_RecordingConn``), not merely on whether it raised — the three
mutations that survived round 1 (the pre-check deleted, the fallback dead-coded,
the error message blanked) each go red against the guard that targets them.
"""
from __future__ import annotations

import sqlite3

import pytest

from chela import dispatcher


class _RecordingConn:
    """Wraps a real ``sqlite3.Connection``, appending every SQL statement passed
    to ``execute`` to ``self.statements`` before delegating — lets a test assert
    on what ``ensure_schema`` actually DID (which DDL it attempted, or didn't),
    not just on whether it happened to raise.

    ``fake_empty_pragma``, when set, makes any ``PRAGMA table_info`` query return
    an empty result instead of the real one — modelling the benign race from
    issue #515: another connection's own ALTER lands between this connection's
    ``table_info`` read and its write, so every column this connection thinks is
    missing is actually already there by the time its own ALTER runs.
    """

    def __init__(self, conn, fake_empty_pragma: bool = False):
        self._conn = conn
        self.statements: list[str] = []
        self._fake_empty_pragma = fake_empty_pragma

    def execute(self, sql, *args, **kwargs):
        self.statements.append(sql)
        if self._fake_empty_pragma and sql.strip().upper().startswith("PRAGMA TABLE_INFO"):
            return iter(())
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


def _legacy_db_missing_pr_url(path):
    """A pre-migration ``runs`` table — same shape ``test_dispatcher_adopt.py``
    uses to model a database that genuinely predates a column, here missing just
    ``pr_url`` (the very first migration in the list)."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE runs (task_id TEXT PRIMARY KEY, workflow_path TEXT NOT NULL, "
        "title TEXT NOT NULL, status TEXT NOT NULL)"
    )
    conn.commit()
    conn.close()


def _legacy_db_with_adopt_row(path):
    """Same base pre-migration table, missing every migrated column (including
    ``adopted``), seeded with one ``adopt-*`` row — the shape CMX-321's backfill
    exists for."""
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE runs (task_id TEXT PRIMARY KEY, workflow_path TEXT NOT NULL, "
        "title TEXT NOT NULL, status TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO runs (task_id, workflow_path, title, status) "
        "VALUES ('adopt-1', 'wf', 't', 'open')"
    )
    conn.commit()
    conn.close()


def test_a_readonly_database_missing_a_column_raises_loudly(tmp_path):
    """The genuine failure: the column is missing AND the ALTER TABLE cannot run
    because the connection cannot write. This must not look like success — and
    the failure must SAY which column and why (#515: an empty message reproduces
    the exact inscrutability #515 exists to remove)."""
    db = tmp_path / "runs.db"
    _legacy_db_missing_pr_url(db)

    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        with pytest.raises(dispatcher.SchemaMigrationError) as exc_info:
            dispatcher.ensure_schema(ro)
    finally:
        ro.close()

    message = str(exc_info.value)
    assert "pr_url" in message, f"error must name the missing column: {message!r}"
    assert "readonly" in message.lower(), (
        f"error must carry the underlying sqlite reason: {message!r}"
    )


def test_a_writable_database_with_every_column_already_present_stays_silent(tmp_path):
    """MUST BE ACCEPTED — the half a strict fix could break: an already-migrated,
    writable database migrates silently and successfully, exactly as before."""
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(str(db))
    dispatcher.ensure_schema(conn)   # first open: creates + migrates everything

    dispatcher.ensure_schema(conn)   # second open: every column already present

    conn.close()


def test_a_writable_database_missing_a_column_still_migrates(tmp_path):
    """MUST BE ACCEPTED — a genuinely missing column on a WRITABLE connection is
    still added, same as always; only the readonly case is now loud. Asserts the
    ALTER was actually issued (not just that the column exists afterwards by some
    other means)."""
    db = tmp_path / "runs.db"
    _legacy_db_missing_pr_url(db)

    conn = sqlite3.connect(str(db))
    wrapped = _RecordingConn(conn)
    dispatcher.ensure_schema(wrapped)

    assert any(
        "ALTER TABLE runs ADD COLUMN pr_url" in s for s in wrapped.statements
    ), "the missing column must actually be migrated via ALTER TABLE"

    conn.row_factory = sqlite3.Row
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    conn.close()
    assert "pr_url" in cols


def test_a_readonly_connection_on_a_current_schema_attempts_no_ddl(tmp_path):
    """⭐⭐ MUST BE ACCEPTED — the sandboxed-agent path (#502): a read-only
    connection whose schema is ALREADY CURRENT opens and returns normally, having
    attempted NO DDL at all.

    Not just "did not raise": on this DB, a mutated pre-check that fires every
    ALTER anyway would ALSO not raise (an existing column's ALTER hits
    'duplicate column name' before 'readonly database', and that is swallowed as
    the benign race) — so "no exception" can't tell the fast path from the
    fallback rescuing a mistake. Observing "no ALTER attempted" can.

    Negative control (docs/defeat_shapes/370-*.md): revert
    ``existing_columns = {row[1] for row in conn.execute(...)}`` to
    ``existing_columns: set[str] = set()`` — every column looks missing, every
    ALTER fires, this assertion goes RED even though no exception is raised.
    """
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(str(db))
    dispatcher.ensure_schema(conn)  # migrate to current schema
    conn.close()

    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        wrapped = _RecordingConn(ro)
        dispatcher.ensure_schema(wrapped)  # must not raise
        assert not any("ALTER TABLE" in s.upper() for s in wrapped.statements), (
            "schema was already current — no ALTER should have been attempted, "
            f"got: {wrapped.statements!r}"
        )
    finally:
        ro.close()


def test_a_duplicate_column_race_is_swallowed_not_escalated(tmp_path):
    """The benign race (#515): another connection's own ALTER lands between this
    connection's ``PRAGMA table_info`` read and its write, so every ALTER this
    connection attempts hits 'duplicate column name' on an already-current
    column. That must be swallowed, not escalated to SchemaMigrationError.

    Negative control (docs/defeat_shapes/370-*.md): dead-code the swallow branch
    (``if False and "duplicate column name" in str(e).lower():``) and this call
    raises ``SchemaMigrationError`` instead of returning — RED.
    """
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(str(db))
    dispatcher.ensure_schema(conn)  # fully migrate for real

    racing = _RecordingConn(conn, fake_empty_pragma=True)
    dispatcher.ensure_schema(racing)  # must not raise

    assert any("ALTER TABLE" in s.upper() for s in racing.statements), (
        "the faked-empty PRAGMA must have driven ensure_schema to attempt DDL "
        "for this assertion to mean anything"
    )


def test_the_adopted_backfill_runs_once_then_never_again(tmp_path):
    """⚖️🚪 CMX-321. The UPDATE that backfills ``adopted=1`` for pre-existing
    ``adopt-*`` rows must fire on the tick that ADDS the column, and never again
    after — gated on ``added`` (the column having been added THIS call). A
    missing gate resurfaces CMX-276's bug: an ``adopt-*`` row struck ``done`` on
    the very next reconcile because a live re-evaluation disagreed with the one
    that ran at claim time."""
    db = tmp_path / "runs.db"
    _legacy_db_with_adopt_row(db)

    conn = sqlite3.connect(str(db))
    first = _RecordingConn(conn)
    dispatcher.ensure_schema(first)
    assert any("UPDATE runs SET adopted" in s for s in first.statements), (
        "adopted was just added by this call — the backfill must run"
    )

    second = _RecordingConn(conn)
    dispatcher.ensure_schema(second)
    assert not any("UPDATE runs SET adopted" in s for s in second.statements), (
        "adopted was already present — the backfill must not run again"
    )

    conn.close()
