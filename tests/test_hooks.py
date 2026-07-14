"""Claude Code hooks → the event log. Ingestion only, and observe-only.

What these lock in, in the order an agent's safety depends on it:

  * the endpoint NEVER answers a gate — no ``permissionDecision``, no
    ``hookSpecificOutput``, ever. A field in that response silently starts deciding the
    user's permission prompts for them, so it is asserted on, not assumed;
  * the endpoint never fails its caller — a malformed body, a garbage payload and an
    oversized POST all return 200. An agent is *blocked* on this request;
  * an AskUserQuestion's per-option ``label`` and ``description`` survive into the log —
    the whole reason hooks beat the transcript is that they carry the full ``tool_input``
    *before* the answer, and clipping a huge payload must not take them out;
  * correlation to a window is off the session's ORIGIN directory — never off ``cwd``,
    which moves the moment an agent ``cd``s (CMX-48: that filed the orchestrator's every
    event against another agent's window) — and AMBIGUITY RESOLVES TO NONE: a wrongly
    filed event is worse than an unfiled one;
  * the committed plugin manifest still matches the spec the code generates.

Fully programmatic: no tmux, no Claude Code, no daemon.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from chela import event_log, hooks, transcripts
from chela.dashboard import app as dash

REPO = Path(__file__).resolve().parent.parent

# Captured before the autouse fixture below stubs it out, for the one test that wants
# the real tmux path (with subprocess itself faked).
_REAL_PANES = hooks._panes


@pytest.fixture(autouse=True)
def log_file(tmp_path, monkeypatch):
    """Never the real ``~/.chela/events.jsonl`` (the read cache is path-keyed)."""
    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))


@pytest.fixture(autouse=True)
def no_tmux(monkeypatch):
    """No tmux in a unit test: correlation resolves to nothing unless a test says so."""
    monkeypatch.setattr(hooks, "_panes", lambda force=False: {})


@pytest.fixture
def client():
    return dash.app.test_client()


def _body(**over) -> dict:
    body = {
        "session_id": "e2b61683-9f3a-4c30-bff0-f4fc487a4e77",
        "transcript_path": "/home/u/.claude/projects/-repo/e2b61683.jsonl",
        "cwd": "/repo",
        "prompt_id": "dce929f3",
        "permission_mode": "auto",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi", "description": "Echo"},
        "tool_use_id": "toolu_01G8",
    }
    body.update(over)
    return body


# The payload Claude Code 2.1.207 actually delivers for a blocked question: the options,
# with their descriptions, are ALL there — before the human answers. The transcript has
# nothing at this moment, which is why the gates are pane-scraped today.
ASKUQ = _body(
    hook_event_name="PermissionRequest",
    tool_name="AskUserQuestion",
    tool_input={"questions": [{
        "question": "Which storage backend?",
        "header": "Storage",
        "multiSelect": False,
        "options": [
            {"label": "SQLite", "description": "One file, no server, transactional."},
            {"label": "Postgres", "description": "A server to run, but concurrent writers."},
        ],
    }]},
)


# --- the record ------------------------------------------------------------------

def test_event_type_is_namespaced():
    assert hooks.event_type("PreToolUse") == "hook.pre_tool_use"
    assert hooks.event_type("PermissionRequest") == "hook.permission_request"
    assert hooks.event_type("Stop") == "hook.stop"


def test_ingest_appends_a_well_formed_record():
    record = hooks.ingest("PreToolUse", _body())

    assert record["type"] == "hook.pre_tool_use"
    assert record["seq"] == 1
    assert record["boot_id"]
    assert record["session_id"] == "e2b61683-9f3a-4c30-bff0-f4fc487a4e77"
    assert record["summary"] == "Bash: echo hi"          # one line, not the payload
    assert record["payload"]["tool_input"]["command"] == "echo hi"
    assert record["payload"]["permission_mode"] == "auto"

    # And it is durable, not just returned.
    assert event_log.read()["events"][0]["seq"] == 1


def test_event_comes_from_the_url_not_the_body():
    """The URL is what the plugin we ship controls; a body could claim anything."""
    record = hooks.ingest("PostToolUse", _body(hook_event_name="SessionEnd"))
    assert record["type"] == "hook.post_tool_use"


def test_askuserquestion_options_reach_the_log_in_full():
    """The point of the whole subsystem: the options, with descriptions, BEFORE the answer."""
    record = hooks.ingest("PermissionRequest", ASKUQ)

    assert record["type"] == "hook.permission_request"
    assert record["summary"] == "permission asked — AskUserQuestion: Which storage backend?"
    options = record["payload"]["tool_input"]["questions"][0]["options"]
    assert [o["label"] for o in options] == ["SQLite", "Postgres"]
    assert options[0]["description"] == "One file, no server, transactional."
    assert options[1]["description"] == "A server to run, but concurrent writers."


@pytest.mark.parametrize("event,body,expected", [
    ("PostToolUse", _body(), "Bash done: echo hi"),
    ("PermissionDenied", _body(), "permission denied — Bash: echo hi"),
    ("PreToolUse", _body(tool_name="Read", tool_input={"file_path": "/a/b.py"}), "Read: /a/b.py"),
    ("UserPromptSubmit", _body(prompt="fix the  parser"), "prompt: fix the parser"),
    ("SessionStart", _body(source="startup"), "session start (startup)"),
    ("SessionEnd", _body(reason="clear"), "session end (clear)"),
    ("Stop", _body(last_assistant_message="done"), "stopped: done"),
    ("Notification", _body(message="waiting on you"), "notification: waiting on you"),
    ("PreCompact", _body(), "compacting"),
])
def test_summaries_are_one_line(event, body, expected):
    assert hooks.summarize(event, body) == expected


def test_a_long_summary_is_truncated_not_wrapped():
    summary = hooks.summarize("PreToolUse", _body(tool_input={"command": "x" * 500}))
    assert len(summary) <= hooks.MAX_SUMMARY
    assert summary.endswith("…")


# --- bounds ----------------------------------------------------------------------

def test_a_huge_tool_input_is_clipped_not_dropped():
    """A `Write` of a big file carries the file. Keep the event, bound the line."""
    record = hooks.ingest("PreToolUse", _body(
        tool_name="Write",
        tool_input={"file_path": "/a/big.py", "content": "y" * 50_000},
    ))
    content = record["payload"]["tool_input"]["content"]
    assert len(content) < 50_000
    assert content.endswith("chars]")
    assert record["payload"]["tool_input"]["file_path"] == "/a/big.py"   # the useful part
    assert record["summary"] == "Write: /a/big.py"


def test_a_payload_past_the_bound_degrades_to_a_stub():
    body = _body(tool_input={f"k{i}": "z" * 1500 for i in range(60)})
    record = hooks.ingest("PreToolUse", body)
    assert record["payload"]["clipped"] is True
    assert record["payload"]["tool_name"] == "Bash"
    assert "tool_input" not in record["payload"]


def test_clipping_leaves_askuserquestion_intact():
    """The bound must never be what eats an option's description."""
    payload = hooks.clip_payload(ASKUQ)
    options = payload["tool_input"]["questions"][0]["options"]
    assert options[1]["description"] == "A server to run, but concurrent writers."


# --- a malformed payload is dropped, never raised --------------------------------

@pytest.mark.parametrize("body", [None, [], "a string", 7])
def test_a_non_object_body_is_dropped_without_raising(body):
    assert hooks.ingest("PreToolUse", body) is None
    assert event_log.read()["events"] == []


def test_an_unknown_event_is_dropped():
    assert hooks.ingest("NotAHookEvent", _body()) is None
    assert event_log.read()["events"] == []


def test_ingest_swallows_an_append_failure(monkeypatch):
    """Its caller is an agent blocked on this request. It does not get an exception."""
    def boom(*a, **kw):
        raise RuntimeError("disk is on fire")
    monkeypatch.setattr(hooks.event_log, "append", boom)
    assert hooks.ingest("PreToolUse", _body()) is None


# --- correlation: session -> window, with no pane read ----------------------------
#
# CMX-48. Correlation used to match the payload's `cwd` against `#{pane_current_path}`,
# and `cwd` is not an identity: the orchestrator (window @0, launched in ~) `cd`-ed into
# the chelamux repo, and every one of its events was then filed against @1 — the window
# of a DIFFERENT agent, which genuinely lives there. The key is now the session's ORIGIN
# directory, encoded as the project slug Claude Code writes its transcript under, which
# is fixed at session start and survives any `cd`.

def _panes(mapping):
    """``{origin cwd: [(wid, command), …]}`` — keyed as the pane table really is."""
    return lambda force=False: {
        transcripts.encode_cwd(cwd): panes for cwd, panes in mapping.items()
    }


def _slugless():
    """Clear the process-lifetime session→slug cache between tests."""
    hooks._slug_cache.clear()


@pytest.fixture(autouse=True)
def clean_slug_cache():
    _slugless()
    yield
    _slugless()


def test_correlates_a_session_to_its_window(monkeypatch):
    monkeypatch.setattr(hooks, "_panes", _panes({"/repo": [("@3", "claude")]}))
    assert hooks.wid_for_session("s1", "/p/projects/-repo/s1.jsonl") == "@3"


def test_a_cd_ed_agent_is_filed_against_its_OWN_window(monkeypatch):
    """THE bug. @0 was launched in ~ and `cd`-ed into the repo where @1 lives.

    Its payload `cwd` is now @1's directory — and it must STILL resolve to @0. This is
    the exact event that was filed against the wrong agent.
    """
    monkeypatch.setattr(hooks, "_panes", _panes({
        "/home/u": [("@0", "claude")],          # the orchestrator, launched in ~
        "/home/u/repo": [("@1", "claude")],     # a different agent, living in the repo
    }))
    record = hooks.ingest("PreToolUse", _body(
        session_id="cf19ca61-ffbb-4dbf-a8c7-66b74294fa69",
        transcript_path="/p/projects/-home-u/cf19ca61-ffbb-4dbf-a8c7-66b74294fa69.jsonl",
        cwd="/home/u/repo",                     # the mutable thing — a red herring now
    ))
    assert record["wid"] == "@0"


def test_a_worktree_agent_still_correlates(monkeypatch):
    """The dispatcher's agents are launched IN their worktree — don't break them."""
    wt = "/home/u/.chela/worktrees/proj/958d67d435b2"
    monkeypatch.setattr(hooks, "_panes", _panes({wt: [("@43", "claude")]}))
    slug = transcripts.encode_cwd(wt)
    assert hooks.wid_for_session("s9", f"/p/projects/{slug}/s9.jsonl") == "@43"


def test_two_agents_in_one_origin_correlate_to_nothing(monkeypatch):
    """A wrongly filed event is worse than an unfiled one — never guess."""
    monkeypatch.setattr(
        hooks, "_panes", _panes({"/repo": [("@3", "claude"), ("@7", "claude")]}))
    assert hooks.wid_for_session("s1", "/p/projects/-repo/s1.jsonl") is None


def test_a_shell_pane_does_not_shadow_the_agent(monkeypatch):
    monkeypatch.setattr(
        hooks, "_panes", _panes({"/repo": [("@2", "bash"), ("@3", "claude")]}))
    assert hooks.wid_for_session("s1", "/p/projects/-repo/s1.jsonl") == "@3"


def test_an_unknown_session_correlates_to_nothing_not_to_a_cwd_guess(monkeypatch):
    """An unresolvable session is None. It is NEVER the window whose cwd happens to
    match — that fallback is the bug, and it does not exist any more."""
    monkeypatch.setattr(hooks, "_panes", _panes({"/repo": [("@3", "claude")]}))
    monkeypatch.setattr(hooks, "_slug_from_disk", lambda session_id: None)
    # cwd IS /repo, and /repo IS @3 — and the answer is still None.
    assert hooks.wid_for_session("unknown", None) is None
    assert hooks.ingest("PreToolUse", _body(
        session_id="unknown", transcript_path=None, cwd="/repo"))["wid"] is None
    assert hooks.wid_for_session(None, None) is None


def test_a_session_with_no_transcript_path_is_found_on_disk(monkeypatch, tmp_path):
    """The fallback for a payload that carries no `transcript_path`."""
    session = "e2b61683-9f3a-4c30-bff0-f4fc487a4e77"
    projects = tmp_path / "projects"
    (projects / "-repo").mkdir(parents=True)
    (projects / "-repo" / f"{session}.jsonl").write_text("{}\n")
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", projects)
    monkeypatch.setattr(hooks, "_panes", _panes({"/repo": [("@3", "claude")]}))
    assert hooks.wid_for_session(session, None) == "@3"


def test_a_session_id_cannot_walk_out_of_the_projects_dir(monkeypatch, tmp_path):
    """It is pasted into a glob. It is validated, not trusted."""
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", tmp_path)
    assert hooks.session_slug("../../etc/passwd", None) is None


def test_the_event_still_lands_when_the_window_is_unknown():
    record = hooks.ingest("PreToolUse", _body())
    assert record["wid"] is None
    assert record["payload"]["cwd"] == "/repo"      # nothing is lost but the shortcut
    assert record["session_id"] == _body()["session_id"]


def test_correlation_reads_tmux_once_not_the_pane(monkeypatch):
    """One `tmux list-windows`, no pgrep, no capture-pane, no /proc walk — an agent is
    BLOCKED on this, at PreToolUse volume."""
    calls = []

    class Result:
        returncode = 0
        stdout = "@3\tclaude\t/repo\n@4\tbash\t/other\n"

    def fake_run(argv, **kw):
        calls.append(argv)
        return Result()

    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")      # as PM2 pins it
    monkeypatch.setattr(hooks, "_panes", _REAL_PANES)      # undo the autouse stub
    monkeypatch.setattr(hooks.subprocess, "run", fake_run)
    monkeypatch.setattr(hooks, "_panes_cache", {"ts": 0.0, "by_slug": {}})

    assert hooks.wid_for_session("s1", "/p/projects/-repo/s1.jsonl") == "@3"
    assert len(calls) == 1
    assert calls[0][:2] == ["tmux", "list-windows"]

    # A second event inside the TTL costs nothing at all.
    assert hooks.wid_for_session("s1", "/p/projects/-repo/s1.jsonl") == "@3"
    assert len(calls) == 1
    flat = [arg for call in calls for arg in call]
    assert not any("capture-pane" in a or "pgrep" in a for a in flat)


def test_the_slug_is_resolved_once_and_cached(monkeypatch):
    """The session→slug half must not hit the disk per event either."""
    globs = []

    def counting_glob(session_id):
        globs.append(session_id)
        return "-repo"

    monkeypatch.setattr(hooks, "_slug_from_disk", counting_glob)
    monkeypatch.setattr(hooks, "_panes", _panes({"/repo": [("@3", "claude")]}))
    for _ in range(20):
        assert hooks.wid_for_session("s1", None) == "@3"      # no transcript_path
    assert len(globs) == 1


# --- the endpoint ----------------------------------------------------------------

def test_endpoint_appends_and_answers_nothing(client):
    resp = client.post("/hooks/PermissionRequest", json=ASKUQ)

    assert resp.status_code == 200
    # OBSERVE-ONLY. A decision in this body silently answers the user's prompts for them.
    body = resp.get_json()
    assert body == {}
    assert "hookSpecificOutput" not in body
    assert "permissionDecision" not in body
    assert "decision" not in body

    events = event_log.read()["events"]
    assert len(events) == 1
    assert events[0]["type"] == "hook.permission_request"
    labels = [o["label"] for o in
              events[0]["payload"]["tool_input"]["questions"][0]["options"]]
    assert labels == ["SQLite", "Postgres"]


def test_endpoint_does_not_fail_a_blocked_agent(client):
    """Unparseable, empty, non-object: 200 every time. A 500 here is a stalled tool call."""
    for data in (b"{not json", b"", b"[1,2,3]"):
        resp = client.post("/hooks/PreToolUse", data=data,
                           content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json() == {}
    assert event_log.read()["events"] == []


def test_endpoint_rejects_an_unknown_event(client):
    assert client.post("/hooks/DropTables", json=_body()).status_code == 404
    assert event_log.read()["events"] == []


def test_endpoint_will_not_read_an_oversized_body(client):
    resp = client.post("/hooks/PreToolUse", data=b"x" * (hooks.MAX_BODY + 1),
                       content_type="application/json")
    assert resp.status_code == 200
    assert event_log.read()["events"] == []


# --- the plugin ------------------------------------------------------------------

def test_hooks_spec_registers_every_event_over_http():
    spec = hooks.hooks_spec(port=5001)["hooks"]
    assert set(spec) == set(hooks.HOOK_EVENTS)
    for event, entries in spec.items():
        hook, = entries[0]["hooks"]
        # http, not command: no shell, no process spawn per tool call, and no chatty
        # .bashrc able to corrupt the JSON contract with stray stdout.
        assert hook["type"] == "http"
        assert hook["url"] == f"http://127.0.0.1:5001/hooks/{event}"
        assert hook["timeout"] == hooks.HOOK_TIMEOUT <= 2   # the agent BLOCKS on this
        assert ("matcher" in entries[0]) == (event in hooks.TOOL_EVENTS)


def test_the_committed_plugin_still_matches_the_code():
    """A manifest is static JSON — nothing but this test stops it rotting."""
    manifest = json.loads((REPO / "plugin" / "hooks" / "hooks.json").read_text())
    assert manifest == hooks.hooks_spec(port=5001)

    plugin = json.loads((REPO / "plugin" / ".claude-plugin" / "plugin.json").read_text())
    assert plugin == hooks.plugin_manifest()

    market = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    assert market == hooks.marketplace_manifest("./plugin")
    assert market["plugins"][0]["name"] == "chela"


def test_render_plugin_bakes_in_a_nondefault_port(tmp_path):
    """The port is a literal in the manifest: Claude Code does not expand env vars in it."""
    directory = hooks.render_plugin(tmp_path / "p", port=5099)
    spec = json.loads((directory / "hooks" / "hooks.json").read_text())
    url = spec["hooks"]["PreToolUse"][0]["hooks"][0]["url"]
    assert url == "http://127.0.0.1:5099/hooks/PreToolUse"

    # It is self-contained: a plugin AND a one-plugin marketplace, so it installs
    # either with --plugin-dir or with /plugin marketplace add.
    assert (directory / ".claude-plugin" / "plugin.json").exists()
    market = json.loads((directory / ".claude-plugin" / "marketplace.json").read_text())
    assert market["plugins"][0]["source"] == "./"
