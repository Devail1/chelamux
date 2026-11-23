"""CMX-478 — a dispatched agent's permission prompt (``hook.permission_request``) queues ONE
orchestrator-inbox notice, at once, per prompt.

On 11-22 a dispatched run sat on a permission prompt auto mode handed to a human, and nothing
said so for 20 minutes. tmux, statuses, the peer socket and the runs DB are stubbed; the event
log is per-test, and the end-to-end test drives the real ``/hooks/PermissionRequest`` route."""
from __future__ import annotations

import pytest

from chela import dispatcher, event_log, hooks, inbox

ORCH = "@1"
AGENT = "@41"
HUMAN = "@7"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("CHELA_INBOX_FILE", str(tmp_path / "inbox.json"))
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.delenv("CHELA_ORCHESTRATOR_WID", raising=False)
    monkeypatch.setattr(inbox, "INBOX_ENABLED", True)
    monkeypatch.setattr(dispatcher, "DB_PATH", tmp_path / "scheduler.db")
    monkeypatch.setattr(inbox.epoch, "current", lambda: "e1")
    monkeypatch.setattr(hooks, "_current_epoch", lambda: "e1")
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid", lambda: {ORCH: inbox.BUSY})
    monkeypatch.setattr(inbox.discovery, "get_windows_by_id",
                        lambda: {ORCH: "orchestrator", AGENT: "cmx-9", HUMAN: "notes"})
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


def _run(status="running", **over):
    return {"task_id": "CMX-9", "title": "fix the parser", "status": status,
            "branch_name": "cmx-9", "window_name": "cmx-9", "window_id": AGENT,
            "window_epoch": "e1", **over}


def _body(command="python3 - <<'EOF'\nprint(1)\nEOF", tool="Bash", **over):
    tool_input = {"command": command} if tool == "Bash" else {"questions": [
        {"question": "Which?", "header": "Pick", "multiSelect": False,
         "options": [{"label": "A", "description": "a"}, {"label": "B", "description": "b"}]}]}
    body = {"session_id": "s-1", "hook_event_name": "PermissionRequest", "tool_name": tool,
            "tool_input": tool_input, "permission_mode": "auto"}
    body.update(over)
    return body


def _ask(wid, epoch="e1", **body_over):
    body = _body(**body_over)
    return event_log.append(inbox.PROMPT_TYPE, hooks.summarize("PermissionRequest", body),
                            hooks.clip_payload(body), wid=wid, epoch=epoch)


def _queued(kind=inbox.PROMPT_KIND):
    return [e for e in inbox.load()["queue"] if e["kind"] == kind]


def _events(prompts, runs=None, store=None, now=1000.0, statuses=None):
    store = {**inbox._empty(), "orchestrator": ORCH} if store is None else store
    return inbox.prompt_events(prompts, [_run()] if runs is None else runs, store, now,
                               now_epoch="e1", statuses=statuses), store


# --- VERIFY: the acceptance test — a fake hook, end to end -------------------------------

def test_a_fake_permission_request_hook_for_a_dispatched_run_queues_one_inbox_event(
        monkeypatch):
    """🔴 GUARD (acceptance): POST a ``PermissionRequest`` hook for a dispatched run's window
    through the real route → the next inbox tick queues exactly ONE notice naming the task,
    the window, the tool and the command. The same hook fired again (a re-fire of the same
    prompt) and later ticks add nothing."""
    from chela.dashboard import app as dash
    monkeypatch.setattr(hooks, "wid_for_session", lambda *a, **k: AGENT)
    client = dash.app.test_client()
    runs = [_run()]
    inbox.tick({}, runs=runs)                       # arms the cursor at the log's tip

    resp = client.post("/hooks/PermissionRequest", json=_body(command="python3 x.py"))
    assert resp.status_code == 200 and resp.get_json() == {}
    inbox.tick({}, runs=runs)

    [event] = _queued()
    assert event["summary"] == (
        "✋ CMX-9 · @41 is waiting on a permission prompt: Bash python3 x.py — answer it in "
        "its pane. The agent is blocked until a human does.")
    assert event["wid"] == AGENT
    assert event["payload"]["task_id"] == "CMX-9"
    assert event["payload"]["tool"] == "Bash"
    assert event["payload"]["detail"] == "python3 x.py"

    client.post("/hooks/PermissionRequest", json=_body(command="python3 x.py"))
    inbox.tick({}, runs=runs)
    inbox.tick({}, runs=runs)
    assert len(_queued()) == 1


def test_a_different_prompt_is_a_second_notice():
    """Dedupe is per PROMPT, not per run: a second, different request is told too."""
    runs = [_run()]
    inbox.tick({}, runs=runs)
    _ask(AGENT, command="one")
    _ask(AGENT, command="two")
    inbox.tick({}, runs=runs)
    assert [e["payload"]["detail"] for e in _queued()] == ["one", "two"]


def test_the_first_tick_only_anchors_and_never_replays_old_prompts():
    _ask(AGENT)
    inbox.tick({}, runs=[_run()])
    assert _queued() == []


def test_a_prompt_does_not_become_a_denial_notice_and_a_denial_still_does():
    """Both kinds share one cursor; each record reaches only its own scan."""
    from chela import notify
    runs = [_run()]
    inbox.tick({}, runs=runs)
    _ask(AGENT, command="asked")
    body = {"session_id": "s", "tool_name": "Bash", "tool_input": {"command": "refused"}}
    event_log.append(notify.DENIED_TYPE, "", hooks.clip_payload(body), wid=AGENT, epoch="e1")
    inbox.tick({}, runs=runs)
    assert [e["payload"]["detail"] for e in _queued()] == ["asked"]
    assert [e["payload"]["detail"] for e in _queued(inbox.DENIED_KIND)] == ["refused"]


# --- who it is about --------------------------------------------------------------------

def test_the_orchestrators_own_prompt_is_not_a_notice():
    out, _ = _events([_ask(ORCH)], runs=[_run(window_id=ORCH)])
    assert out == []


def test_a_prompt_from_a_window_with_no_active_run_is_not_a_notice():
    out, _ = _events([_ask(HUMAN)])
    assert out == []
    out, _ = _events([{**_ask(AGENT), "wid": None}])
    assert out == []


@pytest.mark.parametrize("status", ["awaiting_review", "failed", "done", "needs_human"])
def test_a_settled_runs_prompt_is_not_a_notice(status):
    out, _ = _events([_ask(AGENT)], runs=[_run(status)])
    assert out == []


def test_a_claimed_run_counts_as_active():
    out, _ = _events([_ask(AGENT)], runs=[_run("claimed")])
    assert len(out) == 1


def test_a_prompt_stamped_by_a_dead_tmux_server_is_not_attributed():
    out, _ = _events([_ask(AGENT, epoch="e0")])
    assert out == []


def test_an_ask_user_question_is_not_a_permission_prompt_notice():
    """A question has its own Telegram path; only a permission prompt is told here."""
    out, _ = _events([_ask(AGENT, tool="AskUserQuestion")])
    assert out == []
    out, _ = _events([_ask(AGENT, tool="Bash")])
    assert [e["payload"]["tool"] for e in out] == ["Bash"]


# --- the dedupe key -----------------------------------------------------------------------

def test_a_repeat_inside_the_ttl_is_not_a_second_notice_but_after_it_is():
    record = _ask(AGENT)
    out, store = _events([record], now=1000.0)
    assert len(out) == 1
    out, store = _events([record], store=store, now=1000.0 + 599)
    assert out == []
    out, _ = _events([record], store=store, now=1000.0 + 600)
    assert len(out) == 1


def test_prompt_key_prefers_the_tool_use_id():
    assert inbox.prompt_key(_ask(AGENT, tool_use_id="toolu_9")) == "toolu_9"
    a = inbox.prompt_key(_ask(AGENT, command="x"))
    assert a.startswith("req:") and a == inbox.prompt_key(_ask(AGENT, command="x"))
    assert a != inbox.prompt_key(_ask(AGENT, command="y"))
    assert a != inbox.prompt_key(_ask(AGENT, command="x", session_id="s-2"))
    assert a != inbox.prompt_key(_ask(HUMAN, command="x"))
    assert a != inbox.prompt_key(_ask(AGENT, command="x", tool="AskUserQuestion"))


def test_a_queued_prompt_notice_is_stale_once_the_run_settled():
    [event], _ = _events([_ask(AGENT)])
    assert inbox.stale_reason(event, [_run("running")]) is None
    assert inbox.stale_reason(event, [_run("awaiting_review")]) == (
        "run is now 'awaiting_review' — the agent is no longer waiting on the prompt")


@pytest.mark.parametrize(("status", "told"), [
    (inbox.BUSY, False), (inbox.IDLE, False), (inbox.WAITING, True), (None, True)])
def test_a_prompt_already_answered_by_the_tick_is_not_a_notice(status, told):
    """A human who approved in seconds leaves the agent busy (or idle): no false alarm. An
    unreadable status is not evidence of an answer, so it is still told."""
    statuses = {} if status is None else {AGENT: status}
    out, store = _events([_ask(AGENT)], statuses=statuses)
    assert len(out) == (1 if told else 0)
    assert bool(store["prompt_keys"]) is told   # an untold prompt is not marked as told


def test_tick_hands_the_live_statuses_to_the_prompt_scan(monkeypatch):
    """Through tick(): an agent already busy again by the tick is not reported."""
    monkeypatch.setattr(inbox.agent_manager, "status_by_wid",
                        lambda: {ORCH: inbox.BUSY, AGENT: inbox.BUSY})
    runs = [_run()]
    inbox.tick({}, runs=runs)
    _ask(AGENT)
    inbox.tick({}, runs=runs)
    assert _queued() == []
