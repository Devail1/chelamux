"""Token usage across EVERY Claude Code transcript — the Cost tab's "Usage" view (CMX-38).

The Cost view lists $ only for sessions whose statusLine writes a cost file, so it cannot
see what actually drains the plan limits: judges, subagents, dispatched agents, background
sessions, or a third-party bot driving ``claude -p --resume``. On 2026-10-08 such a bot
emptied the 5-hour limit with a broken prompt cache (~4% cache hit, 300-490k tokens
WRITTEN per call) and nothing in chela showed it. This module reads the transcripts
themselves, which every Claude Code session writes whether or not it has a statusLine.

Three reads, all computed on request by the dashboard (no daemon of its own):

* :func:`limits` — the 5h / 7d ``used_percentage`` + reset time from the freshest
  statusLine ``rate_limits`` in ``~/.chela/context/`` (the same files the gear menu's
  context bar reads), a burn rate in %/h from successive samples, and whether that rate
  reaches 100% before the reset. Missing, stale (> :data:`LIMIT_STALE_S`) or
  already-reset data is ``None`` — UNKNOWN, never 0%.
* :func:`aggregate` — one row per transcript file over a window: requests, input /
  cache-write / cache-read / output tokens, a weighted total, the cache-hit rate and a
  ``cache_broken`` flag. Rows are deduped by ``(message.id, requestId)`` (Claude Code
  writes one line per content block, each repeating the same usage) and ``<synthetic>``
  rows (limit-hit placeholders) are skipped.
* :func:`report` — both of the above for the dashboard, with a short result cache and an
  mtime filter so only transcripts touched today are opened at all.

Ported from the orchestrator's 2026-10-08 watchdog, which flagged all three bot sessions
at a 4-5% hit rate. Timestamps are parsed as UTC (``calendar.timegm``): a local-time
parse in that watchdog's first draft silently dropped the newest hour.
"""
from __future__ import annotations

import calendar
import glob
import json
import logging
import os
import threading
import time
from pathlib import Path

from chela.config import CONTEXT_CACHE_DIR

log = logging.getLogger(__name__)

# Weighted total: what a token of each kind costs relative to a fresh input token.
W_INPUT = 1.0
W_CACHE_WRITE = 1.25
W_CACHE_READ = 0.1
W_OUTPUT = 5.0

# cache_broken: hit rate under this, over at least this many requests, with more than
# this many prompt tokens (input + cache_read + cache_write). The 10-08 bot sat at ~4%.
BROKEN_HIT_RATE = 0.5
BROKEN_MIN_REQUESTS = 10
BROKEN_MIN_TOKENS = 1_000_000

# A rate_limits sample older than this is unknown, not a reading.
LIMIT_STALE_S = 1800
# Burn rate is measured against a sample at least this much older than the current one,
# so two writes a few seconds apart don't produce a wild %/h.
BURN_MIN_GAP_S = 600
# The same limit window can report a resets_at that jitters by a few seconds.
RESET_TOLERANCE_S = 120

WINDOW_30M_S = 1800
RESULT_TTL_S = 45.0
TOP_N = 25
SYNTHETIC_MODEL = "<synthetic>"

# The extra transcript roots (globs) setting — a config.json key / env var, read-only in
# the Usage view — and its default: a WSL host's Windows-side Claude Code. A root that
# matches nothing is simply skipped.
EXTRA_ROOTS_KEY = "usage_extra_roots"
EXTRA_ROOTS_ENV = "CHELA_USAGE_EXTRA_ROOTS"
DEFAULT_EXTRA_ROOTS = ("/mnt/c/Users/*/.claude/projects",)

LIMIT_KEYS = ("five_hour", "seven_day")


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def parse_ts(ts: str) -> int | None:
    """``2026-10-08T12:34:56.789Z`` -> epoch seconds, read as UTC. None if unparsable.

    ⛔ ``calendar.timegm``, never ``time.mktime``: transcripts stamp UTC, and a
    local-time parse shifts every record by the host's offset — on a UTC+3 host the
    newest three hours land in the future of a "last 30 min" window and vanish.
    """
    if not isinstance(ts, str) or len(ts) < 19:
        return None
    try:
        return calendar.timegm(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        return None


def _int(v) -> int:
    return v if isinstance(v, int) and v > 0 else 0


def parse_line(line: str) -> dict | None:
    """One transcript line -> a usage record, or None when it carries no billable usage.

    Also returns the ``ai-title`` / ``cwd`` facts a row is labelled with, as
    ``{"title": ...}`` / ``{"cwd": ...}`` side records (no ``t``).
    """
    if '"usage"' not in line and '"ai-title"' not in line:
        return None
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict):
        return None
    if d.get("type") == "ai-title":
        title = d.get("aiTitle")
        return {"title": title} if isinstance(title, str) and title else None
    if d.get("type") != "assistant":
        return None
    m = d.get("message")
    if not isinstance(m, dict):
        return None
    u = m.get("usage")
    if not isinstance(u, dict):
        return None
    model = m.get("model")
    if model == SYNTHETIC_MODEL:
        return None  # a limit-hit placeholder, not a request
    t = parse_ts(d.get("timestamp"))
    if t is None:
        return None
    return {
        "t": t,
        "key": (m.get("id"), d.get("requestId")),
        "in": _int(u.get("input_tokens")),
        "cw": _int(u.get("cache_creation_input_tokens")),
        "cr": _int(u.get("cache_read_input_tokens")),
        "out": _int(u.get("output_tokens")),
        "model": model if isinstance(model, str) else None,
        "cwd": d.get("cwd") if isinstance(d.get("cwd"), str) else None,
    }


class _FileState:
    __slots__ = ("size", "mtime", "records", "title", "cwd")

    def __init__(self):
        self.size = 0
        self.mtime = 0.0
        self.records: list[dict] = []
        self.title: str | None = None
        self.cwd: str | None = None


# path -> _FileState. Transcripts are append-only, so a re-read only parses the bytes
# appended since the last one; a file that shrank (rewritten) is parsed from scratch.
_FILES: dict[str, _FileState] = {}
_FILES_LOCK = threading.Lock()


def _read_file(path: str, st: os.stat_result, keep_since: int) -> _FileState:
    with _FILES_LOCK:
        fs = _FILES.get(path)
    if fs is None or st.st_size < fs.size:
        fs = _FileState()
    if st.st_size > fs.size:
        try:
            with open(path, "rb") as fh:
                fh.seek(fs.size)
                chunk = fh.read(st.st_size - fs.size)
        except OSError:
            return fs
        # Only consume through the last newline: a line still being written is read
        # whole on the next pass instead of half now.
        end = chunk.rfind(b"\n")
        if end >= 0:
            for raw in chunk[: end + 1].splitlines():
                rec = parse_line(raw.decode("utf-8", errors="ignore"))
                if rec is None:
                    continue
                if "t" not in rec:
                    fs.title = rec["title"]
                    continue
                if rec["cwd"]:
                    fs.cwd = rec["cwd"]
                if rec["t"] >= keep_since:
                    fs.records.append(rec)
            fs.size += end + 1
    fs.mtime = st.st_mtime
    fs.records = [r for r in fs.records if r["t"] >= keep_since]
    with _FILES_LOCK:
        _FILES[path] = fs
    return fs


# ---------------------------------------------------------------------------
# Transcript discovery
# ---------------------------------------------------------------------------

def default_root() -> Path:
    from chela import transcripts
    return transcripts.CLAUDE_PROJECTS_DIR


def _cast_roots(raw) -> list[str]:
    """A roots setting -> a list of absolute globs: a JSON list, or one string split on
    newlines/commas (``"[]"`` is the env spelling of "none"). Raises ValueError on a bad
    value, which :func:`chela.config.dashboard_setting` treats as absent."""
    if isinstance(raw, str) and raw.strip().startswith("["):
        raw = json.loads(raw)
    return normalize_roots(raw)


def extra_roots_setting() -> tuple[list[str], str]:
    """``(extra roots, source)`` — a plain config setting, resolved like every other
    dashboard_setting knob: ``$CHELA_USAGE_EXTRA_ROOTS`` beats ``usage_extra_roots`` in
    ``~/.chela/config.json`` beats :data:`DEFAULT_EXTRA_ROOTS`. ``source`` is ``"env"``,
    ``"config"`` or ``"default"``.

    UNSET falls back to the default (a WSL host's Windows side); an EXPLICITLY EMPTY
    value (``[]``, or ``""`` in config.json) scans no extra root. An empty env var is
    unset, as for every other knob — export ``"[]"`` to turn the extra roots off there.
    """
    from chela import config
    v, source = config._resolve_dashboard_setting(EXTRA_ROOTS_KEY, EXTRA_ROOTS_ENV, None, _cast_roots)
    if source == "env":
        return v, "env"
    if source == "dashboard":
        return v, "config"
    try:
        from chela import userconfig
        stored = userconfig.get(EXTRA_ROOTS_KEY)
    except Exception:  # noqa: BLE001 — a bad config must not blank the view
        stored = None
    # dashboard_setting skips "" as absent; here it is the explicit "none".
    if stored == "":
        return [], "config"
    return list(DEFAULT_EXTRA_ROOTS), "default"


def extra_roots() -> list[str]:
    """The configured extra transcript roots (globs); see :func:`extra_roots_setting`."""
    return extra_roots_setting()[0]


def normalize_roots(raw) -> list[str]:
    """A list, or one string split on newlines/commas -> a list of absolute globs.
    Raises ValueError on a relative entry: a relative root would resolve against
    whatever cwd the dashboard happened to start in."""
    if isinstance(raw, str):
        items = raw.replace(",", "\n").splitlines()
    elif isinstance(raw, list) and all(isinstance(s, str) for s in raw):
        items = raw
    else:
        raise ValueError("roots must be a string or a list of strings")
    out = []
    for s in (x.strip() for x in items):
        if not s:
            continue
        if not os.path.isabs(os.path.expanduser(s)):
            raise ValueError(f"not an absolute path: {s}")
        out.append(s)
    return out


def resolve_roots(roots: list[str]) -> list[str]:
    """Expand each root glob to existing directories, de-duplicated, in order."""
    seen, out = set(), []
    for r in roots:
        for d in sorted(glob.glob(os.path.expanduser(r))):
            real = os.path.realpath(d)
            if real not in seen and os.path.isdir(real):
                seen.add(real)
                out.append(d)
    return out


def _transcripts(dirs: list[str], since: float):
    """Yield ``(root, path, stat)`` for every ``*.jsonl`` under ``dirs`` modified at or
    after ``since`` — the mtime filter that keeps a months-old archive unread."""
    for root in dirs:
        for path in glob.iglob(os.path.join(root, "**", "*.jsonl"), recursive=True):
            try:
                st = os.stat(path)
            except OSError:
                continue
            if st.st_mtime >= since:
                yield root, path, st


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def weighted(c: dict) -> float:
    return (c["in"] * W_INPUT + c["cw"] * W_CACHE_WRITE
            + c["cr"] * W_CACHE_READ + c["out"] * W_OUTPUT)


def hit_rate(c: dict) -> float | None:
    """cache_read / (input + cache_read + cache_write); None with no prompt tokens."""
    tot = c["in"] + c["cr"] + c["cw"]
    return c["cr"] / tot if tot else None


def is_cache_broken(c: dict) -> bool:
    hit = hit_rate(c)
    return (hit is not None and hit < BROKEN_HIT_RATE
            and c["requests"] >= BROKEN_MIN_REQUESTS
            and c["in"] + c["cr"] + c["cw"] > BROKEN_MIN_TOKENS)


def aggregate(sessions: list[dict], since: int, until: int, buckets: int) -> list[dict]:
    """Per-session totals over ``[since, until]``, heaviest (weighted) first.

    ``sessions`` is ``[{"id", "records", ...label facts}]``. A ``(message.id,
    requestId)`` pair counts ONCE across every session — the same request logged by a
    parent transcript and a copy of it must not double — except a record with neither
    id, which has nothing to dedupe on and counts on its own.
    """
    seen: set = set()
    span = max(1, until - since)
    rows = []
    for s in sessions:
        c = {"requests": 0, "in": 0, "cw": 0, "cr": 0, "out": 0}
        spark = [0.0] * buckets
        models: dict[str, int] = {}
        last_t = None
        for r in s["records"]:
            if not since <= r["t"] <= until:
                continue
            k = r["key"]
            if k != (None, None):
                if k in seen:
                    continue
                seen.add(k)
            c["requests"] += 1
            for f in ("in", "cw", "cr", "out"):
                c[f] += r[f]
            if r["model"]:
                models[r["model"]] = models.get(r["model"], 0) + 1
            i = min(buckets - 1, (r["t"] - since) * buckets // span)
            spark[i] += weighted(r)
            last_t = r["t"] if last_t is None else max(last_t, r["t"])
        if not c["requests"]:
            continue
        hit = hit_rate(c)
        rows.append({
            **{k: v for k, v in s.items() if k != "records"},
            "requests": c["requests"],
            "input": c["in"],
            "cache_write": c["cw"],
            "cache_read": c["cr"],
            "output": c["out"],
            "weighted": round(weighted(c)),
            "cache_hit": round(hit, 4) if hit is not None else None,
            "cache_broken": is_cache_broken(c),
            "model": max(models, key=models.get) if models else None,
            "last_ts": last_t,
            "spark": [round(v) for v in spark],
        })
    rows.sort(key=lambda r: r["weighted"], reverse=True)
    return rows


def _session_facts(root: str, path: str, fs: _FileState) -> dict:
    rel = os.path.relpath(path, root)
    stem = Path(path).stem
    parts = Path(rel).parts
    # <encoded-cwd>/<session>/subagents/agent-*.jsonl: the parent session is parts[1].
    subagent = "subagents" in parts
    session_id = parts[1] if subagent and len(parts) > 2 else stem
    return {
        "id": rel,
        "session_id": session_id,
        "subagent": stem if subagent else None,
        "project": os.path.basename((fs.cwd or "").rstrip("/\\")) or parts[0],
        "ai_title": fs.title,
        "root": root,
    }


def collect(dirs: list[str], since: int) -> list[dict]:
    """Every transcript under ``dirs`` touched since ``since``, as ``aggregate`` input."""
    out = []
    live = set()
    for root, path, st in _transcripts(dirs, since):
        live.add(path)
        fs = _read_file(path, st, since)
        if fs.records:
            out.append({**_session_facts(root, path, fs), "records": fs.records})
    with _FILES_LOCK:
        for p in [p for p in _FILES if p not in live]:
            del _FILES[p]
    return out


def label(row: dict, window_names: dict[str, str]) -> str:
    """CMX-33's rule: a chela window's session is labelled by its window name; any
    other session by its project dir + session-id prefix."""
    name = window_names.get(row["session_id"])
    base = name or f'{row["project"]} · {row["session_id"][:8]}'
    return f'{base} › {row["subagent"]}' if row.get("subagent") else base


# ---------------------------------------------------------------------------
# Limits (statusLine rate_limits)
# ---------------------------------------------------------------------------

# In-process sample history for the burn rate: (t, key, pct, resets_at). Fed by every
# limits() call, merged with context_snapshots history when the daemon records it.
_SAMPLES: list[tuple[float, str, float, int]] = []
_SAMPLES_MAX = 2000
_SAMPLES_LOCK = threading.Lock()


def _cache_files(cache_dir: Path) -> list[Path]:
    from chela import context
    return list(cache_dir.glob("*.json")) + list((cache_dir / context.SESSION_CACHE_SUBDIR).glob("*.json"))


def freshest_limits(cache_dir: Path) -> dict:
    """``{key: (mtime, used_percentage, resets_at)}`` from the freshest cache file that
    carries each limit."""
    best: dict = {}
    for f in _cache_files(cache_dir):
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
                best[key] = (mtime, float(pct), int(reset) if isinstance(reset, (int, float)) else None)
    return best


def burn_rate(samples: list[tuple[float, float, int | None]], cur: tuple[float, float, int | None]) -> float | None:
    """%/h between ``cur`` and the newest sample of the SAME limit window (same
    resets_at) at least :data:`BURN_MIN_GAP_S` older. None when there is no such sample —
    a new window, or too little history — never a guessed 0."""
    t, pct, reset = cur
    prev = None
    for st, sp, sr in samples:
        if reset is None or sr is None or abs(sr - reset) > RESET_TOLERANCE_S:
            continue
        if t - st >= BURN_MIN_GAP_S and (prev is None or st > prev[0]):
            prev = (st, sp)
    if prev is None:
        return None
    return (pct - prev[1]) / ((t - prev[0]) / 3600)


def _db_samples(key: str, since: float) -> list[tuple[float, float, int | None]]:
    """context_snapshots' history of one limit — present when the daemon's capture runs."""
    from contextlib import closing
    from datetime import datetime, timezone

    from chela import context
    pct_col, reset_col = (("rate_limit_pct", "rate_limit_resets_at") if key == "five_hour"
                          else ("weekly_rl_pct", "weekly_rl_resets_at"))
    since_iso = datetime.fromtimestamp(since, timezone.utc).isoformat()
    try:
        with closing(context._get_db()) as conn:
            rows = conn.execute(
                f"SELECT ts, {pct_col} AS pct, {reset_col} AS reset FROM context_snapshots "
                f"WHERE ts >= ? AND {pct_col} IS NOT NULL", (since_iso,)).fetchall()
    except Exception:  # noqa: BLE001 — history is a bonus; the in-process ring suffices
        return []
    out = []
    for r in rows:
        try:
            out.append((datetime.fromisoformat(r["ts"]).timestamp(), float(r["pct"]), r["reset"]))
        except (TypeError, ValueError):
            continue
    return out


def limits(cache_dir: Path | None = None, now: float | None = None, history: bool = True) -> dict:
    """The 5h / 7d limit bars. Each value is ``None`` when unknown; see module doc."""
    now = time.time() if now is None else now
    best = freshest_limits(cache_dir or CONTEXT_CACHE_DIR)
    out = {}
    for key in LIMIT_KEYS:
        b = best.get(key)
        entry = {"used_pct": None, "resets_at": None, "sampled_at": None,
                 "burn_pct_per_h": None, "projected_pct": None, "hits_100_before_reset": None,
                 "reason": None}
        if b is None:
            entry["reason"] = "no statusLine rate_limits found"
        elif now - b[0] > LIMIT_STALE_S:
            entry["reason"] = f"newest sample is {int((now - b[0]) // 60)} min old"
            entry["sampled_at"] = b[0]
        elif b[2] is not None and b[2] <= now:
            entry["reason"] = "the limit window has reset since the newest sample"
            entry["sampled_at"] = b[0]
        else:
            mtime, pct, reset = b
            entry.update(used_pct=pct, resets_at=reset, sampled_at=mtime)
            with _SAMPLES_LOCK:
                if not any(s[1] == key and s[0] == mtime for s in _SAMPLES):
                    _SAMPLES.append((mtime, key, pct, reset))
                    del _SAMPLES[:-_SAMPLES_MAX]
                ring = [(t, p, r) for t, k, p, r in _SAMPLES if k == key]
            samples = ring + (_db_samples(key, now - 8 * 86400) if history else [])
            rate = burn_rate(samples, (mtime, pct, reset))
            if rate is not None:
                entry["burn_pct_per_h"] = round(rate, 2)
                if reset is not None:
                    projected = pct + max(0.0, rate) * (reset - now) / 3600
                    entry["projected_pct"] = round(projected, 1)
                    entry["hits_100_before_reset"] = rate > 0 and projected >= 100
        out[key] = entry
    return out


# ---------------------------------------------------------------------------
# The dashboard's report
# ---------------------------------------------------------------------------

_REPORT: dict = {}
_REPORT_LOCK = threading.Lock()
# One scan at a time: concurrent requests would otherwise both append to the same
# per-file state. The second waits, then usually finds the first one's result cached.


def utc_day_start(now: float) -> int:
    return int(now // 86400 * 86400)


def _windows(dirs: list[str], now: float) -> dict:
    day0 = utc_day_start(now)
    sessions = collect(dirs, day0)
    n = int(now)
    since_30m = n - WINDOW_30M_S
    return {
        "30m": {"since": since_30m, "until": n,
                "rows": aggregate(sessions, since_30m, n, 15)[:TOP_N]},
        "today": {"since": day0, "until": n,
                  "rows": aggregate(sessions, day0, n, 24)[:TOP_N]},
    }


def report(window_names: dict[str, str] | None = None, now: float | None = None,
           roots: list[str] | None = None, use_cache: bool = True) -> dict:
    """Limits + top consumers for the dashboard. ``window_names`` maps a chela window's
    session id to its window name, for :func:`label`."""
    now = time.time() if now is None else now
    if roots is None:
        roots, source = extra_roots_setting()
    else:
        source = "argument"
    configured = [str(default_root())] + roots
    dirs = resolve_roots(configured)
    cache_key = tuple(dirs)
    with _REPORT_LOCK:
        hit = _REPORT.get("v") if use_cache else None
        if hit and hit[0] == cache_key and 0 <= now - hit[1] < RESULT_TTL_S:
            windows = hit[2]
        else:
            windows = _windows(dirs, now)
            _REPORT["v"] = (cache_key, now, windows)
    names = window_names or {}
    labelled = {
        w: {**v, "rows": [{**r, "label": label(r, names), "chela_window": names.get(r["session_id"])}
                          for r in v["rows"]]}
        for w, v in windows.items()
    }
    return {
        "generated_at": now,
        "limits": limits(now=now),
        "windows": labelled,
        "roots": {"default": str(default_root()), "extra": roots, "source": source,
                  "scanned": dirs},
        "thresholds": {"hit_rate": BROKEN_HIT_RATE, "min_requests": BROKEN_MIN_REQUESTS,
                       "min_tokens": BROKEN_MIN_TOKENS},
    }
