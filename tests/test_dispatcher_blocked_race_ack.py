"""``chela judge ack-blocked-race`` (CMX-336) — the operator exit for a `J_BLOCKED_RACE`
row that can never resolve on its own once its PR is merged or closed.

``_blocked_race_resolved`` (chela/runtime_truth.py) clears a row on exactly one condition:
the PR's head moving past the judged commit (`judge_sha != pr_head_sha`). That can never
happen once the PR is merged or closed — the branch is gone, nothing will ever push to it
again — so `chela doctor` (and the ntfy notifier it feeds) would report the row forever,
with no operator exit besides hand-editing `scheduler.db`.

`dispatcher.acknowledge_blocked_race` is that exit. These tests pin: it stamps
`blocked_race_ack_by`/`_at`/`_note`/`_sha` WITHOUT touching `judge_state`/`judge_detail`/
`judge_sha` themselves (the verdict is acknowledged, never rewritten); it refuses when there
is nothing to acknowledge (wrong `judge_state`, unknown run); and the acknowledgement is
scoped to the exact `judge_sha` it was given for, so a later race on a NEW sha is not
silently covered by an old acknowledgement.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from chela import dispatcher, event_log, judge


@pytest.fixture(autouse=True)
def _own_runs_db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")


def _row(conn, task_id="abc123", **over) -> sqlite3.Row:
    fields = {
        "task_id": task_id, "workflow_path": "/repo/WORKFLOW.md", "title": "do a thing",
        "status": "done", "window_name": None, "worktree_path": "/wt/abc123",
        "branch_name": "test-1", "started_at": "2026-07-14T10:00:00+00:00", "attempt": 1,
        "task_number": 1, "pr_url": "https://github.com/o/r/pull/80", "pr_state": "merged",
        "judge_state": judge.J_BLOCKED_RACE, "judge_sha": "de291ca34a62",
        "judge_detail": "a guard SURVIVED corruption",
        "review_history": json.dumps([]),
    }
    fields.update(over)
    cols = ", ".join(fields)
    conn.execute(
        f"INSERT INTO runs ({cols}) VALUES ({', '.join('?' * len(fields))})",
        tuple(fields.values()),
    )
    conn.commit()
    return conn.execute("SELECT * FROM runs WHERE task_id=?", (task_id,)).fetchone()


# --- (a) the happy path: stamps the ack columns, leaves the verdict untouched -----------

def test_acknowledge_stamps_by_at_note_sha_without_touching_the_verdict(tmp_path, monkeypatch):
    monkeypatch.setenv("USER", "liav")
    with dispatcher._db() as conn:
        _row(conn)

    result = dispatcher.acknowledge_blocked_race("abc123", note="already shipped, safe to ack")

    assert result["ok"] is True
    assert result["task_id"] == "abc123"
    assert result["by"] == "liav"
    assert result["sha"] == "de291ca34a62"
    assert result["pr_url"] == "https://github.com/o/r/pull/80"

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] == "liav"
    assert row["blocked_race_ack_sha"] == "de291ca34a62"
    assert row["blocked_race_ack_note"] == "already shipped, safe to ack"
    assert row["blocked_race_ack_at"]
    # ⛔ the original verdict is UNTOUCHED — acknowledged, not rewritten.
    assert row["judge_state"] == judge.J_BLOCKED_RACE
    assert row["judge_sha"] == "de291ca34a62"
    assert row["judge_detail"] == "a guard SURVIVED corruption"


def test_acknowledge_logs_an_event(tmp_path):
    with dispatcher._db() as conn:
        _row(conn)

    dispatcher.acknowledge_blocked_race("abc123", by="liav")

    kinds = [e["type"] for e in event_log.read()["events"]]
    assert "blocked_race_ack" in kinds


def test_acknowledge_event_payload_carries_who_acknowledged_it(tmp_path):
    """The durable audit record — not just the fact that an event of this type fired
    (that alone survives a defeat that hardcodes the payload's ``by`` field to ``""``
    while the DB column and the printed message still carry the real actor)."""
    with dispatcher._db() as conn:
        _row(conn)

    dispatcher.acknowledge_blocked_race("abc123", by="someone-distinctive")

    events = [e for e in event_log.read()["events"] if e["type"] == "blocked_race_ack"]
    assert events, "expected a blocked_race_ack event"
    assert events[-1]["payload"]["by"] == "someone-distinctive"


def test_acknowledge_event_payload_carries_the_acknowledged_sha(tmp_path):
    """CMX-336 rework round 2: the payload's ``sha`` is the exact ``judge_sha`` this
    acknowledgement covers — scoping the ack to that sha is the invariant the whole
    feature leans on, and the event is its durable audit record. Same defeat shape as
    the ``by`` test above (`docs/defeat_shapes/336-*.md`), but on a different field: a
    defeat that hardcodes the payload's ``sha`` to ``""`` while the DB column and
    ``result["sha"]`` still carry the real value would otherwise survive, since no test
    read the payload's ``sha`` back independently."""
    with dispatcher._db() as conn:
        _row(conn, judge_sha="de291ca34a62")

    dispatcher.acknowledge_blocked_race("abc123")

    events = [e for e in event_log.read()["events"] if e["type"] == "blocked_race_ack"]
    assert events, "expected a blocked_race_ack event"
    assert events[-1]["payload"]["sha"] == "de291ca34a62"


def test_acknowledge_event_payload_carries_the_note(tmp_path):
    """CMX-336 rework round 3: the same defeat shape as the ``by``/``sha`` payload tests
    above (`docs/defeat_shapes/336-*.md`), on the third of the four stamped fields — the
    note is the 'why' of the who/when/why this command is documented to stamp. It lives in
    exactly two places, the DB column and this payload; only the column was ever read back
    by a test, so a defeat that hardcodes the payload's ``note`` to ``""`` would otherwise
    survive."""
    with dispatcher._db() as conn:
        _row(conn)

    dispatcher.acknowledge_blocked_race("abc123", note="already shipped, safe to ack")

    events = [e for e in event_log.read()["events"] if e["type"] == "blocked_race_ack"]
    assert events, "expected a blocked_race_ack event"
    assert events[-1]["payload"]["note"] == "already shipped, safe to ack"


def test_acknowledge_event_payload_carries_the_ack_timestamp(tmp_path):
    """CMX-336 rework round 3: same shape again, on the fourth and last stamped field —
    ``at`` is WHEN it was acknowledged. ``event_log.append``'s own docstring says the
    payload, not the summary, is what a filter/de-dup/UI actually works with, so a blank
    ``at`` in the audit record is not covered by the event's own envelope timestamp. A
    defeat that hardcodes the payload's ``at`` to ``""`` would otherwise survive, since no
    test read the payload's ``at`` back independently."""
    with dispatcher._db() as conn:
        _row(conn)

    dispatcher.acknowledge_blocked_race("abc123")

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    events = [e for e in event_log.read()["events"] if e["type"] == "blocked_race_ack"]
    assert events, "expected a blocked_race_ack event"
    assert events[-1]["payload"]["at"]
    assert events[-1]["payload"]["at"] == row["blocked_race_ack_at"]


def test_acknowledge_event_payload_carries_the_task_id(tmp_path):
    """CMX-336 rework round 5: same defeat shape as the ``by``/``sha``/``note``/``at``
    payload tests above (`docs/defeat_shapes/336-*.md`), on the identity fields —
    ``task_id`` and ``pr_url`` are what makes the audit record attributable to a specific
    run at all. Only ``result["task_id"]``/``result["pr_url"]`` were ever read back by a
    test; a defeat that hardcodes the payload's ``task_id`` to ``""`` (while the return
    value and DB row still carry the real one) would otherwise survive."""
    with dispatcher._db() as conn:
        _row(conn, task_id="distinctive-task-id")

    dispatcher.acknowledge_blocked_race("distinctive-task-id")

    events = [e for e in event_log.read()["events"] if e["type"] == "blocked_race_ack"]
    assert events, "expected a blocked_race_ack event"
    assert events[-1]["payload"]["task_id"] == "distinctive-task-id"


def test_acknowledge_event_payload_carries_the_pr_url(tmp_path):
    """CMX-336 rework round 5: same defeat shape, on the other identity field. A defeat
    that hardcodes the payload's ``pr_url`` to ``None`` (while ``result["pr_url"]`` still
    carries the real value) would otherwise survive, since no test read the payload's
    ``pr_url`` back independently."""
    with dispatcher._db() as conn:
        _row(conn, pr_url="https://github.com/o/r/pull/431")

    dispatcher.acknowledge_blocked_race("abc123")

    events = [e for e in event_log.read()["events"] if e["type"] == "blocked_race_ack"]
    assert events, "expected a blocked_race_ack event"
    assert events[-1]["payload"]["pr_url"] == "https://github.com/o/r/pull/431"


def test_acknowledge_defaults_by_to_env_user_when_not_given(tmp_path, monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.setenv("USERNAME", "windows-liav")
    with dispatcher._db() as conn:
        _row(conn)

    result = dispatcher.acknowledge_blocked_race("abc123")
    assert result["by"] == "windows-liav"


def test_acknowledge_falls_back_to_unknown_with_no_actor_available(tmp_path, monkeypatch):
    monkeypatch.delenv("USER", raising=False)
    monkeypatch.delenv("USERNAME", raising=False)
    with dispatcher._db() as conn:
        _row(conn)

    result = dispatcher.acknowledge_blocked_race("abc123")
    assert result["by"] == "unknown"


def test_acknowledge_records_the_explicitly_supplied_by_not_the_env_fallback(tmp_path, monkeypatch):
    """The explicit ``--by NAME`` is WHO — the first of the four things this command is
    documented to stamp. Set ``$USER`` to something ELSE so a defeat that ignores the
    ``by`` argument and falls through to the env fallback chain (e.g. corrupting
    ``who = (by or "").strip() or ...`` into ``who = ("" or "").strip() or ...``) is
    caught: it would record the env user instead of the name actually passed."""
    monkeypatch.setenv("USER", "env-fallback-user")
    with dispatcher._db() as conn:
        _row(conn)

    result = dispatcher.acknowledge_blocked_race("abc123", by="someone-distinctive")
    assert result["by"] == "someone-distinctive"

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] == "someone-distinctive"


# --- (a3) the CAS matches a row whose judge_sha is NULL too ------------------------------

def test_acknowledge_matches_a_row_whose_judge_sha_is_null(tmp_path):
    """CMX-336 rework round 3: the CAS clause deliberately reads ``judge_sha IS ?`` rather
    than ``judge_sha = ?`` — SQLite's ``=`` never matches NULL, so a row with no recorded
    ``judge_sha`` at all could never be acknowledged under ``= ?``. That row is the MOST
    stuck row this ticket exists for: ``_blocked_race_resolved``'s ``sha and head and sha
    != head`` also can't fire without a sha, so acknowledgement is its only exit. Every
    other fixture in this file sets a non-NULL ``judge_sha``, where ``IS`` and ``=`` are
    indistinguishable; this one pins the NULL case so a mutation from ``IS`` to ``=`` goes
    red."""
    with dispatcher._db() as conn:
        _row(conn, judge_sha=None)

    result = dispatcher.acknowledge_blocked_race("abc123", by="liav")

    assert result["ok"] is True
    assert result["sha"] is None

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] == "liav"
    assert row["blocked_race_ack_sha"] is None
    assert row["judge_state"] == judge.J_BLOCKED_RACE


def test_acknowledging_a_null_judge_sha_row_actually_clears_it_from_the_scan(tmp_path):
    """CMX-336 rework round 4: round 3 proved the CAS above *matches* a NULL-``judge_sha``
    row and stamps ``blocked_race_ack_sha=None`` on it, but that stamp is only worth
    anything if ``runtime_truth._blocked_race_resolved`` actually honors it — with
    ``judge_sha`` NULL, honoring it depends entirely on ``None == None``. Every fixture in
    tests/test_runtime_truth.py hands `_blocked_race_resolved` a non-NULL ``judge_sha``, so
    that comparison is never exercised for the NULL case; this end-to-end fixture goes
    through the REAL ``acknowledge_blocked_race`` and then the REAL ``_blocked_race_scan``
    so the two halves are pinned together, not proven separately. Weaken the comparison to
    ``== (sha or "")`` and this goes red: an acknowledged NULL-sha row would keep reporting
    forever, which is the exact bug CMX-336 exists to fix."""
    from chela import runtime_truth

    with dispatcher._db() as conn:
        _row(conn, judge_sha=None, pr_head_sha=None)

    result = dispatcher.acknowledge_blocked_race("abc123", by="liav")
    assert result["ok"] is True

    scanned = runtime_truth._blocked_race_scan()
    assert scanned == {}, "an acknowledged NULL-judge_sha row must clear, not keep reporting"


# --- (b) refuses when there is nothing to acknowledge ------------------------------------

def test_acknowledge_refuses_a_run_that_is_not_blocked_race(tmp_path):
    with dispatcher._db() as conn:
        _row(conn, judge_state=judge.J_CLEAN)

    result = dispatcher.acknowledge_blocked_race("abc123")
    assert result["ok"] is False
    assert judge.J_BLOCKED_RACE in result["error"]

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] is None


def test_acknowledge_refuses_an_unknown_run(tmp_path):
    result = dispatcher.acknowledge_blocked_race("nope")
    assert result["ok"] is False
    assert "no run matches" in result["error"]


# --- (c) the scope guard: an ack under an OLD sha does not cover a NEW race --------------

def test_acknowledge_is_scoped_to_the_current_judge_sha(tmp_path, monkeypatch):
    """CAS on judge_state AND judge_sha together: if the row's REAL judge_sha has moved on
    since this call's own read (a fresh judge landed a NEW blocked_race verdict on a
    different commit, in the gap between the read and this write), the acknowledgement must
    be refused rather than silently stamping itself onto the new verdict under the stale
    sha it was given."""
    with dispatcher._db() as conn:
        _row(conn, judge_sha="new-sha")  # the row's REAL, current sha

    stale = dict(dispatcher.list_runs()[0])
    stale["judge_sha"] = "old-sha"  # what a stale read saw before the concurrent re-run
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: stale)

    result = dispatcher.acknowledge_blocked_race("abc123")
    assert result["ok"] is False
    assert "changed while this was being written" in result["error"]

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] is None
    assert row["judge_sha"] == "new-sha"


def test_acknowledge_is_scoped_to_the_current_judge_state_not_only_sha(tmp_path, monkeypatch):
    """CMX-336 rework round 2: the CAS is on ``judge_state`` AND ``judge_sha`` TOGETHER
    (see the docstring above `acknowledge_blocked_race`) — the test above only ever moves
    the sha between the read and the write, so it cannot tell a WHERE clause that checks
    both columns apart from one that checks the sha alone. Here the sha stays IDENTICAL
    but the row's REAL judge_state has moved on (a fresh judge re-run resolved the SAME
    commit to a different verdict, in the gap between the read and this write) — the
    acknowledgement must still be refused, not silently stamped onto the fresh verdict
    just because the sha still matches."""
    with dispatcher._db() as conn:
        _row(conn, judge_sha="same-sha", judge_state=judge.J_CLEAN)  # real, current state

    stale = dict(dispatcher.list_runs()[0])
    stale["judge_state"] = judge.J_BLOCKED_RACE  # what a stale read saw before the re-run
    stale["judge_sha"] = "same-sha"
    monkeypatch.setattr(dispatcher, "resolve_run", lambda ident: stale)

    result = dispatcher.acknowledge_blocked_race("abc123")
    assert result["ok"] is False
    assert "changed while this was being written" in result["error"]

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] is None
    assert row["judge_state"] == judge.J_CLEAN


def test_acknowledge_stamps_ONLY_the_named_run_when_a_sibling_shares_its_judge_sha(tmp_path):
    """🔴 `task_id` is the IDENTITY clause of the CAS, and it is the one clause every other
    test leaves undiscriminating: they each insert exactly ONE row, where `task_id=?` and a
    tautology are indistinguishable. Widen the WHERE to `(task_id=? OR 1)` and they all stay
    green.

    Reachable, not theoretical. Round 3 established that a NULL-`judge_sha`
    `J_BLOCKED_RACE` row is the MOST stuck row this command exists for — and
    `judge_sha IS NULL` is exactly the value two different stuck rows CAN share, since the
    other two CAS clauses (`judge_state`, `judge_sha`) are then identical for both. Losing
    the identity clause would stamp who/when/why onto a run nobody looked at and stop
    `chela doctor` reporting its verdict — silencing an unexamined blocking finding, which
    is the precise failure this whole ticket exists to prevent.

    Both rows here carry `judge_sha=None` so that `task_id` is the ONLY column that
    disagrees — docs/defeat_shapes/336's own item 4 ("each column needs its own fixture
    where it is the ONLY column that disagrees"), applied to the third column.
    """
    with dispatcher._db() as conn:
        _row(conn, task_id="target", judge_sha=None)
        _row(conn, task_id="bystander", judge_sha=None)

    result = dispatcher.acknowledge_blocked_race("target", by="liav", note="the named one")

    assert result["ok"] is True
    with dispatcher._db() as conn:
        rows = {
            r["task_id"]: r for r in
            conn.execute("SELECT * FROM runs WHERE task_id IN ('target','bystander')")
        }

    assert rows["target"]["blocked_race_ack_by"] == "liav"
    assert rows["target"]["blocked_race_ack_at"]

    # ⭐ the whole point: the sibling sharing judge_sha (NULL) must be untouched
    assert rows["bystander"]["blocked_race_ack_by"] is None, (
        "acknowledging one run also stamped a DIFFERENT run that merely shared its "
        "judge_sha — the CAS lost its identity clause, so an unexamined blocking verdict "
        "would stop being reported"
    )
    assert rows["bystander"]["blocked_race_ack_at"] is None
    assert rows["bystander"]["blocked_race_ack_note"] is None
    assert rows["bystander"]["judge_state"] == judge.J_BLOCKED_RACE


def test_acknowledge_stamps_the_REAL_clock_not_a_fixed_timestamp(tmp_path, monkeypatch):
    """`blocked_race_ack_at` is WHEN the acknowledgement happened, and it was the one
    member of the stamped family never asserted against an independently-known value:
    `by`, `note`, `sha`, `task_id` and `pr_url` each have a test that passes a distinctive
    value in and reads that exact value back, but `at` was only asserted TRUTHY and
    SELF-CONSISTENT (payload `at` == the column). Both stay true when the source is a
    hardcoded constant, and a frozen `at` makes every acknowledgement in the audit trail
    claim the same time — the one fact the column exists to carry.

    Pinning it to a monkeypatched clock makes the value independently known, so any
    constant substituted for `_now()` disagrees.
    """
    monkeypatch.setattr(dispatcher, "_now", lambda: "2026-09-03T12:34:56.789012+00:00")
    with dispatcher._db() as conn:
        _row(conn)

    result = dispatcher.acknowledge_blocked_race("abc123", by="liav")

    assert result["at"] == "2026-09-03T12:34:56.789012+00:00", (
        "the acknowledgement's timestamp did not come from the clock — a hardcoded "
        "constant would make every row in the audit trail claim the same time"
    )
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_at"] == "2026-09-03T12:34:56.789012+00:00"


def test_acknowledge_resolves_the_identifier_and_stamps_the_RESOLVED_task_id(tmp_path, monkeypatch):
    """🔴 The acknowledgement lands on the run the operator NAMED, however they named it.

    `resolve_run` accepts a run id, a **branch name**, or a **window name** — that is why
    `ident` and `task_id` are separate variables, and the subcommand's own `--help`
    advertises it. Every other fixture here passes the task id itself, where `ident` and
    `run["task_id"]` are the same string and `task_id = ident` is indistinguishable from
    `task_id = run["task_id"]`.

    This drives the case where they DIFFER: acknowledge by BRANCH name. Under the mutation
    the CAS would look for a row whose `task_id` is the branch name, match nothing, and the
    operator's acknowledgement of a real stuck row would quietly do nothing — while the
    command still reported the branch name back as the task it acknowledged.
    """
    with dispatcher._db() as conn:
        _row(conn, task_id="abc123", branch_name="cmx-336")

    result = dispatcher.acknowledge_blocked_race("cmx-336", by="liav", note="by branch")

    assert result["ok"] is True
    assert result["task_id"] == "abc123", (
        f"the result named {result['task_id']!r} — the identifier the operator typed, not "
        "the run it resolved to"
    )
    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] == "liav", (
        "acknowledging by branch name stamped nothing — the CAS was given the raw "
        "identifier instead of the resolved task_id"
    )


def test_acknowledge_event_payload_records_the_STAMPED_actor_not_the_raw_argument(tmp_path, monkeypatch):
    """The `blocked_race_ack` event is the audit record OF the `blocked_race_ack_by`
    column, so the two must agree.

    They coincide whenever `--by` is given explicitly, which is what every other fixture
    does — so `\"by\": by` (the raw argument) reads back identically to `\"by\": who` (the
    value actually stamped). This drives the one case where they differ: `by=\"\"`, where
    the documented `$USER`/`$USERNAME`/`\"unknown\"` chain fills `who` in. Under the mutation
    the column would say `liav` while the audit trail said `\"\"` — an audit record that
    disagrees with the fact it exists to record.
    """
    monkeypatch.setenv("USER", "liav")
    with dispatcher._db() as conn:
        _row(conn)

    dispatcher.acknowledge_blocked_race("abc123")      # ⭐ no --by: the env chain fills it

    with dispatcher._db() as conn:
        row = conn.execute("SELECT * FROM runs WHERE task_id='abc123'").fetchone()
    assert row["blocked_race_ack_by"] == "liav"        # the column took the env value

    acked = [e for e in event_log.read()["events"] if e["type"] == "blocked_race_ack"]
    assert acked, "no blocked_race_ack event was logged"
    assert acked[0]["payload"]["by"] == "liav", (
        f"the audit event recorded {acked[0]['payload']['by']!r} but the column was stamped "
        "'liav' — the event must record the actor actually stamped, not the raw argument"
    )
