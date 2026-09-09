"""Durable per-session-id record of a ``chela restore --resume`` launch that did not come
up alive (issue #468).

**The gap this closes.** :func:`chela.restore.resume`'s only bound against relaunching a
dead session forever used to be an in-memory ``set`` that lived for the duration of ONE
``resume()`` call — the ``(session_id, stamped_epoch)`` dedup in that function. That set
answers "did THIS pass already resume this session" but forgets everything the instant the
process exits, so a session that comes up dead a second time (a bad session id, an auth
wedge, a crash on load) is relaunched again on the very next ``chela restore --resume`` —
every pass individually looks like it did the right thing, and the fleet crash-loops. This
module is the missing durable half: a small bounded retry count and a reason, per
``session_id``, on disk — the same shape :data:`chela.dispatcher`'s
``judge_cannot_verify_tries`` uses for the identical problem one layer up (an unverifiable
outcome gets a bounded number of retries, then stops being retried automatically and waits
for a human).

Store shape (``CHELA_DIR/resume-attempts.json``)::

    {"<session_id>": {"tries": 1, "reason": "<why the last attempt failed>"}}

A session id is a UUID chela never reissues, so there is no epoch to key this on the way
``session-ids.json`` keys its rows on ``wid`` — the row is meaningful for as long as the
session id itself is (:func:`clear` deletes it once a resume of that session genuinely
succeeds, so a session that dies again later starts its retry count fresh, as a new problem).

Read-modify-write against the file itself on every call, the same pattern
:mod:`chela.sessionids` uses and for the same reason: this has no in-memory registry of its
own to race against a concurrent writer, so two independent ``chela restore --resume``
invocations converge on whatever is on disk last, rather than one clobbering the other's
in-memory copy.
"""
from __future__ import annotations

import json
import logging
import os

from chela.config import CHELA_DIR

log = logging.getLogger(__name__)

_STORE = CHELA_DIR / "resume-attempts.json"

# How many times `chela restore --resume` will relaunch a session whose PREVIOUS launch did
# not come up alive before it stops retrying automatically and reports it as blocked. Small
# and deliberately conservative — not a `CHELA_*`-env-configurable knob like the Dispatch
# tab's retry counts (`docs/SETTINGS_UI_INVENTORY.md`'s inventory), on purpose: each retry
# here opens a new tmux window and sends a real `claude --resume`, so retrying a session
# that is never coming back (a bad id, a permanently wedged auth) is not free the way
# re-checking a judge verdict is, and issue #468's own verify step requires a SECOND
# `chela restore --resume` to already refuse a session whose first attempt failed.
MAX_TRIES = 1


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


def tries(session_id: str) -> int:
    """How many times a resume of ``session_id`` has already been recorded as failed."""
    if not session_id:
        return 0
    return int((_load().get(session_id) or {}).get("tries", 0))


def record_failure(session_id: str, reason: str) -> int:
    """Persist that a resume of ``session_id`` did not come up alive, with ``reason`` — bumps
    and returns the new try count. Raises on a store failure (a bad ``CHELA_DIR``, a
    read-only filesystem) rather than swallowing it: a failure this function could not
    record is a bound :func:`blocked_reason` can no longer enforce, and the caller
    (:func:`chela.restore.resume`) needs to know that rather than silently under-counting.
    """
    if not session_id:
        raise ValueError("record_failure needs a session id, got %r" % (session_id,))
    data = _load()
    prior = int((data.get(session_id) or {}).get("tries", 0))
    data[session_id] = {"tries": prior + 1, "reason": str(reason or "")}
    _save(data)
    return prior + 1


def clear(session_id: str) -> None:
    """Drop any failure record for ``session_id`` — called once a resume of it genuinely
    comes up alive, so a LATER failure of that same session starts counting from zero again
    rather than inheriting an unrelated earlier attempt's tally."""
    if not session_id:
        return
    data = _load()
    if session_id in data:
        del data[session_id]
        _save(data)


def blocked_reason(session_id: str, max_tries: int = MAX_TRIES) -> str | None:
    """``None`` if ``session_id`` may still be attempted; otherwise the reason its last
    recorded failure gave, once its try count has reached ``max_tries``.

    This is the enforcement half — :func:`chela.restore.resume` calls this BEFORE spawning
    anything, so a session already blocked never opens a new tmux window at all, and never
    reaches the liveness check that would record yet another identical failure.
    """
    if not session_id:
        return None
    entry = _load().get(session_id)
    if not entry or int(entry.get("tries", 0)) < max_tries:
        return None
    return entry.get("reason") or "a previous resume attempt did not come up alive"
