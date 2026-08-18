"""Claude Code hooks → the event log. Ingestion only; one event may answer.

What these lock in, in the order an agent's safety depends on it:

  * the endpoint answers **nothing but a question a human actually tapped** (CMX-50). It
    used to answer nothing at all, and that was asserted here — a ``hookSpecificOutput``
    in this response silently decides the user's prompts for them, so it may never appear
    by accident. It now appears for exactly one event (``PermissionRequest`` on an
    ``AskUserQuestion``) and only when :mod:`chela.gateanswer` has a human's answer in
    hand; every other event, and every un-answered question, still returns ``{}``. The
    contract that changed is stated where it lives — see :mod:`tests.test_gateanswer`;
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

import importlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from chela import config, event_log, hooks, messenger, rooms, sessions, transcripts
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


@pytest.fixture(autouse=True)
def no_epoch(monkeypatch):
    """No tmux epoch in a unit test by default: stamped as None unless a test says so.

    The cache in `hooks._current_epoch` lives for the process's lifetime, not the test's
    — reset it too, or a value a prior test set could still be within its TTL here.
    """
    hooks._epoch_cache["ts"] = 0.0
    hooks._epoch_cache["value"] = None
    monkeypatch.setattr(hooks.epoch, "current", lambda: None)


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
    """``{origin cwd: [(wid, command), …]}`` → the ``{wid: Pane}`` map correlation reads.

    ``launched_in`` is the claude PROCESS's cwd, which is the origin directory and does
    not move; the pane path is deliberately set to something else in the ``cd`` test.
    """
    panes = {
        wid: sessions.Pane(wid=wid, path=cwd, command=command, claude_pid=1,
                           launched_in=cwd)
        for cwd, entries in mapping.items()
        for wid, command in entries
    }
    return lambda force=False: panes


def _resumed(wid: str, session_id: str, cwd: str = "/elsewhere") -> sessions.Pane:
    """A pane whose claude was launched with ``--resume`` — the CMX-70 case."""
    return sessions.Pane(wid=wid, path=cwd, command="claude", claude_pid=1,
                         launched_in=cwd, resumed=session_id)


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


def test_a_resumed_session_is_filed_against_the_window_RUNNING_it(monkeypatch):
    """CMX-70. A session resumed from another directory keeps its transcript in the project
    dir it was BORN in — so its slug names a directory no pane is sitting in, and origin
    matching resolves it to None (or, worse, to the unrelated agent that genuinely lives
    there). The pane's own command line settles it: `claude --resume <sid>` IS that window
    claiming that session."""
    session = "7f3a91c2-4b8e-4d15-9c62-1e0d5a8b3f47"
    origin = "/home/u/projects/analytics"                    # where the session was born
    slug = transcripts.encode_cwd(origin)
    panes = {
        # @2 was rebuilt in a DIFFERENT directory and resumed the session:
        "@2": _resumed("@2", session, cwd="/home/u/projects/analytics/data_prep"),
        # …and an unrelated agent genuinely lives in the birth directory:
        "@5": sessions.Pane(wid="@5", path=origin, command="claude", claude_pid=2,
                            launched_in=origin),
    }
    monkeypatch.setattr(hooks, "_panes", lambda force=False: panes)

    assert hooks.wid_for_session(session, f"/p/projects/{slug}/{session}.jsonl") == "@2"
    # …and @5's own events still go to @5.
    assert hooks.wid_for_session("s9", f"/p/projects/{slug}/s9.jsonl") == "@5"


def test_correlation_reads_tmux_once_not_the_pane(monkeypatch, tmp_path):
    """One `tmux list-windows` (+ a couple of small /proc reads), no pgrep, no capture-pane,
    no `claude agents --json` — an agent is BLOCKED on this, at PreToolUse volume."""
    calls = []

    class Result:
        returncode = 0
        stdout = "@3\tclaude\t/repo\t100\n@4\tbash\t/other\t200\n"

    def fake_run(argv, **kw):
        calls.append(argv)
        return Result()

    monkeypatch.setenv("CHELA_TMUX_SESSION", "chela")      # as PM2 pins it
    monkeypatch.setattr(hooks, "_panes", _REAL_PANES)      # undo the autouse stub
    monkeypatch.setattr(sessions, "PROC", tmp_path)        # no claude process to find
    # This budget is the /proc host's: one tmux call and NOTHING else. A host without /proc
    # pays for the same facts with `pgrep`/`ps` instead (chela.sessions._sh), so pin the
    # platform rather than let the assertion below depend on where the suite happens to run.
    monkeypatch.setattr(sessions, "_PROC_HOST", True)
    monkeypatch.setattr(sessions.subprocess, "run", fake_run)
    monkeypatch.setattr(sessions, "_panes_cache", {"ts": 0.0, "panes": {}})

    assert hooks.wid_for_session("s1", "/p/projects/-repo/s1.jsonl") == "@3"
    assert len(calls) == 1
    assert calls[0][:2] == ["tmux", "list-windows"]

    # A second event inside the TTL costs nothing at all.
    assert hooks.wid_for_session("s1", "/p/projects/-repo/s1.jsonl") == "@3"
    assert len(calls) == 1
    flat = [arg for call in calls for arg in call]
    assert not any("capture-pane" in a or "pgrep" in a or "agents" in a for a in flat)


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


# --- $CHELA_WID: the agent SAYING which window it is (CMX-160) -----------------------
#
# Every hook but SessionStart rides `http` — Claude Code's own client, carrying only the
# payload, none of the agent's env. SessionStart is a `command` hook and so inherits the
# process env, letting the agent short-circuit inference entirely via an `X-Chela-Wid`
# header. Still not trusted blind: malformed, empty, or naming a window that is not live
# right now must fall through to the SAME inference as if no header had ever been sent.

def test_explicit_wid_short_circuits_correlation_that_would_otherwise_fail(monkeypatch):
    """The header is checked FIRST — it wins even where the origin-based inference below
    it has nothing at all to go on (no panes stubbed)."""
    monkeypatch.setattr(hooks, "_panes", _panes({"/repo": [("@3", "claude")]}))
    assert hooks.wid_for_session("s1", None, explicit_wid="@3") == "@3"


def test_explicit_wid_naming_a_dead_window_falls_through_to_inference(monkeypatch):
    """A stale/wrong header must never be WORSE than no header — it falls through to the
    same origin-based resolution any other hook uses, not to a hard failure."""
    monkeypatch.setattr(hooks, "_panes", _panes({"/repo": [("@3", "claude")]}))
    assert hooks.wid_for_session(
        "s1", "/p/projects/-repo/s1.jsonl", explicit_wid="@999") == "@3"


@pytest.mark.parametrize("hint", ["", None, "not-a-wid", "@", "@abc", "@3;rm -rf /", "3"])
def test_a_malformed_or_empty_header_is_treated_as_no_header(monkeypatch, hint):
    """Empty is the ordinary case (a session chela did not launch has no ``$CHELA_WID``),
    and the rest is attacker-adjacent input off an HTTP header — shape-checked before it is
    ever trusted, the same discipline a session id gets before it is pasted into a glob."""
    monkeypatch.setattr(hooks, "_panes", _panes({"/repo": [("@3", "claude")]}))
    assert hooks._explicit_wid(hint) is None
    assert hooks.wid_for_session(
        "s1", "/p/projects/-repo/s1.jsonl", explicit_wid=hint) == "@3"


def test_ingest_carries_the_explicit_wid_through_to_the_record(monkeypatch):
    monkeypatch.setattr(hooks, "_panes", lambda force=False: {})   # inference: nothing
    monkeypatch.setattr(hooks, "_slug_from_disk", lambda session_id: None)
    record = hooks.ingest("PreToolUse", _body(), explicit_wid="@3")
    assert record["wid"] is None    # @3 isn't a live pane — the header names a dead window


def test_ingest_files_the_event_under_the_explicit_wid_when_it_is_live(monkeypatch):
    monkeypatch.setattr(hooks, "_panes", _panes({"/repo": [("@3", "claude")]}))
    record = hooks.ingest("PreToolUse", _body(cwd="/elsewhere"), explicit_wid="@3")
    assert record["wid"] == "@3"


# --- rejected_wid: the dead-window signal `_explicit_wid` swallows silently (CMX-192) --
#
# `_explicit_wid` falls through to None for THREE different shapes — unset, malformed, and
# well-formed-but-dead — and that collapse is correct for resolution (a bad header must
# never behave worse than no header). But it means nothing downstream can tell "this
# session simply has no $CHELA_WID" apart from "this session's $CHELA_WID names a window
# that used to exist and doesn't anymore", which is always a fault. `_explicit_wid_dead`
# and the `rejected_wid` it feeds into `ingest` are the one place that distinction survives.

def test_explicit_wid_dead_is_none_when_the_header_is_simply_unset():
    assert hooks._explicit_wid_dead(None, panes={}) is None
    assert hooks._explicit_wid_dead("", panes={}) is None


@pytest.mark.parametrize("hint", ["not-a-wid", "@", "@abc", "@3;rm -rf /", "3"])
def test_explicit_wid_dead_is_none_for_a_malformed_header(hint):
    """Malformed input must never be reported as a dead-window fault either — it is
    attacker-adjacent HTTP input, not a diagnosable window id."""
    assert hooks._explicit_wid_dead(hint, panes={}) is None


def test_explicit_wid_dead_is_none_when_the_header_names_a_live_window():
    panes = {"@3": object()}
    assert hooks._explicit_wid_dead("@3", panes=panes) is None


def test_explicit_wid_dead_names_the_window_when_it_is_not_live():
    """The one case that must survive: well-formed, present, and not live right now."""
    assert hooks._explicit_wid_dead("@999", panes={}) == "@999"


# --- the forced-refresh retry (CMX-231) -----------------------------------------------
#
# `sessions.panes` is TTL-cached (≤1s), so a real, live window can in principle briefly
# look dead to a single un-forced read — if a session restarts its OWN claude process
# INSIDE an already-open window (auto-compact, `/clear` — no window ever closes) and
# `SessionStart` fires before the cache refreshes. `wid_for_session`'s inference fallback
# already handles that shape with one forced re-read on a miss; `_explicit_wid`/
# `_explicit_wid_dead` do the same — but ONLY when `panes=None` (the real caller). A
# caller that passes a fixed snapshot explicitly (every test above) is asking for that
# snapshot's answer, not a retry. The tests above never need `hooks._panes` to answer
# anything in particular — the autouse `no_tmux` fixture's `{}` is enough — because a
# working short-circuit never calls it again; see the test below for the one that actually
# proves the short-circuit fires, by making a hidden retry answer differently.

def test_explicit_wid_retries_a_forced_refresh_before_calling_a_live_window_dead(monkeypatch):
    """Missing from the cached read, present after a forced one: this is the live window,
    not a dead one — the header must win, exactly as if the cache had been fresh."""
    calls = []

    def fake_panes(force=False):
        calls.append(force)
        return {"@299": object()} if force else {}

    monkeypatch.setattr(hooks, "_panes", fake_panes)
    assert hooks._explicit_wid("@299") == "@299"
    assert calls == [False, True]      # cached read first, forced retry only on a miss


def test_explicit_wid_dead_retries_a_forced_refresh_before_reporting_a_fault(monkeypatch):
    """The complementary function must reach the SAME verdict: found on the forced retry
    means this was never a fault, so `rejected_wid` must stay unset."""
    monkeypatch.setattr(hooks, "_panes",
                        lambda force=False: {"@299": object()} if force else {})
    assert hooks._explicit_wid_dead("@299") is None


def test_explicit_wid_dead_still_reports_a_fault_when_the_forced_retry_also_misses(monkeypatch):
    """The retry is one extra look, not infinite trust — a window still missing after a
    fresh tmux read is genuinely dead, and this must still warn."""
    monkeypatch.setattr(hooks, "_panes", lambda force=False: {})
    assert hooks._explicit_wid("@999") is None
    assert hooks._explicit_wid_dead("@999") == "@999"


def test_explicit_wid_never_retries_when_the_caller_supplies_a_fixed_snapshot(monkeypatch):
    """A caller that passes `panes=` explicitly gets exactly that answer — no hidden
    second tmux call behind its back. The autouse `no_tmux` fixture already stubs
    `hooks._panes` to `{}` for both `force=True` and `force=False`, which makes a hidden
    retry indistinguishable from the short-circuit (both land on the same empty answer) —
    so here `_panes` is re-mocked to answer differently by `force`: a forced call reports
    `@299` live. If the short-circuit were ever removed, both functions below would fall
    through to that forced call and flip their verdict; asserting the short-circuited
    answer here catches that."""
    monkeypatch.setattr(hooks, "_panes",
                        lambda force=False: {"@299": object()} if force else {})
    assert hooks._explicit_wid("@299", panes={}) is None
    assert hooks._explicit_wid_dead("@299", panes={}) == "@299"


def test_ingest_never_reports_rejected_wid_when_the_header_is_simply_unset(monkeypatch):
    monkeypatch.setattr(hooks, "_slug_from_disk", lambda session_id: None)
    record = hooks.ingest("SessionStart", _body(), explicit_wid=None)
    assert record["wid"] is None
    assert record["rejected_wid"] is None


def test_ingest_never_reports_rejected_wid_for_a_malformed_header(monkeypatch):
    monkeypatch.setattr(hooks, "_slug_from_disk", lambda session_id: None)
    record = hooks.ingest("SessionStart", _body(), explicit_wid="not-a-wid")
    assert record["rejected_wid"] is None


def test_ingest_never_reports_rejected_wid_when_the_header_is_live(monkeypatch):
    monkeypatch.setattr(hooks, "_panes", _panes({"/repo": [("@3", "claude")]}))
    record = hooks.ingest("SessionStart", _body(cwd="/elsewhere"), explicit_wid="@3")
    assert record["wid"] == "@3"
    assert record["rejected_wid"] is None


def test_ingest_reports_rejected_wid_when_the_header_names_a_dead_window(monkeypatch):
    """This is the shape distinguishable from unset: `wid` still falls through to
    inference (None, here — no panes at all), but `rejected_wid` names the stale header
    a session-that-chela-did-not-launch would never have sent."""
    monkeypatch.setattr(hooks, "_slug_from_disk", lambda session_id: None)
    record = hooks.ingest("SessionStart", _body(), explicit_wid="@999")
    assert record["wid"] is None
    assert record["rejected_wid"] == "@999"


# --- the epoch stamp (CMX-236) ----------------------------------------------------
#
# `runtime_truth._hooks_rejected_wid_report` needs to know WHICH tmux server a record was
# written under to tell a genuine teardown apart from a stale id surviving a server
# restart — `ingest` is the one write path, so it is the one place that can stamp it.

def test_ingest_stamps_every_record_with_the_current_tmux_epoch(monkeypatch):
    monkeypatch.setattr(hooks.epoch, "current", lambda: "1234-5678")
    record = hooks.ingest("PreToolUse", _body())
    assert record["epoch"] == "1234-5678"


def test_ingest_stamps_none_when_the_epoch_cannot_be_read(monkeypatch):
    """No tmux, no server: `chela.epoch.current` already returns `None` for "unknown, not
    stale" — `ingest` must carry that through unchanged, not invent a value."""
    monkeypatch.setattr(hooks.epoch, "current", lambda: None)
    record = hooks.ingest("PreToolUse", _body())
    assert record["epoch"] is None


def test_ingest_caches_the_epoch_instead_of_asking_tmux_on_every_hook(monkeypatch):
    """`chela.epoch.current` is its own tmux subprocess — asking it fresh on every single
    hook (PreToolUse fires per tool call, far hotter than SessionStart) would add a real
    subprocess to the hot path for a value that essentially never changes. One read must
    serve every hook inside the TTL."""
    calls = []
    monkeypatch.setattr(hooks.epoch, "current", lambda: calls.append(1) or "ep-A")
    hooks.ingest("PreToolUse", _body())
    hooks.ingest("PreToolUse", _body())
    hooks.ingest("PreToolUse", _body())
    assert len(calls) == 1


def test_ingest_refreshes_the_epoch_after_the_cache_ttl_expires(monkeypatch):
    """The cache is not permanent — a real tmux server restart must eventually be seen.

    The elapsed time simulated below is a fixed, ABSOLUTE number of seconds — not derived
    from ``_EPOCH_TTL`` itself. A dashboard process lives for days, so if the TTL were ever
    widened to something effectively permanent, a test that measures its own wait relative
    to the constant it is guarding could never observe that (the wait would just widen along
    with it). The bound below pins the TTL to something sane for a live process; the elapsed
    simulation is comfortably past that bound regardless of what ``_EPOCH_TTL`` is set to.
    """
    assert hooks._EPOCH_TTL <= 60.0, (
        "epoch cache TTL must stay bounded — a live dashboard process must eventually see a "
        "tmux server restart, not cache the epoch forever"
    )
    monkeypatch.setattr(hooks.epoch, "current", lambda: "ep-A")
    first = hooks.ingest("PreToolUse", _body())
    assert first["epoch"] == "ep-A"
    monkeypatch.setattr(hooks.epoch, "current", lambda: "ep-B")
    hooks._epoch_cache["ts"] -= 61.0     # simulate 61s elapsing — past any sane TTL bound
    second = hooks.ingest("PreToolUse", _body())
    assert second["epoch"] == "ep-B"


def test_recap_command_carries_the_window_id_as_a_shell_expanded_header():
    """Baked in as ``${CHELA_WID:-}`` — expanded by the agent's OWN shell at hook time, not
    by chela (this string is one manifest shared by the whole fleet, so it cannot bake in
    any one agent's id)."""
    command = hooks.recap_command(port=5001)
    assert 'X-Chela-Wid: ${CHELA_WID:-}' in command
    assert "$CHELA_WID" not in command.replace("${CHELA_WID:-}", "")


# --- live terminal timestamps (CMX-277, mechanism swapped in CMX-285) --------------

def test_timestamp_events_is_exactly_message_display():
    """The one event the inline-timestamp feature answers now — the assistant-message
    display boundary, not the ``UserPromptSubmit``/``Stop`` pair CMX-277 used."""
    assert hooks.TIMESTAMP_EVENTS == frozenset({"MessageDisplay"})


def test_message_display_response_stamps_only_the_first_batch():
    """``MessageDisplay`` fires once per streamed batch of an assistant message — stamp
    only ``index == 0`` so the marker appears exactly once per reply, not before every
    chunk. A later batch gets the empty-object "display unchanged" shape."""
    first = hooks.message_display_response(
        {"hook_event_name": "MessageDisplay", "index": 0, "delta": "Sure, on it."})

    assert first == {"hookSpecificOutput": {
        "hookEventName": "MessageDisplay",
        "displayContent": first["hookSpecificOutput"]["displayContent"],
    }}
    content = first["hookSpecificOutput"]["displayContent"]
    assert content.startswith("[")
    assert content.endswith(" Sure, on it.")
    stamp = content.removesuffix(" Sure, on it.")
    assert len(stamp) == 7 and stamp[0] == "[" and stamp[3] == ":" and stamp[6] == "]"

    later = hooks.message_display_response(
        {"hook_event_name": "MessageDisplay", "index": 1, "delta": " and it's done."})
    assert later == {}


def test_message_display_response_asks_the_module_clock_not_a_fixed_string(monkeypatch):
    """A fixed string like ``"00:00"`` satisfies the HH:MM shape check above just as
    well as a real clock — that gap is exactly what let a frozen-clock mutation survive
    (docs/DEFEAT_SHAPES.md). Monkeypatching ``hooks.time.strftime`` and asserting the exact
    rendered value pins THAT the module's own clock was asked, but a stub of the form
    ``lambda fmt: "12:34"`` accepts and discards ``fmt`` — so it does not pin WHAT was
    asked for, and ``time.strftime("%d:%m")`` (a date, not a time) satisfies both the
    stub and the HH:MM shape check equally well (docs/DEFEAT_SHAPES.md shape 18).
    Capturing the ``fmt`` argument closes that: it pins the format string itself, not
    merely the stubbed return value."""
    captured_fmt = []
    monkeypatch.setattr(hooks.time, "strftime",
                         lambda fmt: captured_fmt.append(fmt) or "12:34")

    resp = hooks.message_display_response(
        {"hook_event_name": "MessageDisplay", "index": 0, "delta": "hi"})

    assert captured_fmt == ["%H:%M"]
    assert resp["hookSpecificOutput"]["displayContent"] == "[12:34] hi"


def test_message_display_response_tolerates_a_missing_or_non_string_delta():
    """The real payload always carries a string ``delta``, but a malformed one must
    degrade to an empty delta, not get stringified into the message a human reads.
    An f-string never raises on ``None``/``42``/a list, so a bare ``startswith`` check
    would pass just as well whether the bad value was dropped OR silently rendered as
    the literal text ``"None"`` — pinning the exact trailing content is what actually
    proves the type guard ran."""
    for delta in (None, 42, ["oops"]):
        resp = hooks.message_display_response(
            {"hook_event_name": "MessageDisplay", "index": 0, "delta": delta})
        content = resp["hookSpecificOutput"]["displayContent"]
        assert content.startswith("[")
        stamp = content[:-1]
        assert len(stamp) == 7 and stamp[3] == ":" and stamp[6] == "]"  # [HH:MM]
        assert content == f"{stamp} "  # a trailing "None"/"42"/"['oops']" fails this


# --- the endpoint ----------------------------------------------------------------

def test_endpoint_appends_and_answers_nothing_that_nobody_answered(client):
    """A question nobody has tapped is a question the endpoint does NOT decide.

    This assertion used to be unconditional — the endpoint answered nothing, ever. CMX-50
    changed that deliberately: a ``PermissionRequest`` for an ``AskUserQuestion`` is now
    held (briefly, boundedly) so a human can answer it from Telegram with no keystrokes.
    Everything that makes that safe is asserted in ``tests/test_gateanswer.py``. What is
    asserted *here* is the floor beneath it: with no window, no bound topic and no human
    (this client has none of them), the body is still ``{}`` — no decision, no deny, and no
    delay. A gate is never answered on a human's behalf just because it arrived.
    """
    resp = client.post("/hooks/PermissionRequest", json=ASKUQ)

    assert resp.status_code == 200
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


def test_endpoint_stamps_a_timestamp_on_the_message_display_hook(client, monkeypatch):
    """``CHELA_TERMINAL_TIMESTAMPS`` defaults OFF (adopter-facing, needs Claude Code
    2.1.152+) — exercise the stamping path by opting in explicitly, the same way an
    adopter who wants it would."""
    monkeypatch.setattr(dash.config, "TERMINAL_TIMESTAMPS", True)
    resp = client.post("/hooks/MessageDisplay", json=_body(
        hook_event_name="MessageDisplay", index=0, delta="On it."))

    assert resp.status_code == 200
    body = resp.get_json()
    content = body["hookSpecificOutput"]["displayContent"]
    assert body["hookSpecificOutput"]["hookEventName"] == "MessageDisplay"
    assert content.startswith("[")
    assert content.endswith(" On it.")

    # UNLIKE every other hook: never ingested. `MessageDisplay` fires once per streamed
    # batch of every assistant reply — logging it would flood the event log with a
    # firehose of near-duplicate "message display" records nobody would read.
    assert event_log.read()["events"] == []


def test_the_timestamp_can_be_turned_off(client, monkeypatch):
    """``CHELA_TERMINAL_TIMESTAMPS=false`` is the escape hatch an adopter on a Claude Code
    version that renders the field badly must be able to flip without code changes."""
    monkeypatch.setattr(dash.config, "TERMINAL_TIMESTAMPS", False)

    resp = client.post("/hooks/MessageDisplay", json=_body(
        hook_event_name="MessageDisplay", index=0, delta="On it."))

    assert resp.get_json() == {}
    assert event_log.read()["events"] == []


def test_terminal_timestamps_defaults_off_with_no_env_var_set(monkeypatch):
    """CMX-277's rework round: this writes a visible line at every message boundary in
    EVERY adopter's terminal, on a mechanism whose own spike names rendering reliability
    across Claude Code versions as unverified — so it must ship OFF, opt-in per install,
    not on-by-default-with-an-escape-hatch. Reloads the real module against a clean env,
    not just a monkeypatched attribute, so a `"true"` fallback string reintroduced into
    :data:`chela.config.TERMINAL_TIMESTAMPS`'s ``os.environ.get`` default is caught here
    even though every other test in this file monkeypatches the attribute directly."""
    monkeypatch.delenv("CHELA_TERMINAL_TIMESTAMPS", raising=False)
    reloaded = importlib.reload(config)
    try:
        assert reloaded.TERMINAL_TIMESTAMPS is False
    finally:
        importlib.reload(config)  # restore whatever env the rest of the suite expects


def test_timestamps_on_does_not_steal_the_post_tool_use_gate_resolution(client, monkeypatch):
    """CMX-277 rework round 5 (still true after CMX-285's mechanism swap): the
    ``MessageDisplay`` branch returns EARLY, ahead of ``hooks.ingest`` and every
    event-specific branch below it — including the ``PostToolUse`` one that calls
    ``gateanswer.gate_resolved``. If ``hooks.TIMESTAMP_EVENTS`` were ever widened to also
    match ``PostToolUse`` (docs/DEFEAT_SHAPES.md shape 12), that widened branch would
    return before ``gate_resolved`` is ever reached, so a held gate would wait out its
    whole wait budget (the CMX-54 regression) instead of being torn down. No other test in
    this file POSTs ``PostToolUse`` with timestamps ON, so nothing else would catch it.
    Pin both halves at once: the body is the empty-object shape a non-timestamp event
    gets, AND the resolution side effect the ``PostToolUse`` branch is responsible for
    still ran.
    """
    monkeypatch.setattr(dash.config, "TERMINAL_TIMESTAMPS", True)
    resolved = []
    monkeypatch.setattr(dash.gateanswer, "gate_resolved", lambda tuid: resolved.append(tuid))

    resp = client.post("/hooks/PostToolUse", json=_body(hook_event_name="PostToolUse",
                                                          tool_use_id="toolu_123"))

    assert resp.status_code == 200
    assert resp.get_json() == {}
    assert resolved == ["toolu_123"]


# Every event other than the two timestamp boundaries — derived from `hooks.HOOK_EVENTS`
# itself, not hand-copied, so an event Claude Code adds later is exercised automatically.
_NON_TIMESTAMP_EVENTS = [e for e in hooks.HOOK_EVENTS if e not in hooks.TIMESTAMP_EVENTS]


@pytest.mark.parametrize("event", _NON_TIMESTAMP_EVENTS)
def test_timestamps_on_leaves_every_non_boundary_event_unchanged(client, monkeypatch, event):
    """CMX-283: the test above pins exactly one hole a judge mutation happened to find —
    widening the membership test to also swallow ``PostToolUse``. That chases the
    short-circuit one event at a time: the next event a widened condition happens to pick
    up (``PermissionRequest``, ``SessionStart``, or an event Claude Code adds tomorrow)
    would need its own dedicated round, forever one step behind whatever the judge finds
    next. Assert the property the short-circuit actually promises, exhaustively instead:
    for every event other than the two timestamp boundaries, whether
    ``CHELA_TERMINAL_TIMESTAMPS`` is on or off must make NO difference to that event's
    response — the branch is a true no-op outside its own two events. A membership test
    widened to also catch this event returns ``hooks.timestamp_response(event)`` in place
    of its real body once the flag is on, which a same-event on/off comparison catches
    directly, without needing to know what that event's real body is or mock its handler's
    side effects. Parametrized off ``hooks.HOOK_EVENTS`` (see `_NON_TIMESTAMP_EVENTS`
    above), so covering a future event needs no new test, only a wider tuple.

    CMX-285 rework round 2 (docs/DEFEAT_SHAPES.md shape 26): the on/off comparison alone
    is not enough once the timestamp branch sits ABOVE ``hooks.ingest`` (CMX-285 moved it
    there so the streamed ``MessageDisplay`` firehose never reaches the log). A widened
    membership test now returns ``{}`` for BOTH flag states — the response comparison
    stays green precisely because the event was swallowed identically either way, and the
    thing actually lost — the event never reaching ``hooks.ingest`` at all — is invisible
    to a response-shape assertion. Restore the property CMX-277's own endpoint test used to
    pin per-event (the ``assert event_log.read()["events"][0]["type"] == "hook.stop"``
    line CMX-285 dropped when it deleted the ``Stop``-specific test): for every
    non-timestamp event, ingest must actually have run, independent of what the HTTP
    response looks like.
    """
    body = _body(hook_event_name=event, tool_name="Bash", tool_use_id=f"toolu_{event}")

    monkeypatch.setattr(dash.config, "TERMINAL_TIMESTAMPS", False)
    resp_off = client.post(f"/hooks/{event}", json=body)

    monkeypatch.setattr(dash.config, "TERMINAL_TIMESTAMPS", True)
    resp_on = client.post(f"/hooks/{event}", json=body)

    assert resp_off.status_code == resp_on.status_code == 200
    assert resp_on.data == resp_off.data

    # Both POSTs must have reached hooks.ingest — one record per call, both typed for
    # THIS event. A short-circuit that swallows the event (with the flag on, off, or
    # both) leaves this list short instead of merely changing what the HTTP body says.
    types = [e["type"] for e in event_log.read()["events"]]
    assert types == [hooks.event_type(event), hooks.event_type(event)]


def test_endpoint_message_display_survives_a_malformed_body(client, monkeypatch):
    """CMX-285 rework round 2: ``message_display_response`` calls ``body.get(...)`` — a
    non-dict body (unparseable JSON, an empty POST, a bare JSON list) would raise
    ``AttributeError`` inside it and turn into a 500 into a live, blocked agent, exactly
    the failure ``test_endpoint_does_not_fail_a_blocked_agent`` pins for ``PreToolUse``.
    That test never exercises ``MessageDisplay`` with the flag on, so the ``isinstance``
    guard on this branch's early-return path — the one place a malformed payload actually
    meets ``message_display_response`` — had nothing watching it (the endpoint's own
    docstring promise: "200 and an empty object, every time").
    """
    monkeypatch.setattr(dash.config, "TERMINAL_TIMESTAMPS", True)
    for data in (b"{not json", b"", b"[1,2,3]"):
        resp = client.post("/hooks/MessageDisplay", data=data,
                           content_type="application/json")
        assert resp.status_code == 200
        assert resp.get_json() == {}
    assert event_log.read()["events"] == []


def test_terminal_timestamps_turns_on_with_the_env_var_set_to_true(monkeypatch):
    """CMX-277 rework round 4: the mirror of the OFF-default test above. Every other
    ON-state test in this file monkeypatches the `TERMINAL_TIMESTAMPS` attribute directly,
    which never exercises the `os.environ.get(...)` parse that actually reads
    `CHELA_TERMINAL_TIMESTAMPS` — so `TERMINAL_TIMESTAMPS = False and os.environ.get(...)`
    (a dead-coded knob that can never turn ON, however the env var is set) left every
    attribute-monkeypatched test green. Reload the real module against a real env var, the
    same shape as the OFF-default test, so the parse path itself is what's pinned."""
    monkeypatch.setenv("CHELA_TERMINAL_TIMESTAMPS", "true")
    try:
        reloaded = importlib.reload(config)
        assert reloaded.TERMINAL_TIMESTAMPS is True
    finally:
        monkeypatch.delenv("CHELA_TERMINAL_TIMESTAMPS", raising=False)
        importlib.reload(config)  # restore whatever env the rest of the suite expects


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
        assert ("matcher" in entries[0]) == (event in hooks.TOOL_EVENTS)
        if event == "SessionStart":
            # The ONE command hook, because SessionStart NEVER fires over http (measured,
            # CMX-41) and a command hook's stdout IS the injected context — which is how
            # the room recap reaches a restarted agent. It is a curl into the SAME endpoint
            # (not a `chela` spawn: chela is not on an agent's PATH), and it fails open —
            # `--fail` so an HTTP error body can never be injected as context, and `|| true`
            # so a dead daemon or a missing curl prints nothing and exits 0.
            assert hook["type"] == "command"
            assert "url" not in hook
            assert "http://127.0.0.1:5001/hooks/SessionStart" in hook["command"]
            assert "--fail" in hook["command"] and hook["command"].endswith("|| true")
            assert hook["timeout"] == hooks.RECAP_TIMEOUT
            # DEFEAT_SHAPES #5: the line above reads the SAME symbol on both sides once
            # rendered, so widening or removing the timeout moves undetected — pin the
            # literal too.
            assert hooks.RECAP_TIMEOUT == 5, hooks.RECAP_TIMEOUT
            continue
        # http, not command: no shell, no process spawn per tool call, and no chatty
        # .bashrc able to corrupt the JSON contract with stray stdout.
        assert hook["type"] == "http"
        assert hook["url"] == f"http://127.0.0.1:5001/hooks/{event}"
        if event == "PermissionRequest":
            # The ONE event allowed to take its time: it is where a gate is answered from
            # a phone, and an answer needs a human to look at it (CMX-50). MEASURED, not
            # assumed: Claude Code honours a declared http-hook timeout verbatim (10s →
            # 10.2s blocked, 65 → 66, 130 → 133 — no 60s clamp) and fails open on expiry.
            assert hook["timeout"] == hooks.GATE_TIMEOUT > 60
        else:
            # Everything else appends and returns. The agent BLOCKS on this request, and
            # PreToolUse/PostToolUse alone are ~78% of the log's volume.
            assert hook["timeout"] == hooks.HOOK_TIMEOUT <= 2


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


# --- the version<->hooks coupling: a hook change without a version bump ships every
# adopter stale hooks forever, because Claude Code keys `/plugin install`/update on the
# version alone (CMX-170) ----------------------------------------------------------------

def test_hooks_fingerprint_matches_the_recorded_version():
    """The recorded fingerprint MOVES when `hooks_spec` changes, only via a version bump.

    Corrupt this by editing `hooks_spec` (add/remove a header) without touching
    `EXPECTED_HOOKS_FINGERPRINT`, or by bumping `plugin.json`'s version without adding a
    fingerprint entry for it — either one must go RED, naming the adopter-stale-hooks trap.
    """
    version = hooks.plugin_manifest()["version"]
    assert version in hooks.EXPECTED_HOOKS_FINGERPRINT, (
        f"plugin.json version {version!r} has no recorded hooks fingerprint — a version "
        "bump must ALSO record hooks.hooks_fingerprint() in EXPECTED_HOOKS_FINGERPRINT."
    )
    assert hooks.hooks_fingerprint() == hooks.EXPECTED_HOOKS_FINGERPRINT[version], (
        "hooks_spec() changed shape without a plugin.json version bump — Claude Code keys "
        "plugin updates on the version alone, so every existing adopter would silently "
        "keep the STALE hooks forever. Bump plugin/.claude-plugin/plugin.json's version "
        "AND record the new hooks.hooks_fingerprint() in EXPECTED_HOOKS_FINGERPRINT."
    )


def test_hooks_fingerprint_is_port_independent():
    """A different dashboard port renders a byte-different manifest but the SAME hooks —
    only a structural change (a header, a timeout, an event) may trip the guard above."""
    assert hooks.hooks_fingerprint(5001) == hooks.hooks_fingerprint(5099)


# --- SessionStart: the room recap, handed to a session that cannot remember it -------
#
# A hook is read at agent STARTUP and an agent's context does not survive its process, so
# everything a room ever told an agent dies with the session it was injected into — and a
# dispatched agent is a fresh session every run. This is the one moment we can hand it
# back. The response IS the agent's context, so what it must never do is print anything
# else: an error page, a stack trace, or boilerplate for an agent that is in no room.

@pytest.fixture
def wired(monkeypatch):
    """@3 and @5 share a room; @7 is in none. tmux is stubbed; nothing is pasted."""
    live = {"@3": "cmx-63", "@5": "cmx-64", "@7": "loner"}
    monkeypatch.setattr(rooms.discovery, "get_windows_by_id", lambda: dict(live))
    monkeypatch.setattr(messenger, "get_windows_by_id", lambda: dict(live))
    monkeypatch.setattr(messenger, "send_tmux", lambda *a, **k: True)
    monkeypatch.setattr(rooms.agent_manager, "status_by_wid",
                        lambda: {w: "idle" for w in live})
    rooms.create("wire")
    rooms.join("wire", "@3")
    rooms.join("wire", "@5")
    rooms.post("wire", "question", "does the parser own the retry?",
               from_wid="@5", targets=["@3"])


def _session_start(client, cwd: str):
    """A SessionStart POST from the session whose ORIGIN is ``cwd`` (`/a` = @3, `/b` = @7)."""
    panes = _panes({cwd: [("@3", "claude")] if cwd == "/a" else [("@7", "claude")]})
    with patch.object(hooks, "_panes", panes):
        slug = transcripts.encode_cwd(cwd)
        return client.post("/hooks/SessionStart", json=_body(
            hook_event_name="SessionStart", source="startup",
            transcript_path=f"/h/.claude/projects/{slug}/s.jsonl"))


def test_a_restarted_agent_is_handed_its_rooms_back(client, wired):
    resp = _session_start(client, "/a")
    assert resp.status_code == 200
    out = resp.get_json()["hookSpecificOutput"]
    assert out["hookEventName"] == "SessionStart"
    assert "does the parser own the retry?" in out["additionalContext"]
    assert "→ YOU" in out["additionalContext"]


def test_an_agent_in_no_room_gets_an_EMPTY_body(client, wired):
    """Byte-for-byte nothing. The stdout of this hook is the agent's context, and most
    agents are in no room — a header for all of them buys nothing for any of them."""
    resp = _session_start(client, "/b")
    assert resp.status_code == 200
    assert resp.data == b""


def test_a_session_that_cannot_be_correlated_gets_NOTHING(client, wired):
    """No window (the autouse stub says: no panes) — never a guess at someone else's rooms."""
    resp = client.post("/hooks/SessionStart", json=_body(hook_event_name="SessionStart"))
    assert resp.status_code == 200 and resp.data == b""


def test_a_broken_recap_is_silent_and_still_200(client, wired):
    """A raise here would print a stack trace into a live agent's context window."""
    with patch.object(rooms, "recap", side_effect=RuntimeError("boom")):
        resp = _session_start(client, "/a")
    assert resp.status_code == 200 and resp.data == b""


def test_the_session_start_hook_still_lands_in_the_log(client, wired):
    """It is a curl into the same endpoint, so SessionStart finally ingests too."""
    _session_start(client, "/a")
    assert [e["type"] for e in event_log.read()["events"]][-1] == "hook.session_start"


# --- SessionStart: the X-Chela-Wid header (CMX-160) -----------------------------------

def test_the_header_hands_back_a_recap_when_ordinary_correlation_has_nothing(
        client, wired):
    """End to end: the payload correlates to nothing at all (no pane sits in its cwd, no
    transcript on disk) — the header alone still resolves it to @3's room."""
    with patch.object(hooks, "_panes",
                      _panes({"/somewhere-unrelated": [("@3", "claude")]})):
        resp = client.post("/hooks/SessionStart", json=_body(
            hook_event_name="SessionStart", source="startup",
            session_id="unrelated-session-id", transcript_path=None, cwd="/nowhere",
        ), headers={"X-Chela-Wid": "@3"})
    assert resp.status_code == 200
    out = resp.get_json()["hookSpecificOutput"]
    assert "does the parser own the retry?" in out["additionalContext"]


def test_a_header_naming_a_dead_window_falls_back_to_ordinary_correlation(
        client, wired):
    """A stale header (the window it names is no longer live) must not be worse than no
    header at all — it falls through to the same origin-based resolution."""
    resp = _session_start(client, "/a")   # unheadered control: @3, via cwd correlation
    with patch.object(hooks, "_panes", _panes({"/a": [("@3", "claude")]})):
        with_header = client.post("/hooks/SessionStart", json=_body(
            hook_event_name="SessionStart", source="startup",
            transcript_path=f"/h/.claude/projects/{transcripts.encode_cwd('/a')}/s.jsonl",
        ), headers={"X-Chela-Wid": "@404"})
    assert resp.get_json() == with_header.get_json()
