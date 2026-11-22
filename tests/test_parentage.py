"""Session parentage (CMX-389): which session launched which, keyed by SESSION id.

The fold (:func:`chela.parentage.fold`) is driven with hand-built event-log records, the
same shapes ``chela.hooks.ingest`` writes; the daemon tick, ``chela spawn``, ``chela parents
backfill`` and ``/api/agents`` are driven through their real entry points with only leaf
I/O faked.
"""
from __future__ import annotations

import json
import sys
from contextlib import contextmanager
from unittest.mock import patch

import pytest

from chela import event_log, main, parentage, spawn

A = "aaaaaaaa-0000-4000-8000-000000000001"
B = "bbbbbbbb-0000-4000-8000-000000000002"
C = "cccccccc-0000-4000-8000-000000000003"
D = "dddddddd-0000-4000-8000-000000000004"
E = "eeeeeeee-0000-4000-8000-000000000005"
CFG = parentage.Settings(window_seconds=600.0, max_depth=3)
T0 = 1_000_000.0


def start(sid, ts, source="startup", cwd="/work"):
    return {"type": "hook.session_start", "ts": ts, "session_id": sid,
            "payload": {"source": source, "cwd": cwd}}


def send(sid, ts, text):
    return {"type": "hook.pre_tool_use", "ts": ts, "session_id": sid,
            "payload": {"tool_name": "SendMessage",
                        "tool_input": {"to": "uds:/x/1.sock", "message": text}}}


def arrive(sid, ts, text, cwd="/work"):
    prompt = (f'<cross-session-message from="uds:/run/user/1000/cc-socks/1.sock" '
              f'from-name="peer">\n{text}\n</cross-session-message>')
    return {"type": "hook.user_prompt_submit", "ts": ts, "session_id": sid,
            "payload": {"prompt": prompt, "cwd": cwd}}


def spawned(child, parent, ts):
    return {"type": "session.spawned", "ts": ts, "session_id": child,
            "payload": {"session_id": child, "parent_session_id": parent}}


def run(events, table=None, cfg=CFG, excluded=lambda cwd: False):
    table = {} if table is None else table
    decisions = parentage.fold(table, {}, events, cfg, excluded)
    return table, decisions


def message_to(child, sender, at, *, started=T0, text="please build the thing"):
    return [start(child, started), send(sender, at - 1, text), arrive(child, at, text)]


# --- the acceptance cases ---------------------------------------------------------------

def test_a_spawn_from_session_a_records_a_as_the_parent():
    table, decisions = run([spawned(B, A, T0)])
    assert table == {B: {"parent": A, "source": "spawn", "ts": T0}}
    assert decisions == [{"child": B, "parent": A, "source": "spawn", "ts": T0,
                          "refused": None}]


def test_a_first_message_inside_the_window_records_its_sender():
    table, _ = run(message_to(B, A, T0 + 300))
    assert table == {B: {"parent": A, "source": "first_message", "ts": T0 + 300}}


def test_a_first_message_at_exactly_the_window_edge_still_counts():
    table, _ = run(message_to(B, A, T0 + 600))
    assert table[B]["parent"] == A


def test_a_first_message_outside_the_window_records_nothing():
    table, decisions = run(message_to(B, A, T0 + 601))
    assert table == {}
    assert decisions == []


def test_a_cycle_is_refused():
    events = [spawned(B, A, T0)] + message_to(A, B, T0 + 60)
    table, decisions = run(events)
    assert A not in table
    assert decisions[-1]["refused"] == "cycle: the parent is the session's own descendant"


def test_a_deeper_cycle_is_refused_too():
    table = {B: {"parent": A, "source": "spawn", "ts": 1.0},
             C: {"parent": B, "source": "spawn", "ts": 2.0}}
    assert parentage.refusal(table, A, C, 9) == \
        "cycle: the parent is the session's own descendant"


@pytest.mark.parametrize("child,parent", [(B, None), (None, A), (B, ""), ("", A), (None, None)])
def test_a_spawn_missing_either_session_id_is_refused_with_its_reason(child, parent):
    # assign() re-checks the ids before writing, so the table alone cannot tell a refusal
    # from a silent skip; the decision's reason is the observable (CMX-464).
    table, decisions = run([spawned(child, parent, T0)])
    assert table == {}
    assert decisions == [{"child": child, "parent": parent, "source": "spawn", "ts": T0,
                          "refused": "missing session id"}]


@pytest.mark.parametrize("child,parent", [(B, None), (None, A), (B, ""), ("", A)])
def test_refusal_names_a_missing_session_id(child, parent):
    assert parentage.refusal({}, child, parent, 3) == "missing session id"
    assert parentage.assign({}, child, parent, "spawn", T0, 3) == "missing session id"


def test_a_session_cannot_be_its_own_parent():
    table, decisions = run([spawned(A, A, T0)])
    assert table == {}
    assert decisions[0]["refused"] == "a session cannot be its own parent"


# --- decided once -----------------------------------------------------------------------

def test_a_parent_is_never_changed_once_set():
    events = [spawned(B, A, T0), spawned(B, C, T0 + 1)] + message_to(B, D, T0 + 30)
    table, decisions = run(events)
    assert table[B] == {"parent": A, "source": "spawn", "ts": T0}
    assert decisions[1]["refused"] == "already has a parent"


def test_only_the_first_message_counts_even_when_its_sender_is_unknown():
    # The first arrival matches no SendMessage (chela's own relay, say); a later message from
    # a real session inside the window still must not become the parent.
    events = [start(B, T0), arrive(B, T0 + 10, "a notice from chela"),
              send(A, T0 + 19, "the real task"), arrive(B, T0 + 20, "the real task")]
    table, _ = run(events)
    assert table == {}


def test_a_session_with_no_observed_startup_is_never_inferred():
    events = [start(B, T0, source="resume"), send(A, T0 + 9, "hi there"),
              arrive(B, T0 + 10, "hi there")]
    table, _ = run(events)
    assert table == {}


def test_a_dispatched_receiver_is_left_out_of_parentage():
    events = [start(B, T0), send(A, T0 + 9, "your brief"),
              arrive(B, T0 + 10, "your brief", cwd="/wt/CMX-1")]
    table, _ = run(events, excluded=lambda cwd: cwd == "/wt/CMX-1")
    assert table == {}
    table, _ = run(events, excluded=lambda cwd: False)
    assert table[B]["parent"] == A


# --- depth ------------------------------------------------------------------------------

def _chain(n):
    """A -> B -> C -> D ... n links deep, as a table."""
    sids = [A, B, C, D, E]
    return {sids[i + 1]: {"parent": sids[i], "source": "spawn", "ts": float(i)}
            for i in range(n)}


def test_max_depth_allows_exactly_that_many_levels():
    table = _chain(2)                              # A -> B -> C: C is 2 levels down
    assert parentage.refusal(table, D, C, 3) is None
    assert parentage.refusal(_chain(3), E, D, 3) == \
        "too deep: the parent is already 3 level(s) down (max 3)"


def test_max_depth_is_configurable():
    cfg = parentage.Settings(window_seconds=600.0, max_depth=1)
    table, decisions = run([spawned(B, A, T0), spawned(C, B, T0 + 1)], cfg=cfg)
    assert C not in table
    assert decisions[1]["refused"] == "too deep: the parent is already 1 level(s) down (max 1)"


def test_ancestors_stops_at_a_loop_in_a_corrupt_file():
    table = {A: {"parent": B}, B: {"parent": A}}
    assert parentage.ancestors(table, A) == [B]


# --- sender matching --------------------------------------------------------------------

def test_two_sessions_sending_the_same_text_is_ambiguous_and_records_nothing():
    events = [start(C, T0), send(A, T0 + 5, "ok go"), send(B, T0 + 6, "ok go"),
              arrive(C, T0 + 7, "ok go")]
    table, _ = run(events)
    assert table == {}


def test_a_send_older_than_the_match_window_is_not_the_sender():
    events = [start(C, T0), send(A, T0 + 10, "do it"),
              arrive(C, T0 + 10 + parentage.SEND_MATCH_SECONDS + 1, "do it")]
    assert run(events)[0] == {}
    events[-1] = arrive(C, T0 + 10 + parentage.SEND_MATCH_SECONDS, "do it")
    assert run(events)[0][C]["parent"] == A


def test_a_send_after_the_arrival_is_not_the_sender():
    events = [start(C, T0), arrive(C, T0 + 10, "do it"), send(A, T0 + 11, "do it")]
    assert run(events)[0] == {}


def test_the_receivers_own_send_never_names_itself():
    events = [start(C, T0), send(C, T0 + 5, "echo"), arrive(C, T0 + 6, "echo")]
    assert run(events)[0] == {}


def test_different_text_is_not_a_match():
    events = [start(C, T0), send(A, T0 + 5, "build the thing"),
              arrive(C, T0 + 6, "something else entirely")]
    assert run(events)[0] == {}


def test_a_clipped_preview_on_either_side_still_matches_on_the_common_prefix():
    long = "x" * 400
    events = [start(C, T0), send(A, T0 + 5, long[:200] + "… [+200 chars]"),
              arrive(C, T0 + 6, long)]
    assert run(events)[0][C]["parent"] == A


def test_a_non_sendmessage_tool_call_is_never_a_sender():
    ev = send(A, T0 + 5, "hello")
    ev["payload"]["tool_name"] = "Bash"
    assert run([start(C, T0), ev, arrive(C, T0 + 6, "hello")])[0] == {}


@pytest.mark.parametrize("prompt, want", [
    ('<cross-session-message from="uds:/a.sock" from-name="x">\nhi\n</cross-session-message>',
     "hi"),
    ('<cross-session-message from="uds:/a.sock">body… [+12 chars]', "body"),
    ('  <cross-session-message from="u">\nlead space ok', "lead space ok"),
    ("hi <cross-session-message from=\"u\">\nnot at the start", None),
    ("<cross-session-messages from=\"u\">\nnear miss", None),
    ("plain prompt", None),
    (None, None),
])
def test_message_body_only_reads_a_leading_envelope(prompt, want):
    assert parentage.message_body(prompt) == want


def test_a_plain_prompt_never_marks_the_first_message_seen():
    events = [start(B, T0), {"type": "hook.user_prompt_submit", "ts": T0 + 1,
                             "session_id": B, "payload": {"prompt": "hello claude"}}]
    events += [send(A, T0 + 9, "task"), arrive(B, T0 + 10, "task")]
    assert run(events)[0][B]["parent"] == A


# --- reboot: keyed by session id, never window id ---------------------------------------

def _rows_after(windows: dict[str, str], key):
    """``/api/agents``-style rows for ``windows`` (wid -> session id), parents looked up by
    ``key(wid, sid)`` against a table recorded BEFORE the reboot under the same key."""
    before = {"@3": A, "@4": B}
    store = {key(w, s): {"parent": A, "source": "spawn", "ts": 1.0}
             for w, s in before.items() if s == B}
    return {w: parentage.row_fields(key(w, s), store) for w, s in windows.items()}


def test_a_reboot_with_new_window_ids_keeps_the_parents():
    rows = _rows_after({"@11": A, "@12": B}, key=lambda wid, sid: sid)
    assert rows["@12"] == {"parent_session_id": A, "parent_source": "spawn"}
    assert rows["@11"] == {"parent_session_id": None, "parent_source": None}


def test_negative_control_keying_by_window_id_loses_the_parents_on_reboot():
    rows = _rows_after({"@11": A, "@12": B}, key=lambda wid, sid: wid)
    assert rows["@12"] == {"parent_session_id": None, "parent_source": None}


HOME = "/home/tester"
WINDOWS_BEFORE = {"alpha": "@3", "beta": "@4"}
WINDOWS_AFTER = {"alpha": "@11", "beta": "@12"}
SID_BY_PID = {1003: A, 1004: B, 1011: A, 1012: B}
PID_BY_WID = {"@3": 1003, "@4": 1004, "@11": 1011, "@12": 1012}


@contextmanager
def _fleet(windows):
    from chela.dashboard import app as dash  # noqa: F401 — imported for the patches below
    with (
        patch.dict("os.environ", {"HOME": HOME}),
        patch("chela.sessions.panes", return_value={}),
        patch("chela.discovery.get_all_windows", return_value=dict(windows)),
        patch("chela.dispatcher.list_runs", return_value=[]),
        patch("chela.agent_manager.session_status_map",
              return_value={"by_pid": {}, "cwd_by_pid": {}}),
        patch("chela.agent_manager.claude_pid", side_effect=lambda wid: PID_BY_WID.get(wid)),
        patch("chela.agent_manager.session_entry",
              side_effect=lambda pid, _m: {"status": "idle", "cwd": HOME, "name": None,
                                           "moved": False,
                                           "session_id": SID_BY_PID.get(pid)} if pid else None),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.agent_manager.is_manual_name", return_value=False),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": None, "recap_ts": None, "pr": None, "ai_title": None}),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.inbox.is_done", return_value=False),
        patch("chela.epoch.is_dangling", return_value=False),
    ):
        yield


def _api_rows(windows):
    from chela.dashboard import app as dash
    with _fleet(windows):
        resp = dash.app.test_client().get("/api/agents")
    assert resp.status_code == 200
    return {a["window_id"]: a for a in resp.get_json()}


def test_api_agents_carries_the_parent_across_a_reboot_simulation():
    parentage.save({"parents": {B: {"parent": A, "source": "spawn", "ts": 1.0}}, "state": {}})
    before = _api_rows(WINDOWS_BEFORE)
    after = _api_rows(WINDOWS_AFTER)              # new window ids, same session ids
    for rows, wid in ((before, "@4"), (after, "@12")):
        assert rows[wid]["parent_session_id"] == A
        assert rows[wid]["parent_source"] == "spawn"
    assert after["@11"]["parent_session_id"] is None
    assert after["@11"]["parent_source"] is None


def test_api_agents_reads_every_row_top_level_when_the_store_cannot_be_read(monkeypatch):
    def boom():
        raise RuntimeError("store exploded")
    monkeypatch.setattr(parentage, "parents", boom)
    rows = _api_rows(WINDOWS_AFTER)
    assert rows["@12"]["parent_session_id"] is None
    assert rows["@12"]["parent_source"] is None


def test_row_fields_with_no_session_id_is_null():
    table = {B: {"parent": A, "source": "spawn"}}
    assert parentage.row_fields(None, table) == {"parent_session_id": None,
                                                 "parent_source": None}


# --- the store --------------------------------------------------------------------------

def test_the_store_lives_at_session_parents_json_under_chela_dir():
    from chela import config
    assert parentage.store_path() == config.CHELA_DIR / "session-parents.json"


@pytest.mark.parametrize("raw", ["not json", "[]", '{"parents": []}', '{"state": {}}'])
def test_an_unreadable_store_reads_as_empty(raw):
    path = parentage.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw)
    assert parentage.load() == {"parents": {}, "state": {}}


def test_a_store_with_a_bad_state_keeps_its_parents():
    path = parentage.store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"parents": {B: {"parent": A}}, "state": []}))
    assert parentage.load() == {"parents": {B: {"parent": A}}, "state": {}}


# --- settings precedence ----------------------------------------------------------------

@pytest.mark.parametrize("arg_w, env_w, want_w, arg_d, env_d, want_d", [
    (None, None, 600.0, None, None, 3),            # defaults: 10 minutes, depth 3
    (None, "5", 300.0, None, "2", 2),              # env over default
    (7, "5", 420.0, 4, "2", 4),                    # explicit arg over env
    (0, "5", 0.0, 1, "2", 1),                      # a falsy explicit arg still wins
])
def test_settings_precedence(monkeypatch, arg_w, env_w, want_w, arg_d, env_d, want_d):
    for name, val in (("CHELA_PARENT_WINDOW_MINUTES", env_w), ("CHELA_PARENT_MAX_DEPTH", env_d)):
        if val is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, val)
    assert parentage.settings(arg_w, arg_d) == parentage.Settings(want_w, want_d)


# --- the daemon tick --------------------------------------------------------------------

def _log(*events):
    for ev in events:
        event_log.append(ev["type"], "", ev["payload"], session_id=ev.get("session_id"))


def test_tick_folds_the_event_log_and_resumes_from_its_cursor(monkeypatch):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    _log(spawned(B, A, 0))
    first = parentage.tick(CFG)
    assert [(d["child"], d["parent"], d["refused"]) for d in first] == [(B, A, None)]
    assert parentage.parents()[B]["source"] == "spawn"
    assert parentage.tick(CFG) == []               # nothing re-read past the cursor
    _log(spawned(C, A, 0))
    assert [d["child"] for d in parentage.tick(CFG)] == [C]


def test_tick_skips_without_writing_when_the_runs_table_cannot_be_read(monkeypatch):
    def boom():
        raise RuntimeError("db locked")
    monkeypatch.setattr("chela.dispatcher.list_runs", boom)
    _log(spawned(B, A, 0))
    assert parentage.tick(CFG) == []
    assert not parentage.store_path().exists()


def test_tick_leaves_a_dispatched_runs_session_without_a_parent(monkeypatch, tmp_path):
    wt = tmp_path / "wt" / "CMX-9"
    wt.mkdir(parents=True)
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [{"worktree_path": str(wt)}])
    _log(start(B, 0, cwd=str(wt)), send(A, 0, "the brief"),
         arrive(B, 0, "the brief", cwd=str(wt / "sub")),
         start(C, 0), send(A, 0, "a task"), arrive(C, 0, "a task", cwd=str(tmp_path / "wt")))
    parentage.tick(CFG)
    assert set(parentage.parents()) == {C}


@pytest.mark.parametrize("cwd, want", [
    ("/wt/CMX-1", True), ("/wt/CMX-1/", True), ("/wt/CMX-1/x", True),
    ("/wt/CMX-10", False), ("/wt", False), ("", False), (None, False),
])
def test_excluder_matches_a_run_worktree_and_what_is_under_it(cwd, want):
    assert parentage.excluder(["/wt/CMX-1"])(cwd) is want


# --- chela spawn ------------------------------------------------------------------------

class _Proc:
    def __init__(self, stdout="@42"):
        self.returncode, self.stdout, self.stderr = 0, stdout, ""


def _cli(argv):
    old = sys.argv
    sys.argv = ["chela", *argv]
    try:
        main.main()
    except SystemExit as e:
        return e.code
    finally:
        sys.argv = old
    return None


@pytest.fixture()
def tmux_spawn(monkeypatch, tmp_path):
    monkeypatch.setattr(spawn.discovery, "ensure_session", lambda: True)
    monkeypatch.setattr(spawn.discovery, "get_all_windows", lambda: {})
    monkeypatch.setattr(spawn.envutil, "scrub_tmux_secrets", lambda: None)
    monkeypatch.setattr(spawn.agent_manager, "lock_window_name", lambda *a, **kw: None)
    monkeypatch.setattr(spawn.subprocess, "run", lambda *a, **kw: _Proc())
    monkeypatch.setattr(spawn.config, "remote_control_enabled", lambda: False)
    monkeypatch.setattr(spawn, "_record_session_id", lambda wid, sid: True)
    monkeypatch.setattr(spawn, "_send", lambda target, text: None)
    monkeypatch.setattr("chela.launcher.record_recent", lambda cwd: None)


def _spawned_records():
    return [r for r in event_log.read(types=["session.spawned"])["events"]]


def test_spawn_window_reports_the_session_id_it_pinned(tmux_spawn, tmp_path):
    result = spawn.spawn_window(tmp_path, command="claude")
    assert result.session_id and len(result.session_id) == 36


def test_spawn_window_reports_no_session_id_when_it_could_not_record_one(
        tmux_spawn, tmp_path, monkeypatch):
    monkeypatch.setattr(spawn, "_record_session_id", lambda wid, sid: False)
    assert spawn.spawn_window(tmp_path, command="claude").session_id is None


def test_chela_spawn_from_a_session_logs_the_launch_record(tmux_spawn, tmp_path, monkeypatch,
                                                          capsys):
    monkeypatch.setattr(parentage, "caller_session", lambda: (A, None))
    assert _cli(["spawn", str(tmp_path)]) is None
    recs = _spawned_records()
    assert len(recs) == 1
    child = recs[0]["payload"]["session_id"]
    assert recs[0]["payload"] == {"session_id": child, "parent_session_id": A, "wid": "@42"}
    assert recs[0]["session_id"] == child and recs[0]["wid"] == "@42"
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    parentage.tick(CFG)
    assert parentage.parents()[child] == {"parent": A, "source": "spawn",
                                          "ts": recs[0]["ts"]}
    assert capsys.readouterr().err == ""


def test_chela_spawn_with_a_stale_wid_records_nothing_and_says_so(tmux_spawn, tmp_path,
                                                                 monkeypatch, capsys):
    monkeypatch.setattr(parentage, "caller_session", lambda: (None, "the note"))
    assert _cli(["spawn", str(tmp_path)]) is None
    assert _spawned_records() == []
    assert "spawn: the note" in capsys.readouterr().err


def test_chela_spawn_with_no_pinned_session_records_nothing_and_says_so(
        tmux_spawn, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(parentage, "caller_session", lambda: (A, None))
    monkeypatch.setattr(spawn, "_record_session_id", lambda wid, sid: False)
    assert _cli(["spawn", str(tmp_path)]) is None
    assert _spawned_records() == []
    assert "was not pinned — no parent recorded" in capsys.readouterr().err


def test_chela_spawn_says_so_when_the_launch_record_cannot_be_written(
        tmux_spawn, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(parentage, "caller_session", lambda: (A, None))
    monkeypatch.setattr(parentage.event_log, "append", lambda *a, **kw: None)
    assert _cli(["spawn", str(tmp_path)]) is None
    assert "could not write the launch record" in capsys.readouterr().err


def test_chela_spawn_from_a_plain_shell_is_silent(tmux_spawn, tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("CHELA_WID", raising=False)
    assert _cli(["spawn", str(tmp_path)]) is None
    assert _spawned_records() == []
    assert capsys.readouterr().err == ""


# --- caller_session: map $CHELA_WID to a session, never guess -----------------------------

@pytest.fixture()
def registry(monkeypatch):
    state = {"pid": 77, "entry": {"sessionId": A, "tmux": "chela:@5.%9"}}
    monkeypatch.setattr(parentage.sessions, "own_claude_pid", lambda: state["pid"])
    monkeypatch.setattr(parentage.sessions, "registry_entry",
                        lambda pid: state["entry"] if pid == 77 else None)
    return state


def test_caller_session_maps_a_live_chela_wid_to_its_session(registry):
    assert parentage.caller_session({"CHELA_WID": "@5"}) == (A, None)
    assert parentage.caller_session({"CHELA_WID": " @5 "}) == (A, None)


def test_caller_session_without_chela_wid_is_a_plain_shell(registry):
    assert parentage.caller_session({}) == (None, None)
    assert parentage.caller_session({"CHELA_WID": "  "}) == (None, None)


def test_caller_session_reads_the_process_environment_by_default(registry, monkeypatch):
    monkeypatch.setenv("CHELA_WID", "@5")
    assert parentage.caller_session() == (A, None)


def test_caller_session_refuses_a_stale_chela_wid(registry):
    assert parentage.caller_session({"CHELA_WID": "@4"}) == (
        None, "$CHELA_WID=@4 is stale (this session runs in @5) — no parent recorded")


def test_caller_session_refuses_a_registry_entry_with_no_window(registry):
    registry["entry"] = {"sessionId": A}
    assert parentage.caller_session({"CHELA_WID": "@5"}) == (
        None, "$CHELA_WID=@5 is stale (this session runs in no window) — no parent recorded")


def test_caller_session_outside_a_claude_session_records_nothing(registry):
    registry["pid"] = None
    assert parentage.caller_session({"CHELA_WID": "@5"}) == (
        None, "$CHELA_WID=@5 is set but this is not running inside a Claude session — "
              "no parent recorded")


def test_caller_session_with_an_unreadable_registry_records_nothing(registry):
    registry["entry"] = None
    assert parentage.caller_session({"CHELA_WID": "@5"}) == (
        None, "cannot read the Claude session registry for pid 77 — no parent recorded")


# --- chela parents backfill -------------------------------------------------------------

def _backfill_log():
    _log(start(B, 0), send(A, 0, "go build"), arrive(B, 0, "go build"))


def test_backfill_is_a_dry_run_that_prints_the_tree(monkeypatch, capsys):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    _backfill_log()
    assert _cli(["parents", "backfill"]) is None
    out = capsys.readouterr().out
    assert f"+ {B} <- {A} (first_message)" in out
    assert f"{A}\n  └ {B}  (first_message)" in out
    assert "dry run — pass --apply to write them" in out
    assert not parentage.store_path().exists()


def test_backfill_apply_writes_the_parents(monkeypatch, capsys):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    _backfill_log()
    assert _cli(["parents", "backfill", "--apply"]) is None
    assert parentage.parents()[B]["parent"] == A
    assert "wrote 1 parent(s)" in capsys.readouterr().out


def test_backfill_honours_its_window_flag(monkeypatch, capsys):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    _log(start(B, 0))
    # The log stamps its own `ts`: back-date the session's start by two minutes.
    path = event_log.log_path()
    recs = [json.loads(line) for line in path.read_text().splitlines()]
    recs[-1]["ts"] -= 120
    path.write_text("".join(json.dumps(r) + "\n" for r in recs))
    _log(send(A, 0, "late"), arrive(B, 0, "late"))
    assert _cli(["parents", "backfill", "--window-minutes", "1"]) is None
    assert "0 new parent(s)" in capsys.readouterr().out
    assert _cli(["parents", "backfill", "--window-minutes", "3"]) is None
    assert "1 new parent(s)" in capsys.readouterr().out


def test_backfill_refuses_when_the_runs_table_cannot_be_read(monkeypatch, capsys):
    def boom():
        raise RuntimeError("db locked")
    monkeypatch.setattr("chela.dispatcher.list_runs", boom)
    assert _cli(["parents", "backfill", "--apply"]) == 1
    assert "refusing to infer" in capsys.readouterr().err
    assert not parentage.store_path().exists()


def test_parents_without_a_subcommand_is_a_usage_error(capsys):
    assert _cli(["parents"]) == 2


def test_apply_backfill_never_changes_a_parent_recorded_meanwhile():
    parentage.save({"parents": {B: {"parent": C, "source": "spawn", "ts": 1.0}}, "state": {}})
    decisions = [{"child": B, "parent": A, "source": "first_message", "ts": 2.0,
                  "refused": None},
                 {"child": D, "parent": A, "source": "first_message", "ts": 3.0,
                  "refused": None},
                 {"child": E, "parent": A, "source": "first_message", "ts": 4.0,
                  "refused": "cycle"}]
    assert parentage.apply_backfill(decisions, CFG) == 1
    assert parentage.parents() == {
        B: {"parent": C, "source": "spawn", "ts": 1.0},
        D: {"parent": A, "source": "first_message", "ts": 3.0}}


def test_apply_backfill_refuses_while_the_daemon_holds_its_tick(monkeypatch):
    from contextlib import contextmanager as cm

    from chela import ticklock

    @cm
    def busy(timeout=60):
        yield "chela-daemon is still mid-tick"
    monkeypatch.setattr(ticklock, "between_ticks", busy)
    with pytest.raises(RuntimeError, match="mid-tick"):
        parentage.apply_backfill([{"child": B, "parent": A, "source": "spawn", "ts": 1.0,
                                   "refused": None}], CFG)
    assert not parentage.store_path().exists()


def test_log_records_reads_rolled_files_oldest_first_without_duplicates():
    base = event_log.log_path()
    base.parent.mkdir(parents=True, exist_ok=True)

    def write(path, seqs):
        path.write_text("".join(json.dumps({"seq": s, "type": "session.spawned"}) + "\n"
                                for s in seqs))
    write(base.with_name(base.name + ".2"), [1, 2])
    write(base.with_name(base.name + ".1"), [2, 3])
    write(base, [4])
    (base.with_name(base.name + ".3")).write_text(
        json.dumps({"seq": 0, "type": "hook.stop"}) + "\n")
    assert [r["seq"] for r in parentage.log_records()] == [1, 2, 3, 4]


def test_tree_lines_nests_children_under_parents():
    table = {B: {"parent": A, "source": "spawn"}, C: {"parent": B, "source": "first_message"},
             D: {"parent": A, "source": "spawn"}}
    assert parentage.tree_lines(table) == [
        A, f"  └ {B}  (spawn)", f"    └ {C}  (first_message)", f"  └ {D}  (spawn)"]


# --- the daemon loop is the writer ------------------------------------------------------

def _one_daemon_tick(monkeypatch, tick):
    from types import SimpleNamespace

    from tests.test_main_dispatch_log import _stub_daemon_loop_inert
    _stub_daemon_loop_inert(monkeypatch)
    monkeypatch.setattr(main, "DISPATCH_WORKFLOWS", [])
    monkeypatch.setattr(main.restore, "boot_pass", lambda *a, **k: None)
    monkeypatch.setattr(main.parentage, "tick", tick)
    after = []
    monkeypatch.setattr(main.context, "prune_snapshots",
                        lambda retention_days: after.append("prune") or 0)
    main.cmd_run(SimpleNamespace())
    return after


def test_the_daemon_loop_runs_the_parentage_tick(monkeypatch):
    calls = []
    _one_daemon_tick(monkeypatch, lambda: calls.append("tick"))
    assert calls == ["tick"]


def test_a_failing_parentage_tick_never_stops_the_daemon_tick(monkeypatch):
    def boom():
        raise RuntimeError("store exploded")
    assert _one_daemon_tick(monkeypatch, boom) == ["prune"]


# --- review fixes: gap log, message text in memory only, no idle rewrites, backfill noise --

@pytest.fixture(autouse=True)
def _fresh_sends(monkeypatch):
    monkeypatch.setattr(parentage, "_SENDS", [])


def test_tick_logs_a_cursor_the_event_log_could_not_honour(monkeypatch, caplog):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    gap = {"resume_from_seq": 9, "reason": "rolled"}
    monkeypatch.setattr(parentage.event_log, "read", lambda *a, **k: {
        "events": [], "gap": gap, "next_seq": 12, "boot_id": "b1"})
    with caplog.at_level("WARNING", logger="chela.parentage"):
        parentage.tick(CFG)
    assert any("could not resume" in r.getMessage() and "rolled" in r.getMessage()
               for r in caplog.records)
    assert parentage.load()["state"]["cursor"] == {"seq": 12, "boot_id": "b1"}


def test_tick_without_a_gap_logs_no_warning(monkeypatch, caplog):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    with caplog.at_level("WARNING", logger="chela.parentage"):
        parentage.tick(CFG)
    assert not [r for r in caplog.records if r.levelname == "WARNING"]


def test_message_text_never_reaches_the_store_but_still_matches_across_ticks(monkeypatch):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    _log(start(B, 0), send(A, 0, "secret plans for the build"))
    parentage.tick(CFG)
    raw = parentage.store_path().read_text()
    assert "secret plans" not in raw
    assert "sends" not in json.loads(raw)["state"]
    _log(arrive(B, 0, "secret plans for the build"))
    parentage.tick(CFG)                            # the sender came from memory
    assert parentage.parents()[B]["parent"] == A
    assert "secret plans" not in parentage.store_path().read_text()


def test_an_old_store_with_message_text_is_scrubbed_on_the_next_tick(monkeypatch):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    parentage.save({"parents": {}, "state": {"sends": [[1.0, A, "old secret"]]}})
    parentage.tick(CFG)
    assert "old secret" not in parentage.store_path().read_text()


def test_an_idle_tick_does_not_rewrite_the_store(monkeypatch):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    _log(spawned(B, A, 0))
    parentage.tick(CFG)
    writes = []
    monkeypatch.setattr(parentage, "save", lambda data: writes.append(data))
    parentage.tick(CFG)
    assert writes == []
    _log(spawned(C, A, 0))
    parentage.tick(CFG)
    assert len(writes) == 1


def test_backfill_does_not_report_children_already_recorded(monkeypatch, capsys):
    monkeypatch.setattr("chela.dispatcher.list_runs", lambda: [])
    _log(spawned(C, A, 0))
    parentage.save({"parents": {C: {"parent": A, "source": "spawn", "ts": 1.0}},
                    "state": {}})
    _backfill_log()
    assert _cli(["parents", "backfill"]) is None
    out = capsys.readouterr().out
    assert f"- {C} <- {A}" not in out
    assert "1 new parent(s), 0 refused, 1 already recorded" in out
