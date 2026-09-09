"""``tool_result`` image extraction — the CMX-338 outbound photo port gap.

ccbot relayed a tool_result's base64 ``image`` content blocks (screenshots and
similar) to Telegram; chelamux's ``_tool_result_text`` only ever collected
``type == "text"`` blocks, so an image block matched neither branch and was
silently dropped. These tests lock in ``_tool_result_images`` and its wiring
into :func:`parse_entries`, including the invariant that a text-only result is
unaffected byte-for-byte (``images`` stays None, never ``[]``).
"""
from __future__ import annotations

import base64

from chela.telegram.parser import Message, _tool_result_images, parse_entries

_PNG_BYTES = b"\x89PNG\r\n\x1a\nfake-png-bytes"
_PNG_B64 = base64.b64encode(_PNG_BYTES).decode()


def _image_block(data: str = _PNG_B64, media_type: str = "image/png") -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _text_block(text: str) -> dict:
    return {"type": "text", "text": text}


# --------------------------------------------------------------------------
# _tool_result_images — direct
# --------------------------------------------------------------------------

def test_tool_result_images_extracts_a_single_block():
    images = _tool_result_images([_image_block()])
    assert images == [("image/png", _PNG_BYTES)]


def test_tool_result_images_returns_none_for_text_only_content():
    assert _tool_result_images([_text_block("hello")]) is None


def test_tool_result_images_type_discriminator_rejects_non_image_even_with_valid_source():
    # The text-block fixture above carries no "source" key at all, so a
    # dead-coded `item.get("type") != "image"` check would still be rejected
    # by the later `isinstance(source, dict)` check — the type check is never
    # actually exercised as the reason the block is skipped. Give this block a
    # fully well-formed base64 image source so ONLY the type discriminator can
    # be why it's rejected.
    block = {"type": "text", "source": {"type": "base64", "media_type": "image/png", "data": _PNG_B64}}
    assert _tool_result_images([block]) is None


def test_tool_result_images_returns_none_for_string_content():
    assert _tool_result_images("plain string result") is None


def test_tool_result_images_returns_none_for_non_iterable_content():
    # DEFEAT_SHAPES 338 round 5: a string SNEAKS past the early
    # `not isinstance(content, list)` guard's real job — it iterates into
    # characters, each of which the later `isinstance(item, dict)` check
    # rejects too, so the string fixture above passes whether or not the
    # early return actually fires. An int is not iterable at all: with the
    # guard dead-coded, `for item in content:` raises TypeError instead of
    # cleanly returning None, so this is what actually pins the guard.
    assert _tool_result_images(12345) is None


def test_tool_result_images_extracts_multiple_blocks_in_order():
    other = b"other-bytes"
    images = _tool_result_images([
        _image_block(),
        _image_block(base64.b64encode(other).decode(), "image/jpeg"),
    ])
    assert images == [("image/png", _PNG_BYTES), ("image/jpeg", other)]


def test_tool_result_images_defaults_media_type_when_absent():
    block = {"type": "image", "source": {"type": "base64", "data": _PNG_B64}}
    assert _tool_result_images([block]) == [("image/png", _PNG_BYTES)]


def test_tool_result_images_skips_malformed_base64_without_raising():
    good = _image_block()
    bad = _image_block(data="not-valid-base64!!!")
    assert _tool_result_images([bad, good]) == [("image/png", _PNG_BYTES)]


def test_tool_result_images_skips_non_base64_source():
    block = {"type": "image", "source": {"type": "url", "url": "https://example/x.png"}}
    assert _tool_result_images([block]) is None


def test_tool_result_images_skips_when_source_is_not_a_dict():
    # DEFEAT_SHAPES 338 round 5: round 4 closed the TYPE ("base64") half of
    # `if not isinstance(source, dict) or source.get("type") != "base64":` on
    # this same line, but every fixture in the suite carries a dict source
    # (well- or malformed) — the sibling isinstance(source, dict) clause has
    # never had a fixture where it's the ONLY thing that can reject the block.
    # A bare string has no .get method, so if this isinstance check is
    # dead-coded, source.get(...) raises AttributeError instead of the block
    # being skipped cleanly — that's what actually pins the guard.
    block = {"type": "image", "source": "not-a-dict"}
    assert _tool_result_images([block]) is None


def test_tool_result_images_source_type_discriminator_rejects_non_base64_even_with_data():
    # The url-source fixture above carries no "data" key, so a dead-coded
    # `source.get("type") != "base64"` check would still be rejected by the
    # later `if not data` check — the base64 discriminator is never actually
    # exercised as the reason the block is skipped. Give it a valid "data" key
    # so ONLY the source-type check can be why this is rejected.
    block = {"type": "image", "source": {"type": "url", "data": _PNG_B64}}
    assert _tool_result_images([block]) is None


def test_tool_result_images_skips_blocks_missing_data():
    block = {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": ""}}
    assert _tool_result_images([block]) is None


def test_tool_result_images_ignores_non_dict_items():
    assert _tool_result_images(["a string entry", 42]) is None


# --------------------------------------------------------------------------
# parse_entries wiring — a tool_result record produces Message.images
# --------------------------------------------------------------------------

def _tool_result_entry(tool_id: str, content: list) -> dict:
    return {
        "type": "user",
        "timestamp": "t",
        "message": {
            "content": [{"type": "tool_result", "tool_use_id": tool_id, "content": content}]
        },
    }


def _tool_use_entry(tool_id: str, name: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": "t",
        "message": {"content": [{"type": "tool_use", "id": tool_id, "name": name, "input": {}}]},
    }


def test_parse_entries_carries_images_on_the_tool_result_message():
    entries = [
        _tool_use_entry("tu_1", "Screenshot"),
        _tool_result_entry("tu_1", [_image_block()]),
    ]
    events, _ = parse_entries(entries)
    result = [m for m in events if m.content_type == "tool_result"][0]
    assert result.tool_name == "Screenshot"
    assert result.images == [("image/png", _PNG_BYTES)]
    assert result.text == ""  # no text block in this result


def test_parse_entries_keeps_text_and_images_together():
    entries = [
        _tool_use_entry("tu_2", "Screenshot"),
        _tool_result_entry("tu_2", [_text_block("captured"), _image_block()]),
    ]
    events, _ = parse_entries(entries)
    result = [m for m in events if m.content_type == "tool_result"][0]
    assert result.text == "captured"
    assert result.images == [("image/png", _PNG_BYTES)]


def test_parse_entries_text_only_tool_result_has_no_images():
    # The MUST-BE-ACCEPTED-UNCHANGED guard: a text-only tool_result carries
    # images=None, exactly as it did before this block existed.
    entries = [
        _tool_use_entry("tu_3", "Bash"),
        _tool_result_entry("tu_3", [_text_block("exit 0")]),
    ]
    events, _ = parse_entries(entries)
    result = [m for m in events if m.content_type == "tool_result"][0]
    assert result.text == "exit 0"
    assert result.images is None


def test_message_images_defaults_to_none():
    assert Message("assistant", "text", "hi").images is None


# --------------------------------------------------------------------------
# skill-body suppression (CMX-348) — a Skill invocation's full body arrives
# as a SEPARATE synthetic "isMeta" user record, keyed back to the tool_use
# via "sourceToolUseID" (not the usual tool_result "tool_use_id"). Relayed
# verbatim this is a 100K+ char wall of text; parse_entries must replace it
# with a short "Loaded skill: <name>" event instead.
# --------------------------------------------------------------------------

def _skill_tool_use_entry(tool_id: str, skill: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": "t",
        "message": {
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "Skill", "input": {"skill": skill}}
            ]
        },
    }


def _skill_tool_result_entry(tool_id: str) -> dict:
    return {
        "type": "user",
        "timestamp": "t",
        "message": {
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": "Launching skill: irrelevant",
                }
            ]
        },
    }


def _skill_body_entry(tool_id: str, body: str) -> dict:
    return {
        "type": "user",
        "isMeta": True,
        "sourceToolUseID": tool_id,
        "timestamp": "t",
        "message": {"content": [{"type": "text", "text": body}]},
    }


def _non_meta_source_id_entry(tool_id: str, body: str) -> dict:
    # Same sourceToolUseID shape as a real skill-body record, but NOT isMeta —
    # an ordinary user turn that merely happens to carry a sourceToolUseID.
    return {
        "type": "user",
        "isMeta": False,
        "sourceToolUseID": tool_id,
        "timestamp": "t",
        "message": {"content": [{"type": "text", "text": body}]},
    }


def _bash_tool_use_entry(tool_id: str) -> dict:
    return {
        "type": "assistant",
        "timestamp": "t",
        "message": {
            "content": [
                {"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": "ls"}}
            ]
        },
    }


def test_skill_body_is_replaced_with_a_short_name_marker():
    big_body = "Base directory for this skill: /x/y\n\n# Some Skill\n\n" + ("word " * 50_000)
    entries = [
        _skill_tool_use_entry("tu_1", "superpowers:brainstorming"),
        _skill_tool_result_entry("tu_1"),
        _skill_body_entry("tu_1", big_body),
    ]
    events, _ = parse_entries(entries)
    user_texts = [m for m in events if m.role == "user" and m.content_type == "text"]
    assert len(user_texts) == 1
    assert user_texts[0].text == "Loaded skill: superpowers:brainstorming"


def test_skill_body_pending_survives_across_poll_cycles():
    # The tool_use and its result land in one poll; the body arrives in the
    # NEXT one — mirrors CMX-348's actual timing and the existing
    # cross-cycle tool-pairing contract this module already relies on.
    events1, pending = parse_entries(
        [_skill_tool_use_entry("tu_2", "handoff"), _skill_tool_result_entry("tu_2")]
    )
    assert not [m for m in events1 if m.role == "user" and m.content_type == "text"]

    events2, pending = parse_entries([_skill_body_entry("tu_2", "# Handoff\n\nbody")], pending)
    user_texts = [m for m in events2 if m.role == "user" and m.content_type == "text"]
    assert len(user_texts) == 1
    assert user_texts[0].text == "Loaded skill: handoff"
    assert "tu_2" not in pending


def test_missing_pending_entry_for_source_id_is_suppressed_as_unknown_skill():
    # A sourceToolUseID that resolves to NOTHING in ``pending`` (e.g. the
    # originating tool_use fell outside this read window — the transcript
    # monitor skipping to EOF on a large file loses it exactly this way, see
    # CMX-348) must still be suppressed, not relayed raw: isMeta +
    # sourceToolUseID together already identify this as tool-injected
    # synthetic content (almost always a skill body, sometimes 100K+ chars),
    # so there is no safe fallback that relays it.
    big_body = "word " * 50_000
    entries = [_skill_body_entry("tu_unknown", big_body)]
    events, _ = parse_entries(entries)
    user_texts = [m for m in events if m.role == "user" and m.content_type == "text"]
    assert len(user_texts) == 1
    assert user_texts[0].text == "Loaded skill: unknown"


def test_isMeta_without_source_id_relays_in_full():
    # isMeta alone (no sourceToolUseID) is the shape of a cross-session peer
    # notification, not a skill body — it must relay in full. Guards against
    # a future refactor collapsing the gate to ``if data.get("isMeta"):``
    # alone, which would silently swallow every peer message too.
    entries = [
        {
            "type": "user",
            "isMeta": True,
            "timestamp": "t",
            "message": {"content": [{"type": "text", "text": "peer notice: hello"}]},
        }
    ]
    events, _ = parse_entries(entries)
    user_texts = [m for m in events if m.role == "user" and m.content_type == "text"]
    assert len(user_texts) == 1
    assert user_texts[0].text == "peer notice: hello"


def test_non_meta_source_id_is_relayed_normally_not_swallowed():
    # A NON-meta user record that happens to carry a sourceToolUseID matching
    # a real pending Skill tool_use must still relay as ordinary user text —
    # the swallow requires isMeta True, not just a matching sourceToolUseID.
    entries = [
        _skill_tool_use_entry("tu_5", "orchestrate"),
        _non_meta_source_id_entry("tu_5", "just a regular message"),
    ]
    events, pending = parse_entries(entries)
    user_texts = [m for m in events if m.role == "user" and m.content_type == "text"]
    assert len(user_texts) == 1
    assert user_texts[0].text == "just a regular message"
    # Not popped: the swallow condition never fired, so the Skill entry is
    # still waiting for its (separate) isMeta body record.
    assert "tu_5" in pending


def test_source_id_resolving_to_non_skill_pending_entry_falls_through():
    # A sourceToolUseID whose pending entry resolves to some OTHER tool (not
    # "Skill") must fall through to ordinary user-text handling, not be
    # swallowed as if it were a skill body.
    entries = [
        _bash_tool_use_entry("tu_6"),
        _skill_body_entry("tu_6", "hello from a non-skill source"),
    ]
    events, _ = parse_entries(entries)
    user_texts = [m for m in events if m.role == "user" and m.content_type == "text"]
    assert len(user_texts) == 1
    assert user_texts[0].text == "hello from a non-skill source"


def test_skill_tool_result_still_carries_its_own_short_text():
    # The Skill tool_result itself ("Launching skill: ...") must keep relaying
    # exactly as before — only the LATER synthetic body record is suppressed.
    entries = [_skill_tool_use_entry("tu_3", "orchestrate"), _skill_tool_result_entry("tu_3")]
    events, _ = parse_entries(entries)
    result = [m for m in events if m.content_type == "tool_result"][0]
    assert result.tool_name == "Skill"
    assert result.text == "Launching skill: irrelevant"


def test_skill_name_falls_back_to_unknown_when_input_has_no_skill_field():
    entries = [
        {
            "type": "assistant",
            "timestamp": "t",
            "message": {
                "content": [{"type": "tool_use", "id": "tu_4", "name": "Skill", "input": {}}]
            },
        },
        _skill_body_entry("tu_4", "# Body"),
    ]
    events, _ = parse_entries(entries)
    user_texts = [m for m in events if m.role == "user" and m.content_type == "text"]
    assert user_texts[0].text == "Loaded skill: unknown"
