"""🔐 CMX-392 — the proof that an override decision came from the operator's BROWSER.

An override approve or deny on the dashboard acts on the operator's behalf. "It came from
loopback" proves nothing: every dispatched agent is on loopback too. Before CMX-392 an agent
approved its own override with a raw ``urllib`` POST that carried no Origin header, which
the old same-origin check read as same-origin.

The proof is a per-browser-session token, the synchronizer pattern bound to a cookie:

* A page the browser loads (``/`` and the ``/override/<id>`` confirm page) gets a session id
  in an ``HttpOnly``, ``SameSite=Lax`` cookie (:data:`COOKIE`) and, embedded in the page,
  that session's token: an HMAC-SHA256 of the id under :data:`_SECRET`, a random key that
  exists only in this dashboard process's memory (never on disk, never in the env, rotated
  on restart).
* A decide request must carry the cookie, the matching token (header :data:`HEADER`, or the
  form field :data:`FORM_FIELD` for the confirm page's plain HTML form) AND an ``Origin``
  naming this host:port. A missing Origin is refused: every browser sends one on a POST.

⚠️ What this does NOT prove. A process running as the operator's own Unix user can still
emulate a browser on purpose: GET ``/``, keep the cookie, read the token from the page,
forge the Origin. Nothing on loopback can tell that apart from a browser; only an OS boundary
can (``docs/SANDBOX_BOUNDARY.md``). What this closes is the blind single POST.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

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
    """The request's session id: its cookie when it carries one (a second tab or a reload
    must not strand the first tab's token), else a fresh one for :func:`set_cookie`."""
    return request.cookies.get(COOKIE) or new_session_id()


def set_cookie(resp, session_id: str) -> None:
    """``Lax``, not ``Strict``: the operator reaches ``/override/<id>`` from a Telegram or
    notification link, a cross-site navigation. Under ``Strict`` that GET arrives without the
    cookie, a fresh session is minted over it, and every open tab's token goes stale. ``Lax``
    still withholds the cookie from a cross-site POST, and :func:`refusal` checks Origin."""
    resp.set_cookie(COOKIE, session_id, httponly=True, samesite="Lax", path="/")


def refusal(request) -> str | None:
    """None when ``request`` proves a browser session of this dashboard; else why not."""
    origin = request.headers.get("Origin") or ""
    if not origin:
        return "no Origin header: a decision must come from the dashboard page in a browser"
    # The whole origin, host:port included (a sibling port on this host is another page).
    # Either scheme: behind the TLS-terminating proxy (Caddy on the tailnet) the browser's
    # Origin is https:// while this process sees plain http. An exact match also refuses
    # `null`, userinfo, a path, and any other scheme.
    if origin not in (f"http://{request.host}", f"https://{request.host}"):
        return "cross-origin request"
    # A browser's own word on top (absent from an older browser or a stripping proxy):
    # `same-site` is a sibling port on this host, not this page.
    if request.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
        return "cross-site request"
    sid = request.cookies.get(COOKIE) or ""
    if not sid:
        return "no dashboard session: reload the page"
    sent = request.headers.get(HEADER) or request.form.get(FORM_FIELD) or ""
    if not hmac.compare_digest(sent.encode(), token_for(sid).encode()):
        return "missing or stale session token: reload the page"
    return None
