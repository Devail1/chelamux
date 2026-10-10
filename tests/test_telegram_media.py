"""Inbound media (photo / document) handling — the PTB-free download flow.

Drives :mod:`chela.telegram.media` with duck-typed fake ``msg`` / ``PhotoSize`` /
``Document`` objects and injected ``resolve`` / ``deliver`` callables, so the
gate → pick-largest → download → forward-path contract is locked in without a
live Telegram or the ``[telegram]`` extra. The async coroutines are exercised
with ``asyncio.run`` (the suite has no pytest-asyncio).
"""
from __future__ import annotations

import asyncio

import pytest

from chela.telegram import media


class _FakeFile:
    """A Telegram File whose ``download_to_drive`` writes a placeholder byte.

    Records each download's timeout kwargs in ``download_timeouts`` so the file-fetch
    leg's read timeout is as observable as getFile's (the CMX-63 timeout was on it).
    """

    def __init__(self, downloads: list, download_timeouts: list | None = None,
                 download_errors: list | None = None):
        self._downloads = downloads
        self._download_timeouts = download_timeouts if download_timeouts is not None else []
        # Shared, consumed in order across attempts (each attempt gets a fresh File).
        self._download_errors = download_errors if download_errors is not None else []

    async def download_to_drive(self, path, **timeouts) -> None:
        self._download_timeouts.append(timeouts)
        if self._download_errors:
            raise self._download_errors.pop(0)
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

    async def get_file(self, **_timeouts):
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

    async def get_file(self, **_timeouts):
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


# --------------------------------------------------------------------------
# CMX-63 — retry transient failures; report the REAL cause by exception type
# --------------------------------------------------------------------------

# PTB-shaped stand-ins (same class names + hierarchy as ``telegram.error``, where
# BadRequest is a NetworkError subclass) so the suite needs no [telegram] extra.
class NetworkError(Exception):
    pass


class TimedOut(NetworkError):
    pass


class BadRequest(NetworkError):
    pass


class _ScriptedDoc:
    """A Document whose ``get_file`` raises each scripted error in turn, then succeeds."""

    def __init__(self, errors, *, file_name="shot.png", file_size=None, download_errors=()):
        self.file_name = file_name
        self.file_size = file_size
        self.file_unique_id = "doc"
        self._errors = list(errors)
        self.downloads: list[str] = []
        self.get_file_calls = 0
        self.timeouts: list[dict] = []
        self.download_timeouts: list[dict] = []
        self.download_errors = list(download_errors)

    async def get_file(self, **timeouts):
        self.get_file_calls += 1
        self.timeouts.append(timeouts)
        if self._errors:
            raise self._errors.pop(0)
        return _FakeFile(self.downloads, self.download_timeouts, self.download_errors)


@pytest.fixture
def no_backoff(monkeypatch):
    """Record each backoff ``asyncio.sleep`` duration instead of sleeping.

    The shipped ``RETRY_BACKOFF`` stays in force (never zeroed): zeroing it made a
    retry that hammers with ``sleep(0)`` indistinguishable from one that backs off
    (DEFEAT_SHAPES #63c). Only ``media``'s own ``asyncio`` is swapped, so the event
    loop driving the test is untouched.
    """
    sleeps: list[float] = []

    async def _sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(media, "asyncio", type("_Asyncio", (), {"sleep": staticmethod(_sleep)}))
    return sleeps


def _run_doc(doc, tmp_path):
    msg = _FakeMsg(document=doc)
    deliver = _Deliver()
    asyncio.run(media.receive_document(
        msg, 777, 4, resolve=_bound, deliver=deliver, docs_dir=tmp_path, clock=_CLOCK,
    ))
    return msg, deliver


def test_timeout_twice_then_success_delivers(tmp_path, no_backoff):
    doc = _ScriptedDoc([TimedOut("Timed out"), TimedOut("Timed out")])
    msg, deliver = _run_doc(doc, tmp_path)
    assert doc.get_file_calls == 3
    # Each retry waited its scheduled backoff — not an immediate re-hammer.
    assert no_backoff == list(media.RETRY_BACKOFF)
    assert all(d > 0 for d in no_backoff)
    assert len(deliver.calls) == 1 and "📎 file:" in deliver.calls[0][1]
    assert msg.replies == ["📎 File sent to the agent: shot.png"]


def test_media_fetch_uses_longer_read_timeout(tmp_path, no_backoff):
    doc = _ScriptedDoc([])
    _run_doc(doc, tmp_path)
    assert doc.timeouts == [{"read_timeout": media.MEDIA_READ_TIMEOUT}]
    # The download itself (not just getFile) gets the longer timeout: the CMX-63
    # ReadTimeout was on the file fetch.
    assert doc.download_timeouts == [{"read_timeout": media.MEDIA_READ_TIMEOUT}]
    assert media.MEDIA_READ_TIMEOUT > 5  # PTB's default read timeout


def test_persistent_timeout_reports_timeout_not_size(tmp_path, no_backoff):
    doc = _ScriptedDoc([TimedOut("Timed out")] * 10)
    msg, deliver = _run_doc(doc, tmp_path)
    assert doc.get_file_calls == len(media.RETRY_BACKOFF) + 1
    assert no_backoff == list(media.RETRY_BACKOFF)
    assert deliver.calls == []
    assert msg.replies == ["⏳ Download timed out (network). Please resend."]
    assert "limit" not in msg.replies[0]


def test_network_error_is_retried_and_reported_as_network(tmp_path, no_backoff):
    doc = _ScriptedDoc([NetworkError("connection reset")] * 10)
    msg, _ = _run_doc(doc, tmp_path)
    assert doc.get_file_calls == len(media.RETRY_BACKOFF) + 1
    assert msg.replies == ["⏳ Download timed out (network). Please resend."]


def test_bad_request_too_big_reports_size_without_retry(tmp_path, no_backoff):
    doc = _ScriptedDoc([BadRequest("File is too big")] * 10)
    msg, deliver = _run_doc(doc, tmp_path)
    assert doc.get_file_calls == 1  # a definitive answer — never retried
    assert no_backoff == []
    assert deliver.calls == []
    assert len(msg.replies) == 1 and "download limit" in msg.replies[0]


def test_other_bad_request_is_generic_not_size(tmp_path, no_backoff):
    doc = _ScriptedDoc([BadRequest("Wrong file_id")] * 10)
    msg, _ = _run_doc(doc, tmp_path)
    assert doc.get_file_calls == 1
    assert msg.replies == ["❌ Could not download the file (Wrong file_id)."]


def test_download_leg_timeout_is_retried_and_delivers(tmp_path, no_backoff):
    # The CMX-63 ReadTimeout was on the file fetch itself, not on getFile.
    doc = _ScriptedDoc([], download_errors=[TimedOut("Timed out")])
    msg, deliver = _run_doc(doc, tmp_path)
    assert doc.get_file_calls == 2
    assert no_backoff == [media.RETRY_BACKOFF[0]]
    assert len(deliver.calls) == 1
    assert msg.replies == ["📎 File sent to the agent: shot.png"]


def test_known_oversize_in_update_skips_download(tmp_path, no_backoff):
    doc = _ScriptedDoc([], file_size=25 * 1024 * 1024)
    msg, deliver = _run_doc(doc, tmp_path)
    assert doc.get_file_calls == 0
    assert deliver.calls == []
    assert len(msg.replies) == 1 and "too large" in msg.replies[0]


def test_failure_classification_matches_real_ptb_errors():
    error = pytest.importorskip("telegram.error")
    assert media._failure_text(error.TimedOut()).startswith("⏳")
    assert media._failure_text(error.NetworkError("boom")).startswith("⏳")
    assert "download limit" in media._failure_text(error.BadRequest("File is too big"))
    assert "limit" not in media._failure_text(error.BadRequest("Wrong file_id"))


def test_warning_names_the_exception_class(tmp_path, no_backoff, caplog):
    doc = _ScriptedDoc([TimedOut("Timed out")] * 10)
    with caplog.at_level("WARNING", logger=media.log.name):
        _run_doc(doc, tmp_path)
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "TimedOut" in warnings[0]
