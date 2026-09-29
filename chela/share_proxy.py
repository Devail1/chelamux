"""🔐 The credential proxy a sandboxed share session talks to instead of Anthropic.

A typing guest drives a Claude Code session that runs in a container (see
:mod:`chela.share_sandbox`). Claude needs the operator's Anthropic token to work, and the
goal is that neither the guest nor the model can ever read it. So the token never enters
the guest container at all: the container's Claude is given ``ANTHROPIC_BASE_URL`` pointing
here and a placeholder ``ANTHROPIC_AUTH_TOKEN``, and this proxy — running in a separate
sidecar container, the only thing on the guest's internal network — strips whatever auth
the request carried, adds the real one, and forwards to a FIXED upstream. It is NanoClaw's
OneCLI-gateway pattern, reduced to the one route Claude Code needs.

What this proxy refuses to be:

* **a general egress hop** — the upstream is fixed at startup and only ``/v1/…`` paths are
  forwarded, so the guest cannot use it to reach any other host;
* **a token oracle** — the token is read from a file mounted into the sidecar only, per
  request (so a refresh by the operator's own sessions is picked up), and only ever written
  into the UPSTREAM request's headers, never into a response.

A guest who can type can still spend the operator's usage through it — that is inherent in
letting them drive Claude at all, not a defect of the proxy.

Deliberately stdlib-only and importable with nothing else from chela: the sidecar runs this
file directly (``python share_proxy.py``) from a read-only bind mount, on a stock
``python`` image.

Env: ``CHELA_PROXY_TOKEN_FILE`` (Claude Code's ``.credentials.json``, or a file holding a
bare token — e.g. from ``claude setup-token``), ``CHELA_PROXY_UPSTREAM`` (default
``https://api.anthropic.com``), ``CHELA_PROXY_PORT`` (default 8080).
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_UPSTREAM = "https://api.anthropic.com"
DEFAULT_PORT = 8080
OAUTH_BETA = "oauth-2025-04-20"

# Headers never copied from the guest's request to upstream: every auth header (the whole
# point), the hop-by-hop set, and the ones this proxy recomputes.
_DROP_REQUEST = {
    "authorization", "x-api-key", "proxy-authorization", "cookie",
    "host", "connection", "keep-alive", "proxy-connection", "te", "trailer",
    "transfer-encoding", "upgrade", "content-length",
}
# Hop-by-hop response headers (RFC 9110 §7.6.1) — ours to set, not upstream's.
_DROP_RESPONSE = {"connection", "keep-alive", "transfer-encoding", "trailer", "upgrade"}


def read_token(path: str) -> str | None:
    """The operator's token from ``path``: Claude Code's ``.credentials.json``
    (``claudeAiOauth.accessToken``) or a file holding the bare token. None if unreadable."""
    try:
        with open(path, encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return None
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            tok = (json.loads(raw).get("claudeAiOauth") or {}).get("accessToken")
        except ValueError:
            return None
        return tok or None
    return raw


def upstream_headers(incoming: list[tuple[str, str]], token: str) -> dict[str, str]:
    """The headers to send upstream: the guest's headers minus every auth/hop-by-hop
    header, plus ``Authorization: Bearer <token>`` and the OAuth beta flag merged into
    ``anthropic-beta``."""
    out: dict[str, str] = {}
    betas: list[str] = []
    for k, v in incoming:
        lk = k.lower()
        if lk in _DROP_REQUEST:
            continue
        if lk == "anthropic-beta":
            betas += [b.strip() for b in v.split(",") if b.strip()]
            continue
        out[k] = v
    if OAUTH_BETA not in betas:
        betas.append(OAUTH_BETA)
    out["anthropic-beta"] = ",".join(betas)
    out["Authorization"] = f"Bearer {token}"
    return out


def path_allowed(path: str) -> bool:
    """Only the Messages-API family is forwarded — never an absolute URL (a request line
    naming another host) and never a path that escapes ``/v1/``."""
    if not path.startswith("/v1/"):
        return False
    parsed = urllib.parse.urlsplit(path)
    return not parsed.netloc and ".." not in parsed.path.split("/")


class _Handler(BaseHTTPRequestHandler):
    token_file = ""
    upstream = urllib.parse.urlsplit(DEFAULT_UPSTREAM)

    def log_message(self, fmt, *args):  # one terse line per request, no headers
        sys.stderr.write("share-proxy: %s %s\n" % (self.command, self.path.split("?")[0]))

    def _refuse(self, status: int, msg: str) -> None:
        body = json.dumps({"type": "error", "error": {"type": "proxy_error", "message": msg}}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _forward(self) -> None:
        if not path_allowed(self.path):
            return self._refuse(403, "only /v1/ is forwarded")
        token = read_token(self.token_file)
        if not token:
            return self._refuse(503, "operator token unavailable")
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else None
        up = self.upstream
        conn_cls = http.client.HTTPSConnection if up.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(up.hostname, up.port, timeout=600)
        try:
            conn.request(self.command, self.path, body=body,
                         headers=upstream_headers(list(self.headers.items()), token))
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in _DROP_RESPONSE:
                    self.send_header(k, v)
            self.send_header("connection", "close")
            self.end_headers()
            while True:
                chunk = resp.read1(65536) if hasattr(resp, "read1") else resp.read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except OSError as e:
            try:
                self._refuse(502, f"upstream unreachable: {type(e).__name__}")
            except OSError:
                pass
        finally:
            conn.close()
            self.close_connection = True

    def _local_hello(self) -> bool:
        """Claude Code's startup connectivity check (``HEAD /api/hello``) is answered
        here — it carries no auth and needs no upstream, so it is never forwarded."""
        if self.path.split("?")[0] != "/api/hello":
            return False
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()
        return True

    def do_HEAD(self):
        if not self._local_hello():
            self._refuse(403, "only /v1/ is forwarded")

    def do_GET(self):
        if not self._local_hello():
            self._forward()

    do_POST = _forward


def main() -> None:
    token_file = os.environ.get("CHELA_PROXY_TOKEN_FILE", "")
    if not token_file:
        sys.exit("share-proxy: CHELA_PROXY_TOKEN_FILE is required")
    _Handler.token_file = token_file
    _Handler.upstream = urllib.parse.urlsplit(os.environ.get("CHELA_PROXY_UPSTREAM") or DEFAULT_UPSTREAM)
    port = int(os.environ.get("CHELA_PROXY_PORT") or DEFAULT_PORT)
    ThreadingHTTPServer(("0.0.0.0", port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
