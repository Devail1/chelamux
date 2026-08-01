"""``/api/restore`` + ``/api/restore/resume`` (CMX-208) — the sidebar's one-click resume.

A UI over machinery CMX-195/196 already built and tested (``chela/restore.py``): these
routes classify the same three session-stamped stores ``chela restore`` reads, expose the
resumable MANUAL rows as JSON, and let a click do what a human would otherwise type by hand
(``cd <cwd> && CHELA_WID=@N claude --resume <sid>``) via ``chela.spawn.spawn_window`` — the
same window-open path the "+" launcher and the Telegram ``/new`` bridge use.

These tests stub ``chela.restore.plan`` directly (already thoroughly unit-tested in
``tests/test_restore.py``) rather than re-proving classification here — the thing that is
NEW and needs a guard is the HTTP-layer wiring: what gets fetched, filtered, shaped into
JSON, and what the resume action actually does (spawn + record + cleanup, in that order,
and NOT ahead of a confirmed re-match).
"""
from __future__ import annotations

import pytest

from chela import restore as restore_mod
from chela import spawn as spawn_mod
from chela.dashboard import app as dash

OLD = "786-1784045825"
SID_DEAD = "bbbbbbbb-1111-2222-3333-444444444444"
CWD = "/home/liav/projects/five"


@pytest.fixture
def client():
    return dash.app.test_client()


@pytest.fixture(autouse=True)
def terminals_on(monkeypatch):
    monkeypatch.setattr(dash.config, "TERMINALS_ENABLED", True)


@pytest.fixture(autouse=True)
def restore_sources(monkeypatch):
    """Stub every read ``_restore_verdicts`` makes, so only the route wiring is under
    test — same seam ``tests/test_restore_cli.py``'s ``restore_env`` fixture stubs for
    ``cmd_restore``, applied here to the dashboard's copy of the same gathering."""
    from chela.telegram import bindings as bindings_mod

    monkeypatch.setattr(dash.epoch, "current", lambda: "9001-1784099999")
    monkeypatch.setattr(dash.inbox, "load", lambda: {"watches": {}})
    monkeypatch.setattr(dash.sessionids, "entries", lambda: {})
    monkeypatch.setattr(bindings_mod.BindingRegistry, "load",
                        classmethod(lambda cls, *a, **k: bindings_mod.BindingRegistry("1")))


def _manual(store="session-ids", wid="@5", session_id=SID_DEAD, cwd=CWD, label="five",
            stamped_epoch=OLD):
    return restore_mod.Verdict(store=store, wid=wid, stamped_epoch=stamped_epoch,
                               verdict="MANUAL", session_id=session_id, new_wid=None,
                               cwd=cwd, label=label)


def _revivable(store="session-ids", wid="@5"):
    return restore_mod.Verdict(store=store, wid=wid, stamped_epoch=OLD, verdict="REVIVABLE",
                               session_id="sid-live", new_wid="@42", cwd=CWD, label="five")


# --------------------------------------------------------------------------
# GET /api/restore
# --------------------------------------------------------------------------

def test_lists_a_resumable_MANUAL_row(client, monkeypatch):
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual()])

    resp = client.get("/api/restore")

    assert resp.status_code == 200
    rows = resp.get_json()
    assert rows == [{"store": "session-ids", "wid": "@5", "session_id": SID_DEAD,
                     "cwd": CWD, "label": "five", "stamped_epoch": OLD}]


def test_REVIVABLE_rows_never_appear_in_the_resume_list(client, monkeypatch):
    """🔴 GUARD: a REVIVABLE row's session is already alive under a new address —
    listing it as "resumable" would tell the operator to relaunch a session that is
    already running, doubling it."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_revivable()])

    rows = client.get("/api/restore").get_json()

    assert rows == []


def test_a_MANUAL_row_with_no_manual_command_is_excluded(client, monkeypatch):
    """A row missing cwd or session id can't be relaunched at all — showing a Resume
    button for it would be a dead click."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual(cwd=None)])

    rows = client.get("/api/restore").get_json()

    assert rows == []


def test_nothing_orphaned_is_an_empty_list_not_an_error(client, monkeypatch):
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [])

    resp = client.get("/api/restore")

    assert resp.status_code == 200
    assert resp.get_json() == []


def test_gated_on_terminals_enabled(client, monkeypatch):
    monkeypatch.setattr(dash.config, "TERMINALS_ENABLED", False)
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual()])

    assert client.get("/api/restore").status_code == 404


# --------------------------------------------------------------------------
# POST /api/restore/resume
# --------------------------------------------------------------------------

def _resume(client, **body):
    return client.post("/api/restore/resume", json=body)


def test_resume_requires_store_wid_and_session_id(client):
    resp = _resume(client, store="", wid="", session_id="")
    assert resp.status_code == 400
    assert resp.get_json()["ok"] is False


def test_resume_gated_on_terminals_enabled(client, monkeypatch):
    monkeypatch.setattr(dash.config, "TERMINALS_ENABLED", False)
    resp = _resume(client, store="session-ids", wid="@5", session_id=SID_DEAD)
    assert resp.status_code == 404


def test_resume_refuses_a_row_that_no_longer_matches(client, monkeypatch):
    """🔴 GUARD: the resume must re-classify against a FRESH plan(), never trust the
    request body alone — a row a further restart or a concurrent resume already
    handled must be refused (409), not blindly acted on."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [])   # nothing dangling now
    spawned = []
    monkeypatch.setattr(dash.spawn, "spawn_window", lambda *a, **k: spawned.append(1))

    resp = _resume(client, store="session-ids", wid="@5", session_id=SID_DEAD,
                   stamped_epoch=OLD)

    assert resp.status_code == 409
    assert resp.get_json()["ok"] is False
    assert spawned == [], "a row that failed to re-match must never reach spawn_window"


def test_resume_refuses_when_the_epoch_no_longer_matches(client, monkeypatch):
    """The same guard, one field at a time: a row that re-matches on store/wid/session
    but under a DIFFERENT stamped_epoch has moved on (a further restart reissued it)."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual(stamped_epoch="other-epoch")])

    resp = _resume(client, store="session-ids", wid="@5", session_id=SID_DEAD,
                   stamped_epoch=OLD)

    assert resp.status_code == 409


def test_resume_happy_path_spawns_records_and_cleans_up(client, monkeypatch):
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual()])

    spawn_calls = []
    def fake_spawn(cwd, *, command=None):
        spawn_calls.append((cwd, command))
        return spawn_mod.SpawnResult(ok=True, name="shell-9", wid="@99", cwd=cwd)
    monkeypatch.setattr(dash.spawn, "spawn_window", fake_spawn)

    record_calls = []
    monkeypatch.setattr(dash.sessionids, "set_session_id",
                        lambda wid, sid: record_calls.append((wid, sid)))

    apply_calls = []
    monkeypatch.setattr(restore_mod, "apply", lambda verdicts: apply_calls.append(verdicts))

    resp = _resume(client, store="session-ids", wid="@5", session_id=SID_DEAD,
                   stamped_epoch=OLD)

    assert resp.status_code == 200
    data = resp.get_json()
    assert data == {"ok": True, "name": "shell-9", "cwd": CWD, "wid": "@99"}

    assert spawn_calls == [(CWD, f"claude --resume {SID_DEAD}")], (
        "the spawn must open the row's own recorded cwd and resume ITS session, "
        "verbatim — not a generic shell"
    )
    assert record_calls == [("@99", SID_DEAD)], (
        "the NEW window must be recorded under the RESUMED session id, so a later "
        "chela restore sees @99 -> SID_DEAD, not nothing"
    )
    assert len(apply_calls) == 1 and apply_calls[0][0].wid == "@5", (
        "the OLD dangling row must be handed to restore.apply() so its stale "
        "bookkeeping (archived + removed) doesn't linger and re-appear next list"
    )


def test_resume_never_calls_apply_when_the_spawn_fails(client, monkeypatch):
    """🔴 GUARD: a failed spawn must not still archive-and-remove the old row — that
    would erase the only record of a session that was NEVER actually relaunched,
    leaving nothing anywhere for a human to recover it from."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual()])
    monkeypatch.setattr(dash.spawn, "spawn_window",
                        lambda *a, **k: spawn_mod.SpawnResult(ok=False, error="tmux is unreachable"))

    apply_calls = []
    monkeypatch.setattr(restore_mod, "apply", lambda verdicts: apply_calls.append(verdicts))
    record_calls = []
    monkeypatch.setattr(dash.sessionids, "set_session_id",
                        lambda wid, sid: record_calls.append((wid, sid)))

    resp = _resume(client, store="session-ids", wid="@5", session_id=SID_DEAD,
                   stamped_epoch=OLD)

    assert resp.status_code == 500
    assert resp.get_json() == {"ok": False, "error": "tmux is unreachable"}
    assert apply_calls == []
    assert record_calls == []


def test_resume_a_missing_directory_is_a_400_not_a_500(client, monkeypatch):
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual()])
    monkeypatch.setattr(dash.spawn, "spawn_window",
                        lambda *a, **k: spawn_mod.SpawnResult(ok=False, error="no such directory: /gone"))

    resp = _resume(client, store="session-ids", wid="@5", session_id=SID_DEAD,
                   stamped_epoch=OLD)

    assert resp.status_code == 400


def test_resume_records_nothing_when_spawn_gave_no_wid(client, monkeypatch):
    """An older tmux build that doesn't echo ``#{window_id}`` — spawn still succeeds
    (the window exists), but there is no address to record the session id against."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual()])
    monkeypatch.setattr(dash.spawn, "spawn_window",
                        lambda *a, **k: spawn_mod.SpawnResult(ok=True, name="shell-9", wid=None, cwd=CWD))
    record_calls = []
    monkeypatch.setattr(dash.sessionids, "set_session_id",
                        lambda wid, sid: record_calls.append((wid, sid)))
    monkeypatch.setattr(restore_mod, "apply", lambda verdicts: None)

    resp = _resume(client, store="session-ids", wid="@5", session_id=SID_DEAD,
                   stamped_epoch=OLD)

    assert resp.status_code == 200
    assert record_calls == []
