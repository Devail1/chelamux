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

A second seam these tests stub — ``chela.dispatcher.list_runs`` — is what tells
``_dispatcher_owned_wid_epochs`` which ``(wid, stamped_epoch)`` pairs belong to the
dispatcher's own runs (an agent's window, or its judge's). That is the ONLY thing that
may hide a row or refuse its resume: a row whose ``label`` merely *looks* like a
dispatcher convention (``cmx-*``/``judge-*``) but has no matching ``runs`` row must stay
resumable — see ``test_a_row_named_like_a_dispatcher_convention_but_unowned_stays_resumable``.
"""
from __future__ import annotations

import pytest

from chela import restore as restore_mod
from chela import spawn as spawn_mod
from chela.dashboard import app as dash

OLD = "786-1784045825"
SID_DEAD = "bbbbbbbb-1111-2222-3333-444444444444"
CWD = "/home/liav/projects/five"
JUDGE_CWD = "/home/liav/.chela/worktrees/chelamux/judge-cmx-206"
JUDGE_SID = "cccccccc-1111-2222-3333-444444444444"


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


@pytest.fixture(autouse=True)
def no_dispatcher_runs(monkeypatch):
    """No dispatcher runs by default — every existing (pre-CMX-208-rework) test keeps
    seeing an all-resumable, un-filtered list. Tests of the dispatcher-owned guard
    override this explicitly."""
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [])


@pytest.fixture(autouse=True)
def cwd_alive_by_default(monkeypatch):
    """Every pre-CMX-330 test uses a fake cwd path (``/home/liav/projects/five`` etc.)
    that doesn't exist on the test runner's real filesystem — stub liveness True by
    default so those tests keep exercising only the wiring under test. Tests of the
    dead-cwd guard override this explicitly."""
    monkeypatch.setattr(dash, "_cwd_is_live", lambda cwd: True)


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
    data = resp.get_json()
    assert data == {"rows": [{"store": "session-ids", "wid": "@5", "session_id": SID_DEAD,
                              "cwd": CWD, "label": "five", "stamped_epoch": OLD}],
                    "dispatcher_rows": [], "hidden": 0}


def test_REVIVABLE_rows_never_appear_in_the_resume_list(client, monkeypatch):
    """🔴 GUARD: a REVIVABLE row's session is already alive under a new address —
    listing it as "resumable" would tell the operator to relaunch a session that is
    already running, doubling it."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_revivable()])

    data = client.get("/api/restore").get_json()

    assert data["rows"] == []


def test_a_MANUAL_row_with_no_manual_command_is_excluded(client, monkeypatch):
    """A row missing cwd or session id can't be relaunched at all — showing a Resume
    button for it would be a dead click."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual(cwd=None)])

    data = client.get("/api/restore").get_json()

    assert data["rows"] == []


def test_nothing_orphaned_is_an_empty_list_not_an_error(client, monkeypatch):
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [])

    resp = client.get("/api/restore")

    assert resp.status_code == 200
    assert resp.get_json() == {"rows": [], "dispatcher_rows": [], "hidden": 0}


def test_gated_on_terminals_enabled(client, monkeypatch):
    monkeypatch.setattr(dash.config, "TERMINALS_ENABLED", False)
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual()])

    assert client.get("/api/restore").status_code == 404


# --------------------------------------------------------------------------
# GET /api/restore — dispatcher-owned rows (rework: CMX-208 round 1 shipped this
# without any dispatcher filter at all — a judge/agent row with a FULL cwd+session
# on record, exactly what the roster now populates, would classify MANUAL and get a
# live Resume button the moment its epoch died).
# --------------------------------------------------------------------------

def _judge_row_manual():
    """The shape the PR review measured live from ~/.chela/roster.json: a judge
    window with a session id AND a cwd — the exact row that must never be
    resumable."""
    return _manual(store="session-ids", wid="@138", session_id=JUDGE_SID,
                   cwd=JUDGE_CWD, label="judge-cmx-206", stamped_epoch=OLD)


def test_a_dispatcher_owned_row_via_the_runs_own_window_is_hidden_not_resumable(client, monkeypatch):
    """🔴 GUARD: a run's OWN window (runs.window_id/window_epoch) matching the
    dangling row's (wid, stamped_epoch) must exclude it from `rows` and count it in
    `hidden` — never a name/path guess, a fact off the runs table."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_judge_row_manual()])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [
        {"task_id": "cmx-206", "window_id": "@138", "window_epoch": OLD,
         "judge_window_id": None, "judge_window_epoch": None},
    ])

    data = client.get("/api/restore").get_json()

    assert data["rows"] == []
    assert data["hidden"] == 1
    assert data["dispatcher_rows"] == [{"store": "session-ids", "wid": "@138", "cwd": JUDGE_CWD,
                                        "label": "judge-cmx-206", "stamped_epoch": OLD}]
    assert "session_id" not in data["dispatcher_rows"][0], (
        "a dispatcher-owned row has no resume affordance — it must not even carry the "
        "session id a resume request would need"
    )


def test_a_dispatcher_owned_row_via_the_JUDGE_window_is_hidden_too(client, monkeypatch):
    """The same guard through the OTHER half of a run row: the judge's own
    (judge_window_id, judge_window_epoch) pair — this is the exact live shape from the
    PR review (`@138 judge-cmx-206`, a judge window, not the run's own agent
    window)."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_judge_row_manual()])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [
        {"task_id": "cmx-206", "window_id": "@77", "window_epoch": OLD,
         "judge_window_id": "@138", "judge_window_epoch": OLD},
    ])

    data = client.get("/api/restore").get_json()

    assert data["rows"] == []
    assert data["hidden"] == 1


def test_a_row_named_like_a_dispatcher_convention_but_unowned_stays_resumable(client, monkeypatch):
    """🔴 GUARD (the load-bearing counterweight): a HUMAN session named ``cmx-999``,
    with a cwd OUTSIDE ``~/.chela/worktrees/``, must stay listed and resumable — the
    dispatcher's ``runs`` table has no row claiming its (wid, epoch), so a filter that
    corrupted to a name/path convention (``cmx-*``/``worktrees/``) would wrongly sweep
    it up. Only the runs-table fact may exclude a row."""
    human_row = _manual(store="session-ids", wid="@200", session_id="human-sid",
                        cwd="/home/liav/scratch/whatever", label="cmx-999",
                        stamped_epoch=OLD)
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [human_row])
    # A real dispatcher run exists, but at a DIFFERENT (wid, epoch) — it must not
    # incidentally swallow the human's row.
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [
        {"task_id": "cmx-206", "window_id": "@138", "window_epoch": OLD,
         "judge_window_id": None, "judge_window_epoch": None},
    ])

    data = client.get("/api/restore").get_json()

    assert data["rows"] == [{"store": "session-ids", "wid": "@200", "session_id": "human-sid",
                             "cwd": "/home/liav/scratch/whatever", "label": "cmx-999",
                             "stamped_epoch": OLD}]
    assert data["dispatcher_rows"] == []
    assert data["hidden"] == 0


def test_a_dispatcher_row_at_a_different_epoch_no_longer_owns_the_wid(client, monkeypatch):
    """tmux hands ``@N`` out fresh after a restart — a stale runs-table stamp under an
    OLDER epoch must not claim a row stamped under a different one."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_judge_row_manual()])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [
        {"task_id": "cmx-206", "window_id": None, "window_epoch": None,
         "judge_window_id": "@138", "judge_window_epoch": "some-other-epoch"},
    ])

    data = client.get("/api/restore").get_json()

    assert data["rows"] == [{"store": "session-ids", "wid": "@138", "session_id": JUDGE_SID,
                             "cwd": JUDGE_CWD, "label": "judge-cmx-206", "stamped_epoch": OLD}]
    assert data["dispatcher_rows"] == []


# --------------------------------------------------------------------------
# GET /api/restore — dead-cwd rows (CMX-330: a tmux restart renumbers every window,
# `@889`/`@891`/`@897` -> `@9`/`@11`/`@13`, desyncing the ownership join above from
# the runs table even though the worktree it pointed at is long gone. cwd existence
# must be checked directly, never inferred from whether the ownership join matched.)
# --------------------------------------------------------------------------

def test_a_row_whose_cwd_no_longer_exists_is_hidden_not_resumable(client, monkeypatch):
    """🔴 GUARD: a reaped worktree / deleted branch leaves a MANUAL row with a real
    session id but nowhere to relaunch into — this must be excluded from `rows` even
    though nothing in `dispatcher.list_runs()` claims its (wid, epoch)."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual(wid="@9", cwd="/gone")])
    monkeypatch.setattr(dash, "_cwd_is_live", lambda cwd: cwd != "/gone")

    data = client.get("/api/restore").get_json()

    assert data["rows"] == []
    assert data["hidden"] == 1
    assert data["dispatcher_rows"] == [{"store": "session-ids", "wid": "@9", "cwd": "/gone",
                                        "label": "five", "stamped_epoch": OLD}]
    assert "session_id" not in data["dispatcher_rows"][0], (
        "a dead-cwd row has no resume affordance — it must not even carry the "
        "session id a resume request would need"
    )


def test_a_row_whose_cwd_still_exists_stays_resumable(client, monkeypatch):
    """The counterweight: a live cwd must stay resumable — the check is a real
    filesystem fact, not a blanket hide."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual()])
    monkeypatch.setattr(dash, "_cwd_is_live", lambda cwd: cwd == CWD)

    data = client.get("/api/restore").get_json()

    assert len(data["rows"]) == 1
    assert data["dispatcher_rows"] == []


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


def test_resume_refuses_a_dispatcher_owned_row(client, monkeypatch):
    """🔴 GUARD: even if a dispatcher-owned row's identity somehow reaches the client
    (a stale cache, a hand-built request), the resume route must independently REFUSE
    it — never trust that a row absent from `rows` implies the client can't ask for it
    anyway. spawn_window must never be called."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_judge_row_manual()])
    monkeypatch.setattr(dash.dispatcher, "list_runs", lambda: [
        {"task_id": "cmx-206", "window_id": "@77", "window_epoch": OLD,
         "judge_window_id": "@138", "judge_window_epoch": OLD},
    ])
    spawned = []
    monkeypatch.setattr(dash.spawn, "spawn_window", lambda *a, **k: spawned.append(1))

    resp = _resume(client, store="session-ids", wid="@138", session_id=JUDGE_SID,
                   stamped_epoch=OLD)

    assert resp.status_code == 409
    body = resp.get_json()
    assert body["ok"] is False
    assert "dispatcher" in body["error"].lower()
    assert spawned == [], "a dispatcher-owned row must never reach spawn_window"


def test_resume_refuses_a_row_whose_cwd_no_longer_exists(client, monkeypatch):
    """🔴 GUARD: even if a dead-cwd row's identity somehow reaches the client (a stale
    cache), the resume route must independently refuse it — spawn_window must never be
    called against a directory that isn't there."""
    monkeypatch.setattr(restore_mod, "plan", lambda *a, **k: [_manual()])
    monkeypatch.setattr(dash, "_cwd_is_live", lambda cwd: False)
    spawned = []
    monkeypatch.setattr(dash.spawn, "spawn_window", lambda *a, **k: spawned.append(1))

    resp = _resume(client, store="session-ids", wid="@5", session_id=SID_DEAD,
                   stamped_epoch=OLD)

    assert resp.status_code == 409
    assert resp.get_json()["ok"] is False
    assert spawned == [], "a dead-cwd row must never reach spawn_window"


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
