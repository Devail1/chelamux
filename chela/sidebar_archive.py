"""The sidebar's archive (CMX-75): server-side, and an archived session is CLOSED.

Before this, "Archive" only hid a row in ONE browser (``localStorage``): the window kept
running — its memory, its Remote Control entry, its Telegram topic — and the phone and the
laptop disagreed about what was archived. Now it works the way the Claude desktop app's
archive does:

* **The archive state lives here**, under ``CHELA_DIR``, behind the dashboard's
  ``/api/sidebar/archive`` routes — every device sees the same set.
* **Archiving a HUMAN session closes its window** (:func:`archive_window`), after recording
  what it takes to bring it back: the Claude session id, the cwd, the window name and its
  manual-name flag, the Remote Control name and the bound Telegram topic. The transcript
  stays on disk. The window is killed in THIS chela session only (``<session>:@N``), the
  way ``chela close @N`` does it.
* **Unarchiving resumes it** (:func:`unarchive_session`): a new window in the same cwd,
  under the same name, running ``claude --resume <session id>`` (with ``--remote-control
  <name>`` when it had Remote Control), through the ONE window-open path
  (:func:`chela.spawn.spawn_window`), and a rebind request for its old topic
  (:func:`request_rebind`) that the Telegram daemon honours by REOPENING that topic
  instead of creating a new one (:func:`chela.telegram.reconcile.reconcile_bindings`).
* **A dispatched run is only HIDDEN** (:func:`hide`): its agent and judge windows belong
  to the dispatcher's lifecycle, never to the sidebar.

⛔ **Unknown never reads as OK.** A close you cannot resume loses the session, so archive
refuses whenever it cannot say who the session is (no resolvable session id with a
transcript on disk), or whether it is safe (busy, waiting on you, the orchestrator, a
dispatched window, a status it cannot read while Claude is running).

Telegram: closing the window is what closes its topic — the daemon's reconcile loop
reaps a dead window's binding with ``closeForumTopic`` (archive, never delete). Nothing
here writes ``telegram-bindings.json``: the daemon holds that registry in memory and
saves it from there, so a second writer would be overwritten on its next tick (see
:mod:`chela.sessionids`). The rebind request is a separate file for the same reason.

Store shape (``CHELA_DIR/sidebar-archive.json``)::

    {"hidden":   {"<item key>": <archived_at>, ...},          # hide-only rows (runs)
     "sessions": {"s:<session id>": {<resume record>}, ...}}  # closed human sessions
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass, field

from chela import config, epoch
from chela.config import CHELA_DIR

log = logging.getLogger(__name__)

_STORE = CHELA_DIR / "sidebar-archive.json"
_REBINDS = CHELA_DIR / "topic-rebinds.json"
_LOCK = threading.Lock()

# A rebind request older than this is dropped: its window never came up as an agent (the
# spawn half-launched, or it was closed again), and a stale request must never reopen an
# old topic into a window that happens to wear the same id much later.
REBIND_TTL_SECONDS = 15 * 60

_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_KEY_RE = re.compile(r"^(?:w:|run:|s:)[^\s]{1,200}$")


def session_key(session_id: str) -> str:
    """The item key a closed session is archived under — its stable identity, since the
    ``@N`` it lived at is gone."""
    return f"s:{session_id}"


# --- the store ----------------------------------------------------------------------------

def _read(path) -> dict:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, path)   # atomic: a concurrent reader sees old or new, never half


def _load() -> dict:
    data = _read(_STORE)
    hidden = data.get("hidden")
    sessions = data.get("sessions")
    return {
        "hidden": dict(hidden) if isinstance(hidden, dict) else {},
        "sessions": {k: v for k, v in sessions.items() if isinstance(v, dict)}
        if isinstance(sessions, dict) else {},
    }


def _save(data: dict) -> None:
    _write(_STORE, {"hidden": data["hidden"], "sessions": data["sessions"]})


def state() -> dict:
    """What the sidebar renders: ``{"hidden": [keys], "sessions": [records]}``, sessions
    newest-archived first."""
    data = _load()
    sessions = sorted(data["sessions"].values(), key=lambda r: -(r.get("archived_at") or 0))
    return {"hidden": sorted(data["hidden"]), "sessions": sessions}


def archived_session_ids() -> set[str]:
    """Every session id the archive holds closed — so "Recent sessions" (``/api/restore``)
    does not offer the same dead session a second time."""
    return {r.get("session_id") for r in _load()["sessions"].values() if r.get("session_id")}


def valid_key(key: object) -> bool:
    return isinstance(key, str) and bool(_KEY_RE.match(key))


def hide(keys: list[str]) -> dict:
    """Hide rows without closing anything (a settled dispatched run; a migrated
    localStorage key). Idempotent. Returns :func:`state`."""
    with _LOCK:
        data = _load()
        now = time.time()
        for k in keys:
            if valid_key(k) and not k.startswith("s:") and k not in data["hidden"]:
                data["hidden"][k] = now
        _save(data)
    return state()


def unhide(keys: list[str]) -> dict:
    """Drop hide-only keys (Unarchive on a run row; a row that woke up). Returns
    :func:`state`. A closed session (``s:``) is NOT unhidden here — that is a resume
    (:func:`unarchive_session`), never a silent forget."""
    with _LOCK:
        data = _load()
        drop = set(keys)
        data["hidden"] = {k: v for k, v in data["hidden"].items() if k not in drop}
        _save(data)
    return state()


# --- archive: close a human session -------------------------------------------------------

@dataclass
class Outcome:
    ok: bool
    error: str | None = None
    status: int = 200
    record: dict | None = None
    wid: str | None = None
    name: str | None = None
    extra: dict = field(default_factory=dict)


def resolve_session_id(wid: str, pane=None) -> str | None:
    """The Claude session ``wid`` is running — ONLY when it is positively identified AND its
    transcript is on disk (``claude --resume`` needs it). The cwd guess
    (:func:`chela.sessions.resolve_window`'s last tier) is not an identification, so it
    does not count. ``None`` = unknown, and unknown refuses the archive."""
    from chela import sessions

    try:
        res = sessions.resolve_window(wid, pane=pane)
    except Exception:  # noqa: BLE001 — an unreadable window is an unknown one
        log.warning("archive: resolving %s's session failed", wid, exc_info=True)
        return None
    if not res.ok or not res.session_id or res.source in ("cwd", "none"):
        return None
    if not _SESSION_ID_RE.match(res.session_id):
        return None
    return res.session_id


def window_status(wid: str) -> tuple[bool, str | None]:
    """``(claude_running, busy|idle|waiting|None)`` for ``wid`` — the native
    ``claude agents --json`` view (:func:`chela.agent_manager.session_entry`), the one
    authority for busy/idle/waiting."""
    from chela import agent_manager

    cpid = agent_manager.claude_pid(wid)
    if cpid is None:
        return False, None
    entry = agent_manager.session_entry(cpid, agent_manager.session_status_map())
    return True, (entry["status"] if entry else None)


def _rc_name(wid: str, window_name: str) -> str | None:
    """The name the session shows in claude.ai / the desktop, or None without Remote Control."""
    from chela import rc_rename

    try:
        name = rc_rename.current_name(wid)
    except Exception:  # noqa: BLE001 — a tmux hiccup reads as "no Remote Control"
        return None
    if not name:
        return None
    return window_name if name == rc_rename.UNKNOWN_PUSHED else name


def _bound_thread(wid: str) -> str | None:
    """The Telegram topic ``wid`` is bound to — READ-only (the daemon owns that file)."""
    try:
        from chela.telegram.bindings import BindingRegistry

        return BindingRegistry.load().thread_for_window(wid)
    except Exception:  # noqa: BLE001 — no bridge / unreadable file = no topic to restore
        return None


def close_window(wid: str) -> str | None:
    """Kill ``wid`` inside THIS chela session (``<session>:@N``, so even a race cannot reach
    another tmux session) — the same kill ``chela close @N`` issues. ``None`` on success,
    else the error."""
    try:
        proc = subprocess.run(["tmux", "kill-window", "-t", f"{config.current_session()}:{wid}"],
                              capture_output=True, text=True, timeout=10)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return str(e)
    if proc.returncode != 0:
        return (proc.stderr or proc.stdout or "tmux kill-window failed").strip()
    return None


def archive_window(wid: str, *, orch_wid: str | None, dispatched: set[str]) -> Outcome:
    """Archive the HUMAN session in ``wid``: record how to resume it, then close the window.

    ``orch_wid`` (the decisions-inbox holder) and ``dispatched`` (the window ids the
    dispatcher owns — :func:`chela.dashboard.app._dispatched_wids`) are passed in by the
    caller, which already reads them. Refuses — touching nothing — when the window:

    * is not a live window of this chela session;
    * is the orchestrator;
    * is a dispatched run's agent or judge (hide the RUN instead — :func:`hide`);
    * is busy, or waiting on a human — or runs Claude whose status cannot be read;
    * has no session id chela can positively name with a transcript on disk.

    The record is written BEFORE the kill (a crash between the two leaves a record of a
    live window, which the next ``state`` read shows as already-resumable, never a lost
    session); a failed kill rolls the record back.
    """
    from chela import discovery, sessions

    live = discovery.get_windows_by_id()
    if wid not in live:
        return Outcome(False, f"{wid} is not a live window in this chela session", 404)
    name = live[wid]
    if orch_wid and wid == orch_wid:
        return Outcome(False, f"{wid} ({name}) is the orchestrator — not archived", 409)
    if wid in dispatched:
        return Outcome(False, f"{wid} ({name}) belongs to a dispatched run — its windows are "
                              "the dispatcher's; archive the run row instead (hide only)", 409)
    running, status = window_status(wid)
    if status == "busy":
        return Outcome(False, f"{wid} ({name}) is working — not archived", 409)
    if status == "waiting":
        return Outcome(False, f"{wid} ({name}) is waiting on you — not archived", 409)
    if running and status is None:
        return Outcome(False, f"{wid} ({name}): Claude is running but its status cannot be "
                              "read — not archived (unknown is not idle)", 409)

    pane = sessions.panes(force=True).get(wid)
    sid = resolve_session_id(wid, pane)
    if not sid:
        return Outcome(False, f"{wid} ({name}): its Claude session id cannot be determined — "
                              "closing it could not be undone, so it is not archived", 409)
    cwd = (pane.origin if pane is not None else None) or discovery.get_window_cwd_by_id(wid)
    if not cwd:
        return Outcome(False, f"{wid} ({name}): its working directory cannot be read — "
                              "not archived", 409)

    record = {
        "key": session_key(sid),
        "session_id": sid,
        "cwd": cwd,
        "name": name,
        "manual_name": bool(pane.manual_name) if pane is not None else False,
        "rc_name": _rc_name(wid, name),
        "thread_id": _bound_thread(wid),
        "wid": wid,
        "epoch": epoch.current(),
        "archived_at": time.time(),
    }
    with _LOCK:
        data = _load()
        data["sessions"][record["key"]] = record
        _save(data)
    err = close_window(wid)
    if err:
        with _LOCK:
            data = _load()
            data["sessions"].pop(record["key"], None)
            _save(data)
        return Outcome(False, f"{wid} ({name}): closing the window failed — {err}", 500)
    log.info("archive: closed %s (%s), session %s in %s", wid, name, sid, cwd)
    return Outcome(True, record=record, wid=wid, name=name)


# --- unarchive: resume it -----------------------------------------------------------------

def unarchive_session(key: str) -> Outcome:
    """Resume the archived session ``key``: a new window in its cwd, under its name, running
    ``claude --resume <id>``; its manual-name flag and Remote Control name restored; a
    request queued for the Telegram daemon to REOPEN its old topic and bind it here.

    The record is dropped only once the window is open — a failed spawn keeps it."""
    from chela import agent_manager, sessionids, spawn

    with _LOCK:
        record = _load()["sessions"].get(key)
    if record is None:
        return Outcome(False, "not archived (refresh and retry)", 404)
    sid = record.get("session_id") or ""
    if not _SESSION_ID_RE.match(sid):
        return Outcome(False, "the archived record carries no usable session id", 409)
    cwd = record.get("cwd") or ""
    if not os.path.isdir(cwd):
        return Outcome(False, f"{cwd or 'its directory'} no longer exists — nowhere to resume "
                              "this session into", 409)

    result = spawn.spawn_window(cwd, command=f"claude --resume {sid}",
                                name=record.get("name") or None,
                                remote_control_name=record.get("rc_name") or None)
    if not result.ok:
        return Outcome(False, result.error or "spawn failed", 500)
    wid = result.wid
    if wid:
        try:
            sessionids.set_session_id(wid, sid)
        except Exception:  # noqa: BLE001 — the resume already happened; the pin is best-effort
            log.warning("unarchive: recording session id for %s failed", wid, exc_info=True)
        if record.get("manual_name"):
            agent_manager.mark_manual_name(wid)
        if record.get("thread_id"):
            request_rebind(wid, str(record["thread_id"]))
    with _LOCK:
        data = _load()
        data["sessions"].pop(key, None)
        _save(data)
    log.info("unarchive: resumed session %s as %s (%s) in %s", sid, wid, result.name, cwd)
    return Outcome(True, wid=wid, name=result.name, record=record)


def drop_resumed_elsewhere(is_live) -> list[str]:
    """Forget archived sessions that are running again — resumed by hand outside the sidebar
    (``is_live(session_id) -> bool``). Returns the keys dropped."""
    with _LOCK:
        data = _load()
        gone = [k for k, r in data["sessions"].items()
                if r.get("session_id") and is_live(r["session_id"])]
        if gone:
            for k in gone:
                data["sessions"].pop(k, None)
            _save(data)
    return gone


# --- the Telegram rebind hand-off ---------------------------------------------------------

def request_rebind(wid: str, thread_id: str) -> None:
    """Ask the Telegram daemon to reopen ``thread_id`` and bind it to ``wid`` (a resumed
    archive). Read by the daemon's reconcile loop (:func:`pending_rebinds`)."""
    with _LOCK:
        data = _read(_REBINDS)
        data[wid] = {"thread_id": str(thread_id), "epoch": epoch.current(), "ts": time.time()}
        _write(_REBINDS, data)


def pending_rebinds(now_epoch: str | None, now: float | None = None) -> dict[str, str]:
    """``{wid: thread_id}`` still worth honouring: requested under THIS tmux server (an
    ``@N`` from a dead server is a stranger's) and within :data:`REBIND_TTL_SECONDS`."""
    now = time.time() if now is None else now
    out = {}
    for wid, r in _read(_REBINDS).items():
        if not isinstance(r, dict) or not r.get("thread_id"):
            continue
        if epoch.is_dangling(r.get("epoch"), now_epoch):
            continue
        if now - float(r.get("ts") or 0) > REBIND_TTL_SECONDS:
            continue
        out[wid] = str(r["thread_id"])
    return out


def settle_rebinds(keep: dict[str, str], now_epoch: str | None) -> None:
    """Rewrite the rebind file to only what is still pending after a reconcile tick
    (``keep``) — consumed and expired requests leave."""
    with _LOCK:
        data = _read(_REBINDS)
        alive = pending_rebinds(now_epoch)
        new = {w: r for w, r in data.items() if w in keep and w in alive}
        if new != data:
            _write(_REBINDS, new)
