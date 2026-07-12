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
