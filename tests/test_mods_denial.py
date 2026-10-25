"""CMX-184, Mod 4/4: a classifier denial posted by the Mod straight to ``POST /api/inbox/denial``
queues CMX-155's inbox notice with no event-log tail, and ONE denial is ONE notice with both
layers loaded (the Mod's post plus ``plugin/``'s event-log copy), keyed on its ``tool_use_id``.

``mods/chela-hooks/hooks/denial-cases.ts`` is ONE fixture read by both suites: the mod's
``claude plugin test`` proves it POSTs ``body`` for ``event``; these file that same ``body``
through the REAL route. tmux, statuses, the peer socket and the runs DB are stubbed; the event
log and the inbox store are per-test files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chela import dispatcher, event_log, hooks, inbox, notify

pytest.importorskip("flask")

MOD = Path(__file__).resolve().parent.parent / "mods" / "chela-hooks"
CASES = json.loads(
    (MOD / "hooks" / "denial-cases.ts").read_text()
    .split("export const DENIAL =", 1)[1].rsplit("as const", 1)[0])
ORCH = "@1"
AGENT = CASES["wid"]
KEY = CASES["body"]["tool_use_id"]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [_run()])
    monkeypatch.setattr(inbox.epoch, "current", lambda: "e1")
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: {ORCH: inbox.BUSY})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator", AGENT: "cmx-9"})
    monkeypatch.setattr(inbox.sessions, "session_of_window", lambda wid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "wid_for_session", lambda sid, pane_map=None: None)
    monkeypatch.setattr(inbox.sessions, "transcript_for_window", lambda wid: None)
    monkeypatch.setattr(inbox.transcripts, "transcript_for_cwd", lambda cwd: None)
    monkeypatch.setattr(inbox, "stuck_probes", lambda runs: {})
    from chela import messenger
    monkeypatch.setattr(inbox.messenger, "send_peer",
                        lambda wid, frm, text: messenger.PeerSendResult(False, None))
    monkeypatch.setattr(inbox.messenger, "send_tmux", lambda wid, text: False)
    store = inbox.load()
    store["orchestrator"] = ORCH                    # busy → events queue, readable here
    inbox.save(store)


@pytest.fixture
def client():
    from chela.dashboard.app import app
    return app.test_client()


def _run(**over):
    return {"task_id": "CMX-9", "title": "fix the parser", "status": "running",
            "branch_name": "cmx-9", "window_name": "cmx-9", "window_id": AGENT,
            "window_epoch": "e1", **over}


def _shell_copy(event=None, wid=AGENT):
    """What plugin/'s shell hook leaves in the event log for the same denial."""
    body = {"hook_event_name": "PermissionDenied", **(event or CASES["event"])}
    return event_log.append(notify.DENIED_TYPE, hooks.summarize("PermissionDenied", body),
                            hooks.clip_payload(body), wid=wid, epoch="e1")


def _queued():
    return [e for e in inbox.load()["queue"] if e["kind"] == inbox.DENIED_KIND]


def _post(client, body):
    resp = client.post("/api/inbox/denial", json=body)
    return resp.status_code, resp.get_json()


# --- the direct path -------------------------------------------------------------------

def test_the_mods_post_queues_one_notice_with_the_tool_reason_session_and_window(client):
    """🔴 GUARD: one POST → ONE notice, no event log read, carrying every field the Mod sent."""
    assert _post(client, CASES["body"]) == (200, {"ok": True, "queued": 1})
    [event] = _queued()
    assert event["wid"] == "@41"
    assert event["summary"] == (
        "🔒 CMX-9 · @41 was blocked by its permission classifier: Bash rm -rf build — the "
        "agent is stopped on it: hand the human a bang-prefixed command to run, or redirect "
        "the agent.")
    p = event["payload"]
    assert (p["task_id"], p["tool"], p["detail"]) == ("CMX-9", "Bash", "rm -rf build")
    assert (p["reason"], p["session_id"], p["tool_use_id"]) == (
        "Deletes files outside the task", "s-1", "toolu_01denied")
    assert inbox.load()["denied_cursor"] is None    # the event log was never tailed


def test_the_direct_notice_is_in_the_event_log_as_the_tick_records_it(client):
    _post(client, CASES["body"])
    assert [r["type"] for r in event_log.read(None)["events"]] == ["run_permission_denied"]


# --- both layers: ONE denial, ONE notice -------------------------------------------------

def test_both_layers_loaded_one_denial_is_one_notice(client):
    """🔴 GUARD: the Mod's post, then plugin/'s event-log copy of the SAME denial read by the
    next tick → still exactly one notice."""
    runs = [_run()]
    inbox.tick({}, runs=runs)                       # arms the cursor at the log's tip
    assert _post(client, CASES["body"])[1]["queued"] == 1
    _shell_copy()
    inbox.tick({}, runs=runs)
    assert len(_queued()) == 1
    assert list(inbox.load()["denied_keys"]) == ["toolu_01denied"]


def test_the_key_not_the_cooldown_dedupes_a_copy_that_arrives_after_the_cooldown():
    """🔴 GUARD (by dedupe key): the event-log copy is judged 700 s after the direct post —
    past the 600 s per-run cooldown — and is still not a second notice."""
    store = {**inbox._empty(), "orchestrator": ORCH}
    direct = {"wid": AGENT, "epoch": "e1", "ts": 1000.0, "payload": dict(CASES["body"])}
    assert len(inbox.denied_events([direct], [_run()], store, 1000.0, now_epoch="e1")) == 1
    copy = _shell_copy()
    assert inbox.denied_events([copy], [_run()], store, 1700.0, now_epoch="e1") == []


def test_negative_control_a_different_key_after_the_cooldown_is_a_second_notice():
    """🔴 GUARD (negative control): the same timing with a DIFFERENT tool_use_id files two —
    so the test above is held by the key, not by the cooldown or the timing."""
    store = {**inbox._empty(), "orchestrator": ORCH}
    direct = {"wid": AGENT, "epoch": "e1", "ts": 1000.0, "payload": dict(CASES["body"])}
    assert len(inbox.denied_events([direct], [_run()], store, 1000.0, now_epoch="e1")) == 1
    copy = _shell_copy({**CASES["event"], "tool_use_id": "toolu_02other"})
    assert len(inbox.denied_events([copy], [_run()], store, 1700.0, now_epoch="e1")) == 1


def test_a_copy_without_a_key_falls_back_to_the_cooldown():
    store = {**inbox._empty(), "orchestrator": ORCH}
    copy = {k: v for k, v in CASES["event"].items() if k != "tool_use_id"}
    assert len(inbox.denied_events([_shell_copy(copy)], [_run()], store, 1000.0,
                                   now_epoch="e1")) == 1
    assert inbox.denied_events([_shell_copy(copy)], [_run()], store, 1001.0,
                               now_epoch="e1") == []
    assert store["denied_keys"] == {}


def test_keys_are_remembered_for_an_hour_then_pruned():
    assert inbox.DENIED_KEY_TTL_S == 3600
    store = {**inbox._empty(), "denied_keys": {"old": 1000.0, "new": 1001.0}}
    inbox.denied_events([], [], store, 4600.0)
    assert store["denied_keys"] == {"new": 1001.0}


def test_a_key_is_recorded_even_when_the_cooldown_swallowed_its_notice():
    """A second denial inside the cooldown files nothing, but its copy arriving after the
    cooldown must not file it late."""
    store = {**inbox._empty(), "orchestrator": ORCH}
    first = {"wid": AGENT, "epoch": "e1", "ts": 1000.0,
             "payload": {**CASES["body"], "tool_use_id": "toolu_first"}}
    second = {"wid": AGENT, "epoch": "e1", "ts": 1001.0, "payload": dict(CASES["body"])}
    assert len(inbox.denied_events([first, second], [_run()], store, 1001.0,
                                   now_epoch="e1")) == 1
    assert inbox.denied_events([_shell_copy()], [_run()], store, 1700.0, now_epoch="e1") == []


def test_the_payload_stub_keeps_the_key():
    """A tool input too big for the log is cut to a preview; the dedupe key survives it."""
    huge = {**CASES["event"], "hook_event_name": "PermissionDenied",
            "tool_input": {f"k{i}": "x" * 900 for i in range(60)}}
    stub = hooks.clip_payload(huge)
    assert "tool_input" in stub["clipped_bytes"]
    assert stub["tool_use_id"] == "toolu_01denied"


# --- the same rules as CMX-155 -------------------------------------------------------------

def test_a_direct_denial_from_the_orchestrators_window_is_not_a_notice(client, monkeypatch):
    monkeypatch.setattr(dispatcher, "list_runs", lambda: [_run(window_id=ORCH)])
    assert _post(client, {**CASES["body"], "wid": ORCH}) == (200, {"ok": True, "queued": 0})
    assert _queued() == []


def test_a_direct_denial_from_a_window_with_no_active_run_is_not_a_notice(client):
    assert _post(client, {**CASES["body"], "wid": "@99"}) == (200, {"ok": True, "queued": 0})


def test_with_the_inbox_disabled_nothing_is_filed(client, monkeypatch):
    monkeypatch.setattr(inbox, "INBOX_ENABLED", False)
    assert _post(client, CASES["body"]) == (200, {"ok": True, "queued": 0})
    assert _queued() == []


# --- a body that cannot be filed -----------------------------------------------------------

@pytest.mark.parametrize("body, error", [
    ({k: v for k, v in CASES["body"].items() if k != "tool_use_id"},
     "a denial needs its tool_use_id (the dedupe key)"),
    ({**CASES["body"], "tool_use_id": ""}, "a denial needs its tool_use_id (the dedupe key)"),
    ({**CASES["body"], "tool_use_id": 7}, "a denial needs its tool_use_id (the dedupe key)"),
    ({k: v for k, v in CASES["body"].items() if k != "wid"},
     "a denial needs the window id it came from"),
    ({**CASES["body"], "wid": "@41\n"}, "a denial needs the window id it came from"),
    ({**CASES["body"], "wid": "x@41"}, "a denial needs the window id it came from"),
    ({**CASES["body"], "wid": "@"}, "a denial needs the window id it came from"),
])
def test_a_body_without_a_window_or_a_key_is_refused_and_files_nothing(client, body, error):
    """🔴 GUARD: no key ⇒ 400 — the shell hook's copy still carries the denial, so filing an
    unkeyed one here would be the double notice."""
    assert _post(client, body) == (400, {"ok": False, "error": error})
    assert _queued() == []


def test_a_body_that_is_not_json_typed_is_a_415_and_files_nothing(client):
    """🔴 GUARD: a cross-origin page can POST text/plain with no CORS preflight; this route
    reads application/json only, so such a post cannot forge a notice."""
    resp = client.post("/api/inbox/denial", data=json.dumps(CASES["body"]),
                       content_type="text/plain")
    assert (resp.status_code, resp.get_json()) == (
        415, {"ok": False, "error": "Content-Type must be application/json"})
    assert _queued() == []


def test_a_body_that_is_not_an_object_is_a_400(client):
    resp = client.post("/api/inbox/denial", data="[1]", content_type="application/json")
    assert (resp.status_code, resp.get_json()) == (
        400, {"ok": False, "error": "a JSON object is required"})


def test_a_body_over_the_hook_limit_is_not_read(client):
    resp = client.post("/api/inbox/denial", data="x" * (1024 * 1024 + 1),
                       content_type="application/json")
    assert (resp.status_code, resp.get_json()) == (413, {"ok": False, "error": "body too large"})


def test_a_store_that_cannot_be_written_is_a_503_not_a_crash(client, monkeypatch):
    def boom(body):
        raise RuntimeError("disk full")
    monkeypatch.setattr(inbox, "file_denial", boom)
    assert _post(client, CASES["body"]) == (
        503, {"ok": False, "error": "RuntimeError: disk full"})


def test_the_fixture_is_the_documented_literal():
    assert CASES["body"] == {"wid": "@41", "session_id": "s-1", "tool_name": "Bash",
                             "tool_input": {"command": "rm -rf build"},
                             "tool_use_id": "toolu_01denied",
                             "reason": "Deletes files outside the task"}


def test_the_reason_is_one_line_clipped_to_200_characters(client):
    assert inbox.DENIED_REASON_CHARS == 200
    _post(client, {**CASES["body"], "reason": " a\n\tb  " + "x" * 300})
    reason = _queued()[0]["payload"]["reason"]
    assert reason == "a b " + "x" * 195 + "…" and len(reason) == 200
    assert inbox._one_line("y" * 200, 200) == "y" * 200
