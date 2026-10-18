"""Every forum topic chela has created (CMX-204), so an archived one stays findable.

The Bot API cannot list a group's topics, so once a binding was dropped its thread id
lived only in the bridge log. This ledger keeps ``{thread_id: {window, name, cwd,
created, closed}}`` in ``$CHELA_DIR/telegram-topics.json``. Only the bridge writes it;
the dashboard reads it for a count.

Two uses: auto-topics REOPENS a closed topic made for the same cwd instead of creating
a new one (:func:`chela.telegram.reconcile.reconcile_bindings`), which bounds the list
at one topic per concurrently-open window per folder; and Settings can delete every
closed one in one go (:func:`chela.telegram.reconcile.delete_archived_topics`).

A missing or corrupt file reads as empty: the worst case is today's behaviour (a fresh
topic is created), never a reopen of the wrong one.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from chela import config

# Settings keys in ~/.chela/config.json (chela.userconfig), read by the bridge per tick.
DELETE_ON_CLOSE_KEY = "telegram_delete_on_close"
CLEANUP_KEY = "telegram_cleanup_requested"


def default_topics_path() -> Path:
    return config.CHELA_DIR / "telegram-topics.json"


def _norm_cwd(cwd: str | None) -> str | None:
    return os.path.normpath(cwd) if cwd else None


class TopicLedger:
    """The remembered topics, persisted on every change (changes are rare, edge-triggered)."""

    def __init__(self, path: str | Path | None = None, clock=time.time):
        self._path = Path(path).expanduser() if path else default_topics_path()
        self._clock = clock
        self._topics = self._read()

    def _read(self) -> dict:
        try:
            topics = json.loads(self._path.read_text()).get("topics")
        except (ValueError, OSError, AttributeError):
            return {}
        if not isinstance(topics, dict):
            return {}
        return {str(t): e for t, e in topics.items() if isinstance(e, dict)}

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"topics": self._topics}, indent=2) + "\n")

    def get(self, thread) -> dict | None:
        return self._topics.get(str(thread))

    def opened(self, thread, window: str, cwd: str | None, name: str | None = None) -> None:
        """Record ``thread`` as open and bound to ``window`` (a create, reopen or rename)."""
        entry = self._topics.setdefault(str(thread), {"created": self._clock()})
        entry.update(window=window, cwd=_norm_cwd(cwd), closed=None)
        if name is not None:
            entry["name"] = name
        self._save()

    def closed(self, thread) -> None:
        """Record ``thread`` as closed. A topic made before the ledger existed is added
        with no cwd: never reused, but the cleanup can still find it."""
        entry = self._topics.setdefault(str(thread), {"created": None, "cwd": None})
        entry["closed"] = self._clock()
        self._save()

    def forget(self, thread) -> None:
        if self._topics.pop(str(thread), None) is not None:
            self._save()

    def closed_threads(self) -> list[str]:
        return [t for t, e in self._topics.items() if e.get("closed") is not None]

    def closed_for(self, cwd: str | None) -> list[str]:
        """Closed topics made for ``cwd``, oldest first. None/empty cwd matches nothing."""
        key = _norm_cwd(cwd)
        if key is None:
            return []
        hits = [t for t in self.closed_threads() if self._topics[t].get("cwd") == key]
        return sorted(hits, key=lambda t: self._topics[t].get("created") or 0)
