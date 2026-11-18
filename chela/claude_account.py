"""Which Claude account this machine's CLI is signed in to: the dashboard header (CMX-395).

In an org setup the operator must see at a glance that sessions run on the ORG's Claude
account, not a personal one: a data-policy question, not only convenience. The source is the
official CLI, ``claude auth status --json`` (``email``, ``orgName``, ``subscriptionType``,
``authMethod``, ``loggedIn``, ``loginExpiresAt``), never Claude Code's internal
``~/.claude.json`` or the keychain.

Cost: one subprocess, cached for :data:`TTL_S`. The dashboard's own timer
(:func:`start_background_refresh`) keeps it warm and a dashboard load may ask for a fresh read
(at most every :data:`FRESH_MIN_S`); the page's 30 s tick never spawns it.

Unknown never reads as OK: a missing CLI, a timeout, a non-JSON or shapeless answer, or a
"logged in" with no account email all answer ``state: "unknown"`` with a reason, and the
header draws "account: unknown".

Account switches (the per-session badge). An account switcher runs ``claude auth logout`` and
then ``claude auth login``; ``~/.claude`` is shared, so the CLI account is global and nothing
per process says which account a RUNNING session still holds. So this module records each
change of the signed-in email it observes (A -> B, with the time it saw it) in
``CHELA_DIR/claude-account.json``, and :func:`switch_badge` marks a session that started
before the latest change: it MAY still use A. Never "uses A": that is not knowable here.

The email is shown on the dashboard (that is the point) and kept in the local state file
above, but never logged.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import threading
import time
from datetime import datetime, timezone

from chela import config

log = logging.getLogger(__name__)

ARGV = ("claude", "auth", "status", "--json")
CMD_TIMEOUT_S = 15
# The cached answer is trusted this long; the background timer re-reads on the same period.
TTL_S = 300
# A dashboard load's fresh read, at most this often (a reload storm spawns one CLI, not many).
FRESH_MIN_S = 30
# An "unknown" answer is retried this soon, not after the full TTL (a CLI briefly unavailable
# at dashboard start must not read unknown for five minutes).
UNKNOWN_TTL_S = 30

_lock = threading.Lock()
_cache: dict | None = None
_cache_at = 0.0


def _store_path():
    return config.CHELA_DIR / "claude-account.json"


def _unknown(reason: str) -> dict:
    return {"state": "unknown", "reason": reason}


def parse_ts(v) -> float | None:
    """An epoch-seconds float from ``loginExpiresAt`` or an ISO stamp: epoch seconds or
    milliseconds (a number, or a numeric string) or ISO 8601 (``Z`` or an offset; naive is
    UTC). None for anything else, never a guess."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            v = float(s)
        except ValueError:
            try:
                dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                return None
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
    if isinstance(v, (int, float)):
        if v != v or v <= 0:          # NaN, zero, negative: not a login expiry
            return None
        return v / 1000 if v > 1e11 else float(v)
    return None


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _str(v) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def interpret(data: dict, returncode: int, now: float, warn_days: int) -> dict:
    """What one parsed ``claude auth status --json`` answer means for the header."""
    logged_in = data.get("loggedIn")
    if not isinstance(logged_in, bool):
        return _unknown(f"claude auth status gave no loggedIn (exit {returncode})")
    if logged_in is False:
        return {"state": "logged_out"}
    if returncode != 0:
        # "logged in" on a failing exit is not an answer to trust
        return _unknown(f"claude auth status failed (exit {returncode})")
    email = _str(data.get("email"))
    if email is None:
        return _unknown("claude auth status reports logged in, but no account email")
    out = {
        "state": "ok",
        "email": email,
        "org_name": _str(data.get("orgName")),
        "plan": _str(data.get("subscriptionType")),
        "auth_method": _str(data.get("authMethod")),
        "expires_at": None,
        "expires_in_s": None,
        "warn_days": warn_days,
    }
    expires = parse_ts(data.get("loginExpiresAt"))
    if expires is not None:
        left = expires - now
        out["expires_at"] = _iso(expires)
        out["expires_in_s"] = round(left)
        if left <= warn_days * 86400:
            out["state"] = "expiring"
    return out


def read_cli() -> dict:
    """One ``claude auth status --json``, interpreted. Never raises."""
    try:
        r = subprocess.run(list(ARGV), stdin=subprocess.DEVNULL, capture_output=True, text=True,
                           timeout=CMD_TIMEOUT_S)
    except FileNotFoundError:
        return _unknown("claude CLI not found")
    except subprocess.TimeoutExpired:
        return _unknown(f"claude auth status timed out after {CMD_TIMEOUT_S}s")
    except OSError as e:
        return _unknown(f"claude auth status could not start ({e.__class__.__name__})")
    try:
        data = json.loads(r.stdout or "")
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return _unknown(f"claude auth status gave no JSON object (exit {r.returncode})")
    return interpret(data, r.returncode, time.time(), config.claude_login_warn_days())


def _load_store() -> dict:
    try:
        data = json.loads(_store_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_store(data: dict) -> None:
    path = _store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)   # atomic: a concurrent reader sees old or new, never half


def _valid_change(c) -> dict | None:
    if not isinstance(c, dict):
        return None
    frm, to, at = _str(c.get("from")), _str(c.get("to")), c.get("at")
    if frm is None or to is None or isinstance(at, bool) or not isinstance(at, (int, float)):
        return None
    return {"from": frm, "to": to, "at": float(at)}


def observe(answer: dict, now: float | None = None) -> dict | None:
    """Record ``answer``'s email as the last one seen; when it differs from the previous one,
    record the change. Returns the latest recorded change (or None). An answer without an
    email (logged out, unknown) changes nothing: logout-then-login to the same account is no
    switch, and to another account is one."""
    store = _load_store()
    email = answer.get("email") if answer.get("state") in ("ok", "expiring") else None
    if email:
        prev = _str(store.get("email"))
        changed = prev is None or prev.casefold() != email.casefold()
        if prev is not None and changed:
            store["change"] = {"from": prev, "to": email, "at": time.time() if now is None else now}
            log.info("claude account: the signed-in account changed")
        if changed:
            store["email"] = email
            try:
                _save_store(store)
            except OSError as e:
                log.warning("claude account: could not record the account (%s)", e.__class__.__name__)
    return _valid_change(store.get("change"))


def last_change() -> dict | None:
    """The latest account change recorded, or None. A file read, no subprocess."""
    return _valid_change(_load_store().get("change"))


def status(fresh: bool = False) -> dict:
    """The header's answer: the cached read while younger than :data:`TTL_S`, else a new one.
    ``fresh`` (a dashboard load) re-reads when the cache is older than :data:`FRESH_MIN_S`, and
    an "unknown" answer is re-read after :data:`UNKNOWN_TTL_S`. While another caller is reading
    the CLI (up to :data:`CMD_TIMEOUT_S`), a caller with a cached answer gets that answer at once
    instead of waiting on the lock."""
    global _cache, _cache_at
    if not _lock.acquire(blocking=_cache is None):
        return dict(_cache or {})
    try:
        age = time.monotonic() - _cache_at
        ttl = UNKNOWN_TTL_S if _cache is not None and _cache.get("state") == "unknown" else TTL_S
        if _cache is None or age >= ttl or (fresh and age >= FRESH_MIN_S):
            answer = read_cli()
            try:
                change = observe(answer)
            except Exception:
                log.exception("claude account: recording the account failed")
                change = None
            _cache = {**answer, "change": _public_change(change), "checked_at": _iso(time.time())}
            _cache_at = time.monotonic()
            if answer["state"] == "unknown":
                log.warning("claude account: unknown (%s)", answer.get("reason"))
        return dict(_cache)
    finally:
        _lock.release()


def _public_change(c: dict | None) -> dict | None:
    return None if c is None else {"from": c["from"], "to": c["to"], "at": _iso(c["at"])}


def switch_badge(created, change: dict | None) -> dict | None:
    """The badge for a running session started at ``created`` (ISO or epoch), given the latest
    recorded ``change``: the session MAY still use the old account when it started before the
    change. None when no change was ever observed or it started at or after it. A session whose
    start time is unknown gets the badge too (``started_unknown``): unknown is not "after"."""
    if change is None:
        return None
    started = parse_ts(created)
    if started is not None and started >= change["at"]:
        return None
    return {"from": change["from"], "to": change["to"], "at": _iso(change["at"]),
            "started_unknown": started is None}


def start_background_refresh(stop_event: threading.Event | None = None) -> threading.Thread:
    """Re-read every :data:`TTL_S` on a daemon thread, so an account switch is noticed (and
    the per-session badge appears) even while no dashboard is open. Call once, from the
    dashboard's ``main()``; never at import time or in tests."""
    stop_event = stop_event if stop_event is not None else threading.Event()

    def _loop():
        while not stop_event.is_set():
            try:
                status()
            except Exception:
                log.exception("claude account background refresh failed")
            unknown = (_cache or {}).get("state") == "unknown"
            stop_event.wait(UNKNOWN_TTL_S if unknown else TTL_S)

    t = threading.Thread(target=_loop, name="chela-claude-account-refresh", daemon=True)
    t.start()
    return t
