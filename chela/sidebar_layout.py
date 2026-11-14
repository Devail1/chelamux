"""The sidebar's layout (CMX-82): server-side, so the phone and the laptop agree.

Before this, the group order, the custom groups (and which row sits in which) and the
View menu's choices lived in ONE browser's ``localStorage``: two devices disagreed, and
clearing site data lost them. They are chela state now, under ``CHELA_DIR``, behind the
dashboard's ``/api/sidebar/layout`` routes — the same shape of store as the archive's
(:mod:`chela.sidebar_archive`): one file, written atomically under one lock.

What stays per DEVICE (and never comes here): which groups are collapsed, whether the
sidebar itself is collapsed, and whether the Archived section is open. Those are about
the screen you are holding, not about how you organise your sessions.

* :func:`state` — ``{"layout": {...}}``, or ``{"layout": None}`` while nothing has been
  saved yet. ``None`` is load-bearing: it is how a browser knows it may import its old
  ``localStorage`` values (:func:`import_once`).
* :func:`update` — replace the fields a write names (``order`` / ``custom`` / ``view`` /
  ``width``)
  and leave the others alone, so a reorder on one device does not undo a View change
  another device made a moment earlier.
* :func:`import_once` — the one-time migration: written ONLY when there is no layout
  yet. The first device wins; every later one adopts what the server holds.

The client normalizes ``view`` field by field (``sidebarmodel.normalizeView``), so the
server only bounds its shape — it does not second-guess the menu's vocabulary.

Store shape (``CHELA_DIR/sidebar-layout.json``)::

    {"order":  ["<group key>", ...],
     "custom": {"groups": [{"id": "...", "name": "..."}, ...], "assign": {"<item key>": "<id>"},
                "seen": {"s:<session id>": <epoch ms>}},   # CMX-378, omitted while empty
     "view":   {"groupBy": "...", "sort": "...", ...},
     "width":  288,
     "rows":   {"<group key>": {"keys": ["s:<session id>", ...], "at": <epoch ms>}}}

``width`` (CMX-278) is the desktop sidebar's width in CSS px, clamped to
[:data:`MIN_WIDTH`, :data:`MAX_WIDTH`]; ``None`` until someone drags it (the default).

``rows`` (CMX-381) is the Manual sort's order of the sessions inside each group: the rows'
stable keys (CMX-378's ``s:<session id>``, else ``run:<task>`` / ``w:<window>``) top to
bottom, and ``at``, when that group was last ordered (a session created after it goes to
the top). A group nobody ordered by hand has no entry.
"""
from __future__ import annotations

import json
import os
import threading

from chela.config import CHELA_DIR

_STORE = CHELA_DIR / "sidebar-layout.json"
_LOCK = threading.Lock()

FIELDS = ("order", "custom", "view", "width", "rows")

# Bounds — a layout is a few dozen keys; anything far larger is a bug or abuse, never a
# sidebar. Over-long values are dropped, not truncated into something that half-means.
_MAX_ITEMS = 500
_MAX_STR = 400


def _str(v: object) -> bool:
    return isinstance(v, str) and 0 < len(v) <= _MAX_STR


def _clean_order(v: object) -> list[str]:
    if not isinstance(v, list):
        return []
    return list(dict.fromkeys(k for k in v if _str(k)))[:_MAX_ITEMS]


def _clean_custom(v: object) -> dict:
    v = v if isinstance(v, dict) else {}
    groups, seen = [], set()
    for g in v.get("groups") if isinstance(v.get("groups"), list) else []:
        if isinstance(g, dict) and _str(g.get("id")) and _str(g.get("name")) and g["id"] not in seen:
            seen.add(g["id"])
            groups.append({"id": g["id"], "name": g["name"]})
    assign_in = v.get("assign") if isinstance(v.get("assign"), dict) else {}
    # an assignment to a group that does not exist would re-capture its row the day a
    # group of that id is created — drop it here, as deleting a group does client-side
    assign = dict(list({k: g for k, g in assign_in.items() if _str(k) and g in seen}.items())[:_MAX_ITEMS])
    out = {"groups": groups[:_MAX_ITEMS], "assign": assign}
    # CMX-378: when each session-keyed assignment's session was last seen (epoch ms) — the
    # client prunes an `s:` key only after its session has been gone for days. A stamp is
    # kept only for a key that is still assigned; omitted entirely while there are none.
    seen_in = v.get("seen") if isinstance(v.get("seen"), dict) else {}
    stamps = {k: t for k, t in seen_in.items() if k in assign and _stamp(t)}
    if stamps:
        out["seen"] = stamps
    return out


def _stamp(v: object) -> bool:
    return not isinstance(v, bool) and isinstance(v, (int, float)) and v == v and 0 <= v < float("inf")


def _clean_view(v: object) -> dict:
    if not isinstance(v, dict):
        return {}
    out = {}
    for k, val in v.items():
        if not _str(k):
            continue
        if isinstance(val, bool) or _str(val):
            out[k] = val
        elif isinstance(val, list) and all(_str(x) for x in val):
            out[k] = val[:_MAX_ITEMS]
    return out


# The same bounds as the sidebar's drag handle (frontend/src/features/sidebar, CMX-278). The
# floor is the width the sidebar's foot (CPU/RAM/Disk + the gear) was laid out for.
MIN_WIDTH = 264
MAX_WIDTH = 480


def _clean_width(v: object) -> int | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or v in (float("inf"), float("-inf")):
        return None
    return max(MIN_WIDTH, min(MAX_WIDTH, round(v)))


def _clean_rows(v: object) -> dict:
    """``{group key: {"keys": [row key, ...], "at": epoch ms}}``; an entry with a bad key, no
    keys or no usable stamp is dropped whole (a half-entry would misplace every new row)."""
    if not isinstance(v, dict):
        return {}
    out = {}
    for g, e in v.items():
        if len(out) >= _MAX_ITEMS:
            break
        if not _str(g) or not isinstance(e, dict) or not _stamp(e.get("at")):
            continue
        keys = _clean_order(e.get("keys"))
        if keys:
            out[g] = {"keys": keys, "at": e["at"]}
    return out


_CLEAN = {"order": _clean_order, "custom": _clean_custom, "view": _clean_view, "width": _clean_width,
          "rows": _clean_rows}


def _read() -> dict | None:
    try:
        data = json.loads(_STORE.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {f: _CLEAN[f](data.get(f)) for f in FIELDS}


def _write(data: dict) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, _STORE)   # atomic: a concurrent reader sees old or new, never half


def _empty() -> dict:
    return {f: _CLEAN[f](None) for f in FIELDS}


def state() -> dict:
    """``{"layout": {order, custom, view}}`` — or ``{"layout": None}`` when no device has
    saved one yet (the cue for :func:`import_once`)."""
    return {"layout": _read()}


def _fields(patch: object) -> dict:
    """The known fields ``patch`` names, cleaned. Unknown keys are ignored."""
    if not isinstance(patch, dict):
        return {}
    return {f: _CLEAN[f](patch[f]) for f in FIELDS if f in patch}


def update(patch: object) -> dict:
    """Replace the fields ``patch`` names and keep the rest. Returns :func:`state`."""
    fields = _fields(patch)
    with _LOCK:
        if fields:
            data = _read() or _empty()
            data.update(fields)
            _write(data)
        return state()


def import_once(patch: object) -> tuple[bool, dict]:
    """The one-time migration of a browser's ``localStorage`` layout: written ONLY when
    the server has none yet — it never overwrites a layout another device saved.
    Returns ``(imported, state)``."""
    fields = _fields(patch)
    with _LOCK:
        if _read() is not None or not fields:
            return False, state()
        data = _empty()
        data.update(fields)
        _write(data)
        return True, state()
