"""Incremental transcript monitor — the outbound-relay foundation.

Given a set of tmux window ids (``@N``), each poll resolves every window to its
live Claude Code JSONL transcript, reads only the bytes appended since the last
poll (tracked by byte offset per transcript), parses the new records — pairing
``tool_use`` with ``tool_result`` across cycles — and emits each new message via
a callback. This slice does NOT send to Telegram and does NOT import
``python-telegram-bot``; it just turns JSONL into parsed :class:`Message` events.

**Window → transcript resolution goes through chela's own layer**
(``discovery.get_window_cwd_by_id`` → ``transcripts.transcript_for_cwd``): the
tmux pane's cwd names the ``~/.claude/projects/<encoded-cwd>/`` directory and the
most-recently-written ``*.jsonl`` in it is the live session. There is no
``session_map.json`` / ``SessionStart`` hook here — tmux is the source of truth.

Adapted from six-ddc/ccbot's ``session_monitor.py``
(https://github.com/six-ddc/ccbot, MIT) — reworked onto chela's discovery layer
and made synchronous (no aiofiles / asyncio). See the top-level NOTICE file for
upstream attribution.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable

from chela import discovery, transcripts
from chela.telegram.parser import Message, _Pending, parse_entries, parse_line

log = logging.getLogger(__name__)

# A resolver maps a window id (``@N``) to its live transcript path (or None).
Resolver = Callable[[str], "Path | None"]


def _default_resolver(base: Path | None) -> Resolver:
    """Resolve window id → transcript via chela's discovery/transcripts layer."""

    def resolve(window_id: str) -> Path | None:
        cwd = discovery.get_window_cwd_by_id(window_id)
        return transcripts.transcript_for_cwd(cwd, base=base)

    return resolve


class _Tracked:
    """Per-window read state: which transcript, how far read, pending tools."""

    __slots__ = ("path", "offset", "pending")

    def __init__(self, path: Path, offset: int):
        self.path = path
        self.offset = offset
        self.pending: dict[str, _Pending] = {}


class TranscriptMonitor:
    """Polls tmux windows' transcripts and emits new messages via a callback.

    Usage::

        mon = TranscriptMonitor(on_message=lambda w, m: ...)
        mon.poll(["@1", "@3"])   # call on an interval; only new lines are read

    ``on_message`` is invoked ``(window_id, message)`` for every new event, in
    file order. On first sight of a window's transcript the monitor seeks to
    end-of-file (``start_at_eof=True``, the default) so pre-existing history is
    not replayed as new; pass ``start_at_eof=False`` to read from the beginning
    (used by tests and one-shot backfills).
    """

    def __init__(
        self,
        on_message: Callable[[str, Message], None],
        *,
        start_at_eof: bool = True,
        base: Path | None = None,
        resolver: Resolver | None = None,
    ):
        self._on_message = on_message
        self._start_at_eof = start_at_eof
        self._resolver = resolver or _default_resolver(base)
        # window_id (@N) -> tracked read state
        self._tracked: dict[str, _Tracked] = {}

    def poll(self, window_ids) -> None:
        """Read new transcript lines for each window id and emit their events."""
        for wid in window_ids:
            try:
                self._poll_window(wid)
            except OSError as e:
                log.warning("transcript monitor: %s read failed: %s", wid, e)

    def forget(self, window_id: str) -> None:
        """Drop tracking for a window (e.g. after it closes)."""
        self._tracked.pop(window_id, None)

    # -- internals ---------------------------------------------------------

    def _poll_window(self, window_id: str) -> None:
        path = self._resolver(window_id)
        if path is None or not path.exists():
            return

        tracked = self._tracked.get(window_id)
        if tracked is None or tracked.path != path:
            # First sight of this window, or its transcript rotated (--resume,
            # a new session in the same cwd). Start fresh at EOF so we relay
            # only what is written from here on, never the existing backlog.
            start = path.stat().st_size if self._start_at_eof else 0
            tracked = _Tracked(path, start)
            self._tracked[window_id] = tracked

        entries, new_offset = self._read_new(path, tracked.offset)
        tracked.offset = new_offset
        if not entries:
            return

        events, tracked.pending = parse_entries(entries, tracked.pending)
        for msg in events:
            self._on_message(window_id, msg)

    @staticmethod
    def _read_new(path: Path, offset: int) -> tuple[list[dict], int]:
        """Read complete JSONL records appended after ``offset``.

        Returns ``(records, new_offset)``. The offset only advances past lines
        that parsed as JSON — a trailing partial write (no newline yet) leaves
        the offset before it so the record is picked up whole next poll. A file
        shorter than ``offset`` (truncated, e.g. after ``/clear``) resets to 0.
        """
        size = path.stat().st_size
        if offset > size:
            offset = 0  # truncated / rotated in place — re-read from the top

        records: list[dict] = []
        safe = offset
        with path.open("rb") as f:
            f.seek(offset)
            for raw in f:
                if not raw.endswith(b"\n"):
                    break  # partial trailing write; wait for the rest next poll
                safe += len(raw)
                obj = parse_line(raw.decode("utf-8", "replace"))
                if obj is not None:
                    records.append(obj)
        return records, safe
