"""CMX-36 — an image pasted into a terminal/wall session relays as a Telegram photo.

Pasting a screenshot into a session (dashboard wall or web terminal) saves it under
``/tmp/chela-paste-images/<sha>.png`` and types the path; Claude Code turns that into
an attachment and writes, measured on a live transcript:

* a ``type:user`` record — ``[{text: "… [Image #47]"}, {image: base64 png}]``;
* two ``type:attachment`` records;
* a ``type:user, isMeta:true`` record — ``[{text: "[Image: source: <path>]"}]``.

The topic used to get the text with NO photo, then a second 👤 message carrying the
meta "[Image: source: …]" line. These tests drive that exact shape through
:func:`parse_entries` and the real :class:`RegistryRelay`, counting what reaches
Telegram.
"""
from __future__ import annotations

import base64

from chela.telegram.bindings import BindingRegistry
from chela.telegram.media import INBOUND_IMAGE_MARKER
from chela.telegram.parser import parse_entries
from chela.telegram.relay import RegistryRelay

_PNG = b"\x89PNG\r\n\x1a\npasted-screenshot"
_PNG_FILE = b"\x89PNG\r\n\x1a\nthe-same-screenshot-on-disk"
_TEXT = "ok, see this … [Image #47]"


def _user_turn(text: str, *, with_block: bool = True) -> dict:
    content = [{"type": "text", "text": text}]
    if with_block:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(_PNG).decode()},
        })
    return {"type": "user", "timestamp": "t", "message": {"role": "user", "content": content}}


def _attachment() -> dict:
    return {"type": "attachment", "timestamp": "t", "attachment": {"type": "image"}}


def _meta_source(path) -> dict:
    return {
        "type": "user", "isMeta": True, "timestamp": "t",
        "message": {"role": "user",
                    "content": [{"type": "text", "text": f"[Image: source: {path}]"}]},
    }


def _paste_fixture(tmp_path, *, with_block: bool = True, text: str = _TEXT) -> list[dict]:
    # The source file EXISTS in every fixture, so a fallback that fired while the
    # base64 block was present would post a second photo — caught by the counts.
    src = tmp_path / "33c2fb611d78ebde.png"
    src.write_bytes(_PNG_FILE)
    return [_user_turn(text, with_block=with_block), _attachment(), _attachment(),
            _meta_source(src)]


class _Telegram:
    """The relay's two sinks — text sends and photo batches — recorded in order."""

    def __init__(self):
        self.texts: list[str] = []
        self.photos: list[tuple[str, bytes]] = []
        self.order: list[str] = []

    def send(self, text, parse_mode, message_thread_id=None, **_kw) -> bool:
        self.texts.append(text)
        self.order.append("text")
        return True

    def send_photos(self, images, message_thread_id=None) -> bool:
        self.photos.extend(images)
        self.order.append("photo")
        return True


def _relay(entries: list[dict]) -> _Telegram:
    tg = _Telegram()
    reg = BindingRegistry("777")
    reg.bind("@1", "42")
    relay = RegistryRelay(tg.send, reg, send_photos=tg.send_photos)
    events, _ = parse_entries(entries)
    for msg in events:
        relay.on_message("@1", msg)
    return tg


def test_pasted_image_relays_as_exactly_one_photo(tmp_path):
    tg = _relay(_paste_fixture(tmp_path))
    assert tg.photos == [("image/png", _PNG)]


def test_pasted_image_turn_text_is_sent_once_before_its_photo(tmp_path):
    tg = _relay(_paste_fixture(tmp_path))
    assert len(tg.texts) == 1
    assert "see this" in tg.texts[0]
    assert tg.order == ["text", "photo"]


def test_image_source_meta_line_never_relays_as_a_user_message(tmp_path):
    tg = _relay(_paste_fixture(tmp_path))
    assert not any("Image: source" in t or "chela-paste" in t or "33c2fb61" in t
                   for t in tg.texts)


def test_other_meta_kinds_do_not_leak_as_user_messages():
    # Measured isMeta kinds besides the image line: scheduled-task prompts and
    # idle notices (plain-string content). All are filtered by the flag.
    entries = [
        {"type": "user", "isMeta": True, "timestamp": "t", "turnOrigin": "scheduled",
         "message": {"role": "user", "content": "Check the self-check log and continue."}},
        {"type": "user", "isMeta": True, "timestamp": "t",
         "message": {"role": "user", "content": "[Cross-session idle notice] x is idle"}},
    ]
    events, _ = parse_entries(entries)
    assert [m for m in events if m.role == "user"] == []


def test_source_path_fallback_when_the_turn_has_no_image_block(tmp_path):
    tg = _relay(_paste_fixture(tmp_path, with_block=False))
    assert tg.photos == [("image/png", _PNG_FILE)]
    assert tg.order == ["text", "photo"]


def test_source_path_fallback_skips_a_missing_file(tmp_path):
    entries = [_user_turn(_TEXT, with_block=False), _meta_source(tmp_path / "gone.png")]
    tg = _relay(entries)
    assert tg.photos == []
    assert len(tg.texts) == 1


def test_source_path_fallback_never_reads_a_non_image_file(tmp_path):
    secret = tmp_path / "notes.txt"
    secret.write_bytes(b"not an image")
    entries = [_user_turn(_TEXT, with_block=False), _meta_source(secret)]
    tg = _relay(entries)
    assert tg.photos == []


def test_meta_source_after_an_assistant_turn_is_not_attached_to_an_older_turn(tmp_path):
    entries = _paste_fixture(tmp_path, with_block=False)
    meta = entries.pop()
    entries.append({"type": "assistant", "timestamp": "t",
                    "message": {"content": [{"type": "text", "text": "looking"}]}})
    entries.append(meta)
    tg = _relay(entries)
    assert tg.photos == []


def test_image_inbound_from_telegram_is_not_echoed(tmp_path):
    # A Telegram photo is delivered as "<caption>\n\n📎 image: <path>"; Claude Code
    # attaches it, so the turn carries an image block — which is already in the topic.
    text = f"[Image #12]what about this?\n\n{INBOUND_IMAGE_MARKER}"
    tg = _relay(_paste_fixture(tmp_path, text=text))
    assert tg.photos == []


def test_image_inbound_from_telegram_is_not_echoed_via_the_source_fallback(tmp_path):
    text = f"[Image #12]what about this?\n\n{INBOUND_IMAGE_MARKER}"
    tg = _relay(_paste_fixture(tmp_path, text=text, with_block=False))
    assert tg.photos == []


def test_image_only_turn_still_relays_its_photo(tmp_path):
    entries = [_user_turn("", with_block=True)]
    tg = _relay(entries)
    assert tg.photos == [("image/png", _PNG)]
    assert len(tg.texts) == 1
    assert "Image" in tg.texts[0]
