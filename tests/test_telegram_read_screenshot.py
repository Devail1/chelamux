"""CMX-24 — an agent's Read-tool screenshot must relay as a PHOTO, not a 👤 note.

Reproduces the 2026-10-07 report: an agent opened a screenshot with ``Read``
and the Telegram topic got only "👤 [Image: original 1170x2532, displayed at
924x2000. Multiply coordinates by 1.27 to map to original image.]" — no photo.
Two defects, both pinned here against a fixture built from the real transcript
records (the base64 cut down to a 1x1 PNG, the cwd genericised):

* with tool calls hidden (``CHELA_SHOW_TOOL_CALLS`` unset — the default) the
  image-bearing ``tool_result`` was dropped whole, so ``send_photos`` was never
  reached;
* the Read tool's coordinate note arrives as a SEPARATE ``isMeta`` user record
  and relayed as a user turn.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from chela.telegram.bindings import BindingRegistry
from chela.telegram.parser import parse_entries, parse_line
from chela.telegram.relay import RegistryRelay, TelegramRelay

FIXTURE = Path(__file__).parent / "fixtures" / "read_tool_screenshot_2026-10-07.jsonl"


def _entries() -> list[dict]:
    return [e for e in map(parse_line, FIXTURE.read_text().splitlines()) if e]


def _png_bytes() -> bytes:
    tool_result = _entries()[2]["message"]["content"][0]
    return base64.b64decode(tool_result["content"][0]["source"]["data"])


class _Sender:
    def __init__(self):
        self.texts: list[str] = []

    def __call__(self, text, parse_mode, *args, **kw) -> bool:
        self.texts.append(text)
        return True


class _Photos:
    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, images, *args) -> bool:
        self.calls.append((images, args))
        return True


def _relay_fixture(kind: str, show_tool_calls: bool):
    sender, photos = _Sender(), _Photos()
    if kind == "registry":
        reg = BindingRegistry("777")
        reg.bind("@28", "42")
        relay = RegistryRelay(sender, reg, show_tool_calls=show_tool_calls, send_photos=photos)
    else:
        relay = TelegramRelay(sender, show_tool_calls=show_tool_calls, send_photos=photos)
    events, _ = parse_entries(_entries())
    for ev in events:
        relay.on_message("@28", ev)
    return sender, photos


def test_fixture_is_a_real_png_in_the_standard_tool_result_shape():
    assert _png_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_parser_emits_no_user_event_for_the_read_coordinate_note():
    events, _ = parse_entries(_entries())
    assert [e for e in events if e.role == "user"] == []
    assert not any("Multiply coordinates" in e.text for e in events)


def test_parser_strips_the_note_but_keeps_real_user_text_beside_it():
    entry = {
        "type": "user",
        "message": {"content": [
            {"type": "text", "text": "look at this"},
            {"type": "text", "text": "[Image: original 1280x4742, displayed at 540x2000. "
                                     "Multiply coordinates by 2.37 to map to original image.]"},
        ]},
    }
    events, _ = parse_entries([entry])
    assert [(e.role, e.text) for e in events] == [("user", "look at this")]


def test_parser_strips_the_note_from_a_bare_string_content_item():
    # A content LIST may carry raw strings (not text dicts) — a separate path.
    note = ("[Image: original 2880x1200, displayed at 2000x833. "
            "Multiply coordinates by 1.44 to map to original image.]")
    events, _ = parse_entries([{"type": "user", "message": {"content": [note, "hi"]}}])
    assert [(e.role, e.text) for e in events] == [("user", "hi")]


@pytest.mark.parametrize("kind", ["registry", "single"])
@pytest.mark.parametrize("show_tool_calls", [False, True])
def test_read_screenshot_relays_one_photo_and_no_user_note(kind, show_tool_calls):
    sender, photos = _relay_fixture(kind, show_tool_calls)

    assert len(photos.calls) == 1, "the screenshot never reached send_photos"
    images, args = photos.calls[0]
    assert images == [("image/png", _png_bytes())]
    if kind == "registry":
        assert args == ("42",)  # the window's own topic

    for text in sender.texts:
        assert "Multiply coordinates" not in text
        assert "👤" not in text


def test_hidden_tool_calls_send_the_photo_without_tool_text():
    sender, _ = _relay_fixture("registry", show_tool_calls=False)
    assert sender.texts == []  # tool-call noise stays hidden; only the photo posts


def test_fixture_matches_the_reported_record_shapes():
    # Guards the fixture itself against drifting from the real-world shapes
    # the bug report cites (isMeta note record + image tool_result record).
    meta, result = _entries()[1], _entries()[2]
    assert meta["isMeta"] is True and isinstance(meta["message"]["content"], str)
    assert result["toolUseResult"]["file"]["dimensions"]["originalWidth"] == 1170
    assert json.dumps(result).count('"type": "tool_result"') == 1
