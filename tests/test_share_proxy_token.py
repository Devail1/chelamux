"""🔑♻️ CMX-433: the share proxy sees the host's CURRENT token after a login refresh.

Claude Code refreshes its OAuth token by renaming a new ``.credentials.json`` over the old
one (a new inode), and the refresh revokes the old token. A single-FILE bind mount pins the
inode it started with, so the sidecar kept sending the revoked token forever. These tests
cover the three layers of the fix without docker or the network:

* the launcher's :class:`~chela.share_sandbox.TokenMirror` + the sidecar's mounts, read
  through a model of bind-mount semantics (a FILE mount pins its inode at start, a
  DIRECTORY mount resolves names at read time);
* :mod:`chela.share_proxy` over loopback against a fake upstream: per-request reads, one
  retry on 401 with a refreshed token, and a clear non-401 error when the 401 persists;
* the guest container still mounts no token and no ``.claude``.
"""
from __future__ import annotations

import http.client
import json
import os
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from chela import share_proxy as sp
from chela import share_sandbox as sb

UID, GID = os.getuid(), os.getgid()
SID = "0123456789ab"


def _opt(argv, flag):
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


def _replace(path: Path, text: str) -> None:
    """What Claude Code does on a refresh: write a new file, rename it over the old one."""
    tmp = path.with_name(path.name + ".new")
    tmp.write_text(text)
    os.replace(tmp, path)


def _creds(access: str, refresh: str = "refresh-secret") -> str:
    return json.dumps({"claudeAiOauth": {"accessToken": access, "refreshToken": refresh}})


# =====================================================================================
# the launcher side: the mirror + the sidecar's mounts
# =====================================================================================

class _Container:
    """Reads a path inside a container started with ``argv``'s ``-v`` mounts, the way a
    bind mount does: a FILE mount is opened at start and keeps that inode; a DIRECTORY
    mount resolves the remaining path on the host at read time."""

    def __init__(self, argv):
        self.mounts = []
        for spec in _opt(argv, "-v"):
            src, dst = spec.split(":")[:2]
            pinned = None if os.path.isdir(src) else open(src, "rb")  # noqa: SIM115
            self.mounts.append((src, dst, pinned))

    def read(self, path: str) -> str:
        for src, dst, pinned in self.mounts:
            if pinned is not None and path == dst:
                pinned.seek(0)
                return pinned.read().decode()
            if pinned is None and path.startswith(dst + "/"):
                return Path(src, path[len(dst) + 1:]).read_text()
        raise FileNotFoundError(path)

    def close(self):
        for _, _, f in self.mounts:
            if f is not None:
                f.close()


@pytest.fixture
def host(monkeypatch, tmp_path):
    creds = tmp_path / "claude" / ".credentials.json"
    creds.parent.mkdir()
    creds.write_text(_creds("tok-1"))
    monkeypatch.setenv("CHELA_SHARE_SANDBOX_TOKEN_FILE", str(creds))
    monkeypatch.setattr(sb, "token_mirror_dir", lambda sid: tmp_path / "share-token" / sid)
    return creds


def test_a_rename_over_replace_on_the_host_reaches_the_proxy_sidecar(host):
    mirror = sb.TokenMirror(sb.token_file(), sb.token_mirror_dir(SID))
    assert mirror.sync()
    argv = sb.proxy_run_argv(SID, UID, GID)
    token_path = dict(e.split("=", 1) for e in _opt(argv, "-e"))["CHELA_PROXY_TOKEN_FILE"]
    box = _Container(argv)
    try:
        assert box.read(token_path) == "tok-1"
        _replace(host, _creds("tok-2"))                       # the host refreshes its login
        mirror.sync()                                         # one poll of the launcher
        assert box.read(token_path) == "tok-2"
        _replace(host, _creds("tok-3"))
        mirror.sync()
        assert box.read(token_path) == "tok-3"
    finally:
        box.close()


def test_the_sidecar_mounts_only_the_mirror_dir_and_never_the_credentials(host):
    argv = sb.proxy_run_argv(SID, UID, GID)
    mounts = _opt(argv, "-v")
    assert f"{sb.token_mirror_dir(SID)}:{sb.PROXY_TOKEN_DIR}:ro" in mounts
    assert not [m for m in mounts if str(host) in m or ".claude" in m]


def test_the_mirror_holds_only_the_access_token_never_the_refresh_token(host):
    mirror = sb.TokenMirror(sb.token_file(), sb.token_mirror_dir(SID))
    assert mirror.sync()
    assert mirror.path.read_text() == "tok-1"
    assert oct(mirror.path.stat().st_mode & 0o777) == oct(0o600)
    assert "refresh-secret" not in "".join(p.read_text() for p in mirror.dir.iterdir())


def test_an_unchanged_source_is_not_rewritten(host):
    """⭐ The accepted case: nothing changed on the host, so the mirror is left alone."""
    mirror = sb.TokenMirror(sb.token_file(), sb.token_mirror_dir(SID))
    assert mirror.sync()
    ino = mirror.path.stat().st_ino
    for _ in range(3):
        assert mirror.sync()
    assert mirror.path.stat().st_ino == ino and mirror.path.read_text() == "tok-1"


def test_a_half_written_source_keeps_the_last_good_token(host):
    mirror = sb.TokenMirror(sb.token_file(), sb.token_mirror_dir(SID))
    assert mirror.sync()
    _replace(host, "{not json")
    assert mirror.sync() and mirror.path.read_text() == "tok-1"
    _replace(host, _creds("tok-2"))
    assert mirror.sync() and mirror.path.read_text() == "tok-2"


def test_the_poll_thread_picks_up_a_refresh_and_stop_removes_the_mirror(host):
    mirror = sb.TokenMirror(sb.token_file(), sb.token_mirror_dir(SID))
    assert mirror.sync()
    mirror.start(interval=0.01)
    try:
        _replace(host, _creds("tok-2"))
        for _ in range(500):
            if mirror.path.read_text() == "tok-2":
                break
            threading.Event().wait(0.01)
        assert mirror.path.read_text() == "tok-2"
    finally:
        mirror.stop()
    assert not mirror.dir.exists()


def _stub_run(monkeypatch):
    monkeypatch.setattr(sb, "preflight", lambda cwd, net="none": None)
    monkeypatch.setattr(sb, "claude_binary", lambda: "/usr/bin/true")
    monkeypatch.setattr(sb.signal, "signal", lambda *a: None)
    monkeypatch.setattr(sb, "_hold", lambda msg: None)
    monkeypatch.setattr(sb, "cleanup", lambda sid: None)
    ran, seen = [], {}

    class P:
        returncode, stdout, stderr = 0, "", ""

    def call(argv):
        seen["mirror"] = sb.token_mirror_dir(SID).joinpath(sb.TOKEN_NAME).read_text()
        return 0

    monkeypatch.setattr(sb.subprocess, "run", lambda argv, **k: ran.append(argv) or P())
    monkeypatch.setattr(sb.subprocess, "call", call)
    return ran, seen


def test_run_mirrors_the_token_before_the_proxy_starts_and_removes_it_after(monkeypatch, host):
    ran, seen = _stub_run(monkeypatch)
    assert sb.run(SID, "/tmp", "none") == 0
    assert seen["mirror"] == "tok-1"
    assert any(sb.proxy_name(SID) in a for a in ran)
    assert not sb.token_mirror_dir(SID).exists()


def test_run_refuses_before_any_docker_step_when_no_token_is_readable(monkeypatch, host):
    host.write_text("")
    ran, _ = _stub_run(monkeypatch)
    assert sb.run(SID, "/tmp", "none") == 1
    assert ran == []


# =====================================================================================
# the guest still gets nothing
# =====================================================================================

@pytest.mark.parametrize("mode", sb.NET_MODES)
def test_the_guest_mounts_no_token_and_no_claude_dir(host, mode):
    argv = sb.guest_run_argv(SID, "/tmp", UID, GID, "/usr/bin/true", mode)
    mounts = _opt(argv, "-v")
    for m in mounts:
        assert str(sb.token_mirror_dir(SID)) not in m and "share-token" not in m
        assert str(host) not in m and str(host.parent) not in m
        assert ".claude:" not in m and sb.PROXY_TOKEN_DIR not in m
    envs = dict(e.split("=", 1) for e in _opt(argv, "-e"))
    assert envs["ANTHROPIC_AUTH_TOKEN"] == "placeholder"
    assert "CHELA_PROXY_TOKEN_FILE" not in envs


# =====================================================================================
# the proxy: per-request reads, one retry on 401, a clear error when it persists
# =====================================================================================

class _Upstream:
    """A fake Anthropic: records each Authorization header and answers via ``policy``."""

    def __init__(self, policy):
        self.seen: list[str] = []
        up = self

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_POST(self):
                self.rfile.read(int(self.headers.get("content-length") or 0))
                tok = self.headers.get("Authorization", "").removeprefix("Bearer ")
                up.seen.append(tok)
                status = policy(tok)
                body = json.dumps({"ok": status == 200, "token": tok}).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"


@pytest.fixture
def wire(tmp_path):
    """``start(policy)`` → ``(upstream, post)``; the proxy reads ``tmp_path/token.d/token``."""
    servers = []
    tok = tmp_path / "token.d" / "token"
    tok.parent.mkdir()
    tok.write_text("old")

    def start(policy):
        up = _Upstream(policy)

        class H(sp._Handler):
            token_file = str(tok)
            upstream = urllib.parse.urlsplit(up.url)

            def log_message(self, *a):
                pass

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=proxy.serve_forever, daemon=True).start()
        servers.extend([up.srv, proxy])

        def post():
            c = http.client.HTTPConnection("127.0.0.1", proxy.server_address[1], timeout=10)
            c.request("POST", "/v1/messages", body=b'{"x":1}',
                      headers={"Authorization": "Bearer placeholder",
                               "content-type": "application/json"})
            r = c.getresponse()
            out = (r.status, json.loads(r.read() or b"{}"))
            c.close()
            return out

        return up, post

    start.token = tok
    yield start
    for s in servers:
        s.shutdown()
        s.server_close()


def test_the_proxy_uses_the_new_token_on_the_next_request_after_a_rename(wire):
    up, post = wire(lambda tok: 200)
    assert post()[0] == 200
    _replace(wire.token, "new")
    assert post()[0] == 200
    assert up.seen == ["old", "new"]


def test_an_unchanged_token_is_read_once_per_request_and_never_retried(wire, monkeypatch):
    """⭐ The accepted case: a working token costs exactly one read per request."""
    reads = []
    real = sp.read_token
    monkeypatch.setattr(sp, "read_token", lambda p: reads.append(p) or real(p))
    up, post = wire(lambda tok: 200)
    for _ in range(3):
        assert post() == (200, {"ok": True, "token": "old"})
    assert up.seen == ["old"] * 3
    assert len(reads) == 3


def test_a_401_from_a_token_refreshed_mid_request_is_retried_once_with_the_new_one(wire):
    def policy(tok):
        if tok == "old":
            _replace(wire.token, "new")     # the host refreshed; "old" is now revoked
            return 401
        return 200

    up, post = wire(policy)
    assert post() == (200, {"ok": True, "token": "new"})
    assert up.seen == ["old", "new"]


def test_a_persistent_401_is_a_clear_non_401_error_and_is_not_retried(wire):
    up, post = wire(lambda tok: 401)
    status, body = post()
    assert status == 502                                  # never a 401: no /login in the guest
    assert body["error"]["message"] == sp.LOGIN_EXPIRED
    assert "operator" in sp.LOGIN_EXPIRED and "login expired" in sp.LOGIN_EXPIRED
    assert up.seen == ["old"]                             # unchanged token ⇒ no retry


def test_a_refreshed_token_that_is_also_rejected_ends_in_the_same_clear_error(wire):
    def policy(tok):
        if tok == "old":
            _replace(wire.token, "also-bad")
        return 401

    up, post = wire(policy)
    status, body = post()
    assert (status, body["error"]["message"]) == (502, sp.LOGIN_EXPIRED)
    assert up.seen == ["old", "also-bad"]                 # exactly ONE retry
