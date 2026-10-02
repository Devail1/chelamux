"""🌐 The filtering egress proxy a ``web``-mode sandboxed share session browses through.

A sandboxed share session (:mod:`chela.share_sandbox`) normally has no route anywhere but
the token proxy. In ``web`` mode the guest container STILL sits on its ``--internal``
network with no host address; the one new thing on that network is this proxy, running in
its own sidecar (which, unlike the guest, has an ordinary route out). The guest's HTTP(S)
clients — Claude's WebFetch, ``curl``, ``pip``, the in-container headless Chromium — are
pointed at it with ``HTTPS_PROXY``/``HTTP_PROXY``. Anything that ignores the proxy has
nowhere to go.

What it enforces, per request:

* **public destinations only, checked by IP after DNS.** The proxy resolves the name
  ITSELF, refuses the request if ANY resolved address is not globally routable (loopback,
  RFC 1918, link-local, CGNAT ``100.64/10``, unique-local / link-local IPv6, the IPv4
  embedded in a mapped / 6to4 / Teredo / NAT64 IPv6 address, multicast, reserved, the
  sidecar's own default gateway — i.e. the docker host — and any operator- or launcher-
  supplied ``CHELA_WEB_DENY_NETS``), then connects to the CHECKED address. The guest's
  name is never re-resolved, so there is no DNS-rebinding window between check and use;
* **ports 80 and 443 only**;
* **rate limits** — a per-site bucket (default 1 request/s, burst 3) and a global one
  (default 8/s, burst 20), so a runaway loop cannot hammer a site from the operator's IP.
  Over the limit a request WAITS for its slot (browsers stay usable); one that would wait
  longer than ``MAX_WAIT_S`` is refused with 429;
* an optional operator **denylist / allowlist** of domain suffixes
  (``CHELA_WEB_DENY`` / ``CHELA_WEB_ALLOW``); unset = any public host;
* **a log line per request** — method, host, port, address, path (plain HTTP only:
  HTTPS is tunnelled, not decrypted), status, bytes each way, refusal reason — appended as
  JSON to ``CHELA_WEB_LOG`` (a per-session file the operator can read).

It deliberately does NOT man-in-the-middle TLS: that would need a CA inside the guest and
would let this process read the guest's traffic. It holds no credential of any kind.

Stdlib-only and importable with nothing else from chela, exactly like
:mod:`chela.share_proxy`: the sidecar runs this file directly from a read-only bind mount
on a stock ``python`` image.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import os
import re
import select
import socket
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_PORT = 3128
ALLOWED_PORTS = frozenset({80, 443})
MAX_WAIT_S = 30.0
CONNECT_TIMEOUT_S = 15.0
IDLE_TIMEOUT_S = 300.0

# NAT64 well-known prefix: the low 32 bits are an IPv4 address the gateway would reach.
_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$")
# Hop-by-hop + proxy headers (RFC 9110 §7.6.1) — never forwarded.
_HOP = {"connection", "keep-alive", "proxy-connection", "proxy-authorization",
        "proxy-authenticate", "te", "trailer", "transfer-encoding", "upgrade"}


# --- the address policy -----------------------------------------------------------------

def parse_nets(raw: str) -> list:
    """``"10.9.0.0/16, 203.0.113.7"`` → networks; junk entries are skipped."""
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            out.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            continue
    return out


def _embedded_v4(ip) -> list:
    """IPv4 addresses an IPv6 address smuggles (mapped, 6to4, Teredo, NAT64)."""
    if ip.version != 6:
        return []
    out = [a for a in (ip.ipv4_mapped, ip.sixtofour) if a is not None]
    if ip.teredo:
        out.append(ip.teredo[1])
    if ip in _NAT64:
        out.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
    return out


def ip_refusal(addr: str, deny_nets=()) -> str | None:
    """Why ``addr`` may not be a destination, or None when it is a public address."""
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return f"{addr!r} is not an IP address"
    for cand in [ip, *_embedded_v4(ip)]:
        if (not cand.is_global or cand.is_multicast or cand.is_unspecified
                or cand.is_reserved or cand.is_loopback or cand.is_link_local
                or cand.is_private):
            return f"{addr} is not a public address"
        for net in deny_nets:
            if cand.version == net.version and cand in net:
                return f"{addr} is in a denied network ({net})"
    return None


def default_gateway(route_file: str = "/proc/net/route") -> str | None:
    """This container's IPv4 default gateway (the docker host, from the sidecar's side)."""
    try:
        with open(route_file, encoding="ascii") as f:
            for line in f.readlines()[1:]:
                cols = line.split()
                if len(cols) > 2 and cols[1] == "00000000":
                    return str(ipaddress.IPv4Address(bytes.fromhex(cols[2])[::-1]))
    except (OSError, ValueError):
        return None
    return None


def system_resolver(host: str, port: int) -> list[str]:
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    seen: list[str] = []
    for info in infos:
        a = info[4][0]
        if a not in seen:
            seen.append(a)
    return seen


def _suffix_match(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def parse_domains(raw: str) -> tuple[str, ...]:
    return tuple(d.strip().lower().strip(".") for d in (raw or "").split(",") if d.strip(". "))


def normalize_host(host: str) -> str:
    h = (host or "").strip().lower()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h.rstrip(".")


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
        return True
    except ValueError:
        return False


class Policy:
    """Decides, and resolves, where a request may go. ``resolver(host, port) -> [addr]``
    is injectable so tests never touch real DNS."""

    def __init__(self, *, resolver=system_resolver, deny_nets=(), deny=(), allow=()) -> None:
        self.resolver = resolver
        self.deny_nets = list(deny_nets)
        self.deny = tuple(deny)
        self.allow = tuple(allow)

    def check(self, host: str, port: int) -> tuple[list[str], str | None]:
        """``(checked_addresses, None)`` or ``([], reason)``. Refuses when ANY resolved
        address is non-public, so a name with one public and one private answer can't be
        used to reach the private one."""
        host = normalize_host(host)
        if port not in ALLOWED_PORTS:
            return [], f"port {port} is not allowed (80 and 443 only)"
        if not host:
            return [], "no destination host"
        literal = _is_ip_literal(host)
        if not literal and not _HOST_RE.match(host):
            return [], f"{host!r} is not a valid host name"
        if _suffix_match(host, self.deny):
            return [], f"{host} is on the operator's denylist"
        if self.allow and (literal or not _suffix_match(host, self.allow)):
            return [], f"{host} is not on the operator's allowlist"
        if literal:
            addrs = [host]
        else:
            try:
                addrs = list(self.resolver(host, port))
            except (OSError, UnicodeError) as e:
                return [], f"{host} does not resolve ({type(e).__name__})"
        if not addrs:
            return [], f"{host} does not resolve"
        for a in addrs:
            why = ip_refusal(a, self.deny_nets)
            if why:
                return [], (why if literal else f"{host} resolves to {why}")
        return addrs, None


_dial = socket.create_connection   # the one place a socket is opened (tests stub it)


def connect_pinned(addrs: list[str], port: int, timeout: float = CONNECT_TIMEOUT_S) -> socket.socket:
    """A TCP connection to the first reachable CHECKED address — never to a name."""
    last: OSError | None = None
    for a in addrs:
        try:
            return _dial((a, port), timeout=timeout)
        except OSError as e:
            last = e
    raise last or OSError("no address to connect to")


# --- rate limiting -----------------------------------------------------------------------

_SHORT_SLD = {"co", "com", "net", "org", "gov", "edu", "ac", "or", "ne", "go"}


def rate_key(host: str) -> str:
    """The bucket a host shares: its registrable-ish domain (``www.linkedin.com`` and
    ``linkedin.com`` share one; ``x.co.uk`` keeps three labels)."""
    host = normalize_host(host)
    if _is_ip_literal(host):
        return host
    labels = host.split(".")
    if len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _SHORT_SLD:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])


class RateLimiter:
    """GCRA token buckets: a per-site one and a global one. :meth:`reserve` returns how
    long the caller must wait before sending, or None (and reserves nothing) when that
    would exceed ``max_wait``."""

    def __init__(self, *, host_rps: float = 1.0, host_burst: int = 3,
                 global_rps: float = 8.0, global_burst: int = 20,
                 max_wait: float = MAX_WAIT_S, clock=time.monotonic) -> None:
        self.host_t = 1.0 / host_rps
        self.host_tau = self.host_t * (max(1, host_burst) - 1)
        self.glob_t = 1.0 / global_rps
        self.glob_tau = self.glob_t * (max(1, global_burst) - 1)
        self.max_wait = max_wait
        self.clock = clock
        self._tat: dict[str, float] = {}
        self._glob_tat = 0.0
        self._lock = threading.Lock()

    def reserve(self, host: str) -> float | None:
        key = rate_key(host)
        with self._lock:
            now = self.clock()
            h_tat = max(self._tat.get(key, now), now)
            g_tat = max(self._glob_tat, now)
            wait = max(0.0, h_tat - self.host_tau - now, g_tat - self.glob_tau - now)
            if wait > self.max_wait:
                return None
            # Each bucket books its slot independently (plain GCRA): a request held back
            # by one bucket doesn't push the OTHER bucket's schedule into the future.
            self._tat[key] = h_tat + self.host_t
            self._glob_tat = g_tat + self.glob_t
            return wait


# --- the request log ---------------------------------------------------------------------

class RequestLog:
    def __init__(self, path: str = "", stream=None, clock=time.time) -> None:
        self.path = path
        self.stream = stream if stream is not None else sys.stderr
        self.clock = clock
        self._lock = threading.Lock()

    def write(self, **rec) -> None:
        rec = {"ts": round(self.clock(), 3), **rec}
        line = json.dumps(rec, separators=(",", ":"))
        with self._lock:
            if self.path:
                try:
                    with open(self.path, "a", encoding="utf-8") as f:
                        f.write(line + "\n")
                except OSError:
                    pass
            try:
                self.stream.write("web-proxy: " + line + "\n")
                self.stream.flush()
            except (OSError, ValueError):
                pass


# --- the HTTP handler ---------------------------------------------------------------------

def _split_hostport(authority: str, default_port: int) -> tuple[str, int] | None:
    authority = authority.strip()
    m = re.match(r"^\[([^\]]+)\](?::(\d+))?$", authority) or re.match(r"^([^:]+)(?::(\d+))?$", authority)
    if not m:
        return None
    port = int(m.group(2)) if m.group(2) else default_port
    return m.group(1), port


class Handler(BaseHTTPRequestHandler):
    policy: Policy = Policy()
    limiter: RateLimiter = RateLimiter()
    reqlog: RequestLog = RequestLog()
    sleep = staticmethod(time.sleep)
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # the JSON request log replaces the default
        pass

    def _refuse(self, status: int, msg: str, host: str = "", port: int = 0, path: str = "") -> None:
        self.reqlog.write(method=self.command, host=host, port=port, path=path, status=status,
                          refused=msg)
        body = (msg + "\n").encode()
        try:
            self.send_response(status)
            self.send_header("content-type", "text/plain; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.send_header("connection", "close")
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass
        self.close_connection = True

    def _admit(self, host: str, port: int, path: str = "") -> list[str] | None:
        """Policy + rate limit. Returns the checked addresses, or None after refusing."""
        addrs, why = self.policy.check(host, port)
        if why:
            self._refuse(403, f"refused: {why}", host, port, path)
            return None
        wait = self.limiter.reserve(host)
        if wait is None:
            self._refuse(429, f"rate limited: {rate_key(host)}", host, port, path)
            return None
        if wait > 0:
            self.sleep(wait)
        return addrs

    def do_CONNECT(self):
        hp = _split_hostport(self.path, 443)
        if not hp:
            return self._refuse(400, "bad CONNECT target")
        host, port = normalize_host(hp[0]), hp[1]
        addrs = self._admit(host, port)
        if addrs is None:
            return
        try:
            upstream = connect_pinned(addrs, port)
        except OSError as e:
            return self._refuse(502, f"upstream unreachable: {type(e).__name__}", host, port)
        ip = upstream.getpeername()[0]
        self.send_response(200, "Connection Established")
        self.end_headers()
        self.wfile.flush()
        up, down = self._relay(self.connection, upstream)
        self.reqlog.write(method="CONNECT", host=host, port=port, ip=ip, status=200,
                          bytes_up=up, bytes_down=down)
        self.close_connection = True

    @staticmethod
    def _relay(client: socket.socket, upstream: socket.socket) -> tuple[int, int]:
        up = down = 0
        socks = [client, upstream]
        try:
            while True:
                r, _, x = select.select(socks, [], socks, IDLE_TIMEOUT_S)
                if x or not r:
                    break
                done = False
                for s in r:
                    data = s.recv(65536)
                    if not data:
                        done = True
                        break
                    if s is client:
                        upstream.sendall(data)
                        up += len(data)
                    else:
                        client.sendall(data)
                        down += len(data)
                if done:
                    break
        except OSError:
            pass
        finally:
            upstream.close()
        return up, down

    def _forward_plain(self):
        parsed = urllib.parse.urlsplit(self.path)
        if parsed.scheme != "http" or not parsed.netloc:
            return self._refuse(400, "only absolute http:// URLs (use CONNECT for https)")
        hp = _split_hostport(parsed.netloc.rsplit("@", 1)[-1], 80)
        if not hp:
            return self._refuse(400, "bad URL host")
        host, port = normalize_host(hp[0]), hp[1]
        path = parsed.path or "/"
        addrs = self._admit(host, port, path)
        if addrs is None:
            return
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n) if n else None
        headers = {k: v for k, v in self.headers.items() if k.lower() not in _HOP | {"host"}}
        hostfmt = f"[{host}]" if ":" in host else host
        headers["Host"] = hostfmt if port == 80 else f"{hostfmt}:{port}"
        target = path + (("?" + parsed.query) if parsed.query else "")
        try:
            sock = connect_pinned(addrs, port)
        except OSError as e:
            return self._refuse(502, f"upstream unreachable: {type(e).__name__}", host, port, path)
        ip = sock.getpeername()[0]
        conn = http.client.HTTPConnection(host, port, timeout=CONNECT_TIMEOUT_S * 4)
        conn.sock = sock      # pinned: http.client never resolves the name itself
        down = 0
        try:
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in _HOP and k.lower() != "content-length":
                    self.send_header(k, v)
            self.send_header("connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    down += len(chunk)
            self.reqlog.write(method=self.command, host=host, port=port, ip=ip, path=path,
                              status=resp.status, bytes_up=len(body or b""), bytes_down=down)
        except (OSError, http.client.HTTPException) as e:
            self.reqlog.write(method=self.command, host=host, port=port, ip=ip, path=path,
                              status=502, refused=f"upstream error: {type(e).__name__}")
        finally:
            conn.close()
            self.close_connection = True

    do_GET = do_POST = do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _forward_plain


def configure_from_env(env=os.environ) -> int:
    deny_nets = parse_nets(env.get("CHELA_WEB_DENY_NETS", ""))
    gw = default_gateway()
    if gw:
        deny_nets.append(ipaddress.ip_network(gw))
    Handler.policy = Policy(deny_nets=deny_nets, deny=parse_domains(env.get("CHELA_WEB_DENY", "")),
                            allow=parse_domains(env.get("CHELA_WEB_ALLOW", "")))

    def _f(name, default):
        try:
            v = float(env.get(name) or default)
            return v if v > 0 else default
        except ValueError:
            return default

    Handler.limiter = RateLimiter(host_rps=_f("CHELA_WEB_HOST_RPS", 1.0),
                                  global_rps=_f("CHELA_WEB_GLOBAL_RPS", 8.0))
    Handler.reqlog = RequestLog(env.get("CHELA_WEB_LOG", ""))
    return int(env.get("CHELA_WEB_PORT") or DEFAULT_PORT)


def main() -> None:
    port = configure_from_env()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()


if __name__ == "__main__":
    main()
