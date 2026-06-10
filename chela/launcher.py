"""Launcher: server-side Recent + Favorites store for one-click agent launch.

The dashboard sidebar offers a click-to-launch list of project directories. A
launch spawns a tmux window in that directory and (optionally) runs ``claude``,
so a peer agent is one tap away from any device. State is server-side (under
``CHELA_DIR``) rather than per-browser ``localStorage`` so the same Recent /
Favorites show up on the phone, the desktop, and any Telegram-driven session.

Store shape (``CHELA_DIR/launcher.json``)::

    {
      "recent":    [{"path": "/abs/dir", "ts": 1718000000.0}, ...],  # MRU, capped
      "favorites": [{"path": "/abs/dir", "label": "chela"}, ...]      # user-pinned
    }

Paths are normalised (``realpath`` + ``expanduser``) before storage so the same
directory reached two ways collapses to one identity key.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from chela.config import CHELA_DIR

_STORE = CHELA_DIR / "launcher.json"
_MAX_RECENT = 12

# Base dir scanned for "add a favorite" suggestions (immediate git-repo subdirs).
# No personal paths are baked in — it defaults to ~/projects and is overridable,
# so a fresh public install suggests nothing surprising.
_PROJECTS_DIR = Path(
    os.path.expanduser(os.environ.get("CHELA_PROJECTS_DIR", str(Path.home() / "projects")))
)


def _norm(path: str) -> str:
    """Absolute, symlink-resolved, ~-expanded path — the store's identity key."""
    return os.path.realpath(os.path.expanduser(str(path)))


def _label_for(path: str) -> str:
    return os.path.basename(path.rstrip("/")) or path


def _load() -> dict:
    try:
        data = json.loads(_STORE.read_text())
        if not isinstance(data, dict):
            raise ValueError
    except (OSError, ValueError):
        data = {}
    data.setdefault("recent", [])
    data.setdefault("favorites", [])
    return data


def _save(data: dict) -> None:
    CHELA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = _STORE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2))
    os.replace(tmp, _STORE)   # atomic: a concurrent reader sees old or new, never half


def record_recent(path: str) -> None:
    """Push ``path`` to the front of the MRU recent list (deduped, capped)."""
    p = _norm(path)
    data = _load()
    recent = [e for e in data["recent"] if e.get("path") != p]
    recent.insert(0, {"path": p, "ts": time.time()})
    data["recent"] = recent[:_MAX_RECENT]
    _save(data)


def pin(path: str, label: str | None = None) -> dict:
    """Add ``path`` to favorites (idempotent). Returns the fresh view()."""
    p = _norm(path)
    data = _load()
    if not any(e.get("path") == p for e in data["favorites"]):
        data["favorites"].append({"path": p, "label": label or _label_for(p)})
        _save(data)
    return view()


def unpin(path: str) -> dict:
    """Remove ``path`` from favorites. Returns the fresh view()."""
    p = _norm(path)
    data = _load()
    kept = [e for e in data["favorites"] if e.get("path") != p]
    if len(kept) != len(data["favorites"]):
        data["favorites"] = kept
        _save(data)
    return view()


def view() -> dict:
    """Render-ready lists. Recent excludes anything already favorited (so the two
    sections never show the same dir twice); every entry carries a display label
    and whether the dir still exists on disk."""
    data = _load()
    fav_paths = {e.get("path") for e in data["favorites"]}
    favorites = [
        {"path": e["path"], "label": e.get("label") or _label_for(e["path"]),
         "exists": os.path.isdir(e["path"])}
        for e in data["favorites"] if e.get("path")
    ]
    recent = [
        {"path": e["path"], "label": _label_for(e["path"]),
         "exists": os.path.isdir(e["path"]), "ts": e.get("ts")}
        for e in data["recent"] if e.get("path") and e["path"] not in fav_paths
    ]
    return {"recent": recent, "favorites": favorites}


def suggest() -> list[dict]:
    """Immediate subdirs of ``CHELA_PROJECTS_DIR`` that are git repos, offered as
    favorite candidates (each flagged with whether it's already pinned). Empty
    when the base dir is absent — nothing personal is baked in."""
    try:
        entries = sorted(_PROJECTS_DIR.iterdir())
    except OSError:
        return []
    fav_paths = {e.get("path") for e in _load()["favorites"]}
    out = []
    for d in entries:
        if not d.is_dir() or not (d / ".git").exists():
            continue
        p = _norm(str(d))
        out.append({"path": p, "label": d.name, "pinned": p in fav_paths})
    return out
