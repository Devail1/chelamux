"""Issue #515 — ``ensure_schema`` must tell "column already exists" apart from
"attempt to write a readonly database".

Both raise the SAME ``sqlite3.OperationalError``. Swallowing both meant the first
schema migration to land after the #502 sandbox boundary ships would be silently
skipped for every sandboxed agent (no write access to ``~/.chela``), resurfacing
later as an inscrutable ``no such column`` instead of a loud failure at the point
the migration was actually attempted.
"""
from __future__ import annotations

import sqlite3

import pytest

from chela import dispatcher


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


def test_a_readonly_database_missing_a_column_raises_loudly(tmp_path):
    """The genuine failure: the column is missing AND the ALTER TABLE cannot run
    because the connection cannot write. This must not look like success."""
    db = tmp_path / "runs.db"
    _legacy_db_missing_pr_url(db)

    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        with pytest.raises(dispatcher.SchemaMigrationError):
            dispatcher.ensure_schema(ro)
    finally:
        ro.close()


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
    still added, same as always; only the readonly case is now loud."""
    db = tmp_path / "runs.db"
    _legacy_db_missing_pr_url(db)

    conn = sqlite3.connect(str(db))
    dispatcher.ensure_schema(conn)

    conn.row_factory = sqlite3.Row
    cols = {row[1] for row in conn.execute("PRAGMA table_info(runs)")}
    conn.close()
    assert "pr_url" in cols
