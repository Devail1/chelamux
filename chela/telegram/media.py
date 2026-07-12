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

import logging
import re
import time
from pathlib import Path
from typing import Callable

log = logging.getLogger(__name__)

# The Telegram Bot API caps bot downloads (getFile) at 20 MB; a larger file
# cannot be fetched and is rejected before we attempt the download.
MAX_FILE_BYTES = 20 * 1024 * 1024

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


async def _download(msg, tg_media, path: Path) -> "Path | None":
    """Fetch ``tg_media`` to ``path`` (creating ``documents/``), or None on failure.

    A failed ``getFile``/download (e.g. a file over Telegram's 20 MB bot cap that
    slipped the up-front size check) replies with a friendly note and returns None
    instead of raising, so the update handler stays alive.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        tg_file = await tg_media.get_file()
        await tg_file.download_to_drive(path)
    except Exception:  # BadRequest for oversized files, or any transient fetch error
        log.warning("media download failed", exc_info=True)
        await _reply(
            msg,
            "❌ Could not download the file. It may exceed Telegram's "
            f"{_format_size(MAX_FILE_BYTES)} download limit for bots.",
        )
        return None
    return path


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
    text = f"{caption}\n\n📎 image: {saved}" if caption else f"📎 image: {saved}"
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
