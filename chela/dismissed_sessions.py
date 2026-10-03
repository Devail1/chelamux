"""Dismissed "Recent sessions": the sidebar rows an operator has said they will never resume.

The dashboard's Recent sessions list (``/api/restore``, CMX-208) is a view over the
session-stamped stores ``chela restore`` classifies, so a dead session nobody intends
to resume would otherwise sit there forever. Dismissing one records its Claude
session id here and ``/api/restore`` leaves it out (CMX-437). A row that carries no
session id (a dispatcher row) is recorded by its ``row:<store>|<wid>|<epoch>`` address
key instead — whatever ``dismiss_key`` ``/api/restore`` gave it (CMX-11).

State is server-side (under ``CHELA_DIR``), like :mod:`chela.launcher`, so a dismiss
on the phone also hides the row on the desktop. Store shape
(``CHELA_DIR/dismissed-sessions.json``)::

    {"dismissed": {"<session id | row: key>": 1718000000.0, ...}}

This is a HIDE list and nothing more. It never touches a transcript, a
session-ids/bindings row, or anything ``chela restore`` reads: a dismissed session
can still be resumed by hand, and one that comes back to life shows up under
Sessions like any other live window (that list is built from tmux, not from here).
"""
from __future__ import annotations

import json
import os
import time

from chela.config import CHELA_DIR

_STORE = CHELA_DIR / "dismissed-sessions.json"


def _load() -> dict[str, float]:
    try:
        data = json.loads(_STORE.read_text())
        dismissed = data.get("dismissed") if isinstance(data, dict) else None
    except (OSError, ValueError):
        dismissed = None
    return dict(dismissed) if isinstance(dismissed, dict) else {}


def _save(dismissed: dict[str, float]) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"dismissed": dismissed}, indent=2))
    os.replace(tmp, _STORE)   # atomic: a concurrent reader sees old or new, never half


def ids() -> set[str]:
    """Every dismissed session id."""
    return set(_load())


def dismiss(session_ids: list[str]) -> set[str]:
    """Add ``session_ids`` to the hide list (idempotent). Returns the full set."""
    dismissed = _load()
    now = time.time()
    changed = False
    for sid in session_ids:
        if sid and sid not in dismissed:
            dismissed[sid] = now
            changed = True
    if changed:
        _save(dismissed)
    return set(dismissed)


def undismiss(session_ids: list[str]) -> set[str]:
    """Drop ``session_ids`` from the hide list (the toast's Undo). Returns the full set."""
    dismissed = _load()
    kept = {sid: ts for sid, ts in dismissed.items() if sid not in set(session_ids)}
    if len(kept) != len(dismissed):
        _save(kept)
    return set(kept)
