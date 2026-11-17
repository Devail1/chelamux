"""🔐 CMX-392 — the proof that a dashboard decision came from the operator's BROWSER.

A dashboard decide route (an override approve/deny, an access request's approve/deny/revoke)
acts on the operator's behalf. "It came from loopback" proves nothing: every dispatched agent
is on loopback too. Before CMX-392 an agent approved its own override with a raw
``urllib`` POST that carried no Origin header, which the old same-origin check read as
same-origin.

The proof is a per-browser-session token, the standard synchronizer pattern bound to a cookie:

* A page the browser loads (``/`` and the ``/override/<id>`` confirm page) gets a session id
  in an ``HttpOnly``, ``SameSite=Strict`` cookie (:data:`COOKIE`) and, embedded in the page,
  that session's token: an HMAC of the id under :data:`_SECRET`, a random key that exists only
  in this dashboard process's memory (never on disk, never in the env, rotated on restart).
* A decide request must carry the cookie, the matching token (header :data:`HEADER`, or the
  form field :data:`FORM_FIELD` for the confirm page's plain HTML form) AND an ``Origin`` that
  names this host. A missing Origin is refused here, unlike the generic same-origin check:
  every browser sends Origin on a POST.

Why a token and not a fresh Telegram confirm: Telegram is optional (CMX-103 serves operators
with no bridge), so requiring it would leave a dashboard-only operator no way to approve.

⚠️ What this does NOT prove. A process running as the operator's own Unix user can still
emulate a browser deliberately: GET the page, keep its cookie, scrape the token, forge the
Origin. Nothing on loopback can tell that apart from a browser; only an OS boundary can (the
agent sandbox, ``docs/SANDBOX_BOUNDARY.md``, which also denies loopback). What this closes is
the one-request path, the thing an agent reaches for by reflex, and it turns the remaining
path into a multi-step, unmistakable forgery that the permission classifier and the merge-gate
hook both see. ``docs/TRUST_AND_APPROVAL_POSTURE.md`` records this residual.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from urllib.parse import urlparse

COOKIE = "chela_session"
HEADER = "X-Chela-Session-Token"
FORM_FIELD = "session_token"

# Process-local: a token minted by another process (an agent's `python -c`) is minted under a
# different key and never verifies here.
_SECRET = secrets.token_bytes(32)


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def token_for(session_id: str) -> str:
    """The token a page with this session cookie is given."""
    return hmac.new(_SECRET, session_id.encode(), hashlib.sha256).hexdigest()


def session_for(request) -> str:
    """The request's session id: its cookie when it carries a well-formed one, else a fresh
    one (the caller sets it with :func:`set_cookie`)."""
    sid = request.cookies.get(COOKIE) or ""
    return sid if 16 <= len(sid) <= 64 and sid.isascii() else new_session_id()


def set_cookie(resp, session_id: str) -> None:
    resp.set_cookie(COOKIE, session_id, httponly=True, samesite="Strict", path="/")


def refusal(request) -> str | None:
    """None when ``request`` proves a browser session of this dashboard; else why not."""
    origin = request.headers.get("Origin")
    if not origin:
        return "no Origin header: a decision must come from the dashboard page in a browser"
    if urlparse(origin).netloc != request.host:
        return "cross-origin request"
    if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
        return "cross-site request"
    sid = request.cookies.get(COOKIE)
    if not sid:
        return "no dashboard session: reload the page"
    sent = request.headers.get(HEADER) or request.form.get(FORM_FIELD) or ""
    if not hmac.compare_digest(sent.encode(), token_for(sid).encode()):
        return "missing or stale session token: reload the page"
    return None
