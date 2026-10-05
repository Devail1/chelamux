"""🙋 A sandboxed guest REQUESTS more access; the operator APPROVES it (CMX-7).

A sandboxed session (:mod:`chela.share_sandbox`) sees one workspace and, in web mode, the
public web through a filter. Sometimes the guest needs more: another directory, a domain an
operator allow-list leaves out, or something only the operator can do. This module is the
host half of asking for it. It works like NanoClaw's OneCLI gateway: the sandbox can only
ASK, and a human on the host decides.

**How a request travels.** The guest (or its Claude) runs ``chela-request`` inside the
container, which POSTs to ``/chela/request`` on the session's credential proxy
(:mod:`chela.share_proxy`). The proxy appends one line to ``requests.jsonl`` in the
session's host directory (:func:`chela.share_sandbox.session_dir`). That directory is mounted
into the PROXY only, never the guest, and the proxy offers no way to read it back. So the
guest can file a request and learn nothing else: not the list, not a decision. A request
never acts by itself.

**Where decisions live.** :func:`ingest` copies new lines into ``$CHELA_DIR/share-requests.json``
(mode 0600, never mounted into any container). Only the operator's dashboard routes and the
``chela share-requests`` CLI write a decision, and ``chela.mergegate`` refuses both to a Claude
session. Nothing approves on its own: no default, no timer, no rule.

**What an approval does.**

* **mount** — the guest container is RE-LAUNCHED by its own launcher with the extra bind
  mount at ``/extra/<name>``, read-only unless the operator explicitly allowed write and the
  guest asked for it. A running container's privileges are never widened.
  :func:`mount_refusal` is applied when the operator approves, again every time the launcher
  builds the container (:func:`mount_specs`), and again by the live sandbox check. The
  secrets directories, ``$HOME`` and its ancestors, the tmux socket directory, system
  directories and ``/mnt/*`` are refused even if a record says "approved".
* **domain** — web-mode sessions only. When the operator set ``CHELA_SHARE_WEB_ALLOW``, the
  approved domain is added to that session's allow-list (the web proxy sidecar is restarted
  with it). Without an allow-list every public host is already reachable, so the approval is
  only recorded. The operator's ``CHELA_SHARE_WEB_DENY`` always wins.
* **operation** — recorded and audited only. chela never runs anything for the guest; the
  operator does the operation by hand.

**When it ends.** An approval lasts :data:`DEFAULT_MINUTES` unless the operator picks
another duration (1 min to :data:`MAX_MINUTES`). :func:`active_grants` drops it the moment its
time is up, without waiting for anything to sweep it, and the launcher then relaunches the
container without it. Stopping the share, or turning *Guest typing* off, revokes every
approval of that session (:func:`revoke_session` / :func:`revoke_all`).

Every request, approval, denial, refusal, expiry and revocation is a ``share.request_*``
event in the event log.
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import logging
import os
import re
import time
from pathlib import Path

from chela import config, event_log

log = logging.getLogger(__name__)

KIND_MOUNT, KIND_DOMAIN, KIND_OPERATION = "mount", "domain", "operation"
KINDS = (KIND_MOUNT, KIND_DOMAIN, KIND_OPERATION)
ACCESS_RO, ACCESS_RW = "ro", "rw"

PENDING, APPROVED, DENIED, EXPIRED, REVOKED = "pending", "approved", "denied", "expired", "revoked"

DEFAULT_MINUTES = 60
MAX_MINUTES = 24 * 60
# A guest can file at most this many requests per session; later lines are ignored, so a
# runaway loop can't flood the operator's phone.
MAX_PER_SESSION = 50
MAX_TARGET = 512
MAX_REASON = 1000
REQUESTS_NAME = "requests.jsonl"
EXTRA_ROOT = "/extra"

_SID_RE = re.compile(r"^[0-9a-f]{12}$")
_DOMAIN_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z][a-z0-9-]{0,62}$")
_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")

# Host paths a mount may never be, sit inside, or contain — whatever a record says.
# Under $HOME: the same secrets list a workspace is refused for (chela.share_sandbox).
_SYSTEM_DIRS = ("/proc", "/sys", "/dev", "/run", "/var/run", "/etc", "/boot", "/root",
                "/var/lib/docker", "/mnt")


# --- the store -------------------------------------------------------------------------

def store_path() -> Path:
    return Path(config.CHELA_DIR) / "share-requests.json"


def _empty() -> dict:
    return {"requests": {}, "sessions": {}, "ingested": {}}


def _load() -> dict:
    try:
        data = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    base = _empty()
    for k in base:
        if isinstance(data.get(k), dict):
            base[k] = data[k]
    return base


def _save(store: dict) -> None:
    path = store_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(store, f, indent=1)
    os.replace(tmp, path)


@contextlib.contextmanager
def _locked():
    """The store, under an exclusive lock, saved on a clean exit. The dashboard, the CLI and
    every sandboxed session's launcher all write it."""
    lock = store_path().with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        store = _load()
        before = json.dumps(store, sort_keys=True)
        yield store
        if json.dumps(store, sort_keys=True) != before:   # a launcher polls every 2 s
            _save(store)
    finally:
        os.close(fd)


def _audit(kind: str, summary: str, rec: dict, **extra) -> None:
    payload = {k: rec.get(k) for k in ("id", "sid", "kind", "target", "access", "status",
                                       "expires_at", "decided_by", "wid")}
    payload.update(extra)
    event_log.append(kind, summary, payload, wid=rec.get("wid") or None)


# --- the deny-list ---------------------------------------------------------------------

def _home() -> str:
    return os.path.realpath(os.path.expanduser("~"))


def _tmux_socket_dir() -> str:
    base = os.environ.get("TMUX_TMPDIR") or "/tmp"
    return os.path.join(os.path.realpath(base), f"tmux-{os.getuid()}")


def protected_paths() -> list[str]:
    """Paths a mount may not be, sit inside, or contain."""
    from chela import share_sandbox
    from chela.transcripts import claude_config_dir
    home = _home()
    out = [os.path.join(home, d) for d in share_sandbox.SECRET_DIRS]
    out += [os.path.realpath(str(config.CHELA_DIR)), os.path.realpath(str(claude_config_dir())),
            os.path.realpath(str(share_sandbox.session_root())),
            os.path.realpath(str(share_sandbox.transcripts_root())), _tmux_socket_dir()]
    out += list(_SYSTEM_DIRS)
    return out


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def mount_refusal(path: str) -> str | None:
    """Why ``path`` may not be mounted into a sandboxed session, or None. Checked on the
    path as written AND on what its symlinks resolve to, BEFORE anything about whether it
    exists — so a refused path reads as refused, never as merely missing."""
    if not isinstance(path, str) or not path.strip():
        return "no path given"
    if any(c in path for c in ":,\n\r\0"):
        return "the path contains a character a mount can't carry (: , or a newline)"
    raw = os.path.normpath(os.path.expanduser(path.strip()))
    if not os.path.isabs(raw):
        return "the path must be absolute (a host path, e.g. /home/you/project)"
    home = _home()
    for p in dict.fromkeys((raw, os.path.realpath(raw))):
        if p == os.sep:
            return "the filesystem root can never be mounted"
        if p == home or _under(home, p):
            return "your home directory (or a parent of it) can never be mounted"
        for prot in protected_paths():
            if _under(p, prot):
                return f"{prot} holds secrets or system state — it can never be mounted"
            if _under(prot, p):
                return f"{path} contains {prot}, which holds secrets or system state"
    real = os.path.realpath(raw)
    if not (os.path.isdir(real) or os.path.isfile(real)):
        return f"no such directory or file: {path}"
    return None


def domain_refusal(domain: str) -> str | None:
    """Why ``domain`` can't be added to a session's web allow-list, or None."""
    d = (domain or "").strip().lower().rstrip(".")
    if not _DOMAIN_RE.match(d):
        return "not a domain name (an IP address or a URL is not accepted)"
    deny = [x.strip().lower().strip(".") for x in os.environ.get("CHELA_SHARE_WEB_DENY", "").split(",")
            if x.strip(". ")]
    if any(d == x or d.endswith("." + x) for x in deny):
        return f"{d} is on the operator's CHELA_SHARE_WEB_DENY list"
    return None


# --- ingest (guest → host) -------------------------------------------------------------

def requests_path(sid: str) -> Path:
    from chela import share_sandbox
    return share_sandbox.session_dir(sid) / REQUESTS_NAME


def _clean(line: str) -> dict | None:
    """One line the proxy wrote → the fields a record keeps, or None when malformed. The
    proxy already checked the shape; this is the host's own check, since a line is
    guest-authored data."""
    try:
        d = json.loads(line)
    except ValueError:
        return None
    if not isinstance(d, dict) or d.get("kind") not in KINDS:
        return None
    target, reason = d.get("target"), d.get("reason") or ""
    if not isinstance(target, str) or not target.strip() or not isinstance(reason, str):
        return None
    access = d.get("access") or ACCESS_RO
    if access not in (ACCESS_RO, ACCESS_RW):
        return None
    target = target.strip()[:MAX_TARGET]
    if d["kind"] == KIND_DOMAIN:
        target = target.lower().rstrip(".")
    return {"kind": d["kind"], "target": target, "reason": reason.strip()[:MAX_REASON],
            "access": access if d["kind"] == KIND_MOUNT else None}


def register_session(sid: str, *, cwd: str, net: str, wid: str | None = None,
                     window: str | None = None) -> None:
    """The launcher records which window and mode a session id is — display only."""
    if not _SID_RE.match(sid):
        return
    with _locked() as store:
        store["sessions"][sid] = {"cwd": cwd, "net": net, "wid": wid, "window": window,
                                  "started_at": time.time()}


def ingest(sid: str, now: float | None = None) -> list[dict]:
    """Copy the session's new request lines into the store; return the new records. Each is
    audited (``share.request_filed``) and pushed to the operator once."""
    if not _SID_RE.match(sid):
        return []
    try:
        lines = requests_path(sid).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    now = time.time() if now is None else now
    new: list[dict] = []
    with _locked() as store:
        done = int(store["ingested"].get(sid) or 0)
        if len(lines) <= done:
            return []
        meta = store["sessions"].get(sid) or {}
        count = sum(1 for r in store["requests"].values() if r.get("sid") == sid)
        for n in range(done, len(lines)):
            fields = _clean(lines[n])
            if fields is None or count >= MAX_PER_SESSION:
                continue
            count += 1
            rec = {"id": f"{sid}-{n}", "sid": sid, **fields, "status": PENDING,
                   "filed_at": now, "wid": meta.get("wid"), "window": meta.get("window"),
                   "net": meta.get("net")}
            store["requests"][rec["id"]] = rec
            new.append(rec)
        store["ingested"][sid] = len(lines)
    for rec in new:
        _audit("share.request_filed", f"🙋 {_who(rec)} asks: {describe(rec)}", rec,
               reason=rec["reason"])
        _push(rec)
    return new


def ingest_all(now: float | None = None) -> list[dict]:
    from chela import share_sandbox
    out: list[dict] = []
    try:
        dirs = [d for d in share_sandbox.session_root().iterdir() if _SID_RE.match(d.name)]
    except OSError:
        return out
    for d in dirs:
        out += ingest(d.name, now)
    return out


def _who(rec: dict) -> str:
    return rec.get("window") or rec.get("wid") or f"sandbox {rec.get('sid')}"


def describe(rec: dict) -> str:
    t = rec.get("target")
    if rec.get("kind") == KIND_MOUNT:
        return f"mount {t} ({'read-write' if rec.get('access') == ACCESS_RW else 'read-only'})"
    if rec.get("kind") == KIND_DOMAIN:
        return f"web access to {t}"
    return f"operation: {t}"


def _push(rec: dict) -> None:
    try:
        from chela import notify
        if notify.enabled():
            notify.send(f"🙋 {_who(rec)} asks: {describe(rec)} — approve or deny it in the "
                        "dashboard", title="chela: access request")
    except Exception:  # noqa: BLE001 — a notification must never break ingest
        log.exception("share_requests: push failed")


# --- decisions (operator only) ---------------------------------------------------------

def clamp_minutes(raw) -> int:
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_MINUTES
    return max(1, min(MAX_MINUTES, n))


def approval_refusal(rec: dict, *, rw: bool = False, net: str | None = None) -> str | None:
    """Why ``rec`` can't be approved right now, or None. Shown in the UI before the click."""
    kind = rec.get("kind")
    if kind == KIND_MOUNT:
        why = mount_refusal(rec.get("target") or "")
        if why:
            return why
        if rw and rec.get("access") != ACCESS_RW:
            return "the guest asked for read-only access"
        return None
    if kind == KIND_DOMAIN:
        why = domain_refusal(rec.get("target") or "")
        if why:
            return why
        if (net or rec.get("net")) != "web":
            return "this session has no web access — domain approvals apply to web sessions only"
        return None
    return None


def approve(rid: str, *, by: str, minutes=None, rw: bool = False,
            now: float | None = None) -> tuple[bool, str]:
    """The operator approves ``rid``. Mounts are read-only unless ``rw`` AND the guest asked
    for write. Refused while *Guest typing* is off (that switch revokes approvals)."""
    if not config.share_typing_enabled():
        return False, "Guest typing is off — turn it on in Settings before approving requests"
    now = time.time() if now is None else now
    mins = DEFAULT_MINUTES if minutes in (None, "") else clamp_minutes(minutes)
    with _locked() as store:
        rec = store["requests"].get(rid)
        if rec is None:
            return False, "no such request"
        if rec.get("status") != PENDING:
            return False, f"this request is already {rec.get('status')}"
        why = approval_refusal(rec, rw=rw, net=(store["sessions"].get(rec["sid"]) or {}).get("net"))
        if why is None and rec.get("kind") == KIND_MOUNT:
            rec["path"] = os.path.realpath(os.path.expanduser(rec["target"]))
        if why is None:
            rec.update(status=APPROVED, decided_by=by, decided_at=now,
                       expires_at=now + mins * 60, minutes=mins,
                       rw=bool(rw and rec.get("kind") == KIND_MOUNT))
        snapshot = dict(rec)
    if why:
        _audit("share.request_refused", f"⛔ refused to approve {describe(snapshot)}: {why}",
               snapshot, by=by, reason=why)
        return False, why
    _audit("share.request_approved", f"✅ {by} approved {describe(snapshot)} for "
           f"{_who(snapshot)} ({mins} min{', read-write' if snapshot['rw'] else ''})",
           snapshot, by=by, minutes=mins, rw=snapshot["rw"])
    return True, "approved"


def deny(rid: str, *, by: str, now: float | None = None) -> tuple[bool, str]:
    now = time.time() if now is None else now
    with _locked() as store:
        rec = store["requests"].get(rid)
        if rec is None:
            return False, "no such request"
        if rec.get("status") != PENDING:
            return False, f"this request is already {rec.get('status')}"
        rec.update(status=DENIED, decided_by=by, decided_at=now)
        snapshot = dict(rec)
    _audit("share.request_denied", f"✋ {by} denied {describe(snapshot)}", snapshot, by=by)
    return True, "denied"


def _revoke_where(match, reason: str, by: str, now: float | None) -> list[dict]:
    now = time.time() if now is None else now
    out: list[dict] = []
    with _locked() as store:
        for rec in store["requests"].values():
            if rec.get("status") == APPROVED and match(rec):
                rec.update(status=REVOKED, revoked_at=now, revoked_by=by, revoke_reason=reason)
                out.append(dict(rec))
    for rec in out:
        _audit("share.request_revoked", f"🛑 approval revoked ({reason}): {describe(rec)}",
               rec, by=by, reason=reason)
    return out


def revoke(rid: str, *, by: str, reason: str = "revoked by the operator",
           now: float | None = None) -> list[dict]:
    return _revoke_where(lambda r: r.get("id") == rid, reason, by, now)


def revoke_session(sid: str, reason: str, *, by: str = "chela",
                   now: float | None = None) -> list[dict]:
    """The kill switch for one session: every approval it holds ends now."""
    return _revoke_where(lambda r: r.get("sid") == sid, reason, by, now)


def revoke_all(reason: str, *, by: str = "chela", now: float | None = None) -> list[dict]:
    return _revoke_where(lambda r: True, reason, by, now)


def sweep(now: float | None = None) -> list[dict]:
    """Mark approvals whose time is up as expired (audited). Enforcement does NOT depend on
    this — :func:`active_grants` checks the expiry itself."""
    now = time.time() if now is None else now
    out: list[dict] = []
    with _locked() as store:
        for rec in store["requests"].values():
            if rec.get("status") == APPROVED and not now < float(rec.get("expires_at") or 0):
                rec["status"] = EXPIRED
                out.append(dict(rec))
    for rec in out:
        _audit("share.request_expired", f"⌛ approval expired: {describe(rec)}", rec)
    return out


# --- what a session is allowed right now ------------------------------------------------

def active_grants(sid: str, now: float | None = None) -> list[dict]:
    """Approvals of ``sid`` in force at ``now``: approved, not yet expired. THE gate the
    launcher and the live sandbox check read — nothing else grants access."""
    now = time.time() if now is None else now
    return [r for r in _load()["requests"].values()
            if r.get("sid") == sid and r.get("status") == APPROVED
            and now < float(r.get("expires_at") or 0)]


def mount_specs(sid: str, now: float | None = None) -> list[tuple[str, str, bool]]:
    """``(host path, container path, read-write)`` for each mount ``sid`` may have now. The
    deny-list is applied AGAIN here: an "approved" record for a refused path, or a path whose
    symlinks now resolve somewhere else than when it was approved, mounts nothing."""
    picked: list[tuple[str, bool]] = []
    for r in active_grants(sid, now):
        path = r.get("path")
        if r.get("kind") != KIND_MOUNT or not isinstance(path, str):
            continue
        if mount_refusal(path) or os.path.realpath(path) != path:
            continue
        picked.append((path, bool(r.get("rw"))))
    out: list[tuple[str, str, bool]] = []
    used: set[str] = set()
    for path, rw in sorted(set(picked)):
        if (path, not rw) in picked and not rw:
            continue   # the same path granted both ways: the read-write grant wins
        base = _NAME_RE.sub("-", os.path.basename(path)).strip(".-") or "mount"
        name, n = base, 2
        while name in used:
            name, n = f"{base}-{n}", n + 1
        used.add(name)
        out.append((path, f"{EXTRA_ROOT}/{name}", rw))
    return out


def approved_domains(sid: str, now: float | None = None) -> list[str]:
    return sorted({r["target"] for r in active_grants(sid, now)
                   if r.get("kind") == KIND_DOMAIN and domain_refusal(r.get("target") or "") is None})


# --- the operator's view ----------------------------------------------------------------

def listing(now: float | None = None, limit: int = 50) -> list[dict]:
    """Pending requests (oldest first), then approvals in force, then the most recent
    decided ones — each with ``refusal`` (why Approve is disabled) and ``seconds_left``."""
    now = time.time() if now is None else now
    store = _load()
    recs = list(store["requests"].values())
    out: list[dict] = []
    for r in recs:
        r = dict(r)
        net = (store["sessions"].get(r.get("sid")) or {}).get("net") or r.get("net")
        r["description"] = describe(r)
        if r.get("status") == PENDING:
            r["refusal"] = approval_refusal(r, net=net)
        if r.get("status") == APPROVED:
            r["seconds_left"] = max(0, int(float(r.get("expires_at") or 0) - now))
        out.append(r)
    order = {PENDING: 0, APPROVED: 1}
    out.sort(key=lambda r: (order.get(r.get("status"), 2),
                            r.get("filed_at") or 0 if r.get("status") == PENDING
                            else -(r.get("decided_at") or r.get("filed_at") or 0)))
    return out[:limit]
