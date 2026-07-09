"""Time-based task scheduler.

Persists scheduled tasks in ``~/.chela/scheduler.db`` and, on each ``tick()``,
sends the prompt of any due task to its agent's tmux window. Three schedule
types: ``interval`` ("15m"), ``cron`` ("0 */8 * * *"), and ``once`` (an ISO
timestamp, fired a single time then disabled).
"""
from __future__ import annotations
import logging
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone

from croniter import croniter

from chela.config import CHELA_DIR
from chela.discovery import get_window_id
from chela.messenger import send_tmux
from chela.models import ScheduledTask

log = logging.getLogger(__name__)

DB_PATH = CHELA_DIR / "scheduler.db"

INTERVAL_RE = re.compile(r"^(\d+)(s|m|h|d)$")
INTERVAL_MULTIPLIERS = {"s": 1, "m": 60, "h": 3600, "d": 86400}

_SCHEMA = """
    CREATE TABLE IF NOT EXISTS tasks (
        id INTEGER PRIMARY KEY,
        agent_name TEXT NOT NULL,
        schedule_type TEXT NOT NULL,
        schedule_value TEXT NOT NULL,
        prompt TEXT NOT NULL,
        enabled INTEGER DEFAULT 1,
        last_run TEXT,
        next_run TEXT,
        created_at TEXT NOT NULL
    )
"""

# One shared, WAL-mode connection reused for the process lifetime, guarded by a
# lock. This replaces the old open-a-new-conn-per-call design that (1) ran
# CREATE TABLE + commit on every read, (2) leaked file descriptors whenever a
# call raised between connect() and close(), and (3) had many short-lived
# writers — which, combined with accidental second dashboard processes, kept
# corrupting scheduler.db. WAL tolerates concurrent readers/one writer and
# survives a crashed writer far better than the default rollback journal.
_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    CHELA_DIR.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: the single conn is shared across the Flask request
    # threads and the scheduler poll thread — every access is serialized by _lock.
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(_SCHEMA)   # schema init happens ONCE, here
    conn.commit()
    return conn


def _get_db() -> sqlite3.Connection:
    """Return the shared connection, opening it on first use. Callers MUST hold
    ``_lock`` (schema init + all statements run under it)."""
    global _conn
    if _conn is None:
        _conn = _connect()
    return _conn


def init() -> None:
    """Open the connection and initialize the schema once, at startup, so the
    first request never pays for (or races on) lazy init. Safe to call twice."""
    with _lock:
        _get_db()


def parse_interval_seconds(value: str) -> int:
    """Parse interval string like '30s', '5m', '1h', '8h', '1d' to seconds."""
    m = INTERVAL_RE.match(value)
    if not m:
        raise ValueError(f"Invalid interval: {value}")
    return int(m.group(1)) * INTERVAL_MULTIPLIERS[m.group(2)]


def compute_next_run(schedule_type: str, schedule_value: str, from_time: datetime | None = None) -> str:
    """Compute the next run time as ISO string."""
    now = from_time or datetime.now(timezone.utc)

    if schedule_type == "interval":
        seconds = parse_interval_seconds(schedule_value)
        return (now + timedelta(seconds=seconds)).isoformat()
    elif schedule_type == "cron":
        cron = croniter(schedule_value, now)
        return cron.get_next(datetime).isoformat()
    elif schedule_type == "once":
        return schedule_value  # already an ISO timestamp
    else:
        raise ValueError(f"Unknown schedule type: {schedule_type}")


def add_task(agent_name: str, schedule_type: str, schedule_value: str, prompt: str) -> int:
    """Add a new scheduled task. Returns task ID."""
    now = datetime.now(timezone.utc)
    next_run = compute_next_run(schedule_type, schedule_value, now)

    with _lock:
        conn = _get_db()
        cursor = conn.execute(
            "INSERT INTO tasks (agent_name, schedule_type, schedule_value, prompt, enabled, next_run, created_at) VALUES (?, ?, ?, ?, 1, ?, ?)",
            (agent_name, schedule_type, schedule_value, prompt, next_run, now.isoformat()),
        )
        conn.commit()
        task_id = cursor.lastrowid
    log.info("Added task %d: %s %s %s -> %s", task_id, agent_name, schedule_type, schedule_value, prompt[:50])
    return task_id


def remove_task(task_id: int) -> bool:
    with _lock:
        conn = _get_db()
        cursor = conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()
        return cursor.rowcount > 0


def set_enabled(task_id: int, enabled: bool) -> bool:
    """Enable/disable a task. Returns True if a row was updated. Routed through
    the shared connection so no code opens its own writer on scheduler.db."""
    with _lock:
        conn = _get_db()
        cursor = conn.execute(
            "UPDATE tasks SET enabled = ? WHERE id = ?", (1 if enabled else 0, task_id)
        )
        conn.commit()
        return cursor.rowcount > 0


def agents_with_enabled_schedules() -> set[str]:
    """Return set of agent names that have at least one enabled schedule."""
    with _lock:
        conn = _get_db()
        rows = conn.execute("SELECT DISTINCT agent_name FROM tasks WHERE enabled = 1").fetchall()
    return {r["agent_name"] for r in rows}


def list_tasks() -> list[ScheduledTask]:
    with _lock:
        conn = _get_db()
        rows = conn.execute("SELECT * FROM tasks ORDER BY id").fetchall()
    return [
        ScheduledTask(
            id=r["id"],
            agent_name=r["agent_name"],
            schedule_type=r["schedule_type"],
            schedule_value=r["schedule_value"],
            prompt=r["prompt"],
            enabled=bool(r["enabled"]),
            last_run=r["last_run"],
            next_run=r["next_run"],
        )
        for r in rows
    ]


def tick() -> int:
    """Check all tasks and execute any that are due. Returns number executed."""
    now = datetime.now(timezone.utc)

    # Snapshot enabled tasks under the lock, then release it before doing any
    # tmux I/O — send_tmux can block, and we must not hold the DB lock (which the
    # wall's /api/agents poll also needs) for its duration.
    with _lock:
        conn = _get_db()
        rows = conn.execute(
            "SELECT * FROM tasks WHERE enabled = 1 AND next_run IS NOT NULL"
        ).fetchall()

    executed = 0
    updates: list[tuple[str, tuple]] = []  # (sql, params) to apply after sends
    for row in rows:
        next_run_str = row["next_run"]
        try:
            next_run = datetime.fromisoformat(next_run_str)
        except (ValueError, TypeError):
            continue

        # Make naive datetimes UTC-aware
        if next_run.tzinfo is None:
            next_run = next_run.replace(tzinfo=timezone.utc)

        if now >= next_run:
            agent_name = row["agent_name"]
            prompt = row["prompt"]
            task_id = row["id"]
            schedule_type = row["schedule_type"]
            schedule_value = row["schedule_value"]

            # Find agent window and send
            window_id = get_window_id(agent_name)
            if window_id:
                success = send_tmux(window_id, prompt)
                if success:
                    log.info("Task %d: sent to %s: %s", task_id, agent_name, prompt[:50])
                    executed += 1
                else:
                    log.error("Task %d: failed to send to %s", task_id, agent_name)
            else:
                log.warning("Task %d: agent %s not found, skipping", task_id, agent_name)

            # Update last_run and next_run (even if send failed, to avoid spam)
            if schedule_type == "once":
                updates.append((
                    "UPDATE tasks SET last_run = ?, enabled = 0, next_run = NULL WHERE id = ?",
                    (now.isoformat(), task_id),
                ))
            else:
                new_next = compute_next_run(schedule_type, schedule_value, now)
                updates.append((
                    "UPDATE tasks SET last_run = ?, next_run = ? WHERE id = ?",
                    (now.isoformat(), new_next, task_id),
                ))

    if updates:
        with _lock:
            conn = _get_db()
            for sql, params in updates:
                conn.execute(sql, params)
            conn.commit()
    return executed
