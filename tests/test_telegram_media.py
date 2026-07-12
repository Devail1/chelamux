"""Inbound media (photo / document) handling — the PTB-free download flow.

Drives :mod:`chela.telegram.media` with duck-typed fake ``msg`` / ``PhotoSize`` /
``Document`` objects and injected ``resolve`` / ``deliver`` callables, so the
gate → pick-largest → download → forward-path contract is locked in without a
live Telegram or the ``[telegram]`` extra. The async coroutines are exercised
with ``asyncio.run`` (the suite has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio

from chela.telegram import media


class _FakeFile:
    """A Telegram File whose ``download_to_drive`` writes a placeholder byte."""

    def __init__(self, downloads: list):
        self._downloads = downloads

    async def download_to_drive(self, path) -> None:
        self._downloads.append(str(path))
        with open(path, "wb") as fh:
            fh.write(b"x")


class _FakePhoto:
    def __init__(self, width, height, *, file_size=None, uid="ph", downloads=None,
                 fail=False):
        self.width = width
        self.height = height
        self.file_size = file_size
        self.file_unique_id = uid
        self._downloads = downloads if downloads is not None else []
        self._fail = fail
        self.get_file_calls = 0

    async def get_file(self):
        self.get_file_calls += 1
        if self._fail:
            raise RuntimeError("getFile rejected (too big)")
        return _FakeFile(self._downloads)


class _FakeDoc:
    def __init__(self, file_name=None, *, file_size=None, uid="doc", downloads=None,
                 fail=False):
        self.file_name = file_name
        self.file_size = file_size
        self.file_unique_id = uid
        self._downloads = downloads if downloads is not None else []
        self._fail = fail
        self.get_file_calls = 0

    async def get_file(self):
        self.get_file_calls += 1
        if self._fail:
            raise RuntimeError("getFile rejected")
        return _FakeFile(self._downloads)


class _FakeMsg:
    def __init__(self, *, photo=None, document=None, caption=None):
        self.photo = photo
        self.document = document
        self.caption = caption
        self.replies: list[str] = []

    async def reply_text(self, text: str) -> None:
        self.replies.append(text)


class _Deliver:
    """Records ``(window_id, text)`` deliveries; return value configurable."""

    def __init__(self, ok: bool = True):
        self.ok = ok
        self.calls: list[tuple[str, str]] = []

    def __call__(self, window_id: str, text: str) -> bool:
        self.calls.append((window_id, text))
        return self.ok


def _bound(_chat, _thread):
    return "@5"


def _unbound(_chat, _thread):
    return None


_CLOCK = lambda: 1000  # noqa: E731 — deterministic timestamp for filenames


# --------------------------------------------------------------------------
# Pure helpers
# --------------------------------------------------------------------------

def test_largest_photo_picks_highest_resolution():
    small = _FakePhoto(90, 90)
    big = _FakePhoto(1280, 720)
    mid = _FakePhoto(320, 240)
    assert media._largest_photo([small, big, mid]) is big
    assert media._largest_photo([]) is None


def test_safe_filename_strips_paths_and_specials():
    assert media._safe_filename("../../etc/passwd") == "passwd"
    assert media._safe_filename("my report (final).pdf") == "my_report__final_.pdf"
    assert media._safe_filename("...") == "file"


def test_format_size_is_human_readable():
    assert media._format_size(20 * 1024 * 1024) == "20.0 MB"


# --------------------------------------------------------------------------
# receive_photo
# --------------------------------------------------------------------------

def test_photo_downloads_largest_and_forwards_path(tmp_path):
    downloads: list[str] = []
    photo = _FakePhoto(1280, 720, uid="BIG", downloads=downloads)
    msg = _FakeMsg(photo=[_FakePhoto(90, 90, uid="SMALL", downloads=downloads), photo])
    deliver = _Deliver()
    asyncio.run(media.receive_photo(
        msg, 777, 4, resolve=_bound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    # Only the largest size was fetched, once.
    assert photo.get_file_calls == 1
    # It landed under the documents dir with the timestamped, .jpg name.
    assert len(downloads) == 1
    saved = downloads[0]
    assert saved.startswith(str(tmp_path))
    assert saved.endswith("1000_BIG.jpg")
    # The window (resolved from the topic) got the path, prefixed for Claude Code.
    assert deliver.calls == [("@5", f"📎 image: {saved}")]


def test_photo_prepends_caption(tmp_path):
    photo = _FakePhoto(800, 600)
    msg = _FakeMsg(photo=[photo], caption="look at this bug")
    deliver = _Deliver()
    asyncio.run(media.receive_photo(
        msg, 777, 4, resolve=_bound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    window, text = deliver.calls[0]
    assert text.startswith("look at this bug\n\n📎 image: ")


def test_photo_from_unbound_topic_is_dropped_without_download(tmp_path):
    photo = _FakePhoto(800, 600)
    msg = _FakeMsg(photo=[photo])
    deliver = _Deliver()
    asyncio.run(media.receive_photo(
        msg, 777, 4, resolve=_unbound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    assert photo.get_file_calls == 0
    assert deliver.calls == []
    assert msg.replies == []  # gated out — stay silent
    assert list(tmp_path.iterdir()) == []


def test_oversized_photo_rejected_before_download(tmp_path):
    photo = _FakePhoto(4000, 3000, file_size=media.MAX_FILE_BYTES + 1)
    msg = _FakeMsg(photo=[photo])
    deliver = _Deliver()
    asyncio.run(media.receive_photo(
        msg, 777, 4, resolve=_bound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    assert photo.get_file_calls == 0
    assert deliver.calls == []
    assert msg.replies and "too large" in msg.replies[0]


# --------------------------------------------------------------------------
# receive_document
# --------------------------------------------------------------------------

def test_document_downloads_and_preserves_name(tmp_path):
    downloads: list[str] = []
    doc = _FakeDoc("report.pdf", downloads=downloads)
    msg = _FakeMsg(document=doc)
    deliver = _Deliver()
    asyncio.run(media.receive_document(
        msg, 777, 4, resolve=_bound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    assert doc.get_file_calls == 1
    assert downloads[0].endswith("1000_report.pdf")
    assert deliver.calls == [("@5", f"📎 file: {downloads[0]}")]
    assert msg.replies and "report.pdf" in msg.replies[0]


def test_oversized_document_rejected_before_download(tmp_path):
    doc = _FakeDoc("huge.zip", file_size=media.MAX_FILE_BYTES + 1)
    msg = _FakeMsg(document=doc)
    deliver = _Deliver()
    asyncio.run(media.receive_document(
        msg, 777, 4, resolve=_bound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    assert doc.get_file_calls == 0
    assert deliver.calls == []
    assert msg.replies and "too large" in msg.replies[0]


def test_document_from_unbound_topic_is_dropped(tmp_path):
    doc = _FakeDoc("report.pdf")
    msg = _FakeMsg(document=doc)
    deliver = _Deliver()
    asyncio.run(media.receive_document(
        msg, 777, 4, resolve=_unbound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    assert doc.get_file_calls == 0
    assert deliver.calls == []
    assert msg.replies == []


def test_download_failure_replies_and_does_not_deliver(tmp_path):
    # A getFile that raises (e.g. an under-reported oversized file) must not crash
    # and must not forward a path to the agent.
    doc = _FakeDoc("sneaky.bin", fail=True)
    msg = _FakeMsg(document=doc)
    deliver = _Deliver()
    asyncio.run(media.receive_document(
        msg, 777, 4, resolve=_bound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    assert deliver.calls == []
    assert msg.replies and "Could not download" in msg.replies[0]


def test_delivery_failure_is_reported(tmp_path):
    doc = _FakeDoc("report.pdf")
    msg = _FakeMsg(document=doc)
    deliver = _Deliver(ok=False)
    asyncio.run(media.receive_document(
        msg, 777, 4, resolve=_bound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    assert len(deliver.calls) == 1
    assert msg.replies and "Couldn't deliver" in msg.replies[0]
