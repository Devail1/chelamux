"""CMX-125 — after a reboot the human sessions come back once, a dispatched run keeps ONE
window (its old session is never relaunched into its worktree), and a tmux death does not
spend a run's attempt."""
from __future__ import annotations

import functools
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from chela import dispatcher, main, restore

from tests.test_dispatcher_rework import _FakeTmux, _row, _Source, _status, _wf
from tests.test_main_dispatch_log import _stub_daemon_loop_inert

PREV, NOW = "100-1700000000", "200-1800000000"
SID = "bbbbbbbb-1111-2222-3333-444444444444"
RUN = {"task_id": "CMX-95", "status": "running", "worktree_path": "/wt/CMX-95", "attempt": 3}


def _manual(wid="@3", cwd="/home/u/five", stamped=PREV, label="five"):
    return restore.Verdict("session-ids", wid, stamped, "MANUAL", SID, None, cwd, label)


@pytest.mark.parametrize("cwd,runs,owner", [
    ("/wt/CMX-95", [RUN], "CMX-95"), ("/wt/CMX-95/chela", [RUN], "CMX-95"),
    ("/wt/CMX-95/", [RUN], "CMX-95"), ("/wt/CMX-950", [RUN], None), (None, [RUN], None),
    ("/wt/CMX-95", [{**RUN, "status": "done", "pr_state": "merged"}], None),
    ("/wt/CMX-95", [{**RUN, "status": "failed", "attempt": 3}], None),       # out of tries
    ("/wt/CMX-95", [{**RUN, "status": "failed", "attempt": 2}], "CMX-95"),   # will retry
    ("/wt/CMX-95", [{**RUN, "worktree_path": None}], None), ("/wt/CMX-95", None, None),
])
def test_live_worktree_owner(cwd, runs, owner):
    assert restore.live_worktree_owner(cwd, runs) == owner


def test_resume_never_relaunches_into_a_live_runs_worktree():
    spawned = []
    [r] = restore.resume([_manual(cwd="/wt/CMX-95")], [RUN], resume_blocked=lambda s: None,
                         spawn_window=lambda *a, **k: spawned.append(a))
    assert (spawned, r.action) == ([], restore.SKIPPED) and "CMX-95" in r.detail


def _pass(tmp_path, verdicts, *, now=NOW, enabled=True, marker=PREV, resume_fn=None):
    m, calls, notices = tmp_path / "m.json", [], []
    if marker is not None:
        m.write_text(marker if marker.startswith(("[", "n")) else json.dumps({"epoch": marker}))

    def fake_resume(vs, runs):
        calls.append((list(vs), runs))
        return [restore.ApplyResult(v, restore.RESUMED, "resumed at @9") for v in vs]

    def run(now=now):
        return restore.boot_pass(now, resume_enabled=enabled, marker=m,
                                 gather=lambda: (verdicts, [RUN]), resume_fn=resume_fn or fake_resume,
                                 notify=lambda *a: notices.append(a))
    return run, m, calls, notices


def test_a_new_epoch_resumes_the_ended_epochs_manual_rows_exactly_once(tmp_path):
    revivable = restore.Verdict("session-ids", "@5", PREV, "REVIVABLE", SID, "@8", "/x", "")
    run, m, calls, notices = _pass(tmp_path, [_manual(), _manual(stamped="1-old"), revivable])
    out = run()
    assert calls == [([_manual()], [RUN])]
    assert json.loads(m.read_text()) == {"epoch": NOW, "owed": [   # CMX-152: carried
        {"session_id": SID, "cwd": "/home/u/five", "label": "five", "wid": "@3",
         "store": "session-ids"}]}
    assert out == {"previous": PREV, "epoch": NOW, "resumed": ["five (resumed at @9)"],
                   "not_resumed": []}
    assert notices == [("restore_boot", "tmux restarted — came back: five (resumed at @9); "
                        "not resumed: none", out)]
    assert run() is None and len(calls) == 1 and len(notices) == 1   # same epoch: no-op


def test_flag_off_relaunches_nothing_and_reports(tmp_path):
    run, _, calls, notices = _pass(tmp_path, [_manual()], enabled=False)
    out = run()
    assert calls == [] and out["resumed"] == []
    assert out["not_resumed"] == ["five: CHELA_RESTORE_RESUME is off — report only"]
    assert len(notices) == 1 and "not resumed: five" in notices[0][1]


@pytest.mark.parametrize("now,marker,left", [
    (None, PREV, {"epoch": PREV}),             # tmux unknown: nothing, marker untouched
    (NOW, None, {"epoch": NOW}),               # no marker: a baseline, never a relaunch
    (NOW, "not json", {"epoch": NOW}),
    (NOW, "[]", {"epoch": NOW}),
])
def test_unknown_epoch_or_no_marker_relaunches_nothing(tmp_path, now, marker, left):
    run, m, calls, notices = _pass(tmp_path, [_manual()], now=now, marker=marker)
    assert run() is None and calls == [] and notices == []
    assert json.loads(m.read_text()) == left


def test_nothing_from_the_ended_epoch_sends_no_notice(tmp_path):
    run, _, _, notices = _pass(tmp_path, [_manual(stamped="1-old")])
    assert run() == {"previous": PREV, "epoch": NOW, "resumed": [], "not_resumed": []}
    assert notices == []


def test_a_live_runs_session_is_reported_not_resumed(tmp_path):
    run, _, _, _ = _pass(tmp_path, [_manual(cwd="/wt/CMX-95", label="CMX-95")],
                         resume_fn=restore.resume)
    out = run()
    assert out["resumed"] == []
    assert out["not_resumed"][0].startswith("CMX-95: its worktree belongs to run CMX-95")


def test_a_crashing_launch_still_spends_the_epoch(tmp_path):
    """The marker is written BEFORE the launch: a crash is not retried every tick."""
    def boom(vs, runs):
        raise RuntimeError("spawn exploded")
    run, m, _, notices = _pass(tmp_path, [_manual()], resume_fn=boom)
    with pytest.raises(RuntimeError):
        run()
    assert json.loads(m.read_text()) == {"epoch": NOW}
    assert run() is None and notices == []


def test_the_boot_marker_lives_in_chela_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(restore.config, "CHELA_DIR", tmp_path)
    assert restore.boot_marker_path() == tmp_path / "restore-boot.json"


@pytest.mark.parametrize("enabled", [True, False])
def test_the_daemon_loop_runs_the_boot_pass(monkeypatch, enabled):
    _stub_daemon_loop_inert(monkeypatch)
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.epoch, "current", lambda: NOW)
    monkeypatch.setattr(main.config, "RESTORE_RESUME_ENABLED", enabled)
    calls = []
    monkeypatch.setattr(main.restore, "boot_pass", lambda *a, **k: calls.append((a, k)))
    main.cmd_run(SimpleNamespace())
    assert calls == [((NOW,), {"resume_enabled": enabled, "gather": main._restore_gather,
                               "marker": restore.boot_marker_path()})]


def _real_stores(monkeypatch, verdicts, runs):
    """``_restore_gather``'s inputs faked at their leaves; returns what ``plan`` was given."""
    from chela.telegram.bindings import BindingRegistry
    reg = SimpleNamespace(windows=lambda: ["@4"], epoch_for=lambda wid: {"@4": PREV}[wid])
    monkeypatch.setattr(BindingRegistry, "load", classmethod(lambda cls, *a, **k: reg))
    inbox_state, entries, seen = object(), object(), []
    monkeypatch.setattr(main.inbox, "load", lambda: inbox_state)
    monkeypatch.setattr(main.sessionids, "entries", lambda: entries)
    monkeypatch.setattr(main.epoch, "current", lambda: NOW)
    monkeypatch.setattr(main.restore, "plan", lambda *a: seen.append(a) or verdicts)
    monkeypatch.setattr(main.dispatcher, "list_runs", lambda: runs)
    return seen, (inbox_state, {"@4": PREV}, entries, NOW)


def test_restore_gather_reads_the_stores_and_the_live_runs(monkeypatch):
    verdicts = [_manual()]
    seen, expected = _real_stores(monkeypatch, verdicts, [RUN])
    assert main._restore_gather() == (verdicts, [RUN])
    assert seen == [expected]


def test_the_wired_boot_pass_never_relaunches_into_a_live_runs_worktree(tmp_path, monkeypatch):
    """End to end through the daemon's own gather + the real resume: the session whose
    worktree a live run owns is skipped, the human session next to it comes back."""
    stale = _manual(wid="@5", cwd="/wt/CMX-95", label="CMX-95")
    human = restore.Verdict("session-ids", "@3", PREV, "MANUAL",
                            "cccccccc-1111-2222-3333-444444444444", None, "/home/u/five", "five")
    _real_stores(monkeypatch, [stale, human], [RUN])
    spawned = []
    resume_fn = functools.partial(
        restore.resume,
        spawn_window=lambda cwd, command: spawned.append(cwd) or SimpleNamespace(
            ok=True, wid="@9", error=None),
        check_resumed=lambda wid, sid: True, resume_blocked=lambda sid: None,
        clear_resume_failure=lambda sid: None, record_resume_failure=lambda *a: None,
        kill_window=lambda wid: None, archive=lambda entry: None,
        remove_session=lambda *a: True)
    m = tmp_path / "m.json"
    m.write_text(json.dumps({"epoch": PREV}))
    out = restore.boot_pass(NOW, resume_enabled=True, gather=main._restore_gather, marker=m,
                            resume_fn=resume_fn, notify=lambda *a: None)
    assert spawned == ["/home/u/five"]
    assert out["resumed"] == ["five (resumed at @9)"]
    assert out["not_resumed"] == ["CMX-95: its worktree belongs to run CMX-95, still in "
                                  "flight — the dispatcher gives that run its one window"]


@pytest.mark.parametrize("stamped,attempt,error", [
    (PREV, 1, "tmux server restarted (attempt not counted)"),    # died with the server
    (NOW, 2, "tmux window disappeared"),                          # died on its own
    (None, 2, "tmux window disappeared"),                         # unstamped: unknown
])
def test_a_tmux_restart_does_not_spend_an_attempt(tmp_path, monkeypatch, stamped, attempt, error):
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    wf = _wf(tmp_path)
    with dispatcher._db() as conn:
        _row(conn, workflow_path=str(wf.path), status="running", window_name="test-1",
             pr_url=None, pr_state=None, attempt=2, window_epoch=stamped)
    with patch.object(dispatcher, "load_workflow_cached", return_value=_status(wf)), \
         patch.object(dispatcher, "get_source", return_value=_Source("abc123")), \
         patch.object(dispatcher, "_claim_order", return_value=[]), \
         patch.object(dispatcher.epoch, "current", return_value=NOW), \
         patch.object(dispatcher.subprocess, "run", side_effect=_FakeTmux().run):
        assert dispatcher.tick(wf.path)["reconciled_failed"] == 1
    run = dispatcher.resolve_run("abc123")
    assert (run["status"], run["attempt"], run["last_error"]) == ("failed", attempt, error)
