"""Who is at the dashboard — attribution from a trusted proxy's identity header (CMX-385).

The dashboard still has NO built-in auth (see ``chela.dashboard.app``): the network is the
trust boundary. This module answers a narrower question — *who* pressed a button — so an
approval reads "approved by liav@… via dashboard" instead of just "dashboard".

**Opt-in.** ``CHELA_DASHBOARD_IDENTITY_HEADER`` (the ``dashboard.identity_header``
setting) picks the mode. It is env-only on purpose: it is a trust-boundary setting, and
``~/.chela/config.json`` is the file the no-auth dashboard itself may write.

* ``off`` (default) — nothing changes for a local install.
* ``tailscale`` — read ``Tailscale-User-Login`` / ``Tailscale-User-Name``, which
  ``tailscale serve`` sets (and overwrites when a client sends its own).
* ``cloudflare-access`` — read ``Cf-Access-Jwt-Assertion`` and VERIFY it: RS256 signature
  against the team's certs, ``aud``, ``iss`` and ``exp``. The plain
  ``Cf-Access-Authenticated-User-Email`` header is never read.

**The header is trusted only on the proxy's own path: a loopback peer** (``tailscale
serve`` or ``cloudflared`` on the same host). The same header from any other peer is a
spoof attempt: the request is refused (403) and logged.

⚠️ **Tailscale mode is ATTRIBUTION, not AUTHORITY.** Loopback stops a spoof from the
network, not from a local process: anything on this host — a dispatched agent included —
can ``curl 127.0.0.1`` with ``Tailscale-User-Login: <anyone>``. Nothing may treat a
tailscale-mode login as proof that a human approved. Cloudflare mode is stronger: a forged
JWT fails the signature check even from loopback.
"""
from __future__ import annotations

import ipaddress
import logging
import os
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

MODE_ENV = "CHELA_DASHBOARD_IDENTITY_HEADER"
CF_TEAM_ENV = "CHELA_DASHBOARD_CF_TEAM_DOMAIN"
CF_AUD_ENV = "CHELA_DASHBOARD_CF_AUD"

OFF, TAILSCALE, CLOUDFLARE = "off", "tailscale", "cloudflare-access"
MODES = (OFF, TAILSCALE, CLOUDFLARE)

TS_LOGIN = "Tailscale-User-Login"
TS_NAME = "Tailscale-User-Name"
CF_JWT = "Cf-Access-Jwt-Assertion"

# Cloudflare Access signs with RS256. Pinning the list is what stops an "alg: none" or an
# HS256-with-the-public-key token: PyJWT refuses any algorithm not named here.
CF_ALGORITHMS = ["RS256"]
MAX_LOGIN = 256


class ConfigError(ValueError):
    """The identity setting is set but unusable — the dashboard fails CLOSED on it."""


@dataclass(frozen=True)
class Identity:
    mode: str
    login: str | None = None     # the attributed user, or None (unattributed)
    name: str | None = None      # a display name, when the proxy sends one
    verified: bool = False       # True only for a signature-checked Cloudflare JWT

    def public(self) -> dict:
        return {"mode": self.mode, "login": self.login, "name": self.name,
                "verified": self.verified}


@dataclass(frozen=True)
class Refusal:
    status: int                  # 401 (no/invalid JWT) or 403 (header off the proxy path)
    reason: str
    claimed: str | None = None   # what the refused header claimed, for the log


def mode() -> str:
    """The configured mode. An unknown value raises :class:`ConfigError` — a typo in a
    trust-boundary setting must not silently read as ``off``."""
    raw = (os.environ.get(MODE_ENV) or "").strip().lower()
    if raw in ("", "0", "false", "no", "none"):
        return OFF
    if raw not in MODES:
        raise ConfigError(f"{MODE_ENV}={raw!r} is not one of {', '.join(MODES)}")
    return raw


def cloudflare_settings() -> tuple[str, str]:
    """``(team_domain, aud)`` for cloudflare mode; raises :class:`ConfigError` if unset."""
    team = (os.environ.get(CF_TEAM_ENV) or "").strip().rstrip("/")
    for prefix in ("https://", "http://"):
        if team.lower().startswith(prefix):
            team = team[len(prefix):]
    aud = (os.environ.get(CF_AUD_ENV) or "").strip()
    if not team or "/" in team:
        raise ConfigError(f"{CF_TEAM_ENV} must be your team domain "
                          "(e.g. myteam.cloudflareaccess.com)")
    if not aud:
        raise ConfigError(f"{CF_AUD_ENV} must be the Access application's AUD tag")
    return team, aud


def is_loopback(addr: str | None) -> bool:
    """True only for a loopback peer (127.0.0.0/8, ::1, or ::ffff:127.x). Anything
    unparseable — including no address at all — is NOT loopback."""
    if not addr:
        return False
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return bool((mapped or ip).is_loopback)


def _clean(value: str | None) -> str | None:
    """A header value fit to record as a name: stripped, bounded, printable."""
    v = (value or "").strip()
    if not v or len(v) > MAX_LOGIN or not v.isprintable():
        return None
    return v


# --- Cloudflare Access ----------------------------------------------------------------

_JWKS: dict[str, Any] = {}


def _jwks_client(certs_url: str):
    """One cached PyJWKClient per certs URL (it caches the key set and refetches on an
    unknown ``kid``). Tests replace this function — no network in tests."""
    client = _JWKS.get(certs_url)
    if client is None:
        import jwt
        client = _JWKS[certs_url] = jwt.PyJWKClient(certs_url, cache_keys=True, timeout=10)
    return client


def verify_cloudflare(token: str) -> dict:
    """The verified claims of a Cloudflare Access JWT. Raises on ANY failure: bad
    signature, unknown key, wrong ``aud``/``iss``, expired, or a missing claim."""
    import jwt
    team, aud = cloudflare_settings()
    key = _jwks_client(f"https://{team}/cdn-cgi/access/certs").get_signing_key_from_jwt(token)
    return jwt.decode(token, key.key, algorithms=CF_ALGORITHMS, audience=aud,
                      issuer=f"https://{team}",
                      options={"require": ["exp", "iat", "aud", "iss"]})


# --- the per-request decision ---------------------------------------------------------

def resolve(headers, remote_addr: str | None, *, machine: bool = False) -> Identity | Refusal:
    """Who this request is, or why it is refused.

    ``machine`` marks a route local processes call with no proxy in front (the plugin's
    ``/hooks/*``, the share bridge's terminal socket). In cloudflare mode those may skip
    the JWT — from loopback only; a token they DO carry is still verified.
    """
    m = mode()
    if m == OFF:
        return Identity(OFF)
    local = is_loopback(remote_addr)
    if m == TAILSCALE:
        raw = headers.get(TS_LOGIN)
        if raw is None:
            return Identity(TAILSCALE)
        if not local:
            return Refusal(403, f"{TS_LOGIN} arrived from a non-loopback peer "
                                f"({remote_addr or 'unknown'}); only the proxy may set it",
                           claimed=_clean(raw) or "?")
        login = _clean(raw)
        if login is None:
            return Refusal(403, f"{TS_LOGIN} is empty or malformed")
        return Identity(TAILSCALE, login=login, name=_clean(headers.get(TS_NAME)))
    # cloudflare-access
    cloudflare_settings()                      # misconfigured ⇒ ConfigError, fail closed
    token = (headers.get(CF_JWT) or "").strip()
    if not token:
        if machine and local:
            return Identity(CLOUDFLARE)
        return Refusal(401, f"{CF_JWT} is missing")
    if not local:
        return Refusal(403, f"{CF_JWT} arrived from a non-loopback peer "
                            f"({remote_addr or 'unknown'}); only cloudflared may set it")
    try:
        claims = verify_cloudflare(token)
    except Exception as exc:                   # noqa: BLE001 — every failure is a refusal
        return Refusal(401, f"{CF_JWT} did not verify ({exc.__class__.__name__})")
    login = _clean(claims.get("email")) or _clean(claims.get("common_name"))
    if login is None:
        return Refusal(401, f"{CF_JWT} names no user (no email or common_name claim)")
    return Identity(CLOUDFLARE, login=login, verified=True)
