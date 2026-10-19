"""CMX-275 — a crash restore must never revive a session the user CLOSED.

Live 2026-10-19: a session a boot pass resumed was carried in the marker's ``owed`` list,
closed by hand in the next epoch, and revived by the next crash — ``owed`` is dropped only
when the dead epoch's LAST roster sample shows the session, and a closed one never shows.
The roster tick now records a window that vanished while its server lived as CLOSED, and
every restore path skips (and names) those sessions.
"""
from __future__ import annotations

import functools
import json
import time
from types import SimpleNamespace

import pytest

from chela import restore, roster

A, B, C = "1-100", "2-200", "3-300"
S = "e4c15b05-1111-2222-3333-444444444444"
T = "ffffffff-1111-2222-3333-444444444444"
CWD = "/home/u/projects/site"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(roster, "_STORE", tmp_path / "roster.json")
    return tmp_path / "roster.json"


def _rec(epoch, wins, at, monkeypatch):
    """One reconcile tick: ``wins`` is ``{wid: (name, session_id)}``."""
    monkeypatch.setattr(roster.time, "time", lambda: at)
    roster.record({w: n for w, (n, _) in wins.items()}, set(wins), epoch,
                  lambda wid: CWD, lambda wid: wins[wid][1])


# --- roster.record: what counts as closed ------------------------------------------------

def test_a_window_gone_from_the_next_sample_of_the_same_epoch_is_recorded_closed(
        store, monkeypatch):
    _rec(B, {"@11": ("site", S), "@12": ("other", T)}, 1000.0, monkeypatch)
    _rec(B, {"@12": ("other", T)}, 2000.0, monkeypatch)
    _rec(B, {"@12": ("other", T)}, 2060.0, monkeypatch)       # the server outlived the close
    assert json.loads(store.read_text())["epochs"][B]["closed"] == {
        S: {"wid": "@11", "name": "site", "cwd": CWD, "closed_at": 2000.0}}
    assert roster.closed([B]) == {
        S: {"wid": "@11", "name": "site", "cwd": CWD, "closed_at": 2000.0, "epoch": B}}


def test_a_crash_is_not_a_close_a_new_epoch_never_closes_the_old_ones_windows(
        store, monkeypatch):
    _rec(B, {"@11": ("site", S)}, 1000.0, monkeypatch)
    _rec(C, {}, 2000.0, monkeypatch)
    assert roster.closed([B, C]) == {}


def test_a_closed_session_seen_alive_again_is_unclosed(store, monkeypatch):
    _rec(B, {"@11": ("site", S)}, 1000.0, monkeypatch)
    _rec(B, {}, 2000.0, monkeypatch)
    _rec(B, {"@20": ("site", S)}, 3000.0, monkeypatch)       # resumed by hand
    assert roster.closed([B]) == {}


@pytest.mark.parametrize("last_tick,counted", [(1059.0, False), (1060.0, True)])
def test_a_close_in_the_servers_last_minute_is_shutdown_teardown_not_the_user(
        store, monkeypatch, last_tick, counted):
    """A reboot tears panes down one by one while tmux still answers — those vanishings
    must not read as the user closing them, or the crash they belong to restores nothing."""
    _rec(B, {"@11": ("site", S)}, 900.0, monkeypatch)
    _rec(B, {}, 1000.0, monkeypatch)
    _rec(B, {}, last_tick, monkeypatch)
    assert (S in roster.closed([B])) is counted


def test_a_vanished_window_with_no_session_records_nothing(store, monkeypatch):
    _rec(B, {"@11": ("site", None)}, 1000.0, monkeypatch)
    _rec(B, {}, 2000.0, monkeypatch)
    _rec(B, {}, 3000.0, monkeypatch)
    assert roster.closed([B]) == {}


def test_closed_prefers_the_first_listed_epoch_and_ignores_unknown_ones(store, monkeypatch):
    _rec(A, {"@1": ("old", S)}, 1.0, monkeypatch)
    _rec(A, {}, 2.0, monkeypatch)
    _rec(A, {}, 100.0, monkeypatch)
    _rec(B, {"@11": ("new", S)}, 103.0, monkeypatch)
    _rec(B, {}, 104.0, monkeypatch)
    _rec(B, {}, 200.0, monkeypatch)
    assert roster.closed([B, A, "9-999"])[S]["wid"] == "@11"
    assert roster.closed(["9-999"]) == {}


def test_closed_note_names_the_time_and_the_window(monkeypatch):
    monkeypatch.setattr(restore.time, "localtime", time.gmtime)
    assert restore.closed_note({"closed_at": 1000.0, "wid": "@32"}) == \
        "closed by the user at 1970-01-01 00:16 (was @32)"


# --- the boot pass: the CMX-275 incident end to end -------------------------------------

def _boot(tmp_path, now, marker_state, gathered=None, **seams):
    """The real boot pass over the real roster file + real :func:`restore.resume`, faked
    only at its tmux leaves. ``gathered`` defaults to the real ``plan`` over a session-ids
    row stamping S at @11 under B."""
    m = tmp_path / "restore-boot.json"
    m.write_text(json.dumps(marker_state))
    spawned, notices = [], []
    resume_fn = functools.partial(
        restore.resume,
        spawn_window=lambda cwd, command: spawned.append(command) or SimpleNamespace(
            ok=True, wid=f"@{len(spawned) + 10}", error=None),
        register_orchestrator=lambda wid: {"ok": True},
        check_resumed=lambda wid, sid: True, resume_blocked=lambda sid: None,
        clear_resume_failure=lambda sid: None, record_resume_failure=lambda *a: None,
        kill_window=lambda wid: None, archive=lambda entry: None,
        remove_session=lambda *a: True, unregister_orchestrator=lambda *a: {"ok": True})

    def gather():
        if gathered is not None:
            return gathered, []
        entries = {"@11": {"session_id": S, "epoch": B}}
        return restore.plan({}, {}, entries, now, wid_for_session=lambda sid: None), []

    out = restore.boot_pass(now, resume_enabled=True, gather=gather, marker=m,
                            resume_fn=resume_fn, notify=lambda *a: notices.append(a),
                            wid_for_session=lambda sid: None, **seams)
    return SimpleNamespace(out=out, spawned=spawned, notices=notices,
                           marker=json.loads(m.read_text()))


def _carried_into_b(tmp_path, monkeypatch):
    """Epoch A's boot list has S; the pass into B resumes it (carried in ``owed``)."""
    _rec(A, {"@1": ("orch", T)}, 10.0, monkeypatch)
    owed = [{"session_id": S, "cwd": CWD, "label": "site", "wid": "@32",
             "store": restore.CARRIED}]
    at_b = _boot(tmp_path, B, {"epoch": A, "owed": owed}, gathered=[])
    assert at_b.spawned == [f"claude --resume {S}"]
    assert [o["session_id"] for o in at_b.marker["owed"]] == [S]


def test_a_session_restored_in_b_then_closed_in_b_is_not_resumed_by_bs_crash(
        tmp_path, store, monkeypatch):
    _carried_into_b(tmp_path, monkeypatch)
    _rec(B, {"@11": ("site", S), "@12": ("orch", T)}, 20.0, monkeypatch)
    _rec(B, {"@12": ("orch", T)}, 30.0, monkeypatch)           # the user closes S
    _rec(B, {"@12": ("orch", T)}, 500.0, monkeypatch)          # ... and B lives on

    at_c = _boot(tmp_path, C, {"epoch": B, "owed": [
        {"session_id": S, "cwd": CWD, "label": "site", "wid": "@32",
         "store": restore.CARRIED}]})

    assert S not in " ".join(at_c.spawned)
    assert at_c.marker.get("owed") is None, "a closed session must drop out of owed"
    assert at_c.out["skipped_closed"] == [f"site: {restore.closed_note(roster.closed([B])[S])}"]
    assert "; skipped, closed: site: closed by the user at " in at_c.notices[0][1]
    assert "recovered_from" not in at_c.out, "nothing was recovered — S was closed"


def test_a_session_still_alive_at_bs_crash_is_resumed(tmp_path, store, monkeypatch):
    _carried_into_b(tmp_path, monkeypatch)
    _rec(B, {"@11": ("site", S), "@12": ("orch", T)}, 20.0, monkeypatch)
    _rec(B, {"@11": ("site", S), "@12": ("orch", T)}, 30.0, monkeypatch)

    at_c = _boot(tmp_path, C, {"epoch": B, "owed": [
        {"session_id": S, "cwd": CWD, "label": "site", "wid": "@32",
         "store": restore.CARRIED}]})

    assert at_c.spawned == [f"claude --resume {S}"]
    assert "skipped_closed" not in at_c.out


def test_a_gathered_row_of_a_closed_session_is_skipped_and_alone_still_notifies(tmp_path):
    row = restore.Verdict("session-ids", "@11", B, "MANUAL", S, None, CWD, "site")
    other = restore.Verdict("session-ids", "@12", B, "MANUAL", T, None, CWD, "orch")
    info = {"wid": "@11", "closed_at": 30.0}
    seen = []
    r = _boot(tmp_path, C, {"epoch": B}, gathered=[row],
              roster_epochs=lambda now: [(B, {"@1": {}})],
              closed_lookup=lambda eps: seen.append(eps) or {S: info})
    assert r.spawned == [] and seen == [[B]]
    assert r.out["skipped_closed"] == [f"site: {restore.closed_note(info)}"]
    assert len(r.notices) == 1

    r = _boot(tmp_path, C, {"epoch": B}, gathered=[row, other],
              roster_epochs=lambda now: [(B, {"@1": {}})],
              closed_lookup=lambda eps: {S: info})
    assert r.spawned == [f"claude --resume {T}"]


# --- resume(closed=...) and closed_before_crash: what `chela restore` uses --------------

def test_resume_skips_a_closed_session_without_launching_it():
    spawned = []
    rows = [restore.Verdict("session-ids", "@11", B, "MANUAL", S, None, CWD, "site"),
            restore.Verdict("session-ids", "@12", B, "MANUAL", T, None, CWD, "orch")]
    out = restore.resume(
        rows, [], closed={S: {"wid": "@11", "closed_at": 30.0}},
        spawn_window=lambda cwd, command: spawned.append(command) or SimpleNamespace(
            ok=True, wid="@40", error=None),
        check_resumed=lambda wid, sid: True, resume_blocked=lambda sid: None,
        clear_resume_failure=lambda sid: None, record_resume_failure=lambda *a: None,
        kill_window=lambda wid: None, archive=lambda e: None, remove_session=lambda *a: True)
    assert spawned == [f"claude --resume {T}"]
    assert (out[0].action, out[0].detail) == (
        restore.SKIPPED, restore.closed_note({"wid": "@11", "closed_at": 30.0}))
    assert out[1].action == restore.RESUMED


@pytest.mark.parametrize("now,ended,want", [
    (None, [(B, {"@1": {}})], None),
    (C, [], None),
    (C, [(B, {"@1": {}}), (A, {"@1": {}})], [B]),
    (C, [(B, {}), (A, {"@1": {}})], [B, A]),
])
def test_closed_before_crash_reads_the_epochs_the_boot_pass_reads(now, ended, want):
    seen = []
    got = restore.closed_before_crash(now, roster_epochs=lambda n: ended,
                                      closed_lookup=lambda eps: seen.append(eps) or {S: {}})
    assert got == ({} if want is None else {S: {}})
    assert seen == ([] if want is None else [want])


def test_closed_before_crash_defaults_to_the_real_roster(store, monkeypatch):
    _rec(B, {"@11": ("site", S)}, 1.0, monkeypatch)
    _rec(B, {}, 2.0, monkeypatch)
    _rec(B, {}, 62.0, monkeypatch)
    assert restore.closed_before_crash(C)[S]["wid"] == "@11"
