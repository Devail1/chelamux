"""Every forum topic chela has created (CMX-204), so an archived one stays findable.

The Bot API cannot list a group's topics, so once a binding was dropped its thread id
lived only in the bridge log. This ledger keeps ``{thread_id: {window, name, cwd,
created, closed, session_id}}`` in ``$CHELA_DIR/telegram-topics.json``. Only the bridge writes it;
the dashboard reads it for a count.

Two uses: auto-topics REOPENS a closed topic made for the same cwd instead of creating
a new one (:func:`chela.telegram.reconcile.reconcile_bindings`), which bounds the list
at one topic per concurrently-open window per folder; and Settings can delete every
closed one in one go (:func:`chela.telegram.reconcile.delete_archived_topics`). A third
(CMX-267): a human REOPENING a closed topic resumes the Claude session it was closed on
(:class:`chela.telegram.reconcile.TopicReopenedHandler`), from the ``session_id`` kept here.

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
        # tmp + replace: a crash mid-write must not truncate the ledger and orphan its topics.
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"topics": self._topics}, indent=2) + "\n")
        os.replace(tmp, self._path)

    def get(self, thread) -> dict | None:
        return self._topics.get(str(thread))

    def opened(self, thread, window: str, cwd: str | None, name: str | None = None,
               session_id: str | None = None) -> None:
        """Record ``thread`` as open and bound to ``window`` (a create, reopen or rename).
        ``session_id`` (CMX-450) is the Claude session that window runs, recorded when the
        topic is bound so a restart can find the topic by session; None keeps what is there."""
        entry = self._topics.setdefault(str(thread), {"created": self._clock()})
        entry.update(window=window, cwd=_norm_cwd(cwd), closed=None)
        if name is not None:
            entry["name"] = name
        if session_id:
            entry["session_id"] = session_id
            entry["bound"] = self._clock()
        self._save()

    def closed(self, thread, session_id: str | None = None) -> None:
        """Record ``thread`` as closed. A topic made before the ledger existed is added
        with no cwd: never reused, but the cleanup can still find it. ``session_id`` is the
        Claude session its window was running, what a reopen resumes; None (unknown) keeps
        the one recorded when the topic was bound (CMX-450) rather than erasing it."""
        entry = self._topics.setdefault(str(thread), {"created": None, "cwd": None})
        entry["closed"] = self._clock()
        if session_id or "session_id" not in entry:
            entry["session_id"] = session_id
        self._save()

    def set_session(self, thread, session_id: str) -> bool:
        """Record ``session_id`` on a known ``thread`` that has none yet (CMX-450: a topic
        bound before session ids were recorded). True when it wrote."""
        entry = self._topics.get(str(thread))
        if entry is None or entry.get("session_id") or not session_id:
            return False
        entry["session_id"] = session_id
        entry["bound"] = self._clock()
        self._save()
        return True

    def for_session(self, session_id: str | None) -> list[str]:
        """Every topic recorded for ``session_id``, the one it USED last first (CMX-450,
        CMX-466): after a forced rebind onto an older topic, that one wins. None/empty
        matches nothing.

        "Used last" is the latest of the topic's ``closed``, ``bound`` and ``created``
        stamps, not ``bound`` alone: ``bound`` is written only when a session id was known
        at bind time, so a topic reopened by folder or learnt from a close carries none,
        and an old duplicate with a stamp outranked it (11-22: glide rebound to a topic
        last used on 10-29). The reap closes the topic a session was on when its window
        died, so that close is the freshest evidence of use. Ties go to ``bound``, then
        ``created``."""
        if not session_id:
            return []
        hits = [t for t, e in self._topics.items() if e.get("session_id") == session_id]

        def rank(t):
            e = self._topics[t]
            bound, created = e.get("bound") or 0, e.get("created") or 0
            return (max(e.get("closed") or 0, bound, created), bound, created)
        return sorted(hits, key=rank, reverse=True)

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
