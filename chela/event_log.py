"""Event log — the durable, ordered place an event can finally LIVE.

chela has never had one. ``chela msg`` / ``broadcast`` fire into a pane and vanish, a
watch exists only as a key in ``inbox.json``, the delivery queue is transient — so the
only record of "what happened" was prose, pasted into a session. That is the root cause
behind the inbox bugs of 2026-07-13: a state that was *inferred* rather than observed
(the false ``DIED``), a snapshot with no expiry (the stale ``awaiting_review``), a
notification that was an essay (no title/payload split). **You cannot render, filter,
thread or reconcile prose.** This module is where a fact goes instead.

**The record is PR #51's inbox event, with an envelope.** ``chela/inbox.py`` already
made an event a record — a ``kind``, a one-line ``summary``, a structured ``payload``
— and that is exactly what is stored here, under the envelope a *log* additionally
needs (``seq`` / ``boot_id`` / ``ts`` / ``wid`` / ``session_id``). The inbox's ``kind``
IS this log's ``type`` (see :func:`from_inbox`); there is deliberately no second event
schema for the two to drift apart on.

**``seq`` is monotonic and survives a restart.** It is allocated from a tiny sidecar
(``events.jsonl.state``) under the append lock, not from a process-local counter — a
counter per process would hand two writers the same number. It is never reset while the
state file lives, so a cursor stays valid across a daemon restart.

**``boot_id`` makes a stale cursor DETECTABLE rather than silently wrong.** It names the
epoch a ``seq`` belongs to, is shared by every writer (it lives in the state file, not
in a process), and changes on exactly the two events that can invalidate a cursor:
:func:`new_boot` at daemon startup (events emitted while the daemon was down were never
appended — a hook fails OPEN rather than wedging an agent, so that window is a genuine
hole), and a lost/corrupt state file (the ``seq`` space itself restarts). A reader that
passes its remembered ``boot_id`` back gets told about the gap instead of quietly
resuming into a different numbering.

**Concurrency: a short-held ``flock``, and nothing slow inside it.** Multiple processes
append at once — the daemon, the dashboard, the CLI, and later one hook call per tool
use. An ``O_APPEND`` write of a short line is atomic on POSIX, but ``seq`` allocation is
a read-modify-write and is not, so the lock is what keeps ``seq`` free of duplicates.
The critical section is: read the sidecar → bump it → write the line. No network, no
tmux, no other file — a writer never blocks on anything but the three small writes, and
:func:`append` never raises into its caller (a hook that crashes stalls a live agent).

**Rotation.** An unbounded append is a disk-filler on a busy fleet, so the file rolls at
``CHELA_EVENTS_MAX_BYTES`` and keeps ``CHELA_EVENTS_KEEP`` rolled files. The read API
serves the current file (through a bounded in-memory ring); the rolled files are the
audit trail. A cursor pointing behind what is still served is reported as a gap.

**A corrupt line is skipped, never fatal.** A crash mid-append leaves a torn final line.
Reads drop unparseable lines and count them (``corrupt_lines``) rather than raising: a
log that refuses to be read is worse than a log with a hole in it.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import time
import uuid
from collections import deque
from contextlib import contextmanager
from pathlib import Path

from chela.config import CHELA_DIR

log = logging.getLogger(__name__)

# Live reads come out of this bounded tail, so a follower never re-reads the whole file
# to learn what just happened. Older events stay in the JSONL (and in the rolled files).
RING_SIZE = int(os.environ.get("CHELA_EVENTS_RING", "2000"))

# Roll the file at this size, keeping this many rolled generations (events.jsonl.1 …).
MAX_BYTES = int(os.environ.get("CHELA_EVENTS_MAX_BYTES", str(8 * 1024 * 1024)))
KEEP_ROTATIONS = int(os.environ.get("CHELA_EVENTS_KEEP", "3"))

# How often --follow re-stats the file.
FOLLOW_INTERVAL = 0.5


def log_path() -> Path:
    return Path(os.environ.get("CHELA_EVENTS_FILE") or (CHELA_DIR / "events.jsonl"))


def _state_path() -> Path:
    p = log_path()
    return p.with_name(p.name + ".state")


def _lock_path() -> Path:
    p = log_path()
    return p.with_name(p.name + ".lock")


# --- the seq/boot state (the sidecar) -------------------------------------------

def _new_boot_id() -> str:
    return uuid.uuid4().hex[:12]


def _max_seq_in_file() -> int:
    """Recover ``seq`` from the log itself when the sidecar is gone.

    The last *valid* line, not the last line: a crash mid-append leaves a torn one.
    """
    return max((e["seq"] for e in _parse(log_path())[0]), default=0)


def _read_state() -> dict:
    """``{"boot_id", "seq"}``. A missing or corrupt sidecar is REBUILT, never fatal.

    Losing the sidecar means the ``seq`` space is being re-derived, which is precisely
    when an old cursor stops meaning what it meant — so the rebuild mints a fresh
    ``boot_id`` and every reader is told about it.
    """
    try:
        state = json.loads(_state_path().read_text())
        if isinstance(state, dict) and isinstance(state.get("seq"), int) and state.get("boot_id"):
            return {"boot_id": str(state["boot_id"]), "seq": int(state["seq"])}
    except (FileNotFoundError, json.JSONDecodeError, OSError, ValueError):
        pass
    return {"boot_id": _new_boot_id(), "seq": _max_seq_in_file()}


def _write_state(state: dict) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(path)                      # atomic: never a half-written sidecar


@contextmanager
def _append_lock():
    """Serialise seq-allocation + the append. Hold NOTHING slow inside this."""
    path = _lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def current_boot() -> str:
    """The epoch a ``seq`` from this log belongs to. A reader remembers it with its cursor."""
    return _read_state()["boot_id"]


def new_boot() -> str:
    """Mint a fresh ``boot_id``, keeping ``seq`` monotonic. Called once at daemon startup.

    ``seq`` deliberately does NOT reset: it is the log's identity and a cursor into the
    file, and restarting it would make two different events share a number. What changes
    is the epoch — the daemon was down, so anything that happened in that window (a hook
    firing at an agent while nothing was listening) never reached the log. A reader that
    sees the ``boot_id`` move knows to distrust its cursor rather than resume across a
    hole it cannot see.
    """
    with _append_lock():
        state = _read_state()
        state["boot_id"] = _new_boot_id()
        _write_state(state)
    return state["boot_id"]


# --- append ---------------------------------------------------------------------

def _rotate_locked(path: Path) -> None:
    """Roll events.jsonl → .1 → .2 … Called under the append lock, so no writer races it."""
    oldest = path.with_name(f"{path.name}.{KEEP_ROTATIONS}")
    oldest.unlink(missing_ok=True)
    for i in range(KEEP_ROTATIONS - 1, 0, -1):
        src = path.with_name(f"{path.name}.{i}")
        if src.exists():
            src.rename(path.with_name(f"{path.name}.{i + 1}"))
    path.rename(path.with_name(f"{path.name}.1"))
    log.info("events: rotated %s (>= %d bytes)", path.name, MAX_BYTES)


def _ends_with_newline(path: Path) -> bool:
    """Is the last line of the log terminated — i.e. did the last writer finish?"""
    try:
        with open(path, "rb") as fh:
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) == b"\n"
    except OSError:
        return True                        # unreadable/empty: assume clean, never block a write


def _encode(record: dict) -> str:
    """One record, one line. A payload that will not serialise loses the payload, not the event."""
    try:
        return json.dumps(record, ensure_ascii=False, default=str) + "\n"
    except (TypeError, ValueError):
        log.warning("events: unserialisable payload on %s — dropping the payload",
                    record.get("type"))
        return json.dumps({**record, "payload": {}}, ensure_ascii=False, default=str) + "\n"


def append(type: str, summary: str = "", payload: dict | None = None, *,
           wid: str | None = None, session_id: str | None = None) -> dict | None:
    """Append one event. THE write path — programmatic, so it needs no agent to test.

    ``type`` is the inbox's ``kind`` (see the module docstring): one event schema, not
    two. ``summary`` is collapsed to a single line — it is what a notification renders;
    the ``payload`` is what a filter, a de-dup or a UI actually works with.

    Returns the stored record, or None if the append failed. It NEVER raises: the next
    caller of this function is a Claude Code hook running synchronously inside a live
    agent, and an exception there stalls or breaks that agent. A lost event is a bug; a
    wedged agent is an outage.
    """
    record = {
        "seq": 0,                          # allocated under the lock, below
        "boot_id": "",
        "ts": time.time(),
        "type": str(type),
        "wid": wid,
        "session_id": session_id,
        "summary": " ".join((summary or "").split()),
        "payload": payload if isinstance(payload, dict) else {},
    }
    path = log_path()
    try:
        with _append_lock():
            state = _read_state()
            record["seq"] = state["seq"] + 1
            record["boot_id"] = state["boot_id"]
            line = _encode(record)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                size = path.stat().st_size
            except FileNotFoundError:
                size = 0
            if size and size + len(line.encode()) > MAX_BYTES:
                _rotate_locked(path)
                size = 0
            if size and not _ends_with_newline(path):
                # The previous writer died mid-line. Without this, our O_APPEND lands
                # flush against that torn tail and CORRUPTS OUR OWN EVENT TOO — one
                # crash would eat two events instead of one, and keep eating them until
                # someone noticed. Close their line; ours then stands on its own.
                line = "\n" + line
                log.warning("events: closing a torn final line (a writer died mid-append)")
            # The sidecar is bumped BEFORE the line lands: a crash in between loses an
            # event (the reader sees the seq hole and says so), where the other order
            # would REUSE a seq — two different events with one identity, which nothing
            # downstream could ever untangle.
            _write_state({"boot_id": record["boot_id"], "seq": record["seq"]})
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(line)             # O_APPEND, one short line: atomic on POSIX
    except OSError as exc:                 # a full disk, a read-only FS, a vanished dir
        log.warning("events: append failed (%s) — event dropped: %s", exc, record["type"])
        return None
    return record


def from_inbox(event: dict) -> dict | None:
    """Store an inbox event (``kind``/``summary``/``payload``) as a log record.

    The seam that keeps the two from drifting: the inbox's record shape goes in
    unmapped, ``kind`` → ``type``, and the log adds only the envelope.
    """
    return append(event.get("kind") or "unknown",
                  event.get("summary") or event.get("text") or "",
                  event.get("payload") or {},
                  wid=event.get("wid"))


# --- read ------------------------------------------------------------------------

_ring: deque[dict] = deque(maxlen=RING_SIZE)
_ring_key: tuple | None = None    # (path, size, mtime_ns) — a path change invalidates too
_ring_dropped = False             # the file held more than the ring can carry
_ring_corrupt = 0


def _parse(path: Path) -> tuple[list[dict], int]:
    """Every valid record in ``path``, plus the number of lines that were not one.

    A torn final line (a crash mid-append) and a garbage line are both simply skipped:
    a log you cannot read at all is worse than a log with a hole in it.
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except (FileNotFoundError, OSError):
        return [], 0
    out: list[dict] = []
    corrupt = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            corrupt += 1
            continue
        if isinstance(rec, dict) and isinstance(rec.get("seq"), int):
            out.append(rec)
        else:
            corrupt += 1
    return out, corrupt


def _refresh_ring() -> None:
    """Re-read the tail only when the file actually changed (size/mtime gate)."""
    global _ring, _ring_key, _ring_dropped, _ring_corrupt
    path = log_path()
    try:
        st = path.stat()
        key = (str(path), st.st_size, st.st_mtime_ns)
    except (FileNotFoundError, OSError):
        key = (str(path), -1, -1)
    if key == _ring_key:
        return
    records, corrupt = _parse(path)
    _ring = deque(records, maxlen=RING_SIZE)
    _ring_dropped = len(records) > RING_SIZE
    _ring_corrupt = corrupt
    _ring_key = key


def ring() -> list[dict]:
    """The bounded in-memory tail of the log (live reads never touch the rolled files)."""
    _refresh_ring()
    return list(_ring)


def _gap(after_seq: int | None, after_boot: str | None, boot: str,
         records: list[dict]) -> dict | None:
    """Why the caller's cursor cannot be trusted — or None if it can.

    Reported, never papered over. Silently resuming across a hole is how a reader ends
    up confidently wrong about what happened.
    """
    if after_boot and after_boot != boot:
        return {"reason": "boot_id changed — the daemon restarted or the log was reset; "
                          "events from that window were never appended",
                "cursor_boot_id": after_boot, "boot_id": boot,
                "after_seq": after_seq, "resume_from_seq": 0}
    if after_seq is None:
        return None
    last = records[-1]["seq"] if records else 0
    if after_seq > last:
        return {"reason": "cursor is ahead of the log — the seq space was reset",
                "boot_id": boot, "after_seq": after_seq, "last_seq": last,
                "resume_from_seq": 0}
    first = records[0]["seq"] if records else 0
    if records and after_seq + 1 < first:
        return {"reason": "events before this point are no longer served "
                          "(rotated out, or past the ring)",
                "boot_id": boot, "after_seq": after_seq, "first_seq": first,
                "resume_from_seq": first - 1}
    return None


def read(after_seq: int | None = None, *, after_boot: str | None = None,
         types: list[str] | None = None, wid: str | None = None,
         limit: int | None = None) -> dict:
    """Replay from a cursor, filtered. The ONE authority the UI and the inbox both read.

    Returns ``{boot_id, events, gap, first_seq, last_seq, next_seq, corrupt_lines}``.
    ``gap`` is non-None when the cursor cannot be honoured (see :func:`_gap`) — the
    events are still returned, but the caller has been TOLD it missed something rather
    than being handed a plausible-looking, wrong continuation.

    Resume from ``next_seq``, never from ``last_seq``: ``limit`` takes the ``limit``
    OLDEST events after the cursor (not the newest), so a bounded read is resumable and
    can never skip an event the caller has not seen. ``next_seq`` accounts for that
    truncation; when nothing was truncated it is the log's own position, so a reader
    filtering on one ``type`` still advances past the events it deliberately dropped
    instead of re-scanning them forever.
    """
    _refresh_ring()
    records = list(_ring)
    boot = current_boot()
    gap = _gap(after_seq, after_boot, boot, records)
    if gap is not None:
        after_seq = gap["resume_from_seq"] or None

    last_seq = records[-1]["seq"] if records else 0
    out = records
    if after_seq is not None:
        out = [e for e in out if e["seq"] > after_seq]
    if types:
        wanted = set(types)
        out = [e for e in out if e.get("type") in wanted]
    if wid:
        out = [e for e in out if e.get("wid") == wid]

    next_seq = last_seq
    if limit is not None and limit >= 0 and len(out) > limit:
        out = out[:limit]
        next_seq = out[-1]["seq"] if out else (after_seq or 0)
    return {
        "boot_id": boot,
        "events": out,
        "gap": gap,
        "first_seq": records[0]["seq"] if records else 0,
        "last_seq": last_seq,
        "next_seq": next_seq,
        "corrupt_lines": _ring_corrupt,
    }


def follow(after_seq: int | None = None, *, after_boot: str | None = None,
           types: list[str] | None = None, wid: str | None = None,
           interval: float = FOLLOW_INTERVAL, iterations: int | None = None):
    """Tail the log: yield each :func:`read` batch that has something new in it.

    ``iterations`` bounds the poll loop (tests pass a number; the CLI passes None and
    runs until interrupted).
    """
    cursor = after_seq
    boot = after_boot
    n = 0
    while iterations is None or n < iterations:
        n += 1
        batch = read(cursor, after_boot=boot, types=types, wid=wid)
        boot = batch["boot_id"]            # a gap is reported once, then we resync
        if batch["events"] or batch["gap"]:
            yield batch
        # Advance even on an empty batch: the events were filtered out, not missed, and
        # a cursor that never moves re-scans the same ring on every poll.
        cursor = batch["next_seq"]
        if iterations is None or n < iterations:
            time.sleep(interval)
