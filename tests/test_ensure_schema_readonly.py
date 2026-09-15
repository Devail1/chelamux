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

docs/defeat_shapes/370c-*.md: ``"readonly" in message.lower()`` is a
source-constant check — satisfied identically whether the message interpolates
the real ``sqlite3.OperationalError`` or hard-codes the word "readonly", and
blind to whether that error is actually chained via ``raise ... from e``. Round
3's mutations (drop ``from e``; replace ``{e}`` with a hard-coded
``"readonly database"``) both survived a full green suite. The guard asserts
``exc_info.value.__cause__`` is the real ``sqlite3.OperationalError`` and that
its rendered text appears in the message — pinned to the actual exception, not
a word that happens to be true for this one case.

docs/defeat_shapes/351-*.md: the benign-race check is a WHITELIST
(``"duplicate column name" in str(e).lower()``), but nothing pinned it against
being inverted to a BLACKLIST (``"readonly" not in str(e).lower()``) — every
existing fixture here raises either the benign race or the readonly-database
case, both classified identically by either form, so the inversion survives a
full green suite. A third kind of ``OperationalError`` (``database is
locked``, disk full, a malformed database image — #521) would then take the
swallow path under a blacklist instead of escalating. Closed by
``test_a_third_kind_of_operational_error_on_alter_table_escalates``, which
injects exactly that third kind via ``_RecordingConn.raise_on_alter``.
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
    # docs/defeat_shapes/370c-*.md: "readonly" in message.lower() is a source-constant
    # check — satisfied identically by interpolating the real sqlite error and by a
    # hard-coded "readonly database" literal, so it can't tell the two apart. Assert the
    # exception is actually CHAINED to the underlying sqlite3.OperationalError (dropping
    # `from e` leaves __cause__ None) and that the message renders THAT error's own text
    # (not a constant that merely happens to be true for this one case).
    cause = exc_info.value.__cause__
    assert isinstance(cause, sqlite3.OperationalError), (
        f"the sqlite error must be chained via `raise ... from e`: {cause!r}"
    )
    assert str(cause) in message, (
        f"the error must render the sqlite reason, not a constant: {message!r}"
    )
    # docs/defeat_shapes/370c-*.md: SchemaMigrationError must stay a DIFFERENT type from
    # sqlite3.OperationalError. isinstance(cause, sqlite3.OperationalError) above proves
    # only that the chained CAUSE is an OperationalError, not that SchemaMigrationError
    # itself is distinct from it — if SchemaMigrationError ever became a subclass of
    # sqlite3.OperationalError, every `except sqlite3.OperationalError: pass` elsewhere
    # (e.g. chela/context.py:68) would re-swallow it and silently restore #515.
    assert not issubclass(dispatcher.SchemaMigrationError, sqlite3.OperationalError), (
        "SchemaMigrationError must not be a subclass of sqlite3.OperationalError — "
        "that separation is the whole point of raising it instead of letting the "
        "OperationalError propagate"
    )


def test_a_readonly_database_missing_a_later_column_names_that_column(tmp_path):
    """docs/defeat_shapes/370d-*.md: the ONLY fixture that reaches the
    ``SchemaMigrationError`` branch (``_legacy_db_missing_pr_url``) is missing exactly
    ``pr_url`` — the FIRST column in ``ensure_schema``'s migration list — so
    ``"pr_url" in message`` is satisfied identically whether the message interpolates
    ``_column`` or hard-codes the literal ``"pr_url"``: for this one fixture, the
    rendered value and the asserted literal are the same single case.

    Drop a column that is NOT first in the migration list and assert the error names
    THAT column instead — a hard-coded ``"pr_url"`` can never satisfy this, no matter
    which real column actually failed.

    Negative control: ``f"runs.{_column} is missing and ALTER TABLE failed: {e}"`` ->
    ``f"runs.pr_url is missing and ALTER TABLE failed: {e}"`` (round 4's surviving
    mutation) renders "pr_url" regardless of which column's ALTER actually failed —
    this test goes RED under that mutation even though
    ``test_a_readonly_database_missing_a_column_raises_loudly`` stays green.
    """
    db = tmp_path / "runs.db"
    conn = sqlite3.connect(str(db))
    dispatcher.ensure_schema(conn)  # migrate to current schema (every column present)
    conn.execute("ALTER TABLE runs DROP COLUMN task_number")  # not first in the list
    conn.commit()
    conn.close()

    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        with pytest.raises(dispatcher.SchemaMigrationError) as exc_info:
            dispatcher.ensure_schema(ro)
    finally:
        ro.close()

    message = str(exc_info.value)
    assert "task_number" in message, f"error must name the actually-missing column: {message!r}"
    assert "pr_url" not in message, (
        f"pr_url IS present in this db — it must never appear in the error: {message!r}"
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
    column. That must be swallowed for THAT column only — migration must keep
    going for every column after it — and swallowing a race must never make
    ``added`` believe the raced column was actually added by this call.

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
    # Every column looks missing (the faked PRAGMA returns nothing), so every
    # single one of them races and swallows. `blocked_race_ack_sha` is the LAST
    # column in the migration list: it can only appear here if the loop kept
    # migrating past the FIRST race instead of abandoning the rest.
    # Negative control: swap the swallow's `continue` for `break` — the loop
    # stops at the first raced column (`pr_url`) and this goes RED.
    assert any(
        "ALTER TABLE runs ADD COLUMN blocked_race_ack_sha" in s for s in racing.statements
    ), (
        "losing a race on an early column must not abandon migrating the rest "
        f"— got only: {racing.statements!r}"
    )
    # `adopted` raced too (its ALTER hit 'duplicate column name', same as every
    # other column here) — it was NOT added by THIS call, so the CMX-321
    # backfill gated on `added` must not fire for it.
    # Negative control: record `added` before checking the ALTER succeeded
    # (i.e. add before execute) — a raced column enters `added` anyway and this
    # goes RED.
    assert not any("UPDATE runs SET adopted" in s for s in racing.statements), (
        "adopted was raced (not genuinely added by this call) — the backfill "
        f"must not fire: {racing.statements!r}"
    )


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
    exactly why this needs its own test rather than relying on the others.
    Mirrors ``test_a_third_kind_of_operational_error_on_alter_table_escalates``
    in ``tests/test_context_ensure_schema_readonly.py`` (CMX-371, #520), which
    closed the same shape for ``chela.context.ensure_schema`` — this closes it
    for ``chela.dispatcher.ensure_schema``.
    """
    db = tmp_path / "runs.db"
    _legacy_db_missing_pr_url(db)

    conn = sqlite3.connect(str(db))
    try:
        injecting = _RecordingConn(
            conn, raise_on_alter=sqlite3.OperationalError("database is locked")
        )
        with pytest.raises(dispatcher.SchemaMigrationError) as exc_info:
            dispatcher.ensure_schema(injecting)
    finally:
        conn.close()

    message = str(exc_info.value)
    assert "pr_url" in message, f"error must name the missing column: {message!r}"
    assert "database is locked" in message, (
        f"error must carry the underlying sqlite reason: {message!r}"
    )
    cause = exc_info.value.__cause__
    assert isinstance(cause, sqlite3.OperationalError), (
        f"the sqlite error must be chained via `raise ... from e`: {cause!r}"
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


def _migrated_db_missing_only(path, column_to_drop):
    """A fully-migrated ``runs`` table (every column ``ensure_schema`` knows
    about, including ``adopted``), seeded with one ``adopt-*`` row, with exactly
    ONE column then dropped back out. Unlike ``_legacy_db_with_adopt_row``
    (missing every column at once, so ``pr_url`` and ``adopted`` are ALWAYS
    added together and no assertion can tell which one the gate actually reads
    — docs/defeat_shapes/342), this fixture makes the two columns disagree: only
    ``column_to_drop`` is missing, so ``ensure_schema`` adds that one column and
    nothing else."""
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
    dispatcher.ensure_schema(conn)  # migrate everything, including adopted
    conn.execute(f"ALTER TABLE runs DROP COLUMN {column_to_drop}")
    conn.commit()
    conn.close()


def test_the_adopted_backfill_is_gated_on_adopted_itself_not_a_coinciding_column(tmp_path):
    """⚖️🚪 CMX-321 / docs/defeat_shapes/342. The gate is ``if "adopted" in
    added`` — it must read WHETHER ``adopted`` ITSELF WAS JUST ADDED, not
    whether some other column (e.g. ``pr_url``, the first entry in the
    migration list) happened to be added in the same call. Every existing
    fixture is a bare pre-migration table missing BOTH columns at once, so
    ``"adopted" in added`` and ``"pr_url" in added`` are indistinguishable to
    any assertion built on it — the two quantities coincide in every fixture.
    These two single-column-missing fixtures make them disagree in each
    direction; a gate that reads the wrong column fails exactly one of the two.

    Negative control: replace the gate's ``"adopted"`` with ``"pr_url"`` — the
    first case below (pr_url missing alone) then wrongly fires the backfill,
    and the second case (adopted missing alone) wrongly withholds it."""
    pr_url_missing = tmp_path / "pr_url_missing.db"
    _migrated_db_missing_only(pr_url_missing, "pr_url")
    conn = sqlite3.connect(str(pr_url_missing))
    wrapped = _RecordingConn(conn)
    dispatcher.ensure_schema(wrapped)
    assert any(
        "ALTER TABLE runs ADD COLUMN pr_url" in s for s in wrapped.statements
    ), f"pr_url should have been the only column missing: {wrapped.statements!r}"
    assert not any("ADD COLUMN adopted" in s for s in wrapped.statements), (
        f"adopted must already be present in this fixture: {wrapped.statements!r}"
    )
    assert not any("UPDATE runs SET adopted" in s for s in wrapped.statements), (
        "pr_url alone being added must NOT fire the adopted backfill — "
        f"got: {wrapped.statements!r}"
    )
    conn.close()

    adopted_missing = tmp_path / "adopted_missing.db"
    _migrated_db_missing_only(adopted_missing, "adopted")
    conn = sqlite3.connect(str(adopted_missing))
    wrapped = _RecordingConn(conn)
    dispatcher.ensure_schema(wrapped)
    assert any(
        "ALTER TABLE runs ADD COLUMN adopted" in s for s in wrapped.statements
    ), f"adopted should have been the only column missing: {wrapped.statements!r}"
    assert not any("ADD COLUMN pr_url" in s for s in wrapped.statements), (
        f"pr_url must already be present in this fixture: {wrapped.statements!r}"
    )
    assert any("UPDATE runs SET adopted" in s for s in wrapped.statements), (
        "adopted alone being added MUST fire the backfill — "
        f"got: {wrapped.statements!r}"
    )
    conn.close()
