"""Session id store — ``wid -> session_id`` for windows chela can vouch for.

Two writers. :mod:`chela.spawn` (and the dashboard's resume path) record a row at the
moment they launch a window — chela CHOSE the session id, so this is a fact from birth.
:func:`chela.sessions.resolve_window` (CMX-296) additionally PROMOTES a session id it
resolves by other means — the event log, or the pane's own ``claude --resume <sid>``
command line — the first time either tier succeeds for a window.

**Why `resolve_window`, not the spawn-only population it started from.** An earlier
version of this promotion lived behind :func:`chela.inbox._identity_of`, reached only from
`chela watch` / `register` / `readdress` acting on the *orchestrator's own* window. On the
live fleet that window is telegram-bound and had *already* earned a pin via
:func:`chela.spawn.spawn_window` or the dashboard resume path — so that call site promoted
exactly one window, and it was already covered. The window CMX-296 actually exists for is
any OTHER window chela has identified without having spawned it — an orchestrator or agent
started by hand (a manual ``claude`` in a tmux pane, or a ``--resume`` a human typed outside
chela) — and those are reached by `resolve_window`, not by the orchestrator-identity path:
it runs for every window `chela doctor` and the Telegram outbound relay's transcript poller
resolve, not just the caller's own. Without a durable pin, such a window stays dependent on
the event log's fleet-wide, bounded ring for every future resolution — precisely the gap
CMX-295 closed for spawned windows only.

**Why not ``chela/telegram/bindings.py``.** ``chela-telegram`` builds ONE
``BindingRegistry`` at daemon start (``main.py::_build_bindings_registry``) and
calls ``.save()`` from that SAME in-memory object whenever a reconcile tick
changes something (``main.py:1321-1323``). A ``spawn.py`` write that does its
own ``BindingRegistry.load()`` / ``.set_session_id()`` / ``.save()`` mutates a
*different* object — the daemon's copy never learns about it, and the very
next reconcile save overwrites the file from memory, erasing the write. This
module is a second, single-purpose store the telegram daemon never touches, so
a spawn's write can never be raced by a reconcile save (or vice versa): every
call here does its own read-modify-write round trip against the file, so two
independent writers converge instead of clobbering each other (last write to
the *file* wins, not last write to whichever in-memory copy is staler).

Store shape (``CHELA_DIR/session-ids.json``)::

    {"@42": {"session_id": "<uuid>", "epoch": "<pid>-<start_time>"}, ...}

Not gated on any Telegram topic binding — a session id is recorded at spawn,
often well before (or without ever having) a bound topic. See
``docs/AGENT_IDENTITY.md`` slice 2a.

**Epoch.** ``@N`` is an address, not an identity (``chela/epoch.py``): a
persisted id is only meaningful next to the tmux server that issued it. Every
write is stamped with :func:`chela.epoch.current`; a read that finds a
DIFFERENT epoch (the id was reissued by a later server) or CANNOT determine
the current epoch (tmux unreachable) returns ``None`` rather than the stored
value — an unverifiable claim is not a claim. Either way the row is left in
place: an unreadable epoch is unknown, not proof of staleness, and must never
be treated as license to delete data chela cannot currently verify.
"""
from __future__ import annotations

import json
import logging
import os

from chela import epoch
from chela.config import CHELA_DIR

log = logging.getLogger(__name__)

_STORE = CHELA_DIR / "session-ids.json"


def _norm(wid: str | int | None) -> str | None:
    if wid is None or wid == "":
        return None
    return str(wid)


def _load() -> dict:
    try:
        data = json.loads(_STORE.read_text())
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        data = {}
    return data


def _save(data: dict) -> None:
    CHELA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, _STORE)   # atomic: a concurrent reader sees old or new, never half


def set_session_id(wid: str, session_id: str) -> None:
    """Record ``wid -> session_id``, stamped with the current tmux epoch.

    Read-modify-write against the file itself (never an in-memory registry) so
    this can never be raced by ``chela-telegram``'s reconcile save — see the
    module docstring. Raises on a store failure (a bad ``CHELA_DIR``, a
    read-only filesystem); the caller (:mod:`chela.spawn`) decides what an
    unrecordable id means for the command it is about to send — this function
    stays honest about failure rather than swallowing it.
    """
    w = _norm(wid)
    if w is None:
        raise ValueError(f"set_session_id needs a window id, got {wid!r}")
    data = _load()
    data[w] = {"session_id": str(session_id), "epoch": epoch.current()}
    _save(data)


def entries() -> dict:
    """Every ``wid -> {session_id, epoch}`` row on disk, unfiltered — for reporting
    (:mod:`chela.restore`) only. Unlike :func:`session_id_for` this does NOT drop rows whose
    epoch no longer matches the running tmux server; a report that wants to say which rows
    are dangling needs to see them, not have them silently withheld.
    """
    return _load()


def rekey(old_wid: str, new_wid: str, expected_session_id: str, expected_epoch: str | None) -> bool:
    """Move a REVIVABLE row from its dangling ``old_wid`` to where it is alive now.

    CMX-196's applied form of ``chela restore``'s REVIVABLE remedy: the session recorded at
    ``old_wid`` is confirmed alive under ``new_wid`` (:func:`chela.restore.plan` already did
    that work — this does not re-verify it), so the row is re-stamped with the CURRENT epoch
    at its new address instead of a human re-running ``chela watch`` by hand.

    ``False`` (no-op, nothing written) unless the row at ``old_wid`` STILL matches exactly
    what classification saw — same session id, same dead epoch. A no-op here means the row
    moved on since (a further restart reissued it, or a concurrent writer already handled
    it), and the classification computed a moment earlier is no longer proof of anything.
    """
    w = _norm(old_wid)
    if w is None:
        return False
    data = _load()
    entry = data.get(w)
    if not entry or entry.get("session_id") != expected_session_id or entry.get("epoch") != expected_epoch:
        return False
    del data[w]
    data[_norm(new_wid)] = {"session_id": expected_session_id, "epoch": epoch.current()}
    _save(data)
    return True


def remove(wid: str, expected_session_id: str | None = None,
           expected_epoch: str | None = None) -> bool:
    """Delete a MANUAL row after it has been archived (:func:`chela.roster.archive`).

    ``False`` (no-op) if the row is already gone, or if it no longer matches what
    classification saw — same guard as :func:`rekey`, for the same reason: a row that moved
    on is not the row a stale plan classified, and must not be deleted on its authority.
    """
    w = _norm(wid)
    if w is None:
        return False
    data = _load()
    entry = data.get(w)
    if not entry:
        return False
    if expected_session_id is not None and entry.get("session_id") != expected_session_id:
        return False
    if expected_epoch is not None and entry.get("epoch") != expected_epoch:
        return False
    del data[w]
    _save(data)
    return True


def session_id_for(wid: str | int | None) -> str | None:
    """The session id pinned for ``wid``.

    ``None`` when: nothing was ever recorded; the recorded epoch differs from
    the CURRENT one (the id was reissued to a different window by a later tmux
    server — see ``chela/epoch.py``); or the current epoch cannot be read at
    all (tmux unreachable). The last two are indistinguishable from the
    outside on purpose: an unverifiable claim and a falsified one both resolve
    to "don't act on this", never to an accusation, and the row is left
    exactly as it was so it can resolve again once tmux is reachable.
    """
    w = _norm(wid)
    if w is None:
        return None
    entry = _load().get(w)
    if not entry:
        return None
    now = epoch.current()
    if now is None or entry.get("epoch") != now:
        return None
    return entry.get("session_id")
