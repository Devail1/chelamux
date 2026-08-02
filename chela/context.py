"""Context window usage tracking for agents.

Reads cached status-line JSON files written by a Claude Code status-line script
(see scripts/cache-statusline.sh) and stores snapshots in scheduler.db so the
dashboard can read them instantly without interrupting agents.

Agents' Claude Code status-line scripts cache JSON to
~/.chela/context/{window_name}.json after every assistant message.
"""

import json
import logging
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chela import config, transcripts
from chela.config import CHELA_DIR, CONTEXT_CACHE_DIR

# Context-window size (tokens) assumed when deriving usage from the transcript
# (the fallback): the transcript records token counts but not the window size.
# The statusLine payload, when installed, carries the exact size and overrides
# this. Default 200k; bumped to 1M automatically when observed usage exceeds it.
# A Timing-tab knob (CMX-217) — see config.default_context_window(); read per
# call below, not latched here.

log = logging.getLogger(__name__)

DB_PATH = CHELA_DIR / "scheduler.db"

# Capture interval (seconds) — file reads are cheap, poll every 60s
CONTEXT_CHECK_INTERVAL = 60


def _get_db() -> sqlite3.Connection:
    # Shared file with scheduler/dispatcher — WAL there and here. Callers MUST
    # close the returned connection (use `with closing(_get_db()) as conn:`) so
    # we never leak fds on scheduler.db.
    CHELA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS context_snapshots (
            id INTEGER PRIMARY KEY,
            agent TEXT NOT NULL,
            ts TEXT NOT NULL,
            used_k REAL,
            total_k REAL,
            used_pct REAL,
            messages_k REAL,
            messages_pct REAL,
            free_k REAL,
            free_pct REAL,
            model TEXT,
            cost_usd REAL,
            rate_limit_pct REAL,
            session_name TEXT
        )
    """)
    # Migrate: add columns if missing (existing DBs won't have them)
    for col, typ in [("model", "TEXT"), ("cost_usd", "REAL"), ("rate_limit_pct", "REAL"), ("rate_limit_resets_at", "INTEGER"), ("weekly_rl_pct", "REAL"), ("weekly_rl_resets_at", "INTEGER"), ("session_name", "TEXT")]:
        try:
            conn.execute(f"ALTER TABLE context_snapshots ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError:
            pass  # column already exists
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_ctx_agent_ts ON context_snapshots(agent, ts DESC)
    """)
    conn.commit()
    return conn


def _parse_cache_file(path: Path) -> dict | None:
    """Read and parse a status line cache JSON file."""
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        log.warning("Failed to read cache file %s: %s", path.name, e)
        return None

    ctx = data.get("context_window")
    if not ctx:
        return None

    used_pct = ctx.get("used_percentage")
    window_size = ctx.get("context_window_size")
    if used_pct is None or not window_size:
        return None

    total_k = window_size / 1000
    used_k = round(total_k * used_pct / 100, 1)
    free_pct = ctx.get("remaining_percentage")
    free_k = round(total_k - used_k, 1) if free_pct is not None else None

    # Current usage breakdown (from last API call, may be absent)
    usage = ctx.get("current_usage") or {}
    input_tokens = usage.get("input_tokens")
    messages_k = round(input_tokens / 1000, 1) if input_tokens else None
    messages_pct = round(messages_k / total_k * 100, 1) if messages_k and total_k else None

    # Model name
    model = (data.get("model") or {}).get("display_name")

    # Session cost
    cost_usd = (data.get("cost") or {}).get("total_cost_usd")

    # Rate limits — 5-hour block and 7-day (weekly) block, same shape.
    rate_limit_pct = None
    rate_limit_resets_at = None
    weekly_rl_pct = None
    weekly_rl_resets_at = None
    rl = data.get("rate_limits") or {}
    five_h = rl.get("five_hour") or {}
    if five_h.get("used_percentage") is not None:
        rate_limit_pct = five_h["used_percentage"]
    if five_h.get("resets_at") is not None:
        rate_limit_resets_at = int(five_h["resets_at"])
    seven_d = rl.get("seven_day") or {}
    if seven_d.get("used_percentage") is not None:
        weekly_rl_pct = seven_d["used_percentage"]
    if seven_d.get("resets_at") is not None:
        weekly_rl_resets_at = int(seven_d["resets_at"])

    # Session name
    session_name = data.get("session_name")

    # Git branch — injected by the statusLine hook (not in Claude's payload).
    branch = data.get("branch")

    return {
        "used_k": used_k,
        "total_k": total_k,
        "used_pct": used_pct,
        "messages_k": messages_k,
        "messages_pct": messages_pct,
        "free_k": free_k,
        "free_pct": free_pct,
        "model": model,
        "cost_usd": cost_usd,
        "rate_limit_pct": rate_limit_pct,
        "rate_limit_resets_at": rate_limit_resets_at,
        "weekly_rl_pct": weekly_rl_pct,
        "weekly_rl_resets_at": weekly_rl_resets_at,
        "session_name": session_name,
        "branch": branch,
    }


def capture_all() -> list[dict]:
    """Read cached status line files, parse context data, store in DB."""
    CONTEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    now_ts = time.time()
    results = []
    with closing(_get_db()) as conn:
        # Latest stored ts per agent — lets us skip re-inserting unchanged files.
        latest_ts = {
            row["agent"]: row["max_ts"]
            for row in conn.execute(
                "SELECT agent, MAX(ts) AS max_ts FROM context_snapshots GROUP BY agent"
            )
        }

        for cache_file in CONTEXT_CACHE_DIR.glob("*.json"):
            # Agent name = filename without .json
            agent_name = cache_file.stem

            # Skip stale files (agent likely dead or restarted)
            mtime = cache_file.stat().st_mtime
            if now_ts - mtime > config.cache_stale_seconds():
                continue

            # Stamp ts from the file's mtime (when the agent actually wrote it),
            # not capture time — so "freshest sample" selection in the dashboard
            # reflects real activity instead of every row looking current.
            mtime_iso = datetime.fromtimestamp(mtime, timezone.utc).isoformat()

            # Unchanged file → same mtime → nothing new to record.
            if latest_ts.get(agent_name) == mtime_iso:
                continue

            snap = _parse_cache_file(cache_file)
            if not snap:
                continue

            snap["name"] = agent_name
            snap["ts"] = mtime_iso

            conn.execute(
                "INSERT INTO context_snapshots (agent, ts, used_k, total_k, used_pct, messages_k, messages_pct, free_k, free_pct, model, cost_usd, rate_limit_pct, rate_limit_resets_at, weekly_rl_pct, weekly_rl_resets_at, session_name) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (agent_name, mtime_iso, snap.get("used_k"), snap.get("total_k"), snap.get("used_pct"),
                 snap.get("messages_k"), snap.get("messages_pct"), snap.get("free_k"), snap.get("free_pct"),
                 snap.get("model"), snap.get("cost_usd"), snap.get("rate_limit_pct"), snap.get("rate_limit_resets_at"),
                 snap.get("weekly_rl_pct"), snap.get("weekly_rl_resets_at"), snap.get("session_name")),
            )
            results.append(snap)

        conn.commit()

    if results:
        log.info("Context snapshots captured for %d agents", len(results))

    return results


def prune_snapshots(older_than_days: int = 30) -> int:
    """Delete context_snapshots rows older than `older_than_days`. Returns rows deleted.

    `capture_all` accrues history on a daemon cadence with no natural cap, so this
    keeps scheduler.db bounded — called on its own coarser cadence from the daemon
    loop, independent of how often capture runs.
    """
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=older_than_days)).isoformat()
    with closing(_get_db()) as conn:
        cur = conn.execute("DELETE FROM context_snapshots WHERE ts < ?", (cutoff_iso,))
        conn.commit()
        return cur.rowcount


def get_latest() -> list[dict]:
    """Get most recent context snapshot for each agent from DB. Instant.

    History path: populated by ``capture_all`` (optional). The dashboard reads
    live snapshots via ``live_snapshot`` instead, so the bar never depends on
    the DB being populated.
    """
    with closing(_get_db()) as conn:
        rows = conn.execute("""
            SELECT c.* FROM context_snapshots c
            INNER JOIN (
                SELECT agent, MAX(ts) as max_ts FROM context_snapshots GROUP BY agent
            ) latest ON c.agent = latest.agent AND c.ts = latest.max_ts
        """).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Live snapshots (read on demand — no DB dependency)
#
# The dashboard reads these directly so the context bar works the moment chela
# runs. Per agent we prefer a fresh statusLine cache file (authoritative: exact
# context %, the 5h/7d rate-limit blocks, and cost); when none exists we fall
# back to a coarser context-only estimate derived from the agent's transcript.
# ---------------------------------------------------------------------------

def _cache_snapshot(agent_name: str) -> dict | None:
    """Full snapshot from a fresh statusLine cache file, or None if absent/stale."""
    path = CONTEXT_CACHE_DIR / f"{agent_name}.json"
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return None
    if time.time() - mtime > config.cache_stale_seconds():
        return None
    snap = _parse_cache_file(path)
    if not snap:
        return None
    snap["name"] = agent_name
    snap["ts"] = datetime.fromtimestamp(mtime, timezone.utc).isoformat()
    snap["source"] = "statusline"
    snap["estimated"] = False
    return snap


def _transcript_snapshot(agent_name: str) -> dict | None:
    """Context-only snapshot derived from the agent's active transcript.

    Zero-setup fallback when no statusLine cache exists: no rate-limit/cost data,
    and the window size is estimated (the transcript doesn't record it). The
    window is bumped to 1M when observed usage exceeds the 200k default, so 1M
    sessions don't read as >100%.
    """
    u = transcripts.agent_context_from_transcript(agent_name)
    if not u or not u.get("used_tokens"):
        return None
    used = u["used_tokens"]
    window = config.default_context_window()
    if used > window and used <= 1_000_000:
        window = 1_000_000
    total_k = round(window / 1000, 1)
    used_k = round(used / 1000, 1)
    used_pct = min(100, round(used / window * 100))
    return {
        "name": agent_name,
        "used_k": used_k, "total_k": total_k, "used_pct": used_pct,
        "messages_k": None, "messages_pct": None,
        "free_k": round(total_k - used_k, 1), "free_pct": max(0, 100 - used_pct),
        "model": u.get("model"), "cost_usd": None,
        "rate_limit_pct": None, "rate_limit_resets_at": None,
        "weekly_rl_pct": None, "weekly_rl_resets_at": None,
        "session_name": None, "branch": None, "ts": None,
        "source": "transcript", "estimated": True,
    }


def live_snapshot(agent_name: str) -> dict | None:
    """Best available context snapshot for one agent.

    Fresh statusLine cache (full, authoritative) if present, else a
    transcript-derived estimate, else None.
    """
    return _cache_snapshot(agent_name) or _transcript_snapshot(agent_name)


# ---------------------------------------------------------------------------
# Windowed cost (Today / 7d / 30d) — a period-spend rollup over the history
# `capture_all` accrues in context_snapshots.
#
# cost_usd is CUMULATIVE per session (Claude Code's own running session
# total), and session_name is unique per session — a restarted agent gets a
# new session_name starting near 0. So each session_name's readings are
# MONOTONIC: there are no in-session resets to fight, only session boundaries.
# Windowed spend for one session = max(0, last_cum(<= window_end) -
# last_cum(< window_start)), reading the baseline as 0 when the session has
# no snapshot before window_start (it started inside, or right at, the
# window). An agent (tmux window) can span more than one session within a
# window if it restarted, so we sum across all of an agent's sessions.
# ---------------------------------------------------------------------------

def windowed_cost(window_start: datetime, window_end: datetime) -> list[dict]:
    """Per-agent spend within [window_start, window_end], summed across sessions."""
    start_iso = window_start.isoformat()
    end_iso = window_end.isoformat()

    with closing(_get_db()) as conn:
        rows = conn.execute(
            "SELECT agent, session_name, ts, cost_usd, model FROM context_snapshots "
            "WHERE session_name IS NOT NULL AND ts <= ? "
            "ORDER BY agent, session_name, ts",
            (end_iso,),
        ).fetchall()

    sessions: dict[tuple[str, str], list] = {}
    for r in rows:
        sessions.setdefault((r["agent"], r["session_name"]), []).append(r)

    agent_totals: dict[str, float] = {}
    agent_model: dict[str, str] = {}
    for (agent, _session_name), recs in sessions.items():
        # recs are ts-ascending and already ts <= end_iso (filtered in SQL), so
        # the last cost_usd seen is last_cum(<= window_end), and the last one
        # seen with ts < start_iso is last_cum(< window_start).
        baseline = 0.0
        endval = None
        for r in recs:
            if r["cost_usd"] is None:
                continue
            if r["ts"] < start_iso:
                baseline = r["cost_usd"]
            endval = r["cost_usd"]
            if r["model"]:
                agent_model[agent] = r["model"]
        if endval is None:
            continue
        spend = max(0.0, endval - baseline)
        agent_totals[agent] = agent_totals.get(agent, 0.0) + spend

    return [
        {"name": agent, "model": agent_model.get(agent), "cost_usd": round(total, 2)}
        for agent, total in agent_totals.items()
    ]
