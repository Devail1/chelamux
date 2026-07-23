"""Thread↔window bindings — the registry the multi-topic bridge routes on.

One :class:`BindingRegistry` holds a supergroup ``chat_id`` plus a bidirectional
map between Telegram forum ``thread_id``s and tmux ``window_id``s (``@N``). The
inbound half looks up ``window_for_thread`` (topic → which window) and the
outbound half looks up ``thread_for_window`` (window → which topic), so a single
``chela telegram`` process can bridge N agents ↔ N topics instead of one.

The registry is **pure** — no Telegram calls — and persists to JSON
(default ``~/.chela/telegram-bindings.json``, override with
``CHELA_TELEGRAM_BINDINGS``) so bindings survive a daemon restart. Slice B will
*populate* it at topic-create time; Slice A only consumes and persists it (seed
manually, via ``--wid``/``TELEGRAM_TOPIC_ID`` back-compat, or ``--bind``).

**Normalisation (landmine):** ``thread_id`` is an int on the wire but keys are
compared as ``str`` (same as CMX-8's ``TopicRouter``), so ids round-trip through
JSON and match regardless of int/str origin. A forum's General topic reports no
thread id (``None``/``""``) — that never binds and always looks up as unbound.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from chela import config

log = logging.getLogger(__name__)


def _norm(value: object) -> str | None:
    """Normalise an id to ``str``, or ``None`` for an absent/General-topic id."""
    if value is None or value == "":
        return None
    return str(value)


def default_bindings_path() -> Path:
    """Where bindings persist: ``$CHELA_TELEGRAM_BINDINGS`` or ``$CHELA_DIR``."""
    override = os.environ.get("CHELA_TELEGRAM_BINDINGS")
    if override:
        return Path(override).expanduser()
    return config.CHELA_DIR / "telegram-bindings.json"


class BindingRegistry:
    """A bidirectional ``thread_id ↔ window_id`` map scoped to one chat.

    The map is kept 1:1 in both directions: :meth:`bind` drops any stale binding
    on either side before inserting, so a window can bridge at most one topic and
    a topic at most one window. All ids are stored as ``str`` (see module docs).
    """

    def __init__(self, chat_id: str | int | None = None):
        self._chat_id = _norm(chat_id)
        self._window_to_thread: dict[str, str] = {}
        self._thread_to_window: dict[str, str] = {}
        # window_id -> the name its topic currently carries on Telegram. NOT a
        # source of truth for the agent's name (the tmux window is) — a CACHE of
        # what we last told Telegram, so the reconcile tick can spot a drifted
        # topic and rename it without hitting the Bot API for every window, every
        # tick, forever. An unknown/absent entry just means "resync once".
        self._topic_names: dict[str, str] = {}
        # window_id -> the tmux epoch that ISSUED that id (chela.epoch). ``@3`` is an
        # ADDRESS, not an identity: tmux numbers windows per SERVER, so after a restart
        # the same ``@3`` is a different agent — and a binding that outlives its server
        # would quietly relay THAT agent's pane into the topic a human opened for the one
        # that died, and route their replies back into it. The stamp is what lets the
        # reconcile tick tell "still bound" from "reissued to a stranger". Kept as data,
        # never as a live tmux call: this class stays pure (the epoch is passed in).
        self._epochs: dict[str, str] = {}
        # window_id -> the title text currently pinned in that window's topic. A CACHE
        # of what we last told Telegram (same shape as ``_topic_names``), so the
        # pinned-title sync only calls the Bot API when Claude's own session title
        # (``chela.transcripts.latest_ai_title``) actually changed.
        self._pinned_titles: dict[str, str] = {}
        # window_id -> the message_id of the pinned anchor message in that window's
        # topic. Kept so a title revision EDITS that same message in place
        # (``editMessageText``) instead of pinning a new one each time, which would
        # flap Telegram's "pinned a message" notice on every retitle.
        self._pinned_message_ids: dict[str, str] = {}

    @property
    def chat_id(self) -> str | None:
        """The bound supergroup chat id (the inbound security boundary)."""
        return self._chat_id

    def bind(self, window_id: str, thread_id: str | int, epoch: str | None = None) -> None:
        """Bind ``window_id`` ↔ ``thread_id``, replacing any existing binding.

        ``epoch`` is the tmux server that issued ``window_id`` (:func:`chela.epoch.current`,
        passed in by the caller so this stays pure). Omitting it binds an UNSTAMPED window —
        legal, and what a pre-CMX-77 file reads as, but such a binding cannot be told from a
        stale one and is never reaped as dangling.

        Raises :class:`ValueError` if either id is empty/None (a General-topic
        thread, or a missing window, can never form a binding).
        """
        w = _norm(window_id)
        t = _norm(thread_id)
        if w is None or t is None:
            raise ValueError(f"bind needs a window id and a thread id, got {window_id!r}/{thread_id!r}")
        # Drop any prior binding on either side so both maps stay 1:1.
        old_thread = self._window_to_thread.pop(w, None)
        if old_thread is not None:
            self._thread_to_window.pop(old_thread, None)
        old_window = self._thread_to_window.pop(t, None)
        if old_window is not None:
            self._window_to_thread.pop(old_window, None)
            self._topic_names.pop(old_window, None)
            self._epochs.pop(old_window, None)
            self._pinned_titles.pop(old_window, None)
            self._pinned_message_ids.pop(old_window, None)
        # A fresh binding means a different topic, so whatever name/pin we cached for
        # this window describes someone else's topic now. Drop it: unknown reads as
        # "resync once", which is always safe; a stale value reads as "in sync" and
        # would leave the topic misnamed / mistitled forever.
        self._topic_names.pop(w, None)
        self._pinned_titles.pop(w, None)
        self._pinned_message_ids.pop(w, None)
        self._window_to_thread[w] = t
        self._thread_to_window[t] = w
        self._epochs.pop(w, None)
        if epoch:
            self._epochs[w] = str(epoch)

    def unbind(self, window_id: str) -> bool:
        """Remove the binding for ``window_id``. Returns True if one was removed."""
        w = _norm(window_id)
        if w is None:
            return False
        thread = self._window_to_thread.pop(w, None)
        if thread is None:
            return False
        self._thread_to_window.pop(thread, None)
        self._topic_names.pop(w, None)
        self._epochs.pop(w, None)
        self._pinned_titles.pop(w, None)
        self._pinned_message_ids.pop(w, None)
        return True

    def stamp(self, window_id: str, epoch: str) -> bool:
        """Record the tmux epoch of an ALREADY-bound window. True if that changed anything.

        The upgrade path: a binding written before CMX-77 carries no epoch, so nothing can
        say whether it still names the window it was made for. The reconcile tick stamps a
        binding whose window is live in the CURRENT epoch — an adoption, and the honest limit
        of what can be recovered: a file older than the running tmux server cannot be
        distinguished from one written under it. From then on the binding is verifiable, and
        the next server restart reaps it instead of silently relaying a stranger.
        """
        w = _norm(window_id)
        if w is None or w not in self._window_to_thread or not epoch:
            return False
        if self._epochs.get(w) == str(epoch):
            return False
        self._epochs[w] = str(epoch)
        return True

    def epoch_for(self, window_id: str | int | None) -> str | None:
        """The tmux epoch this window's binding was made in (None → unstamped)."""
        w = _norm(window_id)
        if w is None:
            return None
        return self._epochs.get(w)

    def set_topic_name(self, window_id: str, name: str) -> None:
        """Record the name ``window_id``'s topic now carries on Telegram."""
        w = _norm(window_id)
        if w is not None:
            self._topic_names[w] = name

    def topic_name(self, window_id: str | int | None) -> str | None:
        """The name we last gave this window's topic (None → never synced)."""
        w = _norm(window_id)
        if w is None:
            return None
        return self._topic_names.get(w)

    def set_pinned_title(self, window_id: str, title: str) -> None:
        """Record the title text currently pinned in ``window_id``'s topic."""
        w = _norm(window_id)
        if w is not None:
            self._pinned_titles[w] = title

    def pinned_title(self, window_id: str | int | None) -> str | None:
        """The title we last pinned for this window (None → never pinned)."""
        w = _norm(window_id)
        if w is None:
            return None
        return self._pinned_titles.get(w)

    def set_pinned_message_id(self, window_id: str, message_id: str | int) -> None:
        """Record the message_id of ``window_id``'s pinned anchor message."""
        w = _norm(window_id)
        if w is not None:
            self._pinned_message_ids[w] = str(message_id)

    def pinned_message_id(self, window_id: str | int | None) -> str | None:
        """The message_id of the pinned anchor message (None → not yet pinned)."""
        w = _norm(window_id)
        if w is None:
            return None
        return self._pinned_message_ids.get(w)

    def window_for_thread(self, thread_id: str | int | None) -> str | None:
        """The window bound to ``thread_id`` (None → unbound / General topic)."""
        t = _norm(thread_id)
        if t is None:
            return None
        return self._thread_to_window.get(t)

    def thread_for_window(self, window_id: str | int | None) -> str | None:
        """The thread bound to ``window_id`` (None → the window has no topic)."""
        w = _norm(window_id)
        if w is None:
            return None
        return self._window_to_thread.get(w)

    def windows(self) -> list[str]:
        """All bound window ids — what the outbound monitor polls."""
        return list(self._window_to_thread)

    def __len__(self) -> int:
        return len(self._window_to_thread)

    # -- persistence -------------------------------------------------------

    def to_dict(self) -> dict:
        """Serialise to ``{chat_id, bindings, topic_names, epochs, pinned_titles, pinned_message_ids}``."""
        return {
            "chat_id": self._chat_id,
            "bindings": dict(self._window_to_thread),
            "topic_names": dict(self._topic_names),
            "epochs": dict(self._epochs),
            "pinned_titles": dict(self._pinned_titles),
            "pinned_message_ids": dict(self._pinned_message_ids),
        }

    @classmethod
    def from_dict(cls, data: dict, *, chat_id: str | int | None = None) -> "BindingRegistry":
        """Rebuild a registry from :meth:`to_dict` output.

        ``chat_id`` overrides the persisted value when given — the daemon feeds
        the live ``TELEGRAM_CHAT_ID`` here so env stays the security boundary.
        """
        reg = cls(chat_id if chat_id is not None else data.get("chat_id"))
        epochs = data.get("epochs") or {}
        for window, thread in (data.get("bindings") or {}).items():
            # A file written before CMX-77 carries no epochs: those bindings read as
            # UNSTAMPED, which is honest — chela cannot say which tmux server issued them.
            # The next reconcile tick re-stamps a live one and reaps a dead one as usual.
            reg.bind(window, thread, epochs.get(window))
        # Absent in files written before topic renaming existed: those windows just
        # look unsynced, so the next reconcile tick renames each topic once to the
        # live tmux name and records it. Bind first — bind() clears the cache entry.
        for window, name in (data.get("topic_names") or {}).items():
            if reg.thread_for_window(window) is not None:
                reg.set_topic_name(window, name)
        # Absent in files written before pinned titles existed: those windows just
        # look un-pinned, so the next reconcile tick pins one fresh and records it.
        for window, message_id in (data.get("pinned_message_ids") or {}).items():
            if reg.thread_for_window(window) is not None:
                reg.set_pinned_message_id(window, message_id)
        for window, title in (data.get("pinned_titles") or {}).items():
            if reg.thread_for_window(window) is not None:
                reg.set_pinned_title(window, title)
        return reg

    def save(self, path: str | Path | None = None) -> None:
        """Persist to ``path`` (default :func:`default_bindings_path`)."""
        dest = Path(path).expanduser() if path else default_bindings_path()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load(
        cls, path: str | Path | None = None, *, chat_id: str | int | None = None
    ) -> "BindingRegistry":
        """Load from ``path`` (default :func:`default_bindings_path`).

        A missing or unreadable file yields an empty registry (bound to
        ``chat_id`` if given) rather than raising, so a first run just starts
        empty. ``chat_id`` overrides any persisted chat id.
        """
        src = Path(path).expanduser() if path else default_bindings_path()
        if not src.exists():
            return cls(chat_id)
        try:
            data = json.loads(src.read_text())
        except (json.JSONDecodeError, OSError) as e:
            log.warning("could not read bindings from %s: %s; starting empty", src, e)
            return cls(chat_id)
        return cls.from_dict(data, chat_id=chat_id)
