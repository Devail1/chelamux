"""🔐 CMX-392 test helper: a Flask test client that IS the operator's browser session.

The dashboard's decide routes refuse a request that does not prove one
(:mod:`chela.dashboard.humanproof`), so a test that drives them as the operator wraps its
client here. ``tests/test_dashboard_human_proof.py`` tests the refusals themselves.
"""
from __future__ import annotations

from chela.dashboard import humanproof


def as_browser(client, host: str = "localhost"):
    """Give ``client`` the session cookie a page load sets, plus the Origin and token header
    the page's fetch sends, on every request. A header a test passes explicitly still wins
    (werkzeug applies ``environ_base`` first)."""
    sid = humanproof.new_session_id()
    client.set_cookie(humanproof.COOKIE, sid)
    client.environ_base.update({"HTTP_ORIGIN": f"http://{host}",
                                "HTTP_X_CHELA_SESSION_TOKEN": humanproof.token_for(sid)})
    return client
