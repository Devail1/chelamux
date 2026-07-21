"""Incremental transcript monitor — lock in the two load-bearing behaviours:

  * **incremental offset advance** — each poll reads only the bytes appended
    since the last poll; a fresh window seeks to EOF so history isn't replayed,
    and a mid-write partial line is not consumed until it's complete;
  * **tool pairing across cycles** — a ``tool_use`` read in one poll pairs with
    its ``tool_result`` read in a later poll, so the result event carries the
    originating tool's name.

All against a JSONL fixture on disk with an injected resolver, so no live tmux
or Claude Code session is needed.
"""
from __future__ import annotations

import json

import pytest

from chela.telegram.monitor import TranscriptMonitor
from chela.telegram.parser import Message, parse_entries


def _assistant_text(text: str) -> str:
    return json.dumps(
        {"type": "assistant", "timestamp": "t", "message": {"content": [{"type": "text", "text": text}]}}
    ) + "\n"


def _assistant_tool_use(tool_id: str, name: str) -> str:
    return json.dumps(
        {
            "type": "assistant", "timestamp": "t",
            "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": {}}]},
        }
    ) + "\n"


def _user_tool_result(tool_id: str, text: str) -> str:
    return json.dumps(
        {
            "type": "user", "timestamp": "t",
            "message": {"content": [{"type": "tool_result", "tool_use_id": tool_id, "content": text}]},
        }
    ) + "\n"


def _user_text(text: str) -> str:
    return json.dumps(
        {"type": "user", "timestamp": "t", "message": {"content": [{"type": "text", "text": text}]}}
    ) + "\n"


def _append(path, *lines: str) -> None:
    with path.open("a", encoding="utf-8") as f:
        for line in lines:
            f.write(line)


@pytest.fixture
def collected():
    return []


def _monitor(path, collected, *, start_at_eof=True):
    return TranscriptMonitor(
        on_message=lambda wid, msg: collected.append((wid, msg)),
        start_at_eof=start_at_eof,
        resolver=lambda wid: path,
    )


# --------------------------------------------------------------------------
# incremental offset advance
# --------------------------------------------------------------------------

def test_first_poll_at_eof_skips_backlog(tmp_path, collected):
    """A window seen for the first time relays nothing already on disk."""
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(_assistant_text("old history") + _user_text("older prompt"))

    mon = _monitor(transcript, collected)
    mon.poll(["@1"])

    assert collected == []
    assert mon._tracked["@1"].offset == transcript.stat().st_size


def test_only_new_lines_are_emitted_across_polls(tmp_path, collected):
    """Second poll reads only what was appended after the first."""
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(_assistant_text("history"))

    mon = _monitor(transcript, collected)
    mon.poll(["@1"])          # seeks to EOF, emits nothing
    assert collected == []
    off_after_first = mon._tracked["@1"].offset

    _append(transcript, _assistant_text("first new"), _user_text("a prompt"))
    mon.poll(["@1"])

    kinds = [(m.role, m.content_type, m.text) for _, m in collected]
    assert kinds == [
        ("assistant", "text", "first new"),
        ("user", "text", "a prompt"),
    ]
    assert mon._tracked["@1"].offset > off_after_first
    assert mon._tracked["@1"].offset == transcript.stat().st_size


def test_partial_trailing_line_waits_for_completion(tmp_path, collected):
    """A line without a trailing newline is not consumed until it's complete."""
    transcript = tmp_path / "s.jsonl"
    transcript.write_text("")

    mon = _monitor(transcript, collected)
    mon.poll(["@1"])

    # Write a complete record plus a partial (no newline) tail.
    complete = _assistant_text("complete")
    _append(transcript, complete)
    with transcript.open("a", encoding="utf-8") as f:
        f.write('{"type": "assistant", "message": {"content": [{"type": "text",')
    mon.poll(["@1"])

    assert [m.text for _, m in collected] == ["complete"]
    # Offset stops at the end of the complete line, before the partial write.
    assert mon._tracked["@1"].offset == len(complete)

    # Finish the partial line — next poll picks it up whole.
    _append(transcript, ' "text": "finished"}]}}\n')
    mon.poll(["@1"])
    assert [m.text for _, m in collected] == ["complete", "finished"]


def test_corrupt_complete_line_is_skipped_without_swallowing_the_next(tmp_path, collected):
    """A COMPLETE-but-corrupt line advances the offset and is skipped — but a
    partial FINAL line is never advanced past, so nothing after it is lost.

    ccbot's hardening distinguished *partial* (retry next tick, don't advance the
    offset) from *corrupt* (skip). chela already draws that line at the newline
    boundary in ``_read_new``: the offset only moves past bytes that ended in a
    newline, so a mid-append final record can't be consumed-then-skipped. This
    locks in both halves of the distinction.
    """
    transcript = tmp_path / "s.jsonl"
    transcript.write_text("")
    mon = _monitor(transcript, collected)
    mon.poll(["@1"])

    # A complete but unparseable line (has its newline), then a complete good one,
    # then a partial tail with no newline.
    transcript.write_text("{not valid json at all}\n")
    good = _assistant_text("kept")
    _append(transcript, good)
    with transcript.open("a", encoding="utf-8") as f:
        f.write('{"type": "assistant", "message": {"content": [{"type": "text",')
    mon.poll(["@1"])

    # The corrupt line was skipped (not relayed) but did not swallow the good one;
    # the partial tail left the offset before itself, awaiting completion.
    assert [m.text for _, m in collected] == ["kept"]
    assert mon._tracked["@1"].offset == len("{not valid json at all}\n") + len(good)

    _append(transcript, ' "text": "tail"}]}}\n')
    mon.poll(["@1"])
    assert [m.text for _, m in collected] == ["kept", "tail"]


def test_truncation_resets_offset(tmp_path, collected):
    """A transcript that shrank (e.g. /clear) is re-read from the top."""
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(_assistant_text("before clear"))

    mon = _monitor(transcript, collected)
    mon.poll(["@1"])                      # offset at EOF of the long file
    transcript.write_text(_assistant_text("after clear"))  # now shorter
    mon.poll(["@1"])

    assert [m.text for _, m in collected] == ["after clear"]


# --------------------------------------------------------------------------
# tool pairing
# --------------------------------------------------------------------------

def test_tool_use_and_result_pair_within_one_poll(tmp_path, collected):
    transcript = tmp_path / "s.jsonl"
    transcript.write_text("")

    mon = _monitor(transcript, collected)
    mon.poll(["@1"])
    _append(
        transcript,
        _assistant_tool_use("tu_1", "Read"),
        _user_tool_result("tu_1", "file contents"),
    )
    mon.poll(["@1"])

    events = [m for _, m in collected]
    assert [(m.content_type, m.tool_name, m.tool_use_id) for m in events] == [
        ("tool_use", "Read", "tu_1"),
        ("tool_result", "Read", "tu_1"),   # result carries the paired tool name
    ]
    assert events[1].text == "file contents"


def test_tool_pairing_survives_across_polls(tmp_path, collected):
    """tool_use in one poll pairs with tool_result read in the next."""
    transcript = tmp_path / "s.jsonl"
    transcript.write_text("")

    mon = _monitor(transcript, collected)
    mon.poll(["@1"])

    _append(transcript, _assistant_tool_use("tu_9", "Bash"))
    mon.poll(["@1"])                       # emits the tool_use; result not here yet
    assert mon._tracked["@1"].pending  # carried between cycles

    _append(transcript, _user_tool_result("tu_9", "exit 0"))
    mon.poll(["@1"])

    events = [m for _, m in collected]
    assert events[0].content_type == "tool_use"
    assert events[1].content_type == "tool_result"
    assert events[1].tool_name == "Bash"   # paired despite the cross-poll gap
    assert not mon._tracked["@1"].pending  # cleared once matched


def test_rotation_to_new_transcript_reads_from_zero(tmp_path, collected):
    """A file that rotates under a running window is all-new — read from 0.

    (A ``/clear`` starts a fresh session file whose first turn belongs to this
    session; skipping it to EOF was the bug — a blocked agent showed nothing.)
    """
    first = tmp_path / "a.jsonl"
    second = tmp_path / "b.jsonl"
    first.write_text(_assistant_text("session A"))
    second.write_text(_assistant_text("session B first turn"))

    target = {"path": first}
    mon = TranscriptMonitor(
        on_message=lambda wid, msg: collected.append((wid, msg)),
        resolver=lambda wid: target["path"],
    )
    mon.poll(["@1"])                       # cold-start on A → tracks A at EOF
    assert collected == []

    target["path"] = second               # rotation to a fresh session file
    mon.poll(["@1"])                       # its content is new → read from 0
    assert [m.text for _, m in collected] == ["session B first turn"]

    _append(second, _assistant_text("session B live"))
    mon.poll(["@1"])
    assert [m.text for _, m in collected] == ["session B first turn", "session B live"]


def test_appeared_transcript_emits_from_zero(tmp_path, collected):
    """Core fix: a window polled while it has NO transcript, then one appears
    WITH content already in it, relays that content (was skipped to EOF)."""
    transcript = tmp_path / "s.jsonl"
    target = {"path": None}
    mon = TranscriptMonitor(
        on_message=lambda wid, msg: collected.append((wid, msg)),
        resolver=lambda wid: target["path"],
    )
    mon.poll(["@1"])                       # no transcript yet — remembered empty
    assert collected == []
    assert "@1" not in mon._tracked

    # The agent's first turn lands (question included) before the next poll.
    transcript.write_text(_assistant_text("first turn question"))
    target["path"] = transcript
    mon.poll(["@1"])
    assert [m.text for _, m in collected] == ["first turn question"]


def test_cold_start_over_existing_history_skips_to_eof(tmp_path, collected):
    """A window whose transcript ALREADY exists on the first poll (binding an
    agent with prior history) must NOT replay that backlog."""
    transcript = tmp_path / "s.jsonl"
    transcript.write_text(_assistant_text("prior history"))

    mon = _monitor(transcript, collected)
    mon.poll(["@1"])                       # transcript pre-existed → EOF
    assert collected == []
    assert mon._tracked["@1"].offset == transcript.stat().st_size


def test_appeared_large_backlog_falls_back_to_eof(tmp_path, collected):
    """A freshly-appeared file already larger than the fresh threshold (e.g. a
    --resume that repopulated history) falls back to EOF instead of replaying."""
    from chela.telegram import monitor as monitor_mod

    transcript = tmp_path / "s.jsonl"
    target = {"path": None}
    mon = TranscriptMonitor(
        on_message=lambda wid, msg: collected.append((wid, msg)),
        resolver=lambda wid: target["path"],
    )
    mon.poll(["@1"])                       # seen empty

    big = _assistant_text("x" * (monitor_mod._FRESH_MAX_BYTES + 1))
    transcript.write_text(big)
    target["path"] = transcript
    mon.poll(["@1"])                       # over threshold → EOF, no replay
    assert collected == []
    assert mon._tracked["@1"].offset == transcript.stat().st_size

    _append(transcript, _assistant_text("live after attach"))
    mon.poll(["@1"])
    assert [m.text for _, m in collected] == ["live after attach"]


# --------------------------------------------------------------------------
# parser unit — pending carry-over contract
# --------------------------------------------------------------------------

def test_parse_entries_returns_unmatched_pending():
    use = json.loads(_assistant_tool_use("tu_x", "Grep"))
    events, pending = parse_entries([use])
    assert [e.content_type for e in events] == ["tool_use"]
    assert "tu_x" in pending                       # unmatched use stays pending

    res = json.loads(_user_tool_result("tu_x", "3 matches"))
    events2, pending2 = parse_entries([res], pending)
    assert isinstance(events2[0], Message)
    assert events2[0].content_type == "tool_result"
    assert events2[0].tool_name == "Grep"
    assert pending2 == {}


# --------------------------------------------------------------------------
# resolution: by session id, NOT by cwd (CMX-70)
#
# The monitor used to resolve a window through `discovery.get_window_cwd_by_id` →
# `transcripts.transcript_for_cwd`, and asserted in its own docstring that "tmux is the
# source of truth" for that mapping. It is not: a window rebuilt in one directory running
# `claude --resume` of a session born in another keeps writing to the ORIGINAL project
# dir. On 2026-07-14 the resolver searched an empty directory, returned None, and the
# relay was dead for an hour WITHOUT A SINGLE LOG LINE.
# --------------------------------------------------------------------------

def _projects(tmp_path, monkeypatch):
    from chela import transcripts

    root = tmp_path / "projects"
    root.mkdir()
    monkeypatch.setattr(transcripts, "CLAUDE_PROJECTS_DIR", root)
    return root


def _session_transcript(projects, cwd, session_id, body=""):
    from chela import transcripts

    proj = projects / transcripts.encode_cwd(cwd)
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{session_id}.jsonl"
    path.write_text(body)
    return path


SID = "7f3a91c2-4b8e-4d15-9c62-1e0d5a8b3f47"


def test_the_default_resolver_follows_a_resumed_session_not_the_cwd(
        tmp_path, monkeypatch, collected):
    """THE OUTAGE. The window sits in `data_prep`; its session was born in `analytics` and
    is still writing there. The cwd names a project dir with zero transcripts in it."""
    from chela import sessions, transcripts
    from chela.telegram.monitor import _default_resolver

    projects = _projects(tmp_path, monkeypatch)
    live = _session_transcript(projects, "/home/u/projects/analytics", SID)
    (projects / transcripts.encode_cwd("/home/u/projects/analytics/data_prep")).mkdir()
    monkeypatch.setattr(sessions, "panes", lambda force=False: {"@2": sessions.Pane(
        wid="@2", path="/home/u/projects/analytics/data_prep", command="claude",
        claude_pid=1, launched_in="/home/u/projects/analytics/data_prep", resumed=SID)})

    assert _default_resolver(None)("@2") == live

    mon = TranscriptMonitor(on_message=lambda w, m: collected.append((w, m)),
                            start_at_eof=True)
    mon.poll(["@2"])                                   # binds at EOF (empty file)
    _append(live, _assistant_text("the agent answers"))
    mon.poll(["@2"])
    assert [(w, m.text) for w, m in collected] == [("@2", "the agent answers")]


def test_a_resumed_session_with_a_BIG_history_is_not_replayed_into_the_topic(
        tmp_path, monkeypatch, collected):
    """The `_FRESH_MAX_BYTES` guard, under the case it exists for — and the fix makes it
    MORE load-bearing, not less: resolution by session id is what finally hands the monitor
    a `--resume`'s fat back-history, on a window it has already polled (so the file is
    "fresh" and would otherwise be read from byte 0 — the whole session, into the topic)."""
    from chela import sessions
    from chela.telegram import monitor as monitor_mod

    projects = _projects(tmp_path, monkeypatch)
    panes = {}
    monkeypatch.setattr(sessions, "panes", lambda force=False: dict(panes))

    mon = TranscriptMonitor(on_message=lambda w, m: collected.append((w, m)),
                            start_at_eof=True)
    mon.poll(["@2"])                                   # the window exists; no session yet
    assert collected == []

    # `claude --resume` starts, and the session's whole prior history is already on disk.
    history = _assistant_text("x" * (monitor_mod._FRESH_MAX_BYTES + 1))
    live = _session_transcript(projects, "/home/u/analytics", SID, body=history)
    panes["@2"] = sessions.Pane(wid="@2", path="/home/u/data_prep", command="claude",
                                claude_pid=1, launched_in="/home/u/data_prep", resumed=SID)

    mon.poll(["@2"])
    assert collected == []                             # NOT the whole session, into Telegram
    assert mon._tracked["@2"].offset == live.stat().st_size

    _append(live, _assistant_text("the first live turn after the resume"))
    mon.poll(["@2"])
    assert [m.text for _, m in collected] == ["the first live turn after the resume"]


# --------------------------------------------------------------------------
# and when it resolves to NOTHING, it is LOUD (the whole point of CMX-70)
# --------------------------------------------------------------------------

def test_a_window_with_no_transcript_is_LOUD(tmp_path, monkeypatch, caplog):
    """It used to be silent — `return`, no error, no warning, no log line — while every
    health surface stayed green and the human got nothing back."""
    from chela import event_log, sessions

    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    monkeypatch.setattr(sessions, "panes", lambda force=False: {"@2": sessions.Pane(
        wid="@2", path="/home/u/empty", command="claude", claude_pid=1,
        launched_in="/home/u/empty")})
    _projects(tmp_path, monkeypatch)

    mon = TranscriptMonitor(on_message=lambda w, m: None, start_at_eof=True)
    with caplog.at_level("WARNING"):
        mon.poll(["@2"])
    said = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("@2" in m and "NO transcript" in m for m in said), said
    events = [e for e in event_log.ring() if e["type"] == "relay.transcript_missing"]
    assert len(events) == 1
    assert events[0]["wid"] == "@2"
    assert "/home/u/empty" in events[0]["payload"]["why"]

    # It complains once per outage, not once per poll (this runs every few seconds).
    for _ in range(5):
        mon.poll(["@2"])
    assert len([e for e in event_log.ring()
                if e["type"] == "relay.transcript_missing"]) == 1


def test_an_outage_that_ENDS_says_so_too(tmp_path, monkeypatch):
    """A complaint with no all-clear is an alert nobody can act on."""
    from chela import event_log, sessions

    monkeypatch.setenv("CHELA_EVENTS_FILE", str(tmp_path / "events.jsonl"))
    projects = _projects(tmp_path, monkeypatch)
    panes = {"@2": sessions.Pane(wid="@2", path="/home/u/repo", command="claude",
                                 claude_pid=1, launched_in="/home/u/repo")}
    monkeypatch.setattr(sessions, "panes", lambda force=False: dict(panes))

    mon = TranscriptMonitor(on_message=lambda w, m: None, start_at_eof=True)
    mon.poll(["@2"])                                   # nothing on disk yet → LOUD
    _session_transcript(projects, "/home/u/repo", SID)
    mon.poll(["@2"])                                   # …and now it resolves

    types = [e["type"] for e in event_log.ring() if e["type"].startswith("relay.")]
    assert types == ["relay.transcript_missing", "relay.transcript_found"]
