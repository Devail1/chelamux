"""``chela/roster.py`` — the durable fleet snapshot, keyed by tmux epoch.

**The question this file exists to answer: "what did the dead server have?"** Every other
store chela writes (``inbox.json``, ``telegram-bindings.json``, ``session-ids.json``) only
ever records the CURRENT address of a row — once a tmux server dies, nothing says what
``@N`` used to be, so a dangling stamp can be *detected* (:mod:`chela.epoch`) but never
*explained*. This module is the record that outlives the server: written from the
telegram daemon's reconcile tick (the one place that already has ``live``, ``agents``, and
``epoch.current()`` in hand on a bounded interval — see ``chela/main.py``'s
``_reconcile_loop``), it keeps the last few epochs' worth of "what was running where,
and under which Claude session" so :mod:`chela.restore` has something to join a dangling
row against.

**Epoch-keyed, not flat.** A flat ``{wid: {...}}`` map can only ever answer "what is @N
right now" — exactly what the live stores already do, and exactly what is useless once the
server that issued ``@N`` is gone. Keying by epoch means a dead epoch's whole roster survives
intact even after the fleet moves on to a new one, and a lookup is always
"what was @N *under this specific epoch*", never an accidental cross-epoch collision.

**Retention.** Last 5 epochs, pruned by ``last_seen`` — old enough to survive a couple of
restarts in a row (an OOM loop, a bad deploy) without growing forever.

**Atomic write.** ``roster.json`` is the SOLE recovery source once its epoch is dead — there
is no second copy anywhere to fall back on — and the failure this whole ticket was written
from is a kill *mid-write* (a hard tmux death is exactly the kind of event that can also take
the process writing this file). Every save is temp-file-then-``os.replace``, so a reader
during an interrupted write always sees the complete previous version or the complete new
one, never a half-written one.
"""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

from chela.config import CHELA_DIR

log = logging.getLogger(__name__)

_STORE = CHELA_DIR / "roster.json"

# Old enough to survive a couple of restarts in a row without growing forever; see the
# module docstring.
_MAX_EPOCHS = 5


def _load(path: Path | None = None) -> dict:
    store = path or _STORE
    try:
        data = json.loads(store.read_text())
        if not isinstance(data, dict) or not isinstance(data.get("epochs"), dict):
            raise ValueError
    except (OSError, ValueError):
        data = {"epochs": {}}
    return data


def _save(data: dict, path: Path | None = None) -> None:
    store = path or _STORE
    store.parent.mkdir(parents=True, exist_ok=True)
    tmp = store.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, store)   # atomic — see module docstring


def _prune(epochs: dict) -> None:
    if len(epochs) <= _MAX_EPOCHS:
        return
    ordered = sorted(epochs.items(), key=lambda kv: kv[1].get("last_seen", 0))
    for stale_epoch, _ in ordered[: len(epochs) - _MAX_EPOCHS]:
        del epochs[stale_epoch]


def record(live: dict, agents, now_epoch: str | None,
           cwd_for: Callable[[str], str | None],
           session_for: Callable[[str], str | None] | None = None,
           *, path: Path | None = None) -> dict | None:
    """Snapshot every AGENT window under ``now_epoch`` — called from the reconcile tick.

    ``live`` / ``agents`` / ``cwd_for`` are exactly what ``_reconcile_loop`` already has in
    hand each tick (``tg.live_agent_windows()`` and ``get_window_cwd_by_id``); this adds no
    tmux call of its own beyond ``session_for`` (default :func:`chela.sessions.session_of_window`,
    sharing one ``panes()`` snapshot across the whole tick rather than one call per window).

    ``None`` (and no write) when ``now_epoch`` cannot be read — an unknown epoch has nothing
    to key a snapshot on, and writing one under a fake key would make a later join look like
    it found a match when it didn't.
    """
    if not now_epoch:
        return None
    if session_for is None:
        from chela import sessions
        pane_map = sessions.panes()
        session_for = lambda wid: sessions.session_of_window(wid, pane_map)  # noqa: E731

    data = _load(path)
    epochs = data["epochs"]
    now = time.time()
    rec = epochs.get(now_epoch)
    if rec is None:
        rec = {"first_seen": now, "last_seen": now, "windows": {}}
        epochs[now_epoch] = rec
    rec["last_seen"] = now
    rec["windows"] = {
        wid: {"name": live.get(wid) or "", "cwd": cwd_for(wid), "session_id": session_for(wid)}
        for wid in sorted(agents)
    }
    _prune(epochs)
    _save(data, path)
    return rec


def window(dead_epoch: str | None, wid: str, *, path: Path | None = None) -> dict | None:
    """What ``wid`` was, the last time ``dead_epoch`` was the running tmux server.

    ``None`` when that epoch was never recorded (older than the retention window, or the
    reconcile loop never ran while it was live) or never had this window — the caller falls
    back to whatever the dangling row itself still carries.
    """
    if not dead_epoch:
        return None
    data = _load(path)
    rec = data["epochs"].get(dead_epoch)
    if not rec:
        return None
    return rec["windows"].get(wid)
