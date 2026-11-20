"""CMX-449 — a crash loop's short-lived middle epoch must not hide the real working set.

Live 2026-11-20: tmux restarted twice. The middle epoch lived seconds and held two windows
(the orchestrator and one agent); the epoch before it held the other eleven. The boot pass
compared against the middle epoch only, so two of ~12 sessions came back. And ``chela
restore`` listed most rows "MANUAL (no cwd/session on record)" although each session's
transcript names its cwd.
"""
from __future__ import annotations

import functools
import json
from types import SimpleNamespace

import pytest

from chela import config, restore, roster, sessions

A, B, C, OLD = "1-100", "2-200", "3-300", "0-50"
LONG, SHORT = 3 * 86400.0, 5.0


def _sid(prefix: str, n: int) -> str:
    return f"{prefix}{n:07d}-1111-2222-3333-444444444444"


def _row(epoch, sid, label, *, verdict="MANUAL", new_wid=None, store="session-ids", wid="@1"):
    return restore.Verdict(store, wid, epoch, verdict, sid, new_wid, f"/home/u/{label}", label)


ORCH = _sid("a", 0)
A_ROWS = [_row(A, _sid("a", i), f"proj{i}", wid=f"@{i}") for i in range(10)]
# B: the orchestrator again (same session as A's proj0 — the dedup case) + one agent of its own.
B_ROWS = [_row(B, ORCH, "proj0", wid="@0"), _row(B, _sid("b", 1), "psy", wid="@1")]
ENDED = [(B, {"@0": {}, "@1": {}}), (A, {f"@{i}": {} for i in range(10)}), (OLD, {"@0": {}})]


def _boot(tmp_path, verdicts, *, lifetimes, short_s=900, ended=ENDED, prev=B, live=None):
    m = tmp_path / "restore-boot.json"
    m.write_text(json.dumps({"epoch": prev}))
    spawned, notices = [], []
    resume_fn = functools.partial(
        restore.resume,
        spawn_window=lambda cwd, command: spawned.append(command) or SimpleNamespace(
            ok=True, wid=f"@{len(spawned) + 40}", error=None),
        register_orchestrator=lambda wid: {"ok": True},
        check_resumed=lambda wid, sid: True, resume_blocked=lambda sid: None,
        clear_resume_failure=lambda sid: None, record_resume_failure=lambda *a: None,
        kill_window=lambda wid: None, archive=lambda entry: None,
        remove_session=lambda *a: True, unregister_orchestrator=lambda *a: {"ok": True})
    out = restore.boot_pass(
        C, resume_enabled=True, gather=lambda: (verdicts, []), marker=m, resume_fn=resume_fn,
        notify=lambda *a: notices.append(a), roster_epochs=lambda now: ended,
        roster_lifetimes=lambda: lifetimes, short_epoch_s=short_s,
        wid_for_session=live or (lambda sid: None), closed_lookup=lambda eps: {})
    return SimpleNamespace(out=out, spawned=spawned, notices=notices)


def _launched(r):
    return sorted(c.split()[-1] for c in r.spawned)


# --- the acceptance pair -----------------------------------------------------------------

def test_a_long_then_b_short_then_c_resumes_as_10_plus_bs_2_deduplicated(tmp_path):
    r = _boot(tmp_path, A_ROWS + B_ROWS, lifetimes={A: LONG, B: SHORT})
    want = sorted({v.session_id for v in A_ROWS + B_ROWS})
    assert len(want) == 11                                  # 10 + 2, one session shared
    assert _launched(r) == want
    assert len(r.spawned) == 11                             # the shared one launched ONCE
    assert r.out["recovered_from"] == A
    assert r.out["source_epochs"] == [B, A]
    assert r.out["walked"] == [f"{B} (lived 5s, under 900s)"]
    note = r.notices[0][1]
    assert note.startswith(f"tmux restarted twice; recovered from epoch {A} — came back: ")
    assert note.endswith(f"; walked back past: {B} (lived 5s, under 900s)")


@pytest.mark.parametrize("lifetimes,short_s", [
    ({A: LONG, B: SHORT}, 0),            # negative control: the walk off = the old code
    ({A: LONG, B: LONG}, 900),           # B genuinely lived: it IS the working set
    ({A: LONG}, 900),                    # B's lifetime unknown: never read as short
    ({A: LONG, B: 900.0}, 900),          # exactly the threshold is not under it
])
def test_negative_control_only_bs_two_come_back(tmp_path, lifetimes, short_s):
    r = _boot(tmp_path, A_ROWS + B_ROWS, lifetimes=lifetimes, short_s=short_s)
    assert _launched(r) == sorted(v.session_id for v in B_ROWS)
    assert "recovered_from" not in r.out and "walked" not in r.out
    assert "walked back past" not in r.notices[0][1]


def test_b_one_second_under_the_threshold_is_walked(tmp_path):
    r = _boot(tmp_path, A_ROWS + B_ROWS, lifetimes={A: LONG, B: 899.0})
    assert len(r.spawned) == 11


def test_the_walk_stops_at_the_first_long_epoch_and_never_reaches_older(tmp_path):
    old = _row(OLD, _sid("c", 9), "ancient", wid="@9")
    r = _boot(tmp_path, A_ROWS + B_ROWS + [old], lifetimes={A: LONG, B: SHORT, OLD: LONG})
    assert old.session_id not in " ".join(r.spawned)


def test_a_session_already_live_is_skipped_and_named(tmp_path):
    live_row = _row(A, _sid("a", 3), "proj3", verdict="REVIVABLE", new_wid="@7", wid="@3")
    rows = [v for v in A_ROWS if v.session_id != live_row.session_id] + [live_row] + B_ROWS
    r = _boot(tmp_path, rows, lifetimes={A: LONG, B: SHORT})
    assert live_row.session_id not in " ".join(r.spawned)
    assert len(r.spawned) == 10
    assert r.out["skipped_live"] == ["proj3: already live at @7"]
    assert "; skipped, already live: proj3: already live at @7" in r.notices[0][1]


def test_a_live_row_outside_the_source_epochs_is_not_reported(tmp_path):
    other = _row(OLD, _sid("c", 1), "elsewhere", verdict="REVIVABLE", new_wid="@7")
    r = _boot(tmp_path, B_ROWS + [other], lifetimes={A: LONG, B: LONG})
    assert "skipped_live" not in r.out


def test_one_live_session_seen_in_two_rows_is_named_once(tmp_path):
    sid = _sid("a", 3)
    rows = [_row(A, sid, "proj3", verdict="REVIVABLE", new_wid="@7"),
            _row(B, sid, "proj3", verdict="REVIVABLE", new_wid="@7")]
    r = _boot(tmp_path, rows, lifetimes={A: LONG, B: SHORT})
    assert r.out["skipped_live"] == ["proj3: already live at @7"]


def test_three_restarts_say_so_and_name_every_epoch_walked(tmp_path):
    ended = [(C + "x", {"@0": {}}), (B, {}), (A, {"@0": {}})]
    m = tmp_path / "restore-boot.json"
    m.write_text(json.dumps({"epoch": C + "x"}))
    notices = []
    out = restore.boot_pass(
        "4-400", resume_enabled=False, gather=lambda: ([_row(A, ORCH, "proj0")], []),
        marker=m, notify=lambda *a: notices.append(a), roster_epochs=lambda now: ended,
        roster_lifetimes=lambda: {C + "x": 2.0, A: LONG}, short_epoch_s=900,
        wid_for_session=lambda sid: None, closed_lookup=lambda eps: {})
    assert out["source_epochs"] == [C + "x", B, A]
    assert out["walked"] == [f"{C}x (lived 2s, under 900s)", f"{B} (recorded no windows)"]
    assert notices[0][1].startswith(f"tmux restarted 3 times; recovered from epoch {A} — ")


# --- _source_epochs: the walk, as a table -------------------------------------------------

W = {"@1": {}}


@pytest.mark.parametrize("previous,ended,lifetimes,short_s,want", [
    (B, [(B, W), (A, W)], {B: SHORT, A: LONG}, 900, [B, A]),
    (B, [(B, W), (A, W)], {B: SHORT, A: LONG}, 0, [B]),
    (B, [(B, W), (A, W)], {B: LONG, A: LONG}, 900, [B]),
    (B, [(B, W), (A, {}), (OLD, W)], {B: SHORT}, 900, [B, A, OLD]),   # OLD unknown = long
    (B, [(B, W), (A, W), (OLD, {})], {B: SHORT, A: SHORT}, 900, [B, A]),   # all short
    (B, [(B, W), (A, {})], {B: SHORT}, 900, [B]),        # nothing older had windows
    (B, [(B, {}), (A, {})], {}, 900, [B]),
    (B, [(A, W)], {A: SHORT}, 900, [B]),                 # previous unrecorded: no walk
    (A, [(B, W), (A, W), (OLD, W)], {A: SHORT}, 900, [A, OLD]),   # starts AT previous
])
def test_source_epochs_walk(previous, ended, lifetimes, short_s, want):
    assert restore._source_epochs(previous, ended, lifetimes, short_s) == want


def test_closed_before_crash_reads_the_walked_epochs():
    seen = []
    restore.closed_before_crash(C, roster_epochs=lambda n: [(B, W), (A, W)],
                                roster_lifetimes=lambda: {B: SHORT, A: LONG},
                                short_epoch_s=900, closed_lookup=lambda eps: seen.append(eps) or {})
    assert seen == [[B, A]]


def test_closed_before_crash_and_boot_pass_default_to_the_configured_threshold(
        tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_RESTORE_SHORT_EPOCH_S", "10")
    seen = []
    restore.closed_before_crash(C, roster_epochs=lambda n: [(B, W), (A, W)],
                                roster_lifetimes=lambda: {B: 20.0, A: LONG},
                                closed_lookup=lambda eps: seen.append(eps) or {})
    assert seen == [[B]]
    monkeypatch.setenv("CHELA_RESTORE_SHORT_EPOCH_S", "30")
    restore.closed_before_crash(C, roster_epochs=lambda n: [(B, W), (A, W)],
                                roster_lifetimes=lambda: {B: 20.0, A: LONG},
                                closed_lookup=lambda eps: seen.append(eps) or {})
    assert seen[-1] == [B, A]
    r = _boot(tmp_path, A_ROWS + B_ROWS, lifetimes={A: LONG, B: 20.0}, short_s=None)
    assert len(r.spawned) == 11


def test_boot_pass_defaults_to_the_real_roster_lifetimes(tmp_path, monkeypatch):
    store = tmp_path / "roster.json"
    store.write_text(json.dumps({"epochs": {
        A: {"first_seen": 0.0, "last_seen": LONG, "windows": W},
        B: {"first_seen": LONG + 10, "last_seen": LONG + 12, "windows": W}}}))
    monkeypatch.setattr(roster, "_STORE", store)
    m = tmp_path / "restore-boot.json"
    m.write_text(json.dumps({"epoch": B}))
    out = restore.boot_pass(
        C, resume_enabled=False, gather=lambda: (A_ROWS[:1] + B_ROWS[1:], []), marker=m,
        notify=lambda *a: None, short_epoch_s=900, wid_for_session=lambda sid: None,
        closed_lookup=lambda eps: {})
    assert out["source_epochs"] == [B, A]
    assert out["walked"] == [f"{B} (lived 2s, under 900s)"]


# --- config + roster.lifetimes ----------------------------------------------------------

@pytest.mark.parametrize("raw,want", [
    (None, 900), ("", 900), ("120", 120), ("0", 0), ("junk", 900), ("-5", 0),
    ("999999", 86400),
])
def test_short_epoch_threshold_env(monkeypatch, raw, want):
    if raw is None:
        monkeypatch.delenv("CHELA_RESTORE_SHORT_EPOCH_S", raising=False)
    else:
        monkeypatch.setenv("CHELA_RESTORE_SHORT_EPOCH_S", raw)
    assert config.restore_short_epoch_seconds() == want


def test_roster_lifetimes(tmp_path):
    p = tmp_path / "roster.json"
    p.write_text(json.dumps({"epochs": {
        A: {"first_seen": 100.0, "last_seen": 400.5},
        B: {"first_seen": 100},                       # no last_seen: unknown, not zero
        C: {"last_seen": 100},
        OLD: {"first_seen": 500, "last_seen": 400},   # clock went backwards: clamp to 0
        "x": {"first_seen": "100", "last_seen": 200},
    }}))
    assert roster.lifetimes(path=p) == {A: 300.5, OLD: 0.0}


def test_roster_lifetimes_of_a_missing_file_is_empty(tmp_path):
    assert roster.lifetimes(path=tmp_path / "nope.json") == {}


# --- the transcript fallback: session id → JSONL → cwd ----------------------------------

SID = "dddddddd-1111-2222-3333-444444444444"


def _transcript(tmp_path, lines, sid=SID):
    proj = tmp_path / "projects" / "-home-u-x"
    proj.mkdir(parents=True, exist_ok=True)
    (proj / f"{sid}.jsonl").write_text("".join(
        (ln if isinstance(ln, str) else json.dumps(ln)) + "\n" for ln in lines))
    return tmp_path / "projects"


def test_cwd_for_session_reads_the_first_record_carrying_one(tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    base = _transcript(tmp_path, ["not json", {"type": "summary"}, [1, 2],
                                  {"cwd": "relative/path"}, {"cwd": str(work)},
                                  {"cwd": str(tmp_path)}])
    assert sessions.cwd_for_session(SID, base) == str(work)


@pytest.mark.parametrize("lines", [
    [{"type": "summary"}],                         # no cwd at all
    [{"cwd": 42}],                                  # not a string
    [{"cwd": "relative"}],                          # not absolute
    [{"cwd": "/definitely/not/a/dir/cmx449"}],      # gone: relaunching there only fails
])
def test_cwd_for_session_fails_closed(tmp_path, lines):
    assert sessions.cwd_for_session(SID, _transcript(tmp_path, lines)) is None


def test_cwd_for_session_scans_a_bounded_prefix(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "_CWD_SCAN_LINES", 3)
    base = _transcript(tmp_path, [{}, {}, {}, {"cwd": str(tmp_path)}])
    assert sessions.cwd_for_session(SID, base) is None
    monkeypatch.setattr(sessions, "_CWD_SCAN_LINES", 4)
    assert sessions.cwd_for_session(SID, base) == str(tmp_path)


def test_cwd_for_session_without_a_transcript_or_session_is_none(tmp_path):
    assert sessions.cwd_for_session(SID, tmp_path) is None
    assert sessions.cwd_for_session(None, tmp_path) is None


def test_cwd_for_session_unreadable_transcript_is_none(tmp_path):
    proj = tmp_path / "projects" / "-x"
    (proj / f"{SID}.jsonl").mkdir(parents=True)     # open() raises IsADirectoryError
    assert sessions.cwd_for_session(SID, tmp_path / "projects") is None


def test_cwd_for_session_defaults_to_the_claude_projects_dir(tmp_path, monkeypatch):
    base = _transcript(tmp_path, [{"cwd": str(tmp_path)}])
    monkeypatch.setattr(sessions.transcripts, "CLAUDE_PROJECTS_DIR", base)
    assert sessions.cwd_for_session(SID) == str(tmp_path)


def _plan(roster_row, cwd_for_session):
    calls = []

    def fallback(sid):
        calls.append(sid)
        return cwd_for_session

    vs = restore.plan({}, {}, {"@4": {"session_id": SID, "epoch": A}}, C,
                      roster_lookup=lambda ep, wid: roster_row,
                      wid_for_session=lambda sid: None, cwd_for_session=fallback)
    return vs[0], calls


def test_a_row_with_no_cwd_on_record_gets_its_transcripts_cwd():
    v, calls = _plan({"name": "glide"}, "/home/u/glide")
    assert (v.verdict, v.cwd, calls) == ("MANUAL", "/home/u/glide", [SID])
    assert v.manual_command() == f"cd /home/u/glide && CHELA_WID=@N claude --resume {SID}"


def test_a_cwd_on_record_wins_and_the_transcript_is_not_read():
    v, calls = _plan({"cwd": "/home/u/roster"}, "/home/u/transcript")
    assert (v.cwd, calls) == ("/home/u/roster", [])


def test_a_transcript_without_a_cwd_leaves_the_row_manual_with_nothing_to_run():
    v, _ = _plan({}, None)
    assert v.cwd is None and v.manual_command() is None


def test_no_session_means_no_transcript_lookup():
    calls = []
    vs = restore.plan({}, {"@4": A}, {}, C, roster_lookup=lambda ep, wid: {},
                      wid_for_session=lambda sid: None,
                      cwd_for_session=lambda sid: calls.append(sid) or "/x")
    assert vs[0].cwd is None and calls == []


def test_plan_wires_the_fallback_into_every_store():
    seen = []
    restore.plan({"orchestrator": "@0", "orchestrator_epoch": A,
                  "orchestrator_session": SID}, {"@2": A},
                 {"@4": {"session_id": SID, "epoch": A}}, C,
                 roster_lookup=lambda ep, wid: {"session_id": SID} if wid == "@2" else {},
                 wid_for_session=lambda sid: None,
                 cwd_for_session=lambda sid: seen.append(sid) or "/x")
    assert seen == [SID, SID, SID]


def test_plan_defaults_to_the_real_transcript_reader(tmp_path, monkeypatch):
    base = _transcript(tmp_path, [{"cwd": str(tmp_path)}])
    monkeypatch.setattr(sessions.transcripts, "CLAUDE_PROJECTS_DIR", base)
    vs = restore.plan({}, {}, {"@4": {"session_id": SID, "epoch": A}}, C,
                      roster_lookup=lambda ep, wid: {}, wid_for_session=lambda sid: None)
    assert vs[0].cwd == str(tmp_path)

