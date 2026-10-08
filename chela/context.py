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

from chela import config, dispatcher, transcripts
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


def ensure_schema(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Create/migrate the ``context_snapshots`` table on ``conn``. Idempotent.

    Split out from ``_get_db`` (and named/shaped like
    ``chela.dispatcher.ensure_schema``) so tests can drive it directly against
    a read-only connection.
    """
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
    # Migrate: add columns if missing (existing DBs won't have them).
    #
    # ⛔ #520 (same defect as #515, fixed in dispatcher.ensure_schema by CMX-370):
    # sqlite3.OperationalError is the SAME exception for "duplicate column name: …"
    # (benign — another connection already added it) and "attempt to write a
    # readonly database" (the migration DID NOT HAPPEN — e.g. a sandboxed agent
    # with `~/.chela` denied). Swallowing both meant the second case silently
    # skipped the migration, resurfacing later as an inscrutable `no such column`.
    #
    # Consult PRAGMA table_info first so a column already present is never
    # attempted at all (a readonly, fully-migrated connection issues no DDL and
    # therefore can't fail). A column not listed there that still raises is
    # checked by message as a fallback, for the one case table_info can't rule
    # out: another connection's own ALTER lands between this read and this
    # write and wins the race — genuinely benign. Anything else is the
    # migration having failed and says so loudly via SchemaMigrationError
    # (reused from chela.dispatcher — it must stay a RuntimeError subclass,
    # never an OperationalError one, or this exact except-block elsewhere would
    # re-swallow it and restore #515), chained via `raise ... from e`.
    existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(context_snapshots)")}
    for col, typ in [("model", "TEXT"), ("cost_usd", "REAL"), ("rate_limit_pct", "REAL"), ("rate_limit_resets_at", "INTEGER"), ("weekly_rl_pct", "REAL"), ("weekly_rl_resets_at", "INTEGER"), ("session_name", "TEXT")]:
        if col in existing_columns:
            continue  # already migrated — no DDL attempted, nothing to fail
        try:
            conn.execute(f"ALTER TABLE context_snapshots ADD COLUMN {col} {typ}")
        except sqlite3.OperationalError as e:
            if "duplicate column name" in str(e).lower():
                continue  # lost a race to another connection's own ALTER — benign
            raise dispatcher.SchemaMigrationError(
                f"context_snapshots.{col} is missing and ALTER TABLE failed: {e}"
            ) from e
    return conn


def _get_db() -> sqlite3.Connection:
    # Shared file with scheduler/dispatcher — WAL there and here. Callers MUST
    # close the returned connection (use `with closing(_get_db()) as conn:`) so
    # we never leak fds on scheduler.db.
    CHELA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    ensure_schema(conn)
    # Kept out of ensure_schema: only _get_db's connections are ever writable,
    # so this is the one place that can run a bare (untranslated)
    # CREATE INDEX without risking the same inscrutable OperationalError on a
    # readonly connection that ensure_schema's ALTER-TABLE discrimination
    # exists to eliminate.
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
    _prune_session_cache(older_than_days)
    with closing(_get_db()) as conn:
        cur = conn.execute("DELETE FROM context_snapshots WHERE ts < ?", (cutoff_iso,))
        conn.commit()
        return cur.rowcount


def _prune_session_cache(older_than_days: int) -> None:
    """Drop ``by-session/*.json`` files untouched for ``older_than_days`` (CMX-29).

    One file per Claude session ever run, so unlike ``<window>.json`` (overwritten in
    place) they accrue; the same retention as the snapshot history bounds them.
    """
    cutoff = time.time() - older_than_days * 86400
    try:
        files = list((CONTEXT_CACHE_DIR / SESSION_CACHE_SUBDIR).glob("*.json"))
    except OSError:
        return
    for f in files:
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
        except OSError:
            continue


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

# CMX-29: the hook ALSO writes ``by-session/<session_id>.json``. ``<window>.json`` is
# keyed by the window name the hook resolves from ``$TMUX_PANE`` — and ANY Claude
# process that inherited a pane's environment (a Claude Code background session, a
# subprocess launched from the pane) resolves that same name and overwrites it, so the
# window file alone serves whichever session wrote last. The per-session file cannot
# be clobbered by a different session; the window file is still written (history via
# ``capture_all``, and readers that have no window id) but is only trusted for a
# window when its payload's own ``session_id`` is that window's session.
SESSION_CACHE_SUBDIR = "by-session"


def _session_cache_path(session_id: str) -> Path | None:
    from chela import sessions  # lazy: sessions pulls tmux/process helpers
    if not session_id or not sessions.SESSION_RE.match(session_id):
        return None
    return CONTEXT_CACHE_DIR / SESSION_CACHE_SUBDIR / f"{session_id}.json"


def _payload_session_id(path: Path) -> str | None:
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    sid = data.get("session_id") if isinstance(data, dict) else None
    return sid if isinstance(sid, str) and sid else None


def window_session(window_id: str | None) -> str | None:
    """The session a live window's OWN claude process is running, or None.

    Claude Code's per-pid registry first — a claim the pane's claude process makes about
    itself, so a background session that merely inherited the pane's env cannot speak for
    the window — then :func:`chela.sessions.session_of_window` (event log, ``--resume``).
    """
    if not window_id:
        return None
    from chela import sessions
    try:
        pane = sessions.panes().get(window_id)
        sid = sessions.registry_session(pane.claude_pid) if pane else None
        return sid or sessions.session_of_window(window_id)
    except Exception:  # noqa: BLE001 — a context bar must never 500 the dashboard
        log.debug("window_session(%s) failed", window_id, exc_info=True)
        return None


def _snapshot_from(path: Path, agent_name: str) -> dict | None:
    """Full snapshot from a fresh statusLine cache file, or None if absent/stale."""
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


def _cache_snapshot(agent_name: str, session_id: str | None = None) -> dict | None:
    """Fresh statusLine snapshot for ``agent_name`` — for ``session_id`` when known.

    With a session id, only that session's numbers are served: its ``by-session`` file,
    else the window file if (and only if) its payload names that same session. Without
    one, the window file as-is (the pre-CMX-29 read, for callers with no window id).
    """
    window_file = CONTEXT_CACHE_DIR / f"{agent_name}.json"
    if not session_id:
        return _snapshot_from(window_file, agent_name)
    own = _session_cache_path(session_id)
    if own is not None:
        snap = _snapshot_from(own, agent_name)
        if snap:
            return snap
    if _payload_session_id(window_file) == session_id:
        return _snapshot_from(window_file, agent_name)
    return None


def _transcript_snapshot(agent_name: str, window_id: str | None = None) -> dict | None:
    """Context-only snapshot derived from the agent's active transcript.

    Zero-setup fallback when no statusLine cache exists: no rate-limit/cost data,
    and the window size is estimated (the transcript doesn't record it). The
    window is bumped to 1M when observed usage exceeds the 200k default, so 1M
    sessions don't read as >100%.
    """
    u = transcripts.agent_context_from_transcript(agent_name, window_id)
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


def live_snapshot(agent_name: str, window_id: str | None = None) -> dict | None:
    """Best available context snapshot for one agent.

    Fresh statusLine cache (full, authoritative) if present, else a
    transcript-derived estimate, else None. Pass ``window_id`` whenever the caller
    has one (CMX-29): the cache is then read for THAT window's own session, and the
    transcript fallback resolves the window's own transcript (never "newest JSONL in
    the cwd", which two windows sharing a directory would both be handed).
    """
    if window_id:
        sid = window_session(window_id)
        if sid:
            return (_cache_snapshot(agent_name, sid)
                    or _transcript_snapshot(agent_name, window_id))
    return _cache_snapshot(agent_name) or _transcript_snapshot(agent_name, window_id)


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
