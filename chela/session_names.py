"""Window names and queued titles, kept by SESSION id so a tmux restart cannot lose them (CMX-450).

A manual window name (CMX-62's ``@chela_manual_name``) and a title "Suggest names" queued
for an idle moment (CMX-434's ``@chela_title_pending``) live as tmux window options, and so
they die with the tmux server. A restored window comes back named after its folder and the
queued ``/rename`` is never typed. The window id is an address that a restart reissues; the
session id is the identity that survives it (the same lesson as CMX-378's custom groups).

So both are mirrored here, by the session the window runs, when they are set:

* :func:`record_name` — the dashboard rename (a MANUAL name) records it;
* :func:`record_title` — :mod:`chela.suggest_names` records a queued title, and clears it
  (``None``) once it is typed or dropped.

And :func:`restore_tick` (the ``chela run`` daemon, every tick, BEFORE the queued-title flush)
puts them back on whichever live window runs that session now:

* a stored name goes back on a window that does not carry a manual name, only when no OTHER
  window holds that name (a name is unique, and this never takes one from a live window);
* a stored title goes back in the window's queue (:data:`chela.suggest_names.PENDING_OPTION`),
  and the existing flush types it when the session is idle, exactly as before the restart.
  A title the transcript already shows as the session's ``custom-title`` was applied, so it
  is dropped instead of queued again: the transcript, not this store, says what landed. So
  is one whose session was retitled since it was queued (its ``custom-title`` is no longer
  the one recorded with it, ``title_over``): the user's own ``/rename`` wins.

A window whose session cannot be resolved is left alone; unknown never renames anything.

Store shape (``CHELA_DIR/session-names.json``)::

    {"<session id>": {"name": "tapparty", "title": "One Letter: ladder words", "ts": 1.0}}

``name`` / ``title`` are omitted when unset; an entry with neither is removed, and one not
set for :data:`KEEP_SECONDS` is dropped on the next write.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import subprocess
import time
from contextlib import contextmanager

from chela import config

log = logging.getLogger(__name__)

# A row nobody set for this long is dropped on the next write: the store is read every
# daemon tick, so it must not grow with every session ever renamed.
KEEP_SECONDS = 90 * 86400


def _store():
    return config.CHELA_DIR / "session-names.json"


@contextmanager
def _locked():
    """One writer at a time across processes (the dashboard records, the daemon restores)."""
    path = config.CHELA_DIR / "locks" / "session-names.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _read() -> dict:
    """The store; ``{}`` when missing or unreadable (nothing to restore, never a crash)."""
    try:
        data = json.loads(_store().read_text())
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, dict)}


def _write(data: dict) -> None:
    path = _store()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)     # atomic: a reader sees the old file or the new, never half


def entries() -> dict:
    """Every ``{session id: {name?, title?, ts}}`` row on disk."""
    return _read()


def _set(session_id: str | None, key: str, value: str | None, extra: dict | None = None) -> bool:
    """Best-effort: a store that cannot be written is logged, never raised, because the
    rename or queue it mirrors already happened and must not fail on its account."""
    if not session_id:
        return False
    try:
        with _locked():
            data = _read()
            entry = dict(data.get(session_id) or {})
            if value:
                entry[key] = value
                entry.update(extra or {})
            else:
                entry.pop(key, None)
                for k in extra or {}:
                    entry.pop(k, None)
            if entry.get("name") or entry.get("title"):
                entry["ts"] = time.time()
                data[session_id] = entry
            elif session_id in data:
                del data[session_id]
            else:
                return True           # nothing stored, nothing to forget: no write
            cutoff = time.time() - KEEP_SECONDS
            _write({k: v for k, v in data.items() if _ts(v) >= cutoff})
    except (OSError, ValueError) as e:
        log.warning("session_names: could not record %s of %s: %s", key, session_id, e)
        return False
    return True


def _ts(entry: dict) -> float:
    try:
        return float(entry.get("ts") or 0)
    except (TypeError, ValueError):
        return 0.0                    # unreadable: old enough to prune


def record_name(session_id: str | None, name: str | None) -> bool:
    """Remember ``name`` as the manual window name of ``session_id`` (None forgets it).
    False when there is no session id to key it by: nothing is written."""
    return _set(session_id, "name", name)


def record_title(session_id: str | None, title: str | None) -> bool:
    """Remember ``title`` as queued for ``session_id`` (None: it was typed, or dropped).
    False when there is no session id to key it by: nothing is written.

    Also kept: the title the session showed when this one was queued (``title_over``), so a
    restore can tell that the user retitled the session since and must not be overwritten."""
    over = _applied_title(session_id) if (title and session_id) else None
    return _set(session_id, "title", title, {"title_over": over or ""})


def session_for_window(wid: str) -> str | None:
    """The Claude session ``wid`` runs (live), else its epoch-checked pin, else None."""
    from chela import sessionids, sessions

    try:
        return sessions.session_of_window(wid) or sessionids.session_id_for(wid)
    except Exception:  # noqa: BLE001 — unknown is "not recorded", never a crash
        log.debug("session_names: session of %s unreadable", wid, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Putting them back after a restart
# ---------------------------------------------------------------------------

def _rename(wid: str, name: str) -> bool:
    """Rename ``wid`` to ``name`` and mark it manual, as the dashboard rename does."""
    from chela import agent_manager, rc_rename

    target = f"{config.current_session()}:{wid}"
    try:
        out = subprocess.run(["tmux", "rename-window", "-t", target, name],
                             capture_output=True, text=True, timeout=5)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        log.warning("session_names: rename %s -> %s failed: %s", wid, name, e)
        return False
    if out.returncode != 0:
        log.warning("session_names: rename %s -> %s failed: %s", wid, name,
                    (out.stderr or "").strip())
        return False
    agent_manager.lock_window_name(target)
    agent_manager.mark_manual_name(target)
    rc_rename.request(wid, name)
    return True


def _applied_title(session_id: str) -> str | None:
    from chela import sessions, transcripts

    path = sessions.transcript_for_session(session_id)
    return transcripts.latest_custom_title(path) if path is not None else None


def restore_tick(pane_map=None, windows: dict[str, str] | None = None) -> list[str]:
    """Put each stored name and queued title back on the live window running its session.

    ``pane_map`` / ``windows`` (``{wid: name}``) are read fresh when not given. Returns the
    actions taken, for the log. See the module doc for the rules.
    """
    from chela import discovery, sessions, suggest_names

    data = _read()
    if not data:
        return []
    pane_map = sessions.panes() if pane_map is None else pane_map
    windows = discovery.get_windows_by_id() if windows is None else windows
    actions: list[str] = []
    for sid, entry in data.items():
        wid = sessions.wid_for_session(sid, pane_map)
        if wid is None or wid not in windows:
            continue
        pane = pane_map.get(wid)
        name = entry.get("name")
        if name and pane is not None and not pane.manual_name:
            if windows[wid] != name and any(n == name and w != wid for w, n in windows.items()):
                log.debug("session_names: %s not renamed to %r: another window holds it",
                         wid, name)
            elif _rename(wid, name):
                actions.append(f"{wid} name {windows[wid]} -> {name}")
                windows[wid] = name
        title = entry.get("title")
        if title and pane is not None and pane.title_pending != title:
            applied = _applied_title(sid)
            if applied == title:
                record_title(sid, None)
                actions.append(f"{wid} title {title!r} already applied")
            elif (applied or "") != (entry.get("title_over") or ""):
                record_title(sid, None)       # retitled since it was queued: theirs wins
                actions.append(f"{wid} title {title!r} dropped: retitled to {applied!r}")
            elif suggest_names.queue_title(wid, title):
                actions.append(f"{wid} title {title!r} queued again")
    return actions
