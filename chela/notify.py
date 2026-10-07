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

**One announcer per host (CMX-9).** Both ``chela run`` (the daemon loop) and the
dashboard (``_start_notifier``) drive ``check_waiting`` — each exists for deployments
that lack the other. With both running, each kept its own in-memory ``seen`` set and
every transition was pushed twice. Now only the process holding an exclusive ``flock``
on ``$CHELA_DIR/notify.lock`` sends; the other keeps tracking the waiting set silently.
The lock is held for the holder's lifetime and dropped by the kernel when it dies, so the
standby takes over on its next tick — and, because it tracked the set all along, it
announces only transitions that happen after that, never the windows already announced.

**Classifier denials (CMX-25).** ``waiting`` is reached only at an interactive permission
PROMPT. An auto-mode classifier that REFUSES a tool call never shows one — the session
goes busy → idle and asks for permission in prose, so ``check_waiting`` never fires.
:class:`DeniedWatch` covers that hole: it tails the event log for ``hook.permission_denied``
and pushes one notification per window per :data:`DENIED_COOLDOWN_S`. It reads the LOG,
not the hook route, so whichever process holds the lease announces — the dashboard
receives the hook, but the daemon may be the announcer.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from chela import agent_manager, config, discovery, event_log, hooks
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


LOCK_NAME = "notify.lock"


class OwnerLock:
    """The cross-process "I am this host's announcer" lease: a non-blocking exclusive
    ``flock`` on ``$CHELA_DIR/notify.lock``, kept for as long as this object holds it.

    ``flock`` binds to the open file description, so two instances conflict even inside
    one process — which is what lets a test stand in for daemon + dashboard."""

    def __init__(self, path: Path | str | None = None):
        self._path = Path(path) if path is not None else None
        self._fd: int | None = None
        self._mu = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path if self._path is not None else Path(config.CHELA_DIR) / LOCK_NAME

    def acquire(self) -> bool:
        """True if this instance holds the lock (taking it now if it is free)."""
        with self._mu:
            if self._fd is not None:
                return True
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)
            except OSError:
                log.exception("notify: cannot open %s", self.path)
                return False
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(fd)
                return False
            self._fd = fd
            log.info("notify: this process is now the needs-input announcer (pid %d)",
                     os.getpid())
            return True

    def release(self) -> None:
        with self._mu:
            fd, self._fd = self._fd, None
        if fd is not None:
            os.close(fd)   # closing the description drops the flock


_owner = OwnerLock()   # this process's lease — shared by every caller in the process


def check_waiting(previously_waiting: set[str], owner: OwnerLock | None = None) -> set[str]:
    """Fire one notification per newly-waiting window; return the current set.

    Edge-triggered: a window only notifies on the transition into `waiting`, so
    a pane that sits waiting across many ticks is announced once. Only the holder of
    ``owner`` (default: this process's :data:`_owner` lease) sends; a non-holder still
    returns the current set, so it can take over later without re-announcing.
    """
    if not enabled():
        return set()
    current = waiting_windows()
    if not (owner or _owner).acquire():
        return current
    for name in sorted(current - previously_waiting):
        log.info("notify: %s entered waiting", name)
        send(f"{name} is waiting for input", title=NOTIFY_TITLE)
    return current


# --- classifier denials (CMX-25) ---------------------------------------------------

DENIED_TYPE = hooks.event_type("PermissionDenied")   # "hook.permission_denied"
DENIED_COOLDOWN_S = 10 * 60   # at most one push per window per this many seconds
DENIED_DETAIL_CHARS = 80


def _denied_message(event: dict, names: dict[str, str]) -> str:
    wid = event.get("wid") or ""
    payload = event.get("payload") or {}
    tool = str(payload.get("tool_name") or "tool")
    tool_input = payload.get("tool_input")
    detail = hooks._tool_detail(tool, tool_input if isinstance(tool_input, dict) else {})
    detail = " ".join(detail.split()) or tool
    if len(detail) > DENIED_DETAIL_CHARS:
        detail = detail[:DENIED_DETAIL_CHARS - 1] + "…"
    label = f"{wid} {names[wid]}" if names.get(wid) else wid
    return f"{label}: blocked by its permission classifier: {detail}"


class DeniedWatch:
    """Tail the event log for ``hook.permission_denied`` and push once per window per
    :data:`DENIED_COOLDOWN_S`.

    The first :meth:`check` only anchors the cursor at the log's tip — a fresh process
    never replays denials from before it started. A non-holder of the lease advances its
    cursor too, so when it takes over it announces only denials newer than that. A denial
    with no ``wid`` (a session chela did not launch) is not an agent window and is skipped.

    When the cursor cannot be honoured (``gap`` — the daemon restarted and the boot id
    moved), :func:`chela.event_log.read` resumes from the start of what it serves; only
    events stamped after this watch's previous check are announced then, so a restart never
    replays the ring's old denials onto the phone.
    """

    def __init__(self):
        self._cursor: int | None = None
        self._boot: str | None = None
        self._checked_at = 0.0
        self._last_push: dict[str, float] = {}

    def check(self, owner: OwnerLock | None = None, now: float | None = None) -> list[str]:
        """Push for new denials; return the messages actually sent."""
        if not enabled():
            return []
        now = time.time() if now is None else now
        since, self._checked_at = self._checked_at, time.time()
        if self._cursor is None:
            tip = event_log.tip()
            self._cursor, self._boot = tip["seq"], tip["boot_id"]
            return []
        batch = event_log.read(self._cursor, after_boot=self._boot, types=[DENIED_TYPE])
        self._cursor, self._boot = batch["next_seq"], batch["boot_id"]
        events = [e for e in batch["events"] if e.get("wid")]
        if batch["gap"] is not None:
            events = [e for e in events if (e.get("ts") or 0) >= since]
        if not events or not (owner or _owner).acquire():
            return []
        names = {wid: name for name, wid in discovery.get_all_windows().items()}
        sent: list[str] = []
        for event in events:
            wid = event["wid"]
            last = self._last_push.get(wid)
            if last is not None and now - last < DENIED_COOLDOWN_S:
                continue
            self._last_push[wid] = now
            message = _denied_message(event, names)
            log.info("notify: %s", message)
            send(message, title=NOTIFY_TITLE)
            sent.append(message)
        return sent
