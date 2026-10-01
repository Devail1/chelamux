"""🌐 CMX-418: the opt-in ``web`` network mode for sandboxed share sessions.

Two halves:

* :mod:`chela.share_web_proxy` — the filtering egress proxy. Every destination check is
  exercised with a STUBBED resolver and a stubbed dialer, so no test touches real DNS or
  the internet. The one end-to-end ACCEPT case runs real sockets on loopback: the proxy
  thinks it is dialling a fake PUBLIC address, and the stub dialer routes that to a local
  test server — which also proves the connection is pinned to the CHECKED address.
* :mod:`chela.share_sandbox` — the launcher records the mode in its own argv, a ``none``
  session carries no proxy env and no route, a ``web`` session is still on its internal,
  no-host-address network, and ``check_share_session`` refuses a session whose live shape
  doesn't match the mode it was launched with. No docker is run.
"""
from __future__ import annotations

import http.client
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chela import share_sandbox as sb
from chela import share_web_proxy as wp

UID, GID = os.getuid(), os.getgid()
SID = "0123456789ab"
WORKSPACE = "/tmp"
PUBLIC_IP = "93.184.216.34"      # a public address the stub dialer maps to a local server


# =====================================================================================
# the proxy: address policy
# =====================================================================================

REFUSED_IPS = [
    "127.0.0.1", "127.8.9.10", "10.0.0.1", "10.255.255.254", "172.16.0.1", "172.31.255.254",
    "192.168.1.1", "169.254.169.254", "100.64.0.1", "100.127.255.254",
    "172.17.0.1",                 # docker's default host gateway
    "0.0.0.0", "224.0.0.1", "255.255.255.255", "240.0.0.1",
    "::1", "::", "fe80::1", "fc00::1", "fd12:3456::1",
    "::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:192.168.1.1",
    "2002:7f00:1::1",             # 6to4 wrapping 127.0.0.1
    "2002:c0a8:101::1",           # 6to4 wrapping 192.168.1.1
    "64:ff9b::a00:1",             # NAT64 wrapping 10.0.0.1
    "64:ff9b::a9fe:a9fe",         # NAT64 wrapping 169.254.169.254
]


@pytest.mark.parametrize("addr", REFUSED_IPS)
def test_ip_refusal_refuses_private_loopback_link_local_cgnat_and_v6(addr):
    assert wp.ip_refusal(addr) is not None


@pytest.mark.parametrize("addr", [PUBLIC_IP, "1.1.1.1", "8.8.8.8", "2606:4700:4700::1111"])
def test_ip_refusal_accepts_public_addresses(addr):
    # The negative control: without it, "refuse everything" would pass the table above.
    assert wp.ip_refusal(addr) is None


def test_ip_refusal_honours_extra_deny_nets():
    nets = wp.parse_nets("203.0.114.0/24, 1.2.3.4, junk")
    assert wp.ip_refusal("1.2.3.4", nets) and wp.ip_refusal("203.0.114.9", nets)
    assert wp.ip_refusal("1.2.3.5", nets) is None


def _stub_resolver(table):
    calls = []

    def resolve(host, port):
        calls.append((host, port))
        if host not in table:
            raise socket.gaierror("no such host")
        return list(table[host])
    resolve.calls = calls
    return resolve


@pytest.mark.parametrize("name, answer", [
    ("intranet.example", ["10.1.2.3"]),
    ("router.example", ["192.168.1.1"]),
    ("metadata.example", ["169.254.169.254"]),
    ("loop.example", ["127.0.0.1"]),
    ("cgnat.example", ["100.64.1.1"]),
    ("dockerhost.example", ["172.17.0.1"]),
    ("v6loop.example", ["::1"]),
    ("ula.example", ["fd00::5"]),
    # one public + one private answer: refused, so a mixed record can't reach the private one
    ("mixed.example", [PUBLIC_IP, "10.0.0.7"]),
])
def test_a_hostname_resolving_to_a_private_ip_is_refused(name, answer):
    pol = wp.Policy(resolver=_stub_resolver({name: answer}))
    addrs, why = pol.check(name, 443)
    assert addrs == [] and why and name in why


def test_a_public_hostname_is_accepted_with_its_checked_addresses():
    pol = wp.Policy(resolver=_stub_resolver({"jobs.example": [PUBLIC_IP]}))
    assert pol.check("Jobs.Example.", 443) == ([PUBLIC_IP], None)


@pytest.mark.parametrize("literal", ["127.0.0.1", "10.0.0.1", "[::1]", "169.254.169.254"])
def test_a_private_ip_literal_is_refused_without_resolving(literal):
    res = _stub_resolver({})
    addrs, why = wp.Policy(resolver=res).check(literal, 80)
    assert addrs == [] and why and res.calls == []


def test_the_sidecars_default_gateway_is_denied(tmp_path):
    route = tmp_path / "route"
    # gateway 0x0111A8C0 little-endian = 192.168.17.1 — but use a PUBLIC-looking one so
    # only the explicit gateway deny (not the private-range rule) can refuse it.
    route.write_text("Iface\tDestination\tGateway\tFlags\n"
                     "eth0\t00000000\t01020304\t0003\n")
    gw = wp.default_gateway(str(route))
    assert gw == "4.3.2.1"
    pol = wp.Policy(resolver=_stub_resolver({"gw.example": [gw]}),
                    deny_nets=wp.parse_nets(gw))
    assert pol.check("gw.example", 443)[1]


@pytest.mark.parametrize("port", [22, 25, 8080, 3128, 6379, 5432, 0, 65535])
def test_ports_other_than_80_and_443_are_refused(port):
    pol = wp.Policy(resolver=_stub_resolver({"jobs.example": [PUBLIC_IP]}))
    addrs, why = pol.check("jobs.example", port)
    assert addrs == [] and "port" in why


@pytest.mark.parametrize("port", [80, 443])
def test_ports_80_and_443_are_allowed(port):
    pol = wp.Policy(resolver=_stub_resolver({"jobs.example": [PUBLIC_IP]}))
    assert pol.check("jobs.example", port) == ([PUBLIC_IP], None)


def test_operator_denylist_and_allowlist():
    res = _stub_resolver({"www.linkedin.com": [PUBLIC_IP], "jobs.example": [PUBLIC_IP]})
    deny = wp.Policy(resolver=res, deny=wp.parse_domains("linkedin.com"))
    assert deny.check("www.linkedin.com", 443)[1] and deny.check("jobs.example", 443)[1] is None
    allow = wp.Policy(resolver=res, allow=wp.parse_domains("jobs.example"))
    assert allow.check("jobs.example", 443)[1] is None
    assert allow.check("www.linkedin.com", 443)[1]
    assert allow.check(PUBLIC_IP, 443)[1]           # an IP literal can't dodge an allowlist


def test_an_unresolvable_or_malformed_host_is_refused():
    pol = wp.Policy(resolver=_stub_resolver({}))
    assert pol.check("nope.example", 443)[1]
    assert pol.check("bad host!", 443)[1]
    assert pol.check("", 443)[1]


# =====================================================================================
# the proxy: rate limits
# =====================================================================================

class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_rate_limiter_holds_a_burst_to_one_per_second_per_site():
    clock = Clock()
    rl = wp.RateLimiter(host_rps=1.0, host_burst=3, global_rps=100, global_burst=100,
                        max_wait=60, clock=clock)
    waits = [rl.reserve("www.linkedin.com") for _ in range(6)]
    # three free, then one per second — a burst of six can't all go out at once
    assert waits == [0.0, 0.0, 0.0, 1.0, 2.0, 3.0]
    # the same SITE under another name shares the bucket…
    assert rl.reserve("linkedin.com") == 4.0
    # …another site does not
    assert rl.reserve("jobs.example") == 0.0


def test_rate_limiter_global_cap_spans_sites():
    clock = Clock()
    rl = wp.RateLimiter(host_rps=100, host_burst=100, global_rps=2.0, global_burst=2,
                        max_wait=60, clock=clock)
    waits = [rl.reserve(f"site{i}.example") for i in range(5)]
    assert waits == [0.0, 0.0, 0.5, 1.0, 1.5]


def test_rate_limiter_refuses_past_max_wait_and_reserves_nothing():
    clock = Clock()
    rl = wp.RateLimiter(host_rps=1.0, host_burst=1, global_rps=100, global_burst=100,
                        max_wait=1.5, clock=clock)
    assert [rl.reserve("a.example") for _ in range(3)] == [0.0, 1.0, None]
    clock.t += 2.0      # the refused request reserved nothing: the bucket drains on time
    assert rl.reserve("a.example") == 0.0


def test_rate_limiter_recovers_with_time():
    clock = Clock()
    rl = wp.RateLimiter(host_rps=1.0, host_burst=2, global_rps=100, global_burst=100,
                        max_wait=60, clock=clock)
    assert [rl.reserve("a.example") for _ in range(3)] == [0.0, 0.0, 1.0]
    clock.t += 10
    assert rl.reserve("a.example") == 0.0


@pytest.mark.parametrize("host, key", [
    ("www.linkedin.com", "linkedin.com"), ("media.licdn.com", "licdn.com"),
    ("linkedin.com", "linkedin.com"), ("jobs.bbc.co.uk", "bbc.co.uk"), (PUBLIC_IP, PUBLIC_IP),
])
def test_rate_key_groups_a_site(host, key):
    assert wp.rate_key(host) == key


# =====================================================================================
# the proxy over the wire: ACCEPT a public host (stubbed), refuse the rest, log it all
# =====================================================================================

class _Backend(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        body = f"hello from {self.headers.get('Host')} {self.path}".encode()
        self.send_response(200)
        self.send_header("content-length", str(len(body)))
        self.send_header("connection", "close")
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture
def wire(monkeypatch, tmp_path):
    """A real proxy + a real backend on loopback. The proxy's policy uses a stubbed
    resolver; its dialer maps (PUBLIC_IP, 80/443) to the backend and refuses anything
    else, recording every dial."""
    backend = ThreadingHTTPServer(("127.0.0.1", 0), _Backend)
    threading.Thread(target=backend.serve_forever, args=(0.05,), daemon=True).start()
    bport = backend.server_address[1]
    dials = []
    real_create = socket.create_connection

    def dial(addr, timeout=None):
        dials.append(addr)
        if addr[0] == PUBLIC_IP and addr[1] in (80, 443):
            return real_create(("127.0.0.1", bport), timeout=timeout)
        raise ConnectionRefusedError(f"test dialer: {addr} is not a test destination")

    monkeypatch.setattr(wp, "_dial", dial)
    log_path = tmp_path / "web.log"

    class H(wp.Handler):
        policy = wp.Policy(resolver=_stub_resolver({
            "jobs.example": [PUBLIC_IP], "intranet.example": ["192.168.1.10"],
            "rebind.example": ["10.0.0.9"]}))
        limiter = wp.RateLimiter(host_rps=1.0, host_burst=2, global_rps=100,
                                 global_burst=100, max_wait=0.0)
        reqlog = wp.RequestLog(str(log_path), stream=open(os.devnull, "w"))
        sleep = staticmethod(lambda s: None)

    proxy = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=proxy.serve_forever, args=(0.05,), daemon=True).start()
    state = {"port": proxy.server_address[1], "dials": dials, "log": log_path}
    yield state
    proxy.shutdown()
    backend.shutdown()


def _proxy_socket(state):
    # Connect to the proxy with the REAL create_connection (the module's is the stub).
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(10)
    s.connect(("127.0.0.1", state["port"]))
    return s


def _recv_until(s, marker=b"\r\n\r\n"):
    buf = b""
    while marker not in buf:
        chunk = s.recv(4096)
        if not chunk:
            break
        buf += chunk
    return buf


def _recv_all(s):
    buf = b""
    while True:
        chunk = s.recv(4096)
        if not chunk:
            return buf
        buf += chunk


def _connect(state, target):
    s = _proxy_socket(state)
    s.sendall(f"CONNECT {target} HTTP/1.1\r\nHost: {target}\r\n\r\n".encode())
    return s, _recv_until(s)


def _log(state):
    return [json.loads(line) for line in state["log"].read_text().splitlines()]


def test_a_public_https_host_is_fetched_through_the_proxy(wire):
    s, head = _connect(wire, "jobs.example:443")
    assert head.startswith(b"HTTP/1.1 200"), head
    s.sendall(b"GET /jobs/123 HTTP/1.1\r\nHost: jobs.example\r\nConnection: close\r\n\r\n")
    resp = _recv_all(s)
    s.close()
    assert b"200 OK" in resp and b"hello from jobs.example /jobs/123" in resp
    # pinned: the proxy dialled the CHECKED address, never the name
    assert wire["dials"] == [(PUBLIC_IP, 443)]
    rec = _log(wire)[-1]
    assert rec["method"] == "CONNECT" and rec["host"] == "jobs.example" and rec["status"] == 200
    assert rec["bytes_down"] > 0 and rec["bytes_up"] > 0


def test_a_public_plain_http_url_is_fetched_and_logged_with_its_path(wire):
    c = http.client.HTTPConnection("127.0.0.1", wire["port"], timeout=10)
    c.request("GET", "http://jobs.example/listing?id=7")
    r = c.getresponse()
    body = r.read()
    assert r.status == 200 and b"hello from jobs.example /listing?id=7" in body
    assert wire["dials"] == [(PUBLIC_IP, 80)]
    rec = _log(wire)[-1]
    assert (rec["host"], rec["path"], rec["status"]) == ("jobs.example", "/listing", 200)
    assert rec["bytes_down"] == len(body)


@pytest.mark.parametrize("target", ["intranet.example:443", "rebind.example:443",
                                    "127.0.0.1:443", "[::1]:443", "169.254.169.254:80",
                                    "jobs.example:22", "jobs.example:8080"])
def test_refused_destinations_never_dial_and_are_logged(wire, target):
    s, head = _connect(wire, target)
    s.close()
    assert head.startswith(b"HTTP/1.1 403"), head
    assert wire["dials"] == []
    rec = _log(wire)[-1]
    assert rec["status"] == 403 and rec["refused"]


def test_a_plain_http_url_to_a_private_name_is_refused(wire):
    c = http.client.HTTPConnection("127.0.0.1", wire["port"], timeout=10)
    c.request("GET", "http://intranet.example/admin")
    assert c.getresponse().status == 403 and wire["dials"] == []


def test_the_rate_limit_holds_over_the_wire(wire):
    statuses = []
    for _ in range(4):
        s, head = _connect(wire, "jobs.example:443")
        statuses.append(head.split(b" ", 2)[1])
        s.close()
    # burst 2, max_wait 0: the 3rd and 4th requests of the burst are refused with 429
    assert statuses == [b"200", b"200", b"429", b"429"]
    assert len(wire["dials"]) == 2


def test_a_direct_request_to_the_proxy_is_refused(wire):
    c = http.client.HTTPConnection("127.0.0.1", wire["port"], timeout=10)
    c.request("GET", "/")
    assert c.getresponse().status == 400 and wire["dials"] == []


def test_configure_from_env_reads_the_operator_knobs(monkeypatch, tmp_path):
    monkeypatch.setattr(wp, "default_gateway", lambda: "172.18.0.1")
    port = wp.configure_from_env({
        "CHELA_WEB_PORT": "4000", "CHELA_WEB_DENY": "evil.example",
        "CHELA_WEB_DENY_NETS": "1.2.3.4", "CHELA_WEB_HOST_RPS": "0.5",
        "CHELA_WEB_LOG": str(tmp_path / "l")})
    assert port == 4000
    pol = wp.Handler.policy
    assert pol.deny == ("evil.example",)
    assert {str(n) for n in pol.deny_nets} == {"1.2.3.4/32", "172.18.0.1/32"}
    assert wp.Handler.limiter.host_t == 2.0


# =====================================================================================
# the launcher: the mode is recorded, and none stays none
# =====================================================================================

def _opt(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def test_launcher_argv_records_the_mode_and_verify_pane_reads_it_back():
    for net in sb.NET_MODES:
        argv = sb.launcher_argv(SID, WORKSPACE, net)
        assert sb.verify_pane(argv, "tmux: server", ["docker"]) == (SID, WORKSPACE, net)
    legacy = [sys.executable, "-m", sb.LAUNCH_MODULE, "run", "--id", SID, WORKSPACE]
    assert sb.verify_pane(legacy, "tmux: server", []) == (SID, WORKSPACE, "none")
    with pytest.raises(ValueError):
        sb.launcher_argv(SID, WORKSPACE, "host")
    bad = sb.launcher_argv(SID, WORKSPACE, "web")
    bad[7] = "host"
    assert isinstance(sb.verify_pane(bad, "tmux: server", []), str)


def test_a_none_session_has_no_proxy_env_and_no_route():
    argv = sb.guest_run_argv(SID, WORKSPACE, UID, GID, "/usr/bin/true", "none")
    envs = _opt(argv, "-e")
    assert not [e for e in envs if e.split("=", 1)[0].lower().endswith("_proxy")]
    assert _opt(argv, "--network") == [sb.network_name(SID)]
    assert f"{sb.NET_LABEL}=none" in _opt(argv, "--label")
    assert sb.image() in argv and sb.web_image() not in argv


def test_a_web_session_guest_stays_on_its_internal_network_and_uses_the_proxy():
    argv = sb.guest_run_argv(SID, WORKSPACE, UID, GID, "/usr/bin/true", "web")
    envs = dict(e.split("=", 1) for e in _opt(argv, "-e"))
    assert envs["HTTPS_PROXY"] == envs["HTTP_PROXY"] == sb.WEB_URL
    assert envs["NO_PROXY"] == sb.PROXY_ALIAS          # the token proxy stays direct
    assert envs["ANTHROPIC_BASE_URL"].startswith(f"http://{sb.PROXY_ALIAS}:")
    # still exactly one network — the internal one — and no way to the host
    assert _opt(argv, "--network") == [sb.network_name(SID)]
    for flag in ("-p", "--publish", "--add-host", "--privileged", "--cap-add", "--net"):
        assert flag not in argv
    assert sb.web_image() in argv and f"{sb.NET_LABEL}=web" in _opt(argv, "--label")
    mounts = _opt(argv, "-v")
    assert not [m for m in mounts if any(d in m for d in (".ssh", ".claude:", ".chela", ".config"))]
    assert not [m for m in mounts if "docker.sock" in m or "tmux" in m]


def test_the_network_is_still_internal_with_no_host_address():
    argv = sb.network_create_argv(SID)
    assert "--internal" in argv and "com.docker.network.bridge.inhibit_ipv4=true" in argv


def test_the_web_sidecar_holds_no_token_and_runs_locked_down(monkeypatch):
    monkeypatch.setattr(sb, "host_deny_nets", lambda: ["198.51.100.7"])
    monkeypatch.setenv("CHELA_SHARE_WEB_DENY", "evil.example")
    argv = sb.web_proxy_run_argv(SID, UID, GID)
    mounts = _opt(argv, "-v")
    assert f"{sb.WEB_SCRIPT_MOUNT}:ro" in mounts[0] and len(mounts) == 2
    assert mounts[1].endswith(f":{sb.WEB_LOG_MOUNT}")
    assert not [m for m in mounts if "credentials" in m or sb.PROXY_TOKEN_MOUNT in m]
    assert "--read-only" in argv and _opt(argv, "--cap-drop") == ["ALL"]
    envs = dict(e.split("=", 1) for e in _opt(argv, "-e"))
    assert "198.51.100.7" in envs["CHELA_WEB_DENY_NETS"]
    assert envs["CHELA_WEB_DENY"] == "evil.example"
    assert "-p" not in argv and "--network" not in argv


def test_preflight_requires_the_browser_image_only_for_web(monkeypatch, tmp_path):
    tok = tmp_path / "tok"
    tok.write_text("t")
    ws = tmp_path / "proj"
    ws.mkdir()
    monkeypatch.setenv("CHELA_SHARE_SANDBOX_TOKEN_FILE", str(tok))
    monkeypatch.setattr(sb.shutil, "which", lambda n: f"/usr/bin/{n}")

    class P:
        def __init__(self, rc):
            self.returncode, self.stdout, self.stderr = rc, "", ""

    monkeypatch.setattr(sb, "_docker", lambda *a, **k: P(1 if sb.web_image() in a else 0))
    assert sb.preflight(str(ws), "none") is None
    why = sb.preflight(str(ws), "web")
    assert why and "docker build" in why and sb.web_image() in why
    assert sb.preflight(str(ws), "bogus")


def test_run_brings_up_the_web_sidecar_only_in_web_mode(monkeypatch, tmp_path):
    monkeypatch.setattr(sb, "preflight", lambda cwd, net="none": None)
    monkeypatch.setattr(sb, "claude_binary", lambda: "/usr/bin/true")
    monkeypatch.setattr(sb, "host_deny_nets", lambda: [])
    monkeypatch.setattr(sb, "web_log_path", lambda sid: tmp_path / "share-web" / f"{sid}.log")
    monkeypatch.setattr(sb.signal, "signal", lambda *a: None)
    ran, guest, cleaned = [], [], []

    class P:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(sb.subprocess, "run", lambda argv, **k: ran.append(argv) or P())
    monkeypatch.setattr(sb.subprocess, "call", lambda argv: guest.append(argv) or 0)
    monkeypatch.setattr(sb, "cleanup", lambda sid: cleaned.append(sid))
    assert sb.run(SID, WORKSPACE, "none") == 0
    assert not [a for a in ran if sb.web_proxy_name(SID) in a]
    assert "HTTPS_PROXY=" + sb.WEB_URL not in guest[-1]
    ran.clear()
    assert sb.run(SID, WORKSPACE, "web") == 0
    assert [a for a in ran if a[:2] == ["docker", "run"] and sb.web_proxy_name(SID) in a]
    assert ["docker", "network", "connect", "--alias", sb.WEB_ALIAS,
            sb.network_name(SID), sb.web_proxy_name(SID)] in ran
    assert "HTTPS_PROXY=" + sb.WEB_URL in guest[-1]
    assert (tmp_path / "share-web" / f"{SID}.log").exists()
    assert cleaned == [SID, SID]


def test_cleanup_removes_the_web_sidecar(monkeypatch):
    seen = []
    monkeypatch.setattr(sb, "_docker", lambda *a, **k: seen.append(a))
    sb.cleanup(SID)
    assert ("rm", "-f", sb.web_proxy_name(SID)) in seen


# =====================================================================================
# check_share_session verifies the mode it was launched with
# =====================================================================================

def _guest(mode):
    net = sb.network_name(SID)
    env = ["HOME=/home/guest"] + [f"{k}={v}" for k, v in sb.guest_proxy_env(mode).items()]
    return {
        "State": {"Running": True},
        "Config": {"Labels": {sb.LABEL: SID, sb.NET_LABEL: mode},
                   "User": f"{UID}:{GID}", "Env": env},
        "HostConfig": {"CapDrop": ["ALL"], "CapAdd": None, "Privileged": False,
                       "ReadonlyRootfs": True, "SecurityOpt": ["no-new-privileges"],
                       "Memory": 2 << 30, "PidsLimit": 1024, "NetworkMode": net},
        "NetworkSettings": {"Networks": {net: {}}},
        "Mounts": [
            {"Type": "bind", "Source": os.path.realpath(WORKSPACE),
             "Destination": sb.GUEST_WORKDIR, "RW": True},
            {"Type": "bind", "Source": "/usr/bin/true", "Destination": sb.CLAUDE_MOUNT, "RW": False},
        ],
    }


def _network(mode):
    members = {"a": {"Name": sb.container_name(SID)}, "b": {"Name": sb.proxy_name(SID)}}
    if mode == "web":
        members["c"] = {"Name": sb.web_proxy_name(SID)}
    return {"Internal": True, "Options": {"com.docker.network.bridge.inhibit_ipv4": "true"},
            "Containers": members}


def _web_sidecar():
    return {
        "State": {"Running": True},
        "Config": {"Labels": {sb.LABEL: SID, sb.NET_LABEL: "web"}, "User": f"{UID}:{GID}",
                   "Cmd": ["python", sb.WEB_SCRIPT_MOUNT]},
        "HostConfig": {"CapDrop": ["ALL"], "CapAdd": None, "Privileged": False,
                       "ReadonlyRootfs": True, "SecurityOpt": ["no-new-privileges"]},
        "Mounts": [
            {"Type": "bind", "Source": os.path.join(os.path.dirname(sb.__file__), "share_web_proxy.py"),
             "Destination": sb.WEB_SCRIPT_MOUNT, "RW": False},
            {"Type": "bind", "Source": str(sb.web_log_path(SID)),
             "Destination": sb.WEB_LOG_MOUNT, "RW": True},
        ],
    }


def _verify(info, net, mode, web):
    return sb.verify_container(info, net, SID, WORKSPACE, UID, GID, mode=mode, web=web)


def test_good_none_and_web_sessions_verify():
    assert _verify(_guest("none"), _network("none"), "none", None) is None
    assert _verify(_guest("web"), _network("web"), "web", _web_sidecar()) is None


def _none_gains_web_sidecar_member(info, net, web):
    net["Containers"]["c"] = {"Name": sb.web_proxy_name(SID)}


def _none_gains_proxy_env(info, net, web):
    info["Config"]["Env"].append("HTTPS_PROXY=" + sb.WEB_URL)


def _none_gains_lowercase_all_proxy(info, net, web):
    info["Config"]["Env"].append("all_proxy=http://somewhere:1")


def _none_gains_foreign_member(info, net, web):
    net["Containers"]["z"] = {"Name": "some-other-container"}


def _none_labelled_web(info, net, web):
    info["Config"]["Labels"][sb.NET_LABEL] = "web"


@pytest.mark.parametrize("flip", [_none_gains_web_sidecar_member, _none_gains_proxy_env,
                                  _none_gains_lowercase_all_proxy, _none_gains_foreign_member,
                                  _none_labelled_web])
def test_a_none_session_that_gained_a_route_fails(flip):
    info, net = _guest("none"), _network("none")
    flip(info, net, None)
    assert _verify(info, net, "none", None)


def test_a_none_session_with_a_web_sidecar_running_fails():
    assert "web egress proxy" in _verify(_guest("none"), _network("none"), "none", _web_sidecar())


def _web_net_not_internal(info, net, web):
    net["Internal"] = False


def _web_net_host_address(info, net, web):
    net["Options"] = {}


def _web_guest_extra_network(info, net, web):
    info["NetworkSettings"]["Networks"]["bridge"] = {}


def _web_guest_no_proxy_env(info, net, web):
    info["Config"]["Env"] = ["HOME=/home/guest"]


def _web_guest_proxy_elsewhere(info, net, web):
    info["Config"]["Env"] = [e if not e.startswith("HTTPS_PROXY=") else "HTTPS_PROXY=http://evil:1"
                             for e in info["Config"]["Env"]]


def _web_sidecar_missing_member(info, net, web):
    del net["Containers"]["c"]


def _web_sidecar_privileged(info, net, web):
    web["HostConfig"]["Privileged"] = True


def _web_sidecar_docker_sock(info, net, web):
    web["Mounts"].append({"Type": "bind", "Source": "/var/run/docker.sock",
                          "Destination": "/var/run/docker.sock", "RW": True})


def _web_sidecar_other_cmd(info, net, web):
    web["Config"]["Cmd"] = ["socat", "TCP-LISTEN:3128,fork", "TCP:host.docker.internal:22"]


def _web_sidecar_writable_root(info, net, web):
    web["HostConfig"]["ReadonlyRootfs"] = False


def _web_sidecar_other_session(info, net, web):
    web["Config"]["Labels"][sb.LABEL] = "ffffffffffff"


@pytest.mark.parametrize("flip", [_web_net_not_internal, _web_net_host_address,
                                  _web_guest_extra_network, _web_guest_no_proxy_env,
                                  _web_guest_proxy_elsewhere, _web_sidecar_missing_member,
                                  _web_sidecar_privileged, _web_sidecar_docker_sock,
                                  _web_sidecar_other_cmd, _web_sidecar_writable_root,
                                  _web_sidecar_other_session])
def test_each_web_session_breakage_fails(flip):
    info, net, web = _guest("web"), _network("web"), _web_sidecar()
    assert _verify(info, net, "web", web) is None        # negative control
    flip(info, net, web)
    assert _verify(info, net, "web", web)


def test_a_web_session_without_its_sidecar_fails():
    assert _verify(_guest("web"), _network("web"), "web", None)


@pytest.fixture
def live(monkeypatch):
    state = {"argv": sb.launcher_argv(SID, WORKSPACE, "web"),
             "inspect": (_guest("web"), _network("web"), _web_sidecar())}
    monkeypatch.setattr(sb, "_pane_root", lambda wid: 4242)
    monkeypatch.setattr(sb, "_proc_shape", lambda pid: (state["argv"], "tmux: server", ["docker"]))
    monkeypatch.setattr(sb, "_inspect", lambda sid: state["inspect"])
    return state


def test_check_share_session_accepts_a_live_web_session(live):
    assert sb.check_share_session("@9") == (True, "")


def test_check_share_session_fails_when_launched_none_but_running_web(live):
    live["argv"] = sb.launcher_argv(SID, WORKSPACE, "none")
    ok, why = sb.check_share_session("@9")
    assert ok is False and "network mode" in why


def test_check_share_session_fails_when_launched_web_but_running_none(live):
    live["inspect"] = (_guest("none"), _network("none"), None)
    ok, why = sb.check_share_session("@9")
    assert ok is False and why


def test_window_net_mode_reads_the_launcher_argv(monkeypatch, tmp_path):
    cmd = tmp_path / "cmdline"
    cmd.write_bytes(b"\0".join(a.encode() for a in sb.launcher_argv(SID, WORKSPACE, "web")))
    monkeypatch.setattr(sb, "_pane_root", lambda wid: 4242)
    real_open = open
    monkeypatch.setattr("builtins.open", lambda p, *a, **k: real_open(cmd if p == "/proc/4242/cmdline" else p, *a, **k))
    assert sb.window_net_mode("@9") == "web"
    monkeypatch.setattr(sb, "_pane_root", lambda wid: "no such window")
    assert sb.window_net_mode("@9") is None


# =====================================================================================
# the two entry points
# =====================================================================================

def test_spawn_sandboxed_route_passes_web_through(monkeypatch, tmp_path):
    from chela import spawn
    from chela.dashboard import app as dash
    monkeypatch.setattr(dash, "_require_terminals", lambda: None)
    monkeypatch.setattr(dash.launcher, "record_recent", lambda p: None)
    seen = []
    monkeypatch.setattr(spawn, "spawn_sandbox_window",
                        lambda cwd, net="none": seen.append(net) or spawn.SpawnResult(
                            ok=True, name="sandbox-web-1", wid="@3", cwd=cwd))
    c = dash.app.test_client()
    assert c.post("/api/agents/spawn-sandboxed", json={"cwd": str(tmp_path)}).status_code == 200
    assert c.post("/api/agents/spawn-sandboxed", json={"cwd": str(tmp_path), "web": True}).status_code == 200
    assert c.post("/api/agents/spawn-sandboxed", json={"cwd": str(tmp_path), "web": "yes"}).status_code == 400
    assert seen == ["none", "web"]


def test_spawn_sandbox_window_launches_the_web_mode_launcher(monkeypatch, tmp_path):
    from chela import spawn
    calls, pre = [], []

    class P:
        returncode, stdout, stderr = 0, "@17\n", ""

    monkeypatch.setattr(sb, "preflight", lambda cwd, net="none": pre.append(net))
    monkeypatch.setattr(spawn.discovery, "ensure_session", lambda: True)
    monkeypatch.setattr(spawn.discovery, "get_all_windows", lambda: [])
    monkeypatch.setattr(spawn.agent_manager, "lock_window_name", lambda t: None)
    monkeypatch.setattr(spawn.subprocess, "run", lambda argv, **kw: calls.append(argv) or P())
    r = spawn.spawn_sandbox_window(str(tmp_path), net="web")
    assert r.ok and r.name == "sandbox-web-1" and pre == ["web"]
    cmd = calls[0][calls[0].index("--") + 1:]
    assert sb.verify_pane(cmd, "tmux: server", [])[2] == "web"


def test_cli_share_session_web_flag(monkeypatch, tmp_path):
    import argparse

    from chela import main as cli
    from chela import spawn
    seen = []
    monkeypatch.setattr(spawn, "spawn_sandbox_window",
                        lambda cwd, net="none": seen.append(net) or spawn.SpawnResult(
                            ok=True, name="sandbox-web-1", wid="@3", cwd=cwd))
    cli.cmd_share_session(argparse.Namespace(project=str(tmp_path), web=True))
    cli.cmd_share_session(argparse.Namespace(project=str(tmp_path), web=False))
    assert seen == ["web", "none"]
