"""Inbound media (photo / document) handling — Telegram → tmux.

A photo or file pasted into a bound forum topic is downloaded to
``CHELA_DIR/documents/`` and its saved path forwarded to the topic's tmux window,
so Claude Code can ``Read`` the image or open the file. This is the media
counterpart of the plain-text inbound path in :mod:`chela.telegram.inbound`.

The download/gate/deliver logic lives here as small PTB-free coroutines that
operate on duck-typed ``msg`` / ``PhotoSize`` / ``Document`` objects and injected
``resolve`` (topic → window, the CMX-8 chat/topic gate) and ``deliver``
(window → tmux) callables — so the whole flow is unit-testable with fakes, no
live Telegram and no ``[telegram]`` extra. The thin PTB glue that registers the
``filters.PHOTO`` / ``filters.Document.ALL`` handlers lives in
:mod:`chela.telegram.inbound`.

Adapted from six-ddc/ccbot's ``handlers/document.py`` (MIT). See the top-level
NOTICE file for the upstream copyright and attribution.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# The Telegram Bot API caps bot downloads (getFile) at 20 MB; a larger file
# cannot be fetched and is rejected before we attempt the download.
MAX_FILE_BYTES = 20 * 1024 * 1024

# Media fetches move far more bytes than an ordinary API call, so they get a longer
# read timeout than PTB's 5 s default — a slow link timed out on a small phone
# screenshot (CMX-63).
MEDIA_READ_TIMEOUT = 60.0

# Backoff (seconds) before each RETRY of a transient getFile/download failure;
# its length is the number of retries, so ``len + 1`` attempts in all.
RETRY_BACKOFF = (1.0, 3.0)

# The line :func:`receive_photo` delivers ahead of a downloaded photo's path. Claude
# Code turns that path into an attachment, so the agent's transcript records the turn
# as ``"<caption>\n\n📎 image:"`` + an ``image`` block — and the outbound parser keys
# on this marker to never echo a photo back into the topic it came from (CMX-36).
INBOUND_IMAGE_MARKER = "📎 image:"

# (chat_id, thread_id) -> window_id | None — the router's chat/topic gate.
Resolve = Callable[[object, object], "str | None"]
# (window_id, text) -> ok — the tmux sender (chela.messenger.send_tmux).
Deliver = Callable[[str, str], bool]


def _format_size(num_bytes: int) -> str:
    """Render a byte count as a human-readable size (e.g. '24.3 MB')."""
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


def _safe_filename(name: str) -> str:
    """Sanitise a Telegram-provided filename for safe use as a path component."""
    # Keep only the basename, strip path separators, allow a conservative set.
    name = Path(name).name
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
    # Avoid empty / dotfile-only names.
    return name.strip("._") or "file"


def _largest_photo(photos):
    """The highest-resolution :class:`PhotoSize` in a message, or None.

    Telegram sends a message's photo as a list of sizes; we forward the biggest
    (most detail for the agent), ranked by pixel area then byte size rather than
    trusting list order.
    """
    if not photos:
        return None
    return max(
        photos,
        key=lambda p: (
            (getattr(p, "width", 0) or 0) * (getattr(p, "height", 0) or 0),
            getattr(p, "file_size", 0) or 0,
        ),
    )


def _dest(docs_dir, name: str, clock: Callable[[], float]) -> Path:
    """A unique destination path under ``docs_dir`` for a downloaded file.

    Prefixed with a second-resolution timestamp so re-sends don't clobber, and
    the name is sanitised (Telegram-supplied).
    """
    return Path(docs_dir) / f"{int(clock())}_{_safe_filename(name)}"


async def _reply(msg, text: str) -> None:
    """Best-effort reply into the message's own topic; never wedge the queue."""
    try:
        await msg.reply_text(text)
    except Exception:  # a reply hiccup must not abort the update handler
        log.debug("media reply failed", exc_info=True)


def _class_names(exc: BaseException) -> set[str]:
    """Every class name in ``exc``'s MRO — PTB-free exception matching."""
    return {cls.__name__ for cls in type(exc).__mro__}


def _is_too_big(exc: BaseException) -> bool:
    """Telegram's own "file is too big" refusal (a ``BadRequest``)."""
    return "BadRequest" in _class_names(exc) and "too big" in str(exc).lower()


def _is_transient(exc: BaseException) -> bool:
    """A timeout / network blip worth retrying (and reporting as such).

    PTB's ``BadRequest`` subclasses ``NetworkError`` but is a definitive answer
    from Telegram, so it is never transient.
    """
    names = _class_names(exc)
    if "BadRequest" in names:
        return False
    return bool(names & {"TimedOut", "NetworkError", "TimeoutError"})


def _failure_text(exc: BaseException) -> str:
    """The user-facing reply for a failed fetch, chosen by exception type.

    Only Telegram's explicit "too big" error earns the size message — a timeout
    must never be passed off as the 20 MB cap (CMX-63).
    """
    if _is_too_big(exc):
        return (
            "❌ Could not download the file: it exceeds Telegram's "
            f"{_format_size(MAX_FILE_BYTES)} download limit for bots."
        )
    if _is_transient(exc):
        return "⏳ Download timed out (network). Please resend."
    reason = str(exc) or type(exc).__name__
    return f"❌ Could not download the file ({reason})."


async def _download(msg, tg_media, path: Path) -> "Path | None":
    """Fetch ``tg_media`` to ``path`` (creating ``documents/``), or None on failure.

    A transient failure (timeout / network error) is retried with
    :data:`RETRY_BACKOFF`. A final failure replies with a note naming the real
    cause (see :func:`_failure_text`) and returns None instead of raising, so the
    update handler stays alive.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    attempts = len(RETRY_BACKOFF) + 1
    for attempt in range(1, attempts + 1):
        try:
            tg_file = await tg_media.get_file(read_timeout=MEDIA_READ_TIMEOUT)
            await tg_file.download_to_drive(path, read_timeout=MEDIA_READ_TIMEOUT)
            return path
        except Exception as exc:  # classified below; never wedge the handler
            if _is_transient(exc) and attempt < attempts:
                log.info(
                    "media download attempt %d/%d failed (%s); retrying",
                    attempt, attempts, type(exc).__name__,
                )
                await asyncio.sleep(RETRY_BACKOFF[attempt - 1])
                continue
            log.warning(
                "media download failed after %d attempt(s): %s: %s",
                attempt, type(exc).__name__, exc, exc_info=True,
            )
            await _reply(msg, _failure_text(exc))
            return None
    return None  # unreachable: the loop always returns


async def receive_photo(
    msg,
    chat_id,
    thread_id,
    *,
    resolve: Resolve,
    deliver: Deliver,
    docs_dir,
    clock: Callable[[], float] = time.time,
) -> None:
    """Download a pasted photo and forward its path to the topic's window.

    Gates on ``resolve`` (wrong chat / unbound topic → stay silent, exactly like
    the text path), picks the largest :class:`PhotoSize`, saves it under
    ``docs_dir`` as ``.jpg``, then delivers ``📎 image: <path>`` (with any caption
    prepended) so Claude Code can ``Read`` it by path.
    """
    window_id = resolve(chat_id, thread_id)
    if window_id is None:  # wrong chat / unbound topic — stay silent
        return
    photo = _largest_photo(getattr(msg, "photo", None) or [])
    if photo is None:
        return
    size = getattr(photo, "file_size", None)
    if size and size > MAX_FILE_BYTES:
        await _reply(
            msg,
            f"❌ Image is too large ({_format_size(size)}). Telegram only lets "
            f"bots download files up to {_format_size(MAX_FILE_BYTES)}.",
        )
        return
    name = f"{getattr(photo, 'file_unique_id', '') or 'photo'}.jpg"
    saved = await _download(msg, photo, _dest(docs_dir, name, clock))
    if saved is None:
        return
    caption = (getattr(msg, "caption", None) or "").strip()
    line = f"{INBOUND_IMAGE_MARKER} {saved}"
    text = f"{caption}\n\n{line}" if caption else line
    if deliver(window_id, text):
        await _reply(msg, f"📎 Image sent to the agent: {saved.name}")
    else:
        await _reply(msg, "❌ Couldn't deliver the image to the agent.")


async def receive_document(
    msg,
    chat_id,
    thread_id,
    *,
    resolve: Resolve,
    deliver: Deliver,
    docs_dir,
    clock: Callable[[], float] = time.time,
) -> None:
    """Download a pasted file and forward its path to the topic's window.

    Same flow as :func:`receive_photo`, but preserves the original filename and
    rejects a file whose advertised size exceeds Telegram's 20 MB bot cap
    *before* downloading (the doc-specific guard from ccbot).
    """
    window_id = resolve(chat_id, thread_id)
    if window_id is None:  # wrong chat / unbound topic — stay silent
        return
    doc = getattr(msg, "document", None)
    if doc is None:
        return
    size = getattr(doc, "file_size", None)
    if size and size > MAX_FILE_BYTES:
        await _reply(
            msg,
            f"❌ File is too large ({_format_size(size)}). Telegram only lets "
            f"bots download files up to {_format_size(MAX_FILE_BYTES)}.",
        )
        return
    original = getattr(doc, "file_name", None) or getattr(doc, "file_unique_id", "") or "file"
    saved = await _download(msg, doc, _dest(docs_dir, original, clock))
    if saved is None:
        return
    caption = (getattr(msg, "caption", None) or "").strip()
    text = f"{caption}\n\n📎 file: {saved}" if caption else f"📎 file: {saved}"
    if deliver(window_id, text):
        await _reply(msg, f"📎 File sent to the agent: {getattr(doc, 'file_name', None) or saved.name}")
    else:
        await _reply(msg, "❌ Couldn't deliver the file to the agent.")
