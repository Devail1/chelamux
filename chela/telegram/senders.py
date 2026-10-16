"""Who may drive the bound agents — the per-SENDER gate in front of every inbound update.

The chat-id gate (CMX-8) keeps strangers' chats out, but every MEMBER of the bound Forum
group could still type into every agent, send keys, answer a permission prompt or ask for a
``/screenshot`` — and those agents run unsandboxed on the operator's machine (CMX-185).

``CHELA_TELEGRAM_ALLOWED_USERS`` (comma/space separated numeric Telegram user ids, in
``chela.env`` or ``secrets.env``) closes that: an update whose ``effective_user`` — the
``from`` of a message, a photo, a document AND of a callback-button press — is not on it is
dropped before any handler runs. A rejection is logged (user id + a short hash of the text,
never the text), rate-limited per user, and never answered: a reply would confirm to the
sender that the bot is listening.

**Unset ⇒ today's chat-only behaviour, with a loud warning at boot** (and from
``chela doctor``), rather than locking the operator out on upgrade.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Callable

log = logging.getLogger(__name__)

ALLOWED_USERS_VAR = "CHELA_TELEGRAM_ALLOWED_USERS"

# One rejection line per sender per window: a stranger hammering the group must not be
# able to fill the operator's log.
REJECT_LOG_EVERY_S = 60.0


def parse_allowed_users(raw: str | None) -> tuple[frozenset[str], list[str]]:
    """``"123, 456"`` → ``({"123", "456"}, [])``; tokens that are not a plain ASCII
    number come back in the second list (reported, never allowed)."""
    allowed: set[str] = set()
    bad: list[str] = []
    for tok in (raw or "").replace(",", " ").split():
        if tok.isascii() and tok.isdigit():
            allowed.add(str(int(tok)))
        else:
            bad.append(tok)
    return frozenset(allowed), bad


def allowed_users_from_env() -> frozenset[str]:
    return parse_allowed_users(os.environ.get(ALLOWED_USERS_VAR))[0]


def _update_text(update) -> str:
    """What the sender sent, for the rejection hash only."""
    query = getattr(update, "callback_query", None)
    if query is not None:
        return getattr(query, "data", None) or ""
    msg = getattr(update, "message", None)
    return (getattr(msg, "text", None) or getattr(msg, "caption", None) or "") if msg else ""


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:12]


class SenderGate:
    """``allows(update)`` — True only for an update from an allow-listed user id.

    An empty allowlist is the legacy chat-only mode: every update passes (the chat gate
    downstream still applies) and :meth:`warn_if_open` says so loudly.
    """

    def __init__(self, allowed: frozenset[str], *,
                 clock: Callable[[], float] = time.monotonic,
                 log_every: float = REJECT_LOG_EVERY_S) -> None:
        self.allowed = frozenset(allowed)
        self._clock = clock
        self._log_every = log_every
        self._last_logged: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def warn_if_open(self) -> bool:
        if self.allowed:
            log.info("telegram: accepting updates from %d allow-listed user(s)",
                     len(self.allowed))
            return False
        log.warning(
            "telegram: %s is NOT set — ANY member of the bound chat can type into every "
            "bound agent, send keys and answer its permission prompts. Set it to your "
            "numeric Telegram user id(s) (chela.env or secrets.env).", ALLOWED_USERS_VAR)
        return True

    def allows(self, update) -> bool:
        if not self.allowed:
            return True
        user = getattr(update, "effective_user", None)
        uid = getattr(user, "id", None)
        if uid is not None and str(uid) in self.allowed:
            return True
        self._reject(str(uid), _update_text(update))
        return False

    def _reject(self, uid: str, text: str) -> None:
        now = self._clock()
        last = self._last_logged.get(uid)
        if last is not None and now - last < self._log_every:
            self._suppressed[uid] = self._suppressed.get(uid, 0) + 1
            return
        self._last_logged[uid] = now
        log.warning("telegram: dropped an update from non-allow-listed user %s "
                    "(text sha256 %s; %d more suppressed)",
                    uid, text_hash(text), self._suppressed.pop(uid, 0))
