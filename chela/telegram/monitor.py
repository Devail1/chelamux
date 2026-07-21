"""Incremental transcript monitor — the outbound-relay foundation.

Given a set of tmux window ids (``@N``), each poll resolves every window to its
live Claude Code JSONL transcript, reads only the bytes appended since the last
poll (tracked by byte offset per transcript), parses the new records — pairing
``tool_use`` with ``tool_result`` across cycles — and emits each new message via
a callback. This slice does NOT send to Telegram and does NOT import
``python-telegram-bot``; it just turns JSONL into parsed :class:`Message` events.

**Window → transcript resolution goes through** :mod:`chela.sessions` **— by
``session_id``, NOT by ``cwd``.** It used to be
``discovery.get_window_cwd_by_id`` → ``transcripts.transcript_for_cwd`` (the newest
``*.jsonl`` under ``~/.claude/projects/<encoded-cwd>/``), and this docstring used to
assert that "tmux is the source of truth" for that mapping. **It is not**, and the
assertion failed live on 2026-07-14: a window rebuilt at one directory running
``claude --resume`` of a session born in another kept its transcript in the ORIGINAL
project dir, the resolver searched an empty one, and the relay went dead for an hour
**without a single log line** — bindings reconciled, topics existed, inbound still
worked. The cwd is now the LAST resort, behind the event log (hook-borne session ids)
and the pane's own ``claude --resume`` command line; see :mod:`chela.sessions` for the
three ways a cwd lies. **A window that resolves to nothing is now LOUD** — a warning
and a ``relay.transcript_missing`` event, not silence.

Adapted from six-ddc/ccbot's ``session_monitor.py``
(https://github.com/six-ddc/ccbot, MIT) — reworked onto chela's discovery layer
and made synchronous (no aiofiles / asyncio). See the top-level NOTICE file for
upstream attribution.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from chela import event_log, sessions
from chela.telegram.parser import Message, _Pending, parse_entries, parse_line

log = logging.getLogger(__name__)

# A resolver maps a window id (``@N``) to its live transcript path (or None).
Resolver = Callable[[str], "Path | None"]

# When a transcript first *appears* or *rotates* while the monitor is already
# running, its bytes are all new (this session produced them) and must be read
# from offset 0. The one exception: a ``--resume`` can repopulate a large prior
# history into a brand-new file — replaying that would spam the topic. So if a
# freshly-appeared/rotated file already exceeds this size on first sight, fall
# back to EOF. A genuine fresh first turn (even one carrying an AskUserQuestion)
# is a few KB; real session backlogs run hundreds of KB, so 64 KiB clears fresh
# turns comfortably while catching repopulated history.
_FRESH_MAX_BYTES = 64 * 1024

# A window that resolves to no transcript is relaying NOTHING, so it is complained about
# on the transition and then re-complained about on this interval — a bound window with no
# transcript is not a passing state, and an outage that only logged once at 03:00 is an
# outage nobody sees at 09:00.
_UNRESOLVED_REPEAT_S = 600


def _default_resolver(base: Path | None) -> Resolver:
    """Resolve window id → transcript by ``session_id`` (see :mod:`chela.sessions`)."""

    def resolve(window_id: str) -> Path | None:
        return sessions.transcript_for_window(window_id, base=base)

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
    file order. The EOF-skip is applied ONLY on a genuine cold-start attach —
    binding a window whose transcript *already existed* on the very first poll,
    whose pre-existing history must not be replayed. A transcript that instead
    *appears* or *rotates* (``/clear`` → new session file) while the monitor is
    running is read from offset 0: its content is all new, so a fresh agent's
    first turn — question included — is relayed even when we bound the window
    before its file existed. ``start_at_eof=False`` (tests, one-shot backfills)
    reads every transcript from the beginning.
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
        self._base = base
        self._resolver = resolver or _default_resolver(base)
        # window_id (@N) -> tracked read state
        self._tracked: dict[str, _Tracked] = {}
        # windows polled while they had NO transcript yet; when one finally
        # resolves, the file that appeared is fresh (read from 0, not EOF).
        self._seen_empty: set[str] = set()
        # window_id -> when we last SAID SO. A window with no transcript relays nothing,
        # and the whole of CMX-70 is that this used to happen in total silence.
        self._complained: dict[str, float] = {}

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
        self._seen_empty.discard(window_id)
        self._complained.pop(window_id, None)

    # -- internals ---------------------------------------------------------

    def _poll_window(self, window_id: str) -> None:
        path = self._resolver(window_id)
        if path is None or not path.exists():
            # No transcript yet — remember it so that when one finally appears we
            # know its whole content is fresh (this session) and read from 0.
            self._seen_empty.add(window_id)
            self._unresolved(window_id, path)
            return
        self._resolved(window_id)

        tracked = self._tracked.get(window_id)
        if tracked is None or tracked.path != path:
            start = self._initial_offset(window_id, path, rotated=tracked is not None)
            tracked = _Tracked(path, start)
            self._tracked[window_id] = tracked
        self._seen_empty.discard(window_id)

        entries, new_offset = self._read_new(path, tracked.offset)
        tracked.offset = new_offset
        if not entries:
            return

        events, tracked.pending = parse_entries(entries, tracked.pending)
        for msg in events:
            self._on_message(window_id, msg)

    def _unresolved(self, window_id: str, path: Path | None) -> None:
        """A bound window with no transcript relays NOTHING. Say so — every time, for as
        long as it lasts.

        This is the whole point of CMX-70. The 2026-07-14 outage was not a crash: the
        resolver returned None, the poll returned, and every health surface stayed green
        while a human sat in front of a live topic getting nothing back. So the failure
        goes to the log AND to the event log (where a UI, the inbox and ``chela doctor``'s
        ``relay.transcripts`` fact can all see it), on the transition and then every
        :data:`_UNRESOLVED_REPEAT_S`.
        """
        now = time.time()
        last = self._complained.get(window_id)
        if last is not None and now - last < _UNRESOLVED_REPEAT_S:
            return
        self._complained[window_id] = now
        why = (f"the resolved transcript {path} does not exist" if path is not None
               else sessions.explain(window_id, base=self._base))
        log.warning(
            "transcript monitor: %s resolves to NO transcript — nothing can be relayed "
            "for it (inbound still works, which is exactly why this is invisible): %s",
            window_id, why,
        )
        event_log.append(
            "relay.transcript_missing",
            f"{window_id} resolves to no transcript — outbound is dead for this window",
            {"wid": window_id, "why": why},
            wid=window_id,
        )

    def _resolved(self, window_id: str) -> None:
        """Close the loop on a complaint: an outage that ends must say that too."""
        if self._complained.pop(window_id, None) is None:
            return
        log.info("transcript monitor: %s resolves to a transcript again", window_id)
        event_log.append(
            "relay.transcript_found",
            f"{window_id} resolves to a transcript again — outbound is live",
            {"wid": window_id},
            wid=window_id,
        )

    def _initial_offset(self, window_id: str, path: Path, *, rotated: bool) -> int:
        """Byte offset to start reading a window's transcript for the first time.

        The whole bugfix lives in one distinction:

        * The transcript **appeared** (``window_id`` was polled before while it
          had no file) or **rotated** (the path changed under a running window)
          → its content is all new, read from **0** so a fresh agent's first
          turn (an ``AskUserQuestion`` / permission gate included) is relayed.
        * The transcript **already existed** on the very first poll of this
          window (a cold-start bind to an agent with prior history) → seek to
          **EOF** so months of backlog are not replayed as new.

        Guard: a ``--resume`` can repopulate a large history into a brand-new
        file; if a freshly-appeared/rotated file is already big, fall back to
        EOF (see :data:`_FRESH_MAX_BYTES`). ``start_at_eof=False`` always reads
        from 0 (tests / one-shot backfills).
        """
        if not self._start_at_eof:
            return 0
        size = path.stat().st_size
        fresh = rotated or window_id in self._seen_empty
        if not fresh:
            return size  # cold-start over pre-existing history — skip backlog
        if size > _FRESH_MAX_BYTES:
            log.info(
                "transcript monitor: %s %s transcript %s already %d bytes on "
                "first sight (> %d) — skipping to EOF instead of replaying",
                window_id, "rotated" if rotated else "appeared", path.name,
                size, _FRESH_MAX_BYTES,
            )
            return size
        return 0

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
