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


def test_tool_result_images_returns_none_for_string_content():
    assert _tool_result_images("plain string result") is None


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
