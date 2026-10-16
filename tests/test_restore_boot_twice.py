"""CMX-152 — tmux restarting twice before the roster samples the relaunched windows must not
lose the human sessions: the boot pass looks back to the newest epoch that recorded windows,
carries what it resumed itself until a sample shows it, and never launches a session twice."""
from __future__ import annotations

import functools
import json
from types import SimpleNamespace

import pytest

from chela import restore, roster

A, B, C, OLD = "1-100", "2-200", "3-300", "0-50"
S1 = "aaaaaaaa-1111-2222-3333-444444444444"
S2 = "bbbbbbbb-1111-2222-3333-444444444444"
S3 = "cccccccc-1111-2222-3333-444444444444"
WINS = {"@1": {"name": "x", "cwd": "/x", "session_id": "dddddddd-1111-2222-3333-444444444444"}}


def _row(stamped, sid=S1, cwd="/home/u/orch", label="orch", store="session-ids", wid="@1"):
    return restore.Verdict(store, wid, stamped, "MANUAL", sid, None, cwd, label)


def _boot(tmp_path, now, marker_state, verdicts, ended=None, *, live=lambda sid: None):
    """The real boot pass + the real :func:`restore.resume`, faked only at its tmux leaves."""
    m = tmp_path / "restore-boot.json"
    m.write_text(json.dumps(marker_state))
    spawned, notices, registered, removed = [], [], [], []
    resume_fn = functools.partial(
        restore.resume,
        spawn_window=lambda cwd, command: spawned.append(command) or SimpleNamespace(
            ok=True, wid=f"@{len(spawned) + 10}", error=None),
        register_orchestrator=lambda wid: registered.append(wid) or {"ok": True},
        check_resumed=lambda wid, sid: True, resume_blocked=lambda sid: None,
        clear_resume_failure=lambda sid: None, record_resume_failure=lambda *a: None,
        kill_window=lambda wid: None, archive=lambda entry: None,
        remove_session=lambda *a: removed.append(a) or True, unregister_orchestrator=lambda *a: {"ok": True})
    seams = {} if ended is None else {"roster_epochs": lambda now_epoch: ended,
                                      "wid_for_session": live}
    out = restore.boot_pass(now, resume_enabled=True, gather=lambda: (verdicts, []), marker=m,
                            resume_fn=resume_fn, notify=lambda *a: notices.append(a), **seams)
    return SimpleNamespace(out=out, spawned=spawned, notices=notices, registered=registered,
                           removed=removed,
                           marker=json.loads(m.read_text()))


def test_a_b_c_where_b_recorded_nothing_resumes_a_once_and_never_older(tmp_path):
    verdicts = [_row(A), _row(A, store="inbox.orchestrator"), _row(OLD, sid=S3)]
    r = _boot(tmp_path, C, {"epoch": B}, verdicts,
              [(B, {}), (A, WINS), (OLD, WINS)])
    assert r.spawned == [f"claude --resume {S1}"]                 # once, and never OLD's S3
    assert r.out["recovered_from"] == A
    assert r.notices[0][1].startswith(f"tmux restarted twice; recovered from epoch {A} — "
                                      "came back: orch (resumed at @11)")


@pytest.mark.parametrize("prev,ended", [
    (A, [(A, WINS), (B, {}), (OLD, WINS)]),    # previous recorded WITH windows: no walk
    (B, [(OLD, WINS)]),                        # previous never recorded — unknown, not empty
    (B, [(B, {}), (OLD, {})]),                 # nothing older recorded windows either
])
def test_the_lookback_only_walks_from_a_recorded_empty_previous(tmp_path, prev, ended):
    r = _boot(tmp_path, C, {"epoch": prev}, [_row(OLD, sid=S3)], ended)
    assert r.spawned == [] and "recovered_from" not in r.out


def test_a_session_from_two_epochs_is_launched_from_the_newest_only(tmp_path):
    newer = _row(B, cwd="/home/u/new", label="new")
    r = _boot(tmp_path, C, {"epoch": B}, [_row(A, label="older"), newer], [(B, {}), (A, WINS)])
    assert r.spawned == [f"claude --resume {S1}"] and r.out["resumed"][0].startswith("new ")


def _pass_at_b_then_c(tmp_path, *, live=lambda sid: None, b_windows=None):
    """A → B: the pass resumes S1 (orchestrator) and S2. Then C, before B was sampled."""
    at_b = _boot(tmp_path, B, {"epoch": A},
                 [_row(A, store="inbox.orchestrator"), _row(A, sid=S2, cwd="/home/u/mmo",
                                                            label="mmo", wid="@2")],
                 [(A, WINS)])
    at_c = _boot(tmp_path, C, at_b.marker,
                 [_row(B, cwd=None, store="inbox.orchestrator", wid="@11")],
                 [(B, b_windows or {}), (A, WINS)], live=live)
    return at_b, at_c


def test_sessions_the_b_pass_resumed_come_back_at_c_once_each(tmp_path):
    at_b, at_c = _pass_at_b_then_c(tmp_path)
    assert [o["session_id"] for o in at_b.marker["owed"]] == [S1, S2]
    assert at_c.spawned == [f"claude --resume {S1}", f"claude --resume {S2}"]
    assert at_c.registered == ["@11"]                    # the inbox comes back armed
    assert at_c.removed == []                 # a carried session has no store row to remove
    assert at_c.out["resumed"] == ["orch (resumed at @11, orchestrator re-registered)",
                                   "mmo (resumed at @12)"]
    assert [o["session_id"] for o in at_c.marker["owed"]] == [S1, S2]
    assert at_c.notices[0][1].startswith(f"tmux restarted twice; recovered from epoch {A}")


def test_a_carried_session_already_live_now_is_not_relaunched_but_stays_owed(tmp_path):
    _, at_c = _pass_at_b_then_c(tmp_path, live=lambda sid: "@7" if sid == S1 else None)
    assert at_c.spawned == [f"claude --resume {S2}"]
    assert [o["session_id"] for o in at_c.marker["owed"]] == [S1, S2]


def test_a_session_b_sampled_live_is_paid_and_not_carried(tmp_path):
    b_windows = {"@11": {"name": "orch", "cwd": "/home/u/orch", "session_id": S1}}
    _, at_c = _pass_at_b_then_c(tmp_path, b_windows=b_windows)
    assert at_c.spawned == [f"claude --resume {S2}"]
    assert "recovered_from" in at_c.out              # S2 carried from B's own pass


def test_a_sampled_previous_with_nothing_owed_keeps_the_cmx125_notice(tmp_path):
    r = _boot(tmp_path, B, {"epoch": A}, [_row(A)], [(A, WINS)])
    assert r.notices[0][1] == "tmux restarted — came back: orch (resumed at @11); not resumed: none"
    assert "recovered_from" not in r.out


@pytest.mark.parametrize("owed", [
    "junk", [None], [{"session_id": S1}], [{"cwd": "/x"}], [{"session_id": S1, "cwd": "/x"}],
])
def test_a_malformed_owed_list_launches_only_complete_entries(tmp_path, owed):
    r = _boot(tmp_path, C, {"epoch": B, "owed": owed}, [], [(B, WINS)])
    complete = isinstance(owed, list) and owed[0] and owed[0].get("cwd") and owed[0].get("session_id")
    assert r.spawned == ([f"claude --resume {S1}"] if complete else [])


def test_roster_ended_lists_the_other_epochs_most_recently_seen_first(tmp_path):
    p = tmp_path / "roster.json"
    p.write_text(json.dumps({"epochs": {
        A: {"last_seen": 10, "windows": WINS}, B: {"last_seen": 30},
        C: {"last_seen": 40, "windows": WINS}, OLD: {"last_seen": 5, "windows": {}}}}))
    assert roster.ended(C, path=p) == [(B, {}), (A, WINS), (OLD, {})]


def test_the_production_call_walks_the_real_roster_and_the_real_live_check(tmp_path, monkeypatch):
    """main.py calls boot_pass with NO roster_epochs / wid_for_session (defeat shape 361): the
    defaults must read the real ``roster.json`` and the real ``sessions.wid_for_session``."""
    monkeypatch.setattr(roster, "_STORE", tmp_path / "roster.json")
    (tmp_path / "roster.json").write_text(json.dumps({"epochs": {
        B: {"last_seen": 30, "windows": {}}, A: {"last_seen": 20, "windows": WINS},
        OLD: {"last_seen": 10, "windows": WINS}}}))
    monkeypatch.setattr(restore.sessions, "wid_for_session",
                        lambda sid, *a, **k: "@7" if sid == S2 else None)
    owed = [{"session_id": S2, "cwd": "/home/u/mmo", "label": "mmo", "wid": "@2"}]
    r = _boot(tmp_path, C, {"epoch": B, "owed": owed}, [_row(A), _row(OLD, sid=S3)])
    assert r.spawned == [f"claude --resume {S1}"]    # A walked back to; S2 live; OLD untouched
    assert r.out["recovered_from"] == A
