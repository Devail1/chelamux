"""CMX-337 — a crash restore resumed sessions from an OLDER tmux epoch: the epoch restored two
restarts ago came back next to the one that replaced it, and a window whose live session had
moved on (``/clear``) came back under its launch id. The restore follows the newest epoch's
LIVE sessions, once per window lineage (name + cwd)."""
from __future__ import annotations

import functools
import json
from types import SimpleNamespace

import pytest

from chela import restore, roster, sessions
from chela.sessions import Pane

A, B, C = "1-100", "2-200", "3-300"
OA = "0a0a0a0a-1111-2222-3333-444444444444"     # orchestrator, epoch A
OB = "0b0b0b0b-1111-2222-3333-444444444444"     # orchestrator after /clear in epoch B
MA = "4a4a4a4a-1111-2222-3333-444444444444"     # mmo, epoch A
MB = "4b4b4b4b-1111-2222-3333-444444444444"     # mmo after /clear in epoch B


def _resume_fn(spawned):
    return functools.partial(
        restore.resume,
        spawn_window=lambda cwd, command: spawned.append(command) or SimpleNamespace(
            ok=True, wid=f"@{len(spawned) + 10}", error=None),
        register_orchestrator=lambda wid: {"ok": True},
        check_resumed=lambda wid, sid: True, resume_blocked=lambda sid: None,
        clear_resume_failure=lambda sid: None, record_resume_failure=lambda *a: None,
        kill_window=lambda wid: None, archive=lambda entry: None,
        remove_session=lambda *a: True)


def _boot(tmp_path, now, store, entries):
    """The real boot pass over the real roster FILE, a real plan() as its gather."""
    spawned, notices = [], []
    gather = lambda: (restore.plan(  # noqa: E731
        {}, {}, entries, now, roster_lookup=functools.partial(roster.window, path=store),
        wid_for_session=lambda sid: None), [])
    out = restore.boot_pass(
        now, resume_enabled=True, gather=gather, marker=tmp_path / "restore-boot.json",
        resume_fn=_resume_fn(spawned), notify=lambda *a: notices.append(a),
        roster_epochs=functools.partial(roster.ended, path=store),
        closed_lookup=functools.partial(roster.closed, path=store),
        wid_for_session=lambda sid: None)
    return SimpleNamespace(out=out, spawned=spawned, notices=notices)


def _tick(store, monkeypatch, now_epoch, windows):
    """One reconcile tick through record()'s DEFAULT identity reader.
    ``windows``: ``{wid: (name, cwd, pid, launch_id, live_id)}``."""
    monkeypatch.setattr(sessions, "panes", lambda: {
        w: Pane(w, claude_pid=pid, resumed=launch) for w, (_, _, pid, launch, _) in windows.items()})
    monkeypatch.setattr(sessions, "live_session_of_window",
                        lambda wid, pane_map=None: windows[wid][4])
    return roster.record({w: v[0] for w, v in windows.items()}, set(windows), now_epoch,
                         lambda w: windows[w][1], path=store)


@pytest.fixture
def three_epochs(tmp_path, monkeypatch):
    """A (recorded) → B: the boot pass resumes A's two windows. In B both are `/clear`ed —
    the hooks see the new ids, the command lines keep `--resume <A's id>` — and then the
    live evidence ages out of the event log. Then a crash: C."""
    store = tmp_path / "roster.json"
    (tmp_path / "restore-boot.json").write_text(json.dumps({"epoch": A}))
    _tick(store, monkeypatch, A, {"@2": ("u", "/home/u", 40, None, OA),
                                  "@3": ("mmo", "/p/mmo", 41, None, MA)})
    at_b = _boot(tmp_path, B, store, {"@2": {"session_id": OA, "epoch": A},
                                      "@3": {"session_id": MA, "epoch": A}})
    assert sorted(at_b.spawned) == sorted([f"claude --resume {OA}", f"claude --resume {MA}"])
    b_windows = {"@11": ["u", "/home/u", 50, OA, OB], "@12": ["mmo-2", "/p/mmo", 51, MA, MB]}
    _tick(store, monkeypatch, B, b_windows)
    for w in b_windows.values():
        w[4] = None                                  # the SessionStart record aged out
    _tick(store, monkeypatch, B, b_windows)
    # session-ids.json carries the LAUNCH ids — the store a /clear never rewrites
    return SimpleNamespace(store=store, entries={"@11": {"session_id": OA, "epoch": B},
                                                 "@12": {"session_id": MA, "epoch": B}})


def test_a_crash_after_a_restore_resumes_only_the_newest_epochs_live_sessions_once(
        tmp_path, three_epochs):
    r = _boot(tmp_path, C, three_epochs.store, three_epochs.entries)
    assert sorted(r.spawned) == sorted([f"claude --resume {OB}", f"claude --resume {MB}"])
    assert "recovered_from" not in r.out, "B recorded windows — nothing older is a source"


def test_the_roster_keeps_the_live_id_when_only_the_launch_id_is_left(three_epochs):
    rows = roster._load(three_epochs.store)["epochs"][B]["windows"]
    assert {w: (r["session_id"], r["session_source"]) for w, r in rows.items()} == {
        "@11": (OB, "live"), "@12": (MB, "live")}


def test_a_relaunched_claude_process_may_record_its_launch_id(tmp_path, monkeypatch):
    """The live id sticks to the claude PROCESS: a new pid in the window is a new launch."""
    store = tmp_path / "roster.json"
    _tick(store, monkeypatch, B, {"@1": ("u", "/u", 50, OA, OB)})
    rec = _tick(store, monkeypatch, B, {"@1": ("u", "/u", 77, MA, None)})
    assert rec["windows"]["@1"] == {"name": "u", "cwd": "/u", "session_id": MA,
                                    "session_source": "launch", "pid": 77}


def test_an_unknown_pid_never_holds_a_live_id_over_the_launch_id(tmp_path, monkeypatch):
    store = tmp_path / "roster.json"
    _tick(store, monkeypatch, B, {"@1": ("u", "/u", None, OA, OB)})
    rec = _tick(store, monkeypatch, B, {"@1": ("u", "/u", None, OA, None)})
    assert rec["windows"]["@1"]["session_id"] == OA


def test_no_evidence_at_all_records_no_session_and_no_source(tmp_path, monkeypatch):
    rec = _tick(tmp_path / "roster.json", monkeypatch, B, {"@1": ("u", "/u", 9, None, None)})
    assert rec["windows"]["@1"] == {"name": "u", "cwd": "/u", "session_id": None,
                                    "session_source": None, "pid": 9}


@pytest.mark.parametrize("source,expected", [("live", "sid-roster"), ("launch", "sid-store"),
                                             (None, "sid-store")])
def test_only_a_LIVE_roster_id_outranks_the_stores_own(source, expected):
    row = {"session_id": "sid-roster", "cwd": "/x", **({"session_source": source} if source else {})}
    v = restore._classify("session-ids", "@5", A, "sid-store", B, lambda *a: row,
                          lambda sid: None)
    assert v.session_id == expected


def test_owed_sessions_retire_once_the_epoch_shows_their_window(tmp_path, three_epochs):
    """A's sessions were owed by the A → B pass; B's sample shows both windows (under new ids
    and a `-2` name), so nothing from A is carried into C's marker."""
    r = _boot(tmp_path, C, three_epochs.store, three_epochs.entries)
    marker = json.loads((tmp_path / "restore-boot.json").read_text())
    assert sorted(o["session_id"] for o in marker["owed"]) == sorted([OB, MB])
    assert len(r.spawned) == 2


def _row(stamped, sid, label, cwd="/p/mmo", wid="@1", store="session-ids"):
    return restore.Verdict(store, wid, stamped, "MANUAL", sid, None, cwd, label)


def test_a_carried_session_loses_to_the_newer_epochs_session_of_its_lineage(tmp_path):
    """The live incident's shape, at the pass: A's stale mmo is still owed while B's gather
    holds the live one. Only B's comes back; A's is named, not launched."""
    m = tmp_path / "restore-boot.json"
    m.write_text(json.dumps({"epoch": B, "owed": [
        {"session_id": MA, "cwd": "/p/mmo", "label": "mmo", "wid": "@3", "store": "session-ids"}]}))
    spawned = []
    out = restore.boot_pass(
        C, resume_enabled=True, gather=lambda: ([_row(B, MB, "mmo-2", wid="@12")], []), marker=m,
        resume_fn=_resume_fn(spawned), notify=lambda *a: None,
        roster_epochs=lambda now: [(B, {}), (A, {"@3": {"name": "x", "cwd": "/x"}})],
        closed_lookup=lambda eps: {}, wid_for_session=lambda sid: None)
    assert spawned == [f"claude --resume {MB}"]
    assert out["not_resumed"] == [f"mmo: superseded by session {MB} of the same window, from "
                                  "a newer tmux epoch"]


def test_two_source_epochs_resume_the_newer_window_of_a_lineage_only():
    rows = [_row(B, MB, "mmo"), _row(A, MA, "mmo")]
    keep, superseded = restore._one_per_lineage(rows, [], [B, A])
    assert keep == [rows[0]] and [r.verdict for r in superseded] == [rows[1]]
    assert superseded[0].action == restore.SKIPPED


def test_two_windows_of_one_epoch_sharing_a_lineage_both_come_back():
    """They were alive side by side — neither is a stale copy of the other."""
    rows = [_row(B, MA, "mmo", wid="@1"), _row(B, MB, "mmo-2", wid="@2")]
    assert restore._one_per_lineage(rows, [], [B]) == (rows, [])


def test_a_row_with_no_lineage_is_never_superseded():
    rows = [_row(B, MB, "mmo"), _row(A, MA, "", cwd="/p/mmo")]
    assert restore._one_per_lineage(rows, [], [B, A]) == (rows, [])


@pytest.mark.parametrize("name,cwd,expected", [
    ("mmo", "/p/mmo", ("mmo", "/p/mmo")),
    ("mmo-2", "/p/mmo/", ("mmo", "/p/mmo")),
    ("mmo-2-3", "/p/mmo", ("mmo-2-3", "/p/mmo")),
    ("cmx-337", "/w/repo", ("cmx-337", "/w/repo")),
    ("cmx-337-2", "/w/cmx-337", ("cmx-337", "/w/cmx-337")),
    ("cmx-337", "/w/cmx-338", ("cmx-337", "/w/cmx-338")),
    ("mmo-2", "/w", ("mmo-2", "/w")),
    ("mmo-2x", "/p/mmo", ("mmo-2x", "/p/mmo")),
    ("xmmo-2", "/p/mmo", ("xmmo-2", "/p/mmo")),
    ("", "/w", None),
    ("mmo", None, None),
])
def test_lineage(name, cwd, expected):
    assert restore.lineage(name, cwd) == expected


def test_live_session_of_window_never_reads_the_launch_command_line(monkeypatch):
    pane_map = {"@1": Pane("@1", claude_pid=None, resumed=OA, started=None)}
    assert sessions.live_session_of_window("@1", pane_map) is None
    assert sessions.session_of_window("@1", pane_map) == OA     # the full chain still does
    monkeypatch.setattr(sessions, "registry_session", lambda pid: OB)
    assert sessions.live_session_of_window("@1", pane_map) == OB
    assert sessions.session_of_window("@1", pane_map) == OB


def test_a_carried_row_outranks_an_older_source_epoch_but_not_the_previous_one():
    """The walk-back case (previous recorded no windows): a carried row was resumed INTO
    previous, so it is newer than the older epoch the walk reached."""
    carried, older = _row(B, MB, "mmo"), _row(A, MA, "mmo")
    keep, superseded = restore._one_per_lineage([carried, older], [carried], [B, A])
    assert keep == [carried] and [r.verdict for r in superseded] == [older]
    own = _row(B, OB, "mmo", wid="@9")
    keep, superseded = restore._one_per_lineage([carried, own], [carried], [B, A])
    assert keep == [own] and [r.verdict for r in superseded] == [carried]


def test_a_row_from_an_unknown_epoch_never_crashes_the_pass():
    rows = [_row(B, MB, "mmo"), _row("9-9", MA, "mmo")]
    keep, superseded = restore._one_per_lineage(rows, [], [B])
    assert keep == [rows[0]] and [r.verdict for r in superseded] == [rows[1]]


@pytest.mark.parametrize("wid", [None, ""])
def test_live_session_of_no_window_is_None_without_a_pane_scan(monkeypatch, wid):
    monkeypatch.setattr(sessions, "panes", lambda force=False: pytest.fail("scanned panes"))
    assert sessions.live_session_of_window(wid) is None
