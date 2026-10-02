"""🔐 The credential proxy a sandboxed share session talks to instead of Anthropic.

A typing guest drives a Claude Code session that runs in a container (see
:mod:`chela.share_sandbox`). Claude needs the operator's Anthropic token to work, and the
goal is that neither the guest nor the model can ever read it. So the token never enters
the guest container at all: the container's Claude is given ``ANTHROPIC_BASE_URL`` pointing
here and a placeholder login (or ``ANTHROPIC_AUTH_TOKEN``), and this proxy — running in a separate
sidecar container, the only thing on the guest's internal network — strips whatever auth
the request carried, adds the real one, and forwards to a FIXED upstream. It is NanoClaw's
OneCLI-gateway pattern, reduced to the one route Claude Code needs.

What this proxy refuses to be:

* **a general egress hop** — the upstream is fixed at startup and only ``/v1/…`` paths are
  forwarded, so the guest cannot use it to reach any other host;
* **a token oracle** — the token is read from a file mounted into the sidecar only, per
  request (so a refresh by the operator's own sessions is picked up), and only ever written
  into the UPSTREAM request's headers, never into a response.

On an upstream 401 the token is read once more: if it changed (the host refreshed its
login since this request read it), the request is retried once with the new one. A 401
that persists is answered with a 502 naming the real cause — the HOST's login expired —
because a 401 would send the guest's Claude Code into its own /login flow, which can never
help: the guest holds no credential by design (CMX-433).

A guest who can type can still spend the operator's usage through it — that is inherent in
letting them drive Claude at all, not a defect of the proxy.

Deliberately stdlib-only and importable with nothing else from chela: the sidecar runs this
file directly (``python share_proxy.py``) from a read-only bind mount, on a stock
``python`` image.

**The Telegram outbox (CMX-420).** The guest's transcript lives in its container's tmpfs and
its hooks cannot reach the host, so the ordinary relay has nothing to read for a sandboxed
window. This proxy sees every model response anyway, so it is the one place the session's
replies can be observed from the HOST side: for each completed main-loop turn it parses the
SSE stream (:class:`TurnCollector`) and appends ONE transcript-shaped record — the
assistant's visible text, each tool call reduced to its name — to ``outbox.jsonl`` in
``$CHELA_PROXY_SESSION_DIR``. That directory is a host bind mount on THIS sidecar only; it
is never mounted into the guest. Because the record has the shape of a Claude Code JSONL
line, ``chela telegram`` reads it with the same monitor, parser, formatting, chunking and
offset-dedup it uses for a real transcript. ``proxy-status.json`` beside it records when the
proxy last forwarded such a turn, so ``chela doctor`` can tell a quiet session from a broken
outbox.

Env: ``CHELA_PROXY_TOKEN_FILE`` (Claude Code's ``.credentials.json``, or a file holding a
bare token — e.g. from ``claude setup-token``), ``CHELA_PROXY_UPSTREAM`` (default
``https://api.anthropic.com``), ``CHELA_PROXY_PORT`` (default 8080),
``CHELA_PROXY_SESSION_DIR`` (optional; where the outbox is written — no outbox without it).
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_UPSTREAM = "https://api.anthropic.com"
DEFAULT_PORT = 8080
OAUTH_BETA = "oauth-2025-04-20"
LOGIN_EXPIRED = "The host's Claude login expired — ask the operator to log in again on the host."

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


# --- the Telegram outbox (CMX-420) ------------------------------------------------------

OUTBOX_NAME = "outbox.jsonl"
STATUS_NAME = "proxy-status.json"


def records_turn(method: str, path: str, body: bytes | None) -> bool:
    """Whether a request's response is a main-loop TURN worth relaying: a streamed
    ``POST /v1/messages`` that offers tools. Claude Code's agent loop always sends its
    tool list; its side calls (titles, summaries, token counts) don't, and must not reach
    the operator's topic as if the session had said them."""
    if method != "POST" or path.split("?")[0] != "/v1/messages" or not body:
        return False
    try:
        req = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return False
    return isinstance(req, dict) and req.get("stream") is True and bool(req.get("tools"))


class TurnCollector:
    """Incrementally parses one Anthropic Messages SSE stream into the turn it carries.

    Fed raw response bytes in whatever chunks they arrive; events are framed on blank
    lines and only their ``data:`` lines are read. Text blocks accumulate their
    ``text_delta``s; a ``tool_use`` block keeps only its name and id (the relay shows a
    tool call as one short line, and a tool's input — a whole file for ``Write`` — has no
    business in a chat topic); thinking is not the assistant's visible text and is
    dropped. ``complete`` turns True on ``message_stop`` and only then is :meth:`record`
    non-None — a stream cut off mid-turn is never relayed as if it had finished."""

    def __init__(self) -> None:
        self._buf = b""
        self._blocks: dict[int, dict] = {}
        self.complete = False

    def feed(self, chunk: bytes) -> None:
        self._buf = (self._buf + chunk).replace(b"\r\n", b"\n")
        while b"\n\n" in self._buf:
            frame, self._buf = self._buf.split(b"\n\n", 1)
            data = b"\n".join(line[5:].lstrip() for line in frame.split(b"\n")
                              if line.startswith(b"data:"))
            if not data:
                continue
            try:
                event = json.loads(data)
            except (ValueError, UnicodeDecodeError):
                continue
            if isinstance(event, dict):
                self._event(event)

    def _event(self, ev: dict) -> None:
        kind = ev.get("type")
        if kind == "content_block_start":
            block = ev.get("content_block") or {}
            self._blocks[ev.get("index", len(self._blocks))] = {
                "type": block.get("type"), "text": block.get("text") or "",
                "name": block.get("name"), "id": block.get("id")}
        elif kind == "content_block_delta":
            delta = ev.get("delta") or {}
            block = self._blocks.get(ev.get("index"))
            if block is not None and delta.get("type") == "text_delta":
                block["text"] += delta.get("text") or ""
        elif kind == "message_stop":
            self.complete = True

    def record(self, now: float | None = None) -> dict | None:
        """The turn as ONE Claude-Code-transcript-shaped ``assistant`` record, or None
        when the stream did not complete or said nothing visible."""
        if not self.complete:
            return None
        content: list[dict] = []
        for _i, b in sorted(self._blocks.items()):
            if b["type"] == "text" and b["text"].strip():
                content.append({"type": "text", "text": b["text"]})
            elif b["type"] == "tool_use" and b.get("name"):
                content.append({"type": "tool_use", "id": b.get("id") or "",
                                "name": b["name"], "input": {}})
        if not content:
            return None
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if now is None else now))
        return {"type": "assistant", "timestamp": ts, "source": "share-proxy",
                "message": {"role": "assistant", "content": content}}


class Outbox:
    """The per-session outbox: an append-only JSONL file plus a small status file, in a
    directory only this sidecar mounts read-write. One writer per file (this process);
    the lock keeps two concurrent responses from interleaving a line."""

    def __init__(self, directory: str) -> None:
        self.path = os.path.join(directory, OUTBOX_NAME)
        self.status_path = os.path.join(directory, STATUS_NAME)
        self._lock = threading.Lock()
        self._status = {"started": time.time(), "last_turn": None, "turns": 0}

    def open(self) -> None:
        """Create the outbox (empty) and the status file at startup — so a sandboxed
        window with NO outbox means the proxy never got its directory, not "quiet"."""
        with self._lock:
            with open(self.path, "a", encoding="utf-8"):
                pass
            self._write_status()

    def turn(self, collector: TurnCollector) -> None:
        """A qualifying response finished streaming: append its record (if it had one),
        and stamp the status. The outbox is touched on EVERY completed turn, so its mtime
        trailing ``last_turn`` means the parse is failing, not that the guest went quiet.
        A stream cut off before ``message_stop`` is not a turn: it neither writes nor
        stamps, so it can never make the doctor read a healthy outbox as stale."""
        if not collector.complete:
            return
        rec = collector.record()
        with self._lock:
            now = time.time()
            if rec is not None:
                with open(self.path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            else:
                os.utime(self.path)
            self._status["last_turn"] = now
            self._status["turns"] += 1
            self._write_status()

    def _write_status(self) -> None:
        tmp = self.status_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._status, f)
        os.replace(tmp, self.status_path)


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
    outbox: Outbox | None = None

    def log_message(self, fmt, *args):  # one terse line per request, no headers
        sys.stderr.write("share-proxy: %s %s\n" % (self.command, self.path.split("?")[0]))

    def _refuse(self, status: int, msg: str) -> None:
        body = json.dumps({"type": "error", "error": {"type": "proxy_error", "message": msg}}).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send(self, body: bytes | None, token: str):
        """One upstream request with ``token``; returns ``(conn, response)``."""
        up = self.upstream
        conn_cls = http.client.HTTPSConnection if up.scheme == "https" else http.client.HTTPConnection
        conn = conn_cls(up.hostname, up.port, timeout=600)
        try:
            conn.request(self.command, self.path, body=body,
                         headers=upstream_headers(list(self.headers.items()), token))
            return conn, conn.getresponse()
        except BaseException:
            conn.close()
            raise

    def _forward(self) -> None:
        if not path_allowed(self.path):
            return self._refuse(403, "only /v1/ is forwarded")
        token = read_token(self.token_file)
        if not token:
            return self._refuse(503, "operator token unavailable")
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else None
        conn = None
        try:
            conn, resp = self._send(body, token)
            if resp.status == 401:
                conn.close()
                fresh = read_token(self.token_file)
                if fresh and fresh != token:      # the host refreshed since we read it
                    conn, resp = self._send(body, fresh)
                if resp.status == 401:
                    return self._refuse(502, LOGIN_EXPIRED)
            collector = None
            if self.outbox is not None and resp.status == 200 and \
                    "text/event-stream" in (resp.getheader("content-type") or "") and \
                    records_turn(self.command, self.path, body):
                collector = TurnCollector()
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
                if collector is not None:
                    collector.feed(chunk)
            if collector is not None:
                self._record(collector)
        except OSError as e:
            try:
                self._refuse(502, f"upstream unreachable: {type(e).__name__}")
            except OSError:
                pass
        finally:
            if conn is not None:
                conn.close()
            self.close_connection = True

    def _record(self, collector: TurnCollector) -> None:
        # The outbox is a side channel: nothing it does may break the guest's response.
        try:
            self.outbox.turn(collector)
        except Exception as e:  # noqa: BLE001
            sys.stderr.write(f"share-proxy: outbox write failed: {type(e).__name__}\n")

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
    session_dir = os.environ.get("CHELA_PROXY_SESSION_DIR", "")
    if session_dir:
        _Handler.outbox = Outbox(session_dir)
        try:
            _Handler.outbox.open()
        except OSError as e:
            sys.stderr.write(f"share-proxy: no outbox ({type(e).__name__}) — relay disabled\n")
            _Handler.outbox = None
    ThreadingHTTPServer(("0.0.0.0", port), _Handler).serve_forever()


if __name__ == "__main__":
    main()
