"""The sidebar's layout (CMX-82): group order, custom groups + membership, View settings —
server-side, so the phone and the laptop show the same sidebar.

Before this, all of it lived in ONE browser's ``localStorage``: the two devices disagreed,
and clearing site data lost it. Now it is chela state under ``CHELA_DIR``, behind the
dashboard's ``/api/sidebar/layout`` routes (like :mod:`chela.sidebar_archive`): one store,
one writer (the dashboard process), every write atomic and under a lock.

What does NOT live here, on purpose: which groups are collapsed, whether the sidebar
itself is collapsed, whether the Archived section is open. Those are about the screen in
front of you (a phone folds what a laptop shows), so they stay in that browser.

Each field is written on its own (:func:`update` takes a partial layout), so a View change
on one device never rolls back a group reorder made on another.

**Migration** (:func:`migrate`): a browser that still holds a pre-CMX-82 localStorage
layout offers it once. It is imported ONLY while the server has no layout yet — the first
device wins, every later one adopts the server's.

Store shape (``CHELA_DIR/sidebar-layout.json``)::

    {"order":  ["<group key>", ...],
     "view":   {"groupBy": "folder", "sort": "...", "status": "active", ...},
     "custom": {"groups": [{"id": "g…", "name": "Infra"}, ...], "assign": {"<item key>": "g…"}},
     "updated_at": <epoch seconds>}
"""
from __future__ import annotations

import json
import os
import threading
import time

from chela.config import CHELA_DIR

_STORE = CHELA_DIR / "sidebar-layout.json"
_LOCK = threading.Lock()

FIELDS = ("order", "view", "custom")

# Bounds: a layout is a few hundred bytes in practice; these only stop a bad client from
# growing the file without limit.
_MAX_ORDER = 500
_MAX_GROUPS = 200
_MAX_ASSIGN = 5000
_MAX_VIEW_KEYS = 32
_MAX_STR = 500


def _empty() -> dict:
    return {"order": [], "view": {}, "custom": {"groups": [], "assign": {}}}


# --- validation -----------------------------------------------------------------------------

def _str(v: object, limit: int = _MAX_STR) -> bool:
    return isinstance(v, str) and 0 < len(v) <= limit


def _clean_order(v: object) -> list[str]:
    if not isinstance(v, list) or len(v) > _MAX_ORDER or not all(_str(k) for k in v):
        raise ValueError("order must be a list of group keys")
    return list(dict.fromkeys(v))


def _clean_view(v: object) -> dict:
    """The View menu's choices. Their meaning is the page's (``sidebarmodel.normalizeView``
    fills defaults and drops nonsense on read), so the server only bounds the shape."""
    if not isinstance(v, dict) or len(v) > _MAX_VIEW_KEYS:
        raise ValueError("view must be an object")
    out = {}
    for k, val in v.items():
        if not _str(k, 64):
            raise ValueError("view keys must be short strings")
        if val is None or isinstance(val, (bool, int, float)) or _str(val, 64):
            out[k] = val
        elif isinstance(val, list) and len(val) <= 32 and all(_str(x, 64) for x in val):
            out[k] = list(val)
        else:
            raise ValueError(f"view.{k} has an unsupported value")
    return out


def _clean_custom(v: object) -> dict:
    if not isinstance(v, dict):
        raise ValueError("custom must be an object")
    groups, assign = v.get("groups", []), v.get("assign", {})
    if not isinstance(groups, list) or len(groups) > _MAX_GROUPS:
        raise ValueError("custom.groups must be a list")
    clean_groups, ids = [], set()
    for g in groups:
        if not isinstance(g, dict) or not _str(g.get("id"), 64) or not _str(g.get("name"), 200):
            raise ValueError("each custom group needs a string id and name")
        if g["id"] in ids:
            continue
        ids.add(g["id"])
        clean_groups.append({"id": g["id"], "name": g["name"]})
    if not isinstance(assign, dict) or len(assign) > _MAX_ASSIGN:
        raise ValueError("custom.assign must be an object")
    clean_assign = {}
    for k, gid in assign.items():
        if not _str(k) or not _str(gid, 64):
            raise ValueError("custom.assign maps item keys to group ids")
        if gid in ids:   # an assignment to a group that does not exist means nothing
            clean_assign[k] = gid
    return {"groups": clean_groups, "assign": clean_assign}


_CLEAN = {"order": _clean_order, "view": _clean_view, "custom": _clean_custom}


def clean(patch: object) -> dict:
    """Validate a (partial) layout. Raises ``ValueError`` on anything that is not one —
    including a field this store does not hold (collapsed state is per device, so a
    client that sends it is wrong, not ignored)."""
    if not isinstance(patch, dict) or not patch:
        raise ValueError("expected an object with any of: " + ", ".join(FIELDS))
    unknown = sorted(set(patch) - set(FIELDS))
    if unknown:
        raise ValueError(f"not part of the shared layout: {', '.join(unknown)}")
    return {k: _CLEAN[k](v) for k, v in patch.items()}


# --- the store ------------------------------------------------------------------------------

def _read() -> dict | None:
    """The stored layout, or ``None`` when there is none yet (or it is unreadable)."""
    try:
        data = json.loads(_STORE.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    out = _empty()
    for k in FIELDS:
        if k in data:
            try:
                out[k] = _CLEAN[k](data[k])
            except ValueError:
                pass   # one bad field must not lose the others
    return out


def _write(layout: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({**layout, "updated_at": time.time()}, indent=2))
    os.replace(tmp, _STORE)   # atomic: a concurrent reader sees old or new, never half


def state() -> dict:
    """What the page reads: ``{"exists": bool, "layout": {order, view, custom}}``.
    ``exists`` is False until some device saved (or migrated) a layout."""
    data = _read()
    return {"exists": data is not None, "layout": data if data is not None else _empty()}


def update(patch: object) -> dict:
    """Merge a partial layout (any of :data:`FIELDS`) into the store; the fields it does
    not name are left as they are. Raises ``ValueError`` on a bad patch. Returns
    :func:`state`."""
    fields = clean(patch)
    with _LOCK:
        data = _read() or _empty()
        data.update(fields)
        _write(data)
    return state()


def migrate(layout: object) -> tuple[bool, dict]:
    """Import a browser's pre-CMX-82 localStorage layout — ONLY when the server has none
    yet. Returns ``(imported, state)``; an existing layout is never touched."""
    fields = clean(layout)
    with _LOCK:
        if _read() is not None:
            return False, state()
        data = _empty()
        data.update(fields)
        _write(data)
    return True, state()
