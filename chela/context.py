"""Context window usage tracking for agents.

Reads cached status-line JSON files written by a Claude Code status-line script
(see scripts/cache-statusline.sh) and stores snapshots in scheduler.db so the
dashboard can read them instantly without interrupting agents.

Agents' Claude Code status-line scripts cache JSON to
~/.chela/context/<key>.json after every assistant message, where <key> is
``cachekey.encode(window_name)`` — use :func:`cache_path`, never the raw name.
"""

import json
import logging
import re
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from chela import cachekey, config, dispatcher, transcripts
from chela.config import CHELA_DIR, CONTEXT_CACHE_DIR

# CMX-475: the transcript is the DEFAULT source of the context %, not a fallback. Its last
# assistant turn's usage is the same figure the statusLine payload and `/context` report;
# what the transcript does not record is the window size, which `resolve_window` finds
# from an ordered list of sources and names. An unknown window shows tokens, not a %.

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
            # Agent name = the window name this file's key was encoded from (CMX-30).
            agent_name = cachekey.decode(cache_file.stem)

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
    """Drop ``by-session/*.json`` and ``mod/*.json`` files untouched for ``older_than_days``
    (CMX-29, CMX-477).

    One file per Claude session ever run, so unlike ``<window>.json`` (overwritten in
    place) they accrue; the same retention as the snapshot history bounds them.
    """
    cutoff = time.time() - older_than_days * 86400
    files: list[Path] = []
    for sub in (SESSION_CACHE_SUBDIR, MOD_CACHE_SUBDIR):    # CMX-477: the mod's accrue too
        try:
            files += list((CONTEXT_CACHE_DIR / sub).glob("*.json"))
        except OSError:
            continue
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

def cache_path(agent_name: str) -> Path:
    """The statusLine cache file for a window name (CMX-30).

    Keyed through :func:`chela.cachekey.encode` — the SAME mapping the hook uses to
    write it — so a name with a ``/`` (``<org>/cmx-N-<slug>``) is one flat file, not
    a path into a subdirectory that does not exist.
    """
    return CONTEXT_CACHE_DIR / f"{cachekey.encode(agent_name)}.json"


# CMX-29: the hook ALSO writes ``by-session/<session_id>.json``. ``<window>.json`` is
# keyed by the window name the hook resolves from ``$TMUX_PANE`` — and ANY Claude
# process that inherited a pane's environment (a Claude Code background session, a
# subprocess launched from the pane) resolves that same name and overwrites it, so the
# window file alone serves whichever session wrote last. The per-session file cannot
# be clobbered by a different session; the window file is still written (history via
# ``capture_all``, and readers that have no window id) but is only trusted for a
# window when its payload's own ``session_id`` is that window's session.
SESSION_CACHE_SUBDIR = "by-session"

# CMX-477: the chela-measure mod (``chela/mods/chela-measure``, ``chela install-mod``) writes
# the same statusLine-shaped payload from Claude Code's ``session.measure`` hook, tagged
# ``"source": "mod"``, to ``mod/<session_id>.json``: a file of its own, so the two producers
# never clobber each other and ``chela doctor`` can say which one feeds a session. Readers
# take whichever fresh file is NEWER.
MOD_CACHE_SUBDIR = "mod"


# The plan limits chela draws a bar for (usage.limits); a gateway's spend_limit is not one.
LIMIT_KEYS = ("five_hour", "seven_day")


def cache_files(cache_dir: Path) -> list[tuple[Path, str]]:
    """Every cache file with its producer: the statusLine's (``<window>.json``,
    ``by-session/``) and, CMX-477, the chela-measure mod's (``mod/``)."""
    return ([(f, "statusline") for f in cache_dir.glob("*.json")]
            + [(f, "statusline") for f in (cache_dir / SESSION_CACHE_SUBDIR).glob("*.json")]
            + [(f, "mod") for f in (cache_dir / MOD_CACHE_SUBDIR).glob("*.json")])


def freshest_limits(cache_dir: Path) -> dict:
    """``{key: (mtime, used_percentage, resets_at, source)}`` from the freshest cache file
    that carries each limit, whichever producer wrote it (CMX-477)."""
    best: dict = {}
    for f, source in cache_files(cache_dir):
        try:
            mtime = f.stat().st_mtime
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            continue
        rl = d.get("rate_limits") if isinstance(d, dict) else None
        if not isinstance(rl, dict):
            continue
        for key in LIMIT_KEYS:
            b = rl.get(key)
            if not isinstance(b, dict):
                continue
            pct, reset = b.get("used_percentage"), b.get("resets_at")
            if not isinstance(pct, (int, float)) or isinstance(pct, bool):
                continue
            if key not in best or mtime > best[key][0]:
                best[key] = (mtime, float(pct), int(reset) if isinstance(reset, (int, float)) else None,
                             source)
    return best


def _session_cache_path(session_id: str) -> Path | None:
    # SESSION_RE is the same shape the hook validates inline before writing
    # by-session/<sid>.json (scripts/cache-statusline.sh) — keep the two in step.
    from chela import sessions  # lazy: sessions pulls tmux/process helpers
    if not session_id or not sessions.SESSION_RE.match(session_id):
        return None
    return CONTEXT_CACHE_DIR / SESSION_CACHE_SUBDIR / f"{session_id}.json"


def _mod_cache_path(session_id: str) -> Path | None:
    own = _session_cache_path(session_id)
    return None if own is None else CONTEXT_CACHE_DIR / MOD_CACHE_SUBDIR / own.name


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


def _snapshot_from(path: Path, agent_name: str, source: str = "statusline") -> dict | None:
    """Full snapshot from a fresh statusLine-shaped cache file, or None if absent/stale.

    ``source`` names the producer: ``"statusline"`` (scripts/cache-statusline.sh) or
    ``"mod"`` (the chela-measure mod, CMX-477)."""
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
    snap["source"] = source
    snap["estimated"] = False
    snap["state"] = "ok"
    snap["window_source"] = source
    snap["_mtime"] = mtime
    return snap


def _cache_snapshot(agent_name: str, session_id: str | None = None) -> dict | None:
    """Fresh statusLine snapshot for ``agent_name`` — for ``session_id`` when known.

    With a session id, only that session's numbers are served: its ``by-session`` file,
    else the window file if (and only if) its payload names that same session. Without
    one, the window file as-is (the pre-CMX-29 read, for callers with no window id).

    CMX-477: with a session id, the chela-measure mod's ``mod/<sid>.json`` is a candidate
    too, and the NEWER of the two fresh snapshots wins, whichever wrote it.
    """
    snap = _statusline_snapshot(agent_name, session_id)
    if not session_id:
        return _public(snap)
    mod_file = _mod_cache_path(session_id)
    mod = _snapshot_from(mod_file, agent_name, "mod") if mod_file is not None else None
    if mod and (not snap or mod["_mtime"] > snap["_mtime"]):
        snap = mod
    return _public(snap)


def _public(snap: dict | None) -> dict | None:
    if snap:
        snap.pop("_mtime", None)
    return snap


def _statusline_snapshot(agent_name: str, session_id: str | None) -> dict | None:
    window_file = cache_path(agent_name)
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


def _reported_window(session_id: str | None, agent_name: str | None = None) -> tuple[int, str | None] | None:
    """``(context_window_size, model)`` that ``session_id``'s statusLine last reported.

    CMX-292: read at ANY age — a session's window size does not change while it runs,
    so a cache too stale to serve usage from still knows the window. Only a payload
    whose own ``session_id`` is this session counts: its ``by-session`` file, else the
    window file (when ``agent_name`` is given). None when no cache for this session
    has ever existed, or none carries a window size.
    """
    if not session_id:
        return None
    paths = [_session_cache_path(session_id), _mod_cache_path(session_id)]
    if agent_name:
        paths.append(cache_path(agent_name))
    for path in paths:
        if path is None:
            continue
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):  # ValueError: bad JSON or bytes that aren't UTF-8
            continue
        if not isinstance(data, dict) or data.get("session_id") != session_id:
            continue
        ctx = data.get("context_window")
        size = ctx.get("context_window_size") if isinstance(ctx, dict) else None
        if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
            continue
        model = data.get("model")
        return size, (model.get("display_name") if isinstance(model, dict) else None)
    return None


ONE_M = 1_000_000
STANDARD_WINDOW = 200_000

# A model string naming a 1M-context variant: `opus[1m]`, `claude-sonnet-4-5[1m]`.
_ONE_M_MARKER = re.compile(r"\[1m\]\s*\Z", re.IGNORECASE)

# Claude Code's managed (org) settings file, per platform; it outranks every other
# settings file. A seam: tests point it at a fixture.
MANAGED_SETTINGS = (
    Path("/Library/Application Support/ClaudeCode/managed-settings.json"),
    Path("/etc/claude-code/managed-settings.json"),
)


def has_1m_marker(model: str | None) -> bool:
    return isinstance(model, str) and bool(_ONE_M_MARKER.search(model.strip()))


def _flag_model(args: list[str]) -> str | None:
    """The value of the last ``--model X`` / ``--model=X`` in ``args``."""
    found = None
    for i, a in enumerate(args):
        if a == "--model" and i + 1 < len(args):
            found = args[i + 1]
        elif a.startswith("--model="):
            found = a.split("=", 1)[1]
    return found or None


def _launch_model(session_id: str | None, window_id: str | None) -> str | None:
    """The model the session was LAUNCHED with: ``--model`` on the live claude process's
    argv, else ``$ANTHROPIC_MODEL`` in its environment, else the ``--model`` chela
    recorded for the session (CMX-372 launch settings). None when none names one."""
    from chela import launch_settings, sessions
    if window_id:
        try:
            pane = sessions.panes().get(window_id)
            pid = pane.claude_pid if pane else None
        except Exception:  # noqa: BLE001 — a context bar must never 500 the dashboard
            log.debug("_launch_model(%s): pane lookup failed", window_id, exc_info=True)
            pid = None
        if pid:
            model = _flag_model(launch_settings._claude_args(sessions._cmdline_argv(pid)))
            if model:
                return model
            env_model = launch_settings.read_environ(pid).get("ANTHROPIC_MODEL", "").strip()
            if env_model:
                return env_model
    flags = launch_settings.for_session(session_id).get("flags") if session_id else None
    return _flag_model(flags) if isinstance(flags, list) else None


def _settings_model(cwd: str | None) -> str | None:
    """``model`` from Claude Code's settings, highest precedence first: managed, the
    project's ``.claude/settings.local.json`` and ``.claude/settings.json`` (under the
    session's cwd), then the user's ``settings.json``. None when none sets one."""
    paths = list(MANAGED_SETTINGS)
    if cwd:
        paths += [Path(cwd) / ".claude" / "settings.local.json",
                  Path(cwd) / ".claude" / "settings.json"]
    paths.append(transcripts.claude_config_dir() / "settings.json")
    for path in paths:
        try:
            data = json.loads(path.read_text())
        except (ValueError, OSError):  # absent, unreadable, or not JSON: not a source
            continue
        model = data.get("model") if isinstance(data, dict) else None
        if isinstance(model, str) and model.strip():
            return model.strip()
    return None


def resolve_window(used_tokens: int | None, *, session_id: str | None = None,
                   agent_name: str | None = None, model: str | None = None,
                   cwd: str | None = None, window_id: str | None = None) -> dict:
    """The session's context-window size and the source that decided it (CMX-475).

    ``{"window": int | None, "source": str, "model": str | None}``. Checked in order;
    the first that decides wins:

    1. ``transcript`` — the transcript's model id names a 1M variant (``[1m]``);
    2. ``launch`` — the model the session was launched with (``--model``,
       ``$ANTHROPIC_MODEL``, the CMX-372 record) names a 1M variant;
    3. ``settings`` — Claude Code's ``model`` setting (managed, project, user) does.
       A launch model that names a model WITHOUT the marker overrides settings, as it
       does in Claude Code, so it stops this step rather than falling through to it;
    4. ``statusline`` — the session's own statusLine payload reported the size (at any
       age: the window does not change while a session runs, CMX-292);
    5. ``usage`` — the usage has already passed 200k (or the statusLine's reported size,
       which is then evidently wrong), which only a 1M window holds;
    6. ``config`` — the operator set ``default_context_window`` explicitly.

    Otherwise ``window`` is None and ``source`` is ``unknown``: a bare model id does not
    say which window it runs with, and guessing 200k would read a 1M session as 215%.
    """
    reported = _reported_window(session_id, agent_name)
    label = reported[1] if reported else None
    if has_1m_marker(model):
        return {"window": ONE_M, "source": "transcript", "model": label}
    launched = _launch_model(session_id, window_id)
    if launched is not None:
        if has_1m_marker(launched):
            return {"window": ONE_M, "source": "launch", "model": label}
    elif has_1m_marker(_settings_model(cwd)):
        return {"window": ONE_M, "source": "settings", "model": label}
    used = used_tokens or 0
    if reported and used <= reported[0]:
        return {"window": reported[0], "source": "statusline", "model": label}
    # A usage past 200k — or past a reported window, which is then evidently wrong
    # (CMX-292) — is only held by a 1M window.
    if used > (reported[0] if reported else STANDARD_WINDOW) and used <= ONE_M:
        return {"window": ONE_M, "source": "usage", "model": label}
    explicit = config.default_context_window_explicit()
    if explicit:
        return {"window": explicit, "source": "config", "model": label}
    return {"window": None, "source": "unknown", "model": label}


# Window sources that are an inference, not a statement of the session's window.
_INFERRED_SOURCES = frozenset({"usage", "config"})


def window_for(used_tokens: int, session_id: str | None,
               agent_name: str | None = None, *, model: str | None = None,
               cwd: str | None = None, window_id: str | None = None) -> dict:
    """Size ``used_tokens`` of transcript usage against the session's context window.

    The window comes from :func:`resolve_window`. With none known, ``total_k``,
    ``used_pct``, ``free_k`` and ``free_pct`` are None — the reading is the absolute
    ``used_k`` alone, never a % against a guessed window.
    """
    r = resolve_window(used_tokens, session_id=session_id, agent_name=agent_name,
                       model=model, cwd=cwd, window_id=window_id)
    window = r["window"]
    used_k = round(used_tokens / 1000, 1)
    if not window:
        return {"used_k": used_k, "total_k": None, "used_pct": None, "free_k": None,
                "free_pct": None, "model": r["model"], "window_source": r["source"],
                "estimated": False}
    total_k = round(window / 1000, 1)
    used_pct = min(100, round(used_tokens / window * 100))
    return {
        "used_k": used_k, "total_k": total_k, "used_pct": used_pct,
        "free_k": round(total_k - used_k, 1), "free_pct": max(0, 100 - used_pct),
        "model": r["model"], "window_source": r["source"],
        "estimated": r["source"] in _INFERRED_SOURCES,
    }


def _blank(agent_name: str) -> dict:
    return {
        "name": agent_name, "used_k": None, "total_k": None, "used_pct": None,
        "messages_k": None, "messages_pct": None, "free_k": None, "free_pct": None,
        "model": None, "cost_usd": None,
        "rate_limit_pct": None, "rate_limit_resets_at": None,
        "weekly_rl_pct": None, "weekly_rl_resets_at": None,
        "session_name": None, "branch": None, "ts": None,
        "source": "transcript", "estimated": False, "window_source": None,
        "used_tokens": None,
    }


def transcript_reading(u: dict | None, agent_name: str,
                       window_id: str | None = None) -> dict:
    """A snapshot from one :func:`chela.transcripts.latest_context_usage` result.

    ``state`` is ``ok`` (a reading), ``compacted`` (a ``/compact`` is newer than the last
    usage: the old number is stale and is NOT shown — ``pre_k`` says what it was), or
    ``no_turns`` (no assistant turn yet: a fresh session, or one ``/clear`` just started).
    """
    snap = _blank(agent_name)
    if not u:
        snap["state"] = "no_turns"
        return snap
    snap["ts"] = u.get("ts")
    snap["model"] = u.get("model")
    if u.get("compacted"):
        pre = u.get("pre_tokens") or u.get("stale_used_tokens")
        snap.update(state="compacted", pre_k=round(pre / 1000, 1) if pre else None)
        return snap
    used = u.get("used_tokens")
    if not isinstance(used, int):
        snap["state"] = "no_turns"
        return snap
    w = window_for(used, u.get("session_id"), agent_name, model=u.get("model"),
                   cwd=u.get("cwd"), window_id=window_id)
    snap.update(
        state="ok", used_tokens=used,
        used_k=w["used_k"], total_k=w["total_k"], used_pct=w["used_pct"],
        free_k=w["free_k"], free_pct=w["free_pct"],
        model=w["model"] or u.get("model"),
        estimated=w["estimated"], window_source=w["window_source"],
    )
    return snap


def _transcript_snapshot(agent_name: str, window_id: str | None = None) -> dict | None:
    """The transcript's reading for ``agent_name`` — None only when no transcript resolves
    (a resolved one with no assistant turn yet is a ``no_turns`` reading, not None)."""
    try:
        path = transcripts._resolve_agent_transcript(agent_name, window_id)
        if path is None:
            return None
        u = transcripts.latest_context_usage(path)
        if u and u.get("scan_exhausted"):
            return None  # no reading in the scanned tail: unknown, so the statusLine stands
        return transcript_reading(u, agent_name, window_id)
    except Exception:  # noqa: BLE001 — a context bar must never 500 the dashboard
        # Read on EVERY call now (not only when no statusLine cache exists), so a failing
        # window resolution must leave the statusLine's reading standing, not take it down.
        log.debug("_transcript_snapshot(%s, %s) failed", agent_name, window_id, exc_info=True)
        return None


# Fields only the statusLine payload carries: kept when the transcript's reading wins.
_STATUSLINE_EXTRAS = ("cost_usd", "rate_limit_pct", "rate_limit_resets_at",
                      "weekly_rl_pct", "weekly_rl_resets_at", "session_name", "branch")


def _parse_ts(ts) -> datetime | None:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def pick(statusline: dict | None, transcript: dict | None) -> dict | None:
    """The reading to show, from the two sources (CMX-475). Neither is required.

    * a ``compacted`` or ``no_turns`` transcript wins outright: no turn has run since the
      ``/compact`` (or since ``/clear`` started the session), so any number the statusLine
      still carries is from before it — the stale value this exists to stop showing;
    * otherwise the NEWER of the two by timestamp — the statusLine on a tie, since it also
      carries the exact window size;
    * the transcript's reading keeps the statusLine's cost and rate-limit fields.
    """
    if transcript is None:
        return statusline
    if statusline is None:
        return transcript
    t_state = transcript.get("state")
    if t_state == "ok":
        s_ts, t_ts = _parse_ts(statusline.get("ts")), _parse_ts(transcript.get("ts"))
        if s_ts is None or t_ts is None or t_ts <= s_ts:
            return statusline
    merged = dict(transcript)
    for key in _STATUSLINE_EXTRAS:
        merged[key] = statusline.get(key)
    return merged


def live_snapshot(agent_name: str, window_id: str | None = None) -> dict | None:
    """Best available context snapshot for one agent, or None when neither source has one.

    The transcript's reading and a fresh statusLine cache, chosen by :func:`pick`. Pass
    ``window_id`` whenever the caller has one (CMX-29): the cache is then read for THAT
    window's own session, and the transcript is the window's own (never "newest JSONL in
    the cwd", which two windows sharing a directory would both be handed).
    """
    sid = window_session(window_id) if window_id else None
    statusline = _cache_snapshot(agent_name, sid) if sid else _cache_snapshot(agent_name)
    return pick(statusline, _transcript_snapshot(agent_name, window_id))


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
# Only sessions with at least one snapshot INSIDE the window are counted (CMX-30):
# an agent that did not run in the window gets no row at all, not a $0.00 row.
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
        # CMX-30: a session with NO snapshot inside the window did not run in it —
        # it is skipped, not listed at $0.00. Reading every snapshot `ts <= end`
        # unfiltered listed every agent ever seen (204 of 208 'today' rows were such
        # $0.00 ghosts), which also buried the agents that were genuinely missing.
        if not any(r["ts"] >= start_iso for r in recs):
            continue
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
