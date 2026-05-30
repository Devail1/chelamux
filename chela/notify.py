"""Needs-input notifications — ping a phone when an agent goes to `waiting`.

An agent's pane enters the `waiting` state (per ``claude agents --json``) when
it's blocked on a permission prompt or a question — exactly the moments a human
needs to step in. ``check_waiting`` is called from the daemon loop with the set
of windows that were already waiting; it fires a one-shot notification for each
newly-waiting window and returns the updated set, so a pane that stays waiting
isn't re-notified until it leaves and re-enters the state.

Transport is auto-detected from ``CHELA_NOTIFY_URL`` (no extra dependency —
stdlib ``urllib``):
  - **ntfy**     — POST the message as the body (title via the ``Title`` header).
                   Detected for ``ntfy.sh`` hosts; or set CHELA_NOTIFY_KIND=ntfy.
  - **telegram** — POST ``{chat_id, text}`` to a Bot API ``sendMessage`` URL.
                   The ``chat_id`` is read from the URL query (``?chat_id=...``)
                   or ``CHELA_NOTIFY_CHAT_ID``. Detected for ``api.telegram.org``.
  - **webhook**  — POST JSON ``{title, message, agent, event}``. The fallback.

All sends are best-effort: any failure is logged and swallowed so a flaky
notifier never disturbs the daemon loop.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
import urllib.request

from chela import agent_manager, discovery
from chela.config import NOTIFY_KIND, NOTIFY_TITLE, NOTIFY_URL

log = logging.getLogger(__name__)

_TIMEOUT = 10


def enabled() -> bool:
    return bool(NOTIFY_URL)


def _detect_kind(url: str) -> str:
    if NOTIFY_KIND in ("ntfy", "telegram", "webhook"):
        return NOTIFY_KIND
    host = urllib.parse.urlparse(url).netloc.lower()
    if "api.telegram.org" in host:
        return "telegram"
    if host == "ntfy.sh" or host.endswith(".ntfy.sh"):
        return "ntfy"
    return "webhook"


def _post(req: urllib.request.Request) -> None:
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:  # noqa: S310 — user-configured URL
        resp.read()


def send(message: str, title: str | None = None) -> bool:
    """Fire a single notification. Returns True on success, False on any error."""
    if not NOTIFY_URL:
        return False
    title = title or NOTIFY_TITLE
    kind = _detect_kind(NOTIFY_URL)
    try:
        if kind == "ntfy":
            req = urllib.request.Request(
                NOTIFY_URL, data=message.encode("utf-8"), method="POST",
                headers={"Title": title, "Content-Type": "text/plain; charset=utf-8"},
            )
        elif kind == "telegram":
            parsed = urllib.parse.urlparse(NOTIFY_URL)
            q = urllib.parse.parse_qs(parsed.query)
            chat_id = (q.get("chat_id", [None])[0]
                       or os.environ.get("CHELA_NOTIFY_CHAT_ID", ""))
            base = urllib.parse.urlunparse(parsed._replace(query=""))
            payload = json.dumps({"chat_id": chat_id, "text": f"{title}\n{message}"})
            req = urllib.request.Request(
                base, data=payload.encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json"},
            )
        else:  # webhook
            payload = json.dumps({
                "title": title, "message": message, "event": "waiting",
            })
            req = urllib.request.Request(
                NOTIFY_URL, data=payload.encode("utf-8"), method="POST",
                headers={"Content-Type": "application/json"},
            )
        _post(req)
        return True
    except Exception:
        log.exception("notify: send failed (kind=%s)", kind)
        return False


def waiting_windows() -> set[str]:
    """Names of windows whose claude session is currently `waiting`."""
    status_map = agent_manager.session_status_map()
    by_pid = status_map.get("by_pid", {})
    out: set[str] = set()
    for name, wid in discovery.get_all_windows().items():
        pid = agent_manager.claude_pid(wid)
        if pid is not None and by_pid.get(pid) == "waiting":
            out.add(name)
    return out


def check_waiting(previously_waiting: set[str]) -> set[str]:
    """Fire one notification per newly-waiting window; return the current set.

    Edge-triggered: a window only notifies on the transition into `waiting`, so
    a pane that sits waiting across many ticks is announced once.
    """
    if not enabled():
        return set()
    current = waiting_windows()
    for name in sorted(current - previously_waiting):
        log.info("notify: %s entered waiting", name)
        send(f"{name} is waiting for input", title=NOTIFY_TITLE)
    return current
