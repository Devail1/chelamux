"""Where the share bridges live — their own long-lived process (CMX-434).

A share's bridge (``collab_stream.Bridge``) used to run inside the dashboard, so every
dashboard deploy silently ended every live share. Now exactly ONE process is the
**collab host** at a time: whoever holds an exclusive ``flock`` on
``$CHELA_DIR/collab.lock``. The host runs the bridges, restores persisted shares when it
takes over (``collab_stream.restore_bridges``), and answers an owner-only control socket,
``$CHELA_DIR/collab.sock`` (0600, and every peer's uid checked with ``SO_PEERCRED``).

  * ``chela collab`` (PM2 ``chela-collab``) is the intended host. It waits for the lock,
    restores, serves. A dashboard deploy never touches it; only a change to the share code
    restarts it (``chela.update`` — see ``COLLAB_HOST_PATHS``).
  * The dashboard is a CLIENT of that socket. With no ``chela-collab`` running it hosts the
    bridges itself (taking the same lock), which is the old behaviour plus persistence:
    a dashboard restart then interrupts the shares, and the next dashboard restores them.

The lock file also records which process holds it (``{"role", "pid"}``), so ``chela
update`` / ``chela shares`` can say whose restart will interrupt the live shares.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import socket
import struct
import threading
from pathlib import Path

from chela import collab_stream, config, share_store

log = logging.getLogger(__name__)

LOCK_NAME = "collab.lock"
SOCK_NAME = "collab.sock"
SERVICE = "chela-collab"           # the PM2 name of the dedicated host
DASHBOARD = "chela-dashboard"      # the fallback host
CALL_TIMEOUT = 5.0                 # s — a control call; the host does no slow work inline

# The files a running collab host executes for a share. ``chela update`` restarts
# ``chela-collab`` only when one of these changed — every other deploy leaves live
# shares alone.
COLLAB_HOST_PATHS = (
    "chela/collab_host.py", "chela/collab_stream.py", "chela/share_store.py",
    "chela/e2e.py", "chela/share_sandbox.py", "chela/collab.py",
)


class HostUnavailable(RuntimeError):
    """No collab host answered the control socket."""


def lock_path() -> Path:
    return Path(config.CHELA_DIR) / LOCK_NAME


def sock_path() -> Path:
    return Path(config.CHELA_DIR) / SOCK_NAME


# --- the host lock --------------------------------------------------------------------
_held: dict[str, int] = {}     # lock path -> fd we hold it with
_held_lock = threading.Lock()


def try_become_host(role: str) -> bool:
    """Take the host lock without blocking. True if this process is (now) the host."""
    p = str(lock_path())
    with _held_lock:
        if p in _held:
            return True
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(p, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({"role": role, "pid": os.getpid()}).encode())
        _held[p] = fd
        return True


def is_host() -> bool:
    return str(lock_path()) in _held


def release_host() -> None:
    with _held_lock:
        fd = _held.pop(str(lock_path()), None)
    if fd is not None:
        try:
            os.ftruncate(fd, 0)   # no stale "who hosts" for current_host() to read
        except OSError:
            pass
        try:
            os.close(fd)   # closing the description drops the flock
        except OSError:
            pass


def current_host() -> dict | None:
    """``{"role", "pid"}`` of the live host, or None. Read-only: never touches the lock
    (a probe flock could make a starting host lose its race)."""
    try:
        with open(lock_path(), encoding="utf-8") as f:
            info = json.load(f)
        pid = int(info["pid"])
        os.kill(pid, 0)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return {"role": str(info.get("role") or ""), "pid": pid}


def interruption_notice(restarting: list[str]) -> str:
    """The line a deploy prints BEFORE restarting ``restarting`` (PM2 names): non-empty
    only when that restart takes down the process hosting live shares."""
    n = share_store.count()
    host = current_host()
    if not n:
        return ""
    role = host["role"] if host else ""
    if host and role not in restarting:
        return ""
    who = role or "the share host"
    return (f"⚠️ {n} live share(s) will be interrupted by restarting {who} — guests see "
            "“host restarting…” and reconnect by themselves when it is back.")


# --- the control socket (host side) -----------------------------------------------------

def _peer_uid(conn: socket.socket) -> int | None:
    try:
        creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        return struct.unpack("3i", creds)[1]
    except OSError:
        return None


def handle(req: dict) -> dict:
    """One control request → its reply. The host side of every facade call below."""
    op = req.get("op")
    wid = str(req.get("wid") or "")
    if op == "ping":
        host = current_host()
        return {"ok": True, "role": host["role"] if host else ""}
    if op == "list":
        return {"ok": True, "shares": collab_stream.list_shares()}
    if op == "info":
        return {"ok": True, "info": collab_stream.share_info(wid)}
    if op == "start":
        code = collab_stream.start_bridge(
            wid, allow_typing=bool(req.get("allow_typing")), unsandboxed=req.get("unsandboxed"),
            share_epoch=req.get("share_epoch"))
        return {"ok": True, "code": code}
    if op == "stop":
        collab_stream.stop_bridge(wid)
        return {"ok": True}
    if op == "set_mode":
        try:
            changed = collab_stream.set_share_mode(
                wid, str(req.get("mode") or ""), changed_by=str(req.get("changed_by") or ""),
                window=req.get("window"), ttl_s=req.get("ttl_s"))
        except ValueError as e:
            return {"ok": False, "error": str(e), "kind": "value"}
        return {"ok": True, "changed": changed}
    return {"ok": False, "error": f"unknown op: {op}"}


def _serve_conn(conn: socket.socket) -> None:
    with conn:
        conn.settimeout(CALL_TIMEOUT)
        if _peer_uid(conn) != os.getuid():
            return   # owner-only, whatever the file mode says
        buf = b""
        while not buf.endswith(b"\n") and len(buf) < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        try:
            req = json.loads(buf.decode("utf-8"))
            reply = handle(req) if isinstance(req, dict) else {"ok": False, "error": "bad request"}
        except Exception as e:  # noqa: BLE001
            log.exception("collab_host: request failed")
            reply = {"ok": False, "error": str(e)}
        conn.sendall(json.dumps(reply).encode("utf-8") + b"\n")


def serve(stop: threading.Event) -> socket.socket:
    """Bind the control socket (we hold the lock, so any socket file there is stale) and
    serve it on a daemon thread until ``stop``."""
    p = sock_path()
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old = os.umask(0o177)
    try:
        srv.bind(str(p))
    finally:
        os.umask(old)
    os.chmod(p, 0o600)
    srv.listen(16)
    srv.settimeout(1.0)

    def loop():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            threading.Thread(target=_serve_conn, args=(conn,), daemon=True).start()

    threading.Thread(target=loop, name="collab-host", daemon=True).start()
    return srv


# --- the client (dashboard side) ----------------------------------------------------------

def call(op: str, **kw) -> dict:
    """One request to the running host. Raises HostUnavailable if none answers."""
    p = sock_path()
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(CALL_TIMEOUT)
            s.connect(str(p))
            s.sendall(json.dumps({"op": op, **kw}).encode("utf-8") + b"\n")
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except OSError as e:
        raise HostUnavailable(str(e)) from e
    try:
        return json.loads(buf.decode("utf-8"))
    except ValueError as e:
        raise HostUnavailable(f"bad reply: {e}") from e


# --- the facade app.py calls ----------------------------------------------------------------
# Local (this process is the host) → collab_stream directly, exactly as before CMX-434.
# Remote (chela-collab is) → the socket. Neither → take the lock and become the host.
_hooks: dict = {"on_revoke": None, "on_restored": None}


def set_local_hooks(*, on_revoke=None, on_restored=None) -> None:
    """The dashboard's callbacks for when IT hosts: a bridge failing closed, and the
    shares a takeover restored (so its own share table can show them)."""
    _hooks["on_revoke"] = on_revoke
    _hooks["on_restored"] = on_restored


def become_local_host(role: str = DASHBOARD) -> bool:
    """Take the host role and restore whatever was persisted. False if another process
    holds it."""
    if is_host():
        return True
    if not try_become_host(role):
        return False
    restored = collab_stream.restore_bridges(on_revoke=_hooks["on_revoke"])
    cb = _hooks["on_restored"]
    if cb and restored:
        try:
            cb([r for r in restored if r["restored"]])
        except Exception:  # noqa: BLE001
            log.exception("collab_host: on_restored hook failed")
    return True


def _remote(op: str, **kw) -> dict | None:
    """The reply from the host, or None when THIS process should act locally: it is the
    host, or nobody answers and it just became the host."""
    if is_host():
        return None
    try:
        return call(op, **kw)
    except HostUnavailable:
        if become_local_host():
            return None
        raise


def start_bridge(wid: str, on_revoke=None, *, share_epoch: int | None = None, **policy) -> str | None:
    """``policy`` = ``collab_stream.start_bridge``'s ``allow_typing`` / ``unsandboxed``."""
    r = _remote("start", wid=wid, share_epoch=share_epoch, **policy)
    if r is None:
        return collab_stream.start_bridge(wid, on_revoke=on_revoke, share_epoch=share_epoch, **policy)
    return r.get("code")


def stop_bridge(wid: str) -> None:
    try:
        r = _remote("stop", wid=wid)
    except HostUnavailable:
        share_store.drop(wid)   # nobody is streaming it; make sure no restart revives it
        return
    if r is None:
        collab_stream.stop_bridge(wid)
        release_if_idle()


def release_if_idle() -> None:
    """A dashboard that hosts NO live share gives the lock back, so a waiting
    ``chela-collab`` takes over and the next share lands there, out of reach of the next
    dashboard deploy. The dedicated service never lets go."""
    host = current_host()
    if is_host() and host and host["pid"] == os.getpid() and host["role"] == DASHBOARD \
            and not collab_stream.list_shares():
        release_host()


def share_state(wid: str) -> dict | None:
    st = collab_stream.share_state(wid)    # a bridge in THIS process answers for itself
    if st is not None or is_host():
        return st
    shares = remote_listing()
    st = (shares or {}).get(wid)
    return {"mode": st["mode"], "expires_at": st["expires_at"]} if st else None


def set_share_mode(wid: str, mode: str, **kw) -> dict | None:
    r = _remote("set_mode", wid=wid, mode=mode, **kw)
    if r is None:
        return collab_stream.set_share_mode(wid, mode, **kw)
    if not r.get("ok"):
        raise ValueError(r.get("error") or "share mode change refused")
    return r.get("changed")


def remote_listing() -> dict[str, dict] | None:
    """The remote host's live shares, or None when this process hosts them (its own
    table is kept by the on_revoke hook) or no host answers (keep what we know)."""
    if is_host():
        return None
    try:
        r = call("list")
    except HostUnavailable:
        return None
    return r.get("shares") if r.get("ok") else None


def remote_info(wid: str) -> dict | None:
    try:
        r = call("info", wid=wid)
    except HostUnavailable:
        return None
    return r.get("info")


def join_url(wid: str) -> str:
    return collab_stream.join_url(wid)


# --- `chela collab` -------------------------------------------------------------------------

def run_service(stop: threading.Event | None = None, *, poll: float = 1.0) -> None:
    """The dedicated host: wait for the lock (a dashboard may be hosting until its next
    restart), restore the persisted shares, serve the socket. On SIGINT/SIGTERM every
    bridge tells its guests "restarting" and the store is kept for the next host."""
    import signal

    stop = stop or threading.Event()
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
    waited = False
    while not stop.is_set():
        if try_become_host(SERVICE):
            break
        if not waited:
            host = current_host()
            log.info("collab host: waiting for the host lock (held by %s)",
                     f"{host['role']} pid {host['pid']}" if host else "another process")
            waited = True
        stop.wait(poll)
    if stop.is_set():
        return
    restored = collab_stream.restore_bridges()
    log.info("collab host up: %d share(s) restored, %d not", sum(r["restored"] for r in restored),
             sum(not r["restored"] for r in restored))
    srv = serve(stop)
    try:
        stop.wait()
    finally:
        n = collab_stream.shutdown_all()
        log.info("collab host stopping: %d share(s) told 'restarting' and kept", n)
        try:
            srv.close()
        finally:
            try:
                sock_path().unlink()
            except FileNotFoundError:
                pass
            release_host()
