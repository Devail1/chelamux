"""A reaped worktree must not leave its tmux window behind as a live-looking agent
(CMX-329, issue #403). `_reap_terminal_windows` is the self-healing sweep: every tick,
for THIS workflow, kill any tmux window still alive for a run that has already gone
`done`/`closed` — but ONLY when the window's recorded id+epoch prove chela still owns it.

Observed three times on 2026-08-24 with no special trigger (three ordinary merges, three
survivors): the in-line kill fired at the moment of transition is fire-and-forget and can
silently fail, so nothing ever revisited a straggler. This sweep is the second chance.
"""
from __future__ import annotations

import subprocess

import pytest

from chela import dispatcher

WORKFLOW_PATH = "/repo/WORKFLOW.md"
CURRENT_EPOCH = "1234-5678"
OTHER_EPOCH = "9999-0000"


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher.epoch, "current", lambda: CURRENT_EPOCH)
    return dispatcher


def _seed(conn, task_id: str, status: str, window_id: str | None, window_epoch: str | None):
    conn.execute(
        "INSERT INTO runs (task_id, workflow_path, title, status, window_id, window_epoch, "
        "started_at, attempt) VALUES (?,?,?,?,?,?,?,?)",
        (task_id, WORKFLOW_PATH, "t", status, window_id, window_epoch, dispatcher._now(), 1),
    )


def _live_ids(*ids):
    return lambda: set(ids)


def test_reaps_a_terminal_owned_window_still_alive(db, monkeypatch):
    """⭐ GUARD: `done` + matching epoch + still-live window → killed."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@45"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-1", "done", "@45", CURRENT_EPOCH)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 1
    assert killed == ["@45"]


def test_never_kills_a_window_for_a_non_terminal_run(db, monkeypatch):
    """⛔ NEGATIVE CONTROL — the other half of the guard. `needs_human` and
    `awaiting_review` are explicitly NOT terminal: a human may still act on the window,
    or a rework may still re-spawn into it. Same owned, live window as the positive
    test above — only the status differs — so a broadened WHERE clause (e.g. dropping
    the `status IN (...)` filter) would turn this red for the reason it exists."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@46", "@47"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-2", "needs_human", "@46", CURRENT_EPOCH)
        _seed(conn, "cmx-3", "awaiting_review", "@47", CURRENT_EPOCH)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_does_not_kill_a_dead_window(db, monkeypatch):
    """A terminal row whose window is already gone (the normal, expected case — killed
    at transition time) must not trip a phantom kill-window call."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids())  # nothing live
    with dispatcher._db() as conn:
        _seed(conn, "cmx-4", "done", "@48", CURRENT_EPOCH)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_never_kills_a_window_whose_epoch_does_not_match(db, monkeypatch):
    """⛔ CMX-48: an id stamped under a dead tmux server is a COINCIDENCE if it matches
    something live now, not this run's window — the server was restarted (renumbered
    from `@0`) and `@45` may belong to an unrelated agent today. Only same-epoch ids are
    ever acted on."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@45"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-5", "done", "@45", OTHER_EPOCH)  # stale epoch
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_never_kills_a_window_with_no_recorded_id(db, monkeypatch):
    """A pre-CMX-77 row (or one that never made it past the claim before dying) has no
    `window_id` to verify ownership with at all — never a name-based fallback kill."""
    killed = []
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@45"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-6", "done", None, None)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_unreadable_current_epoch_verifies_nothing(db, monkeypatch):
    """No tmux, no server: an unknown current epoch can never be compared equal to a
    stamp, so nothing is killed (the same fail-closed shape as `epoch.is_dangling`)."""
    killed = []
    monkeypatch.setattr(dispatcher.epoch, "current", lambda: None)
    monkeypatch.setattr(dispatcher, "_kill_window", lambda wid: killed.append(wid))
    monkeypatch.setattr(dispatcher, "_tmux_window_ids", _live_ids("@45"))
    with dispatcher._db() as conn:
        _seed(conn, "cmx-7", "done", "@45", CURRENT_EPOCH)
        conn.commit()
        reaped = dispatcher._reap_terminal_windows(conn, WORKFLOW_PATH)

    assert reaped == 0
    assert killed == []


def test_tmux_window_ids_reads_the_real_ids_column(monkeypatch):
    """`_tmux_window_ids` must ask tmux for `#{window_id}` (an `@N` address), not
    `#W` (the name) — the reap sweep verifies ownership by id, and a name carries no
    epoch to check."""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd

        class R:
            returncode = 0
            stdout = "@1\n@2\n"

        return R()

    monkeypatch.setattr(subprocess, "run", fake_run)
    ids = dispatcher._tmux_window_ids()

    assert ids == {"@1", "@2"}
    assert "#{window_id}" in captured["cmd"]
