"""💳 CMX-435: a sandboxed session's Claude says it runs on the operator's SUBSCRIPTION.

The guest's Claude used to get a placeholder ``ANTHROPIC_AUTH_TOKEN`` and so labelled
itself "API Usage Billing", though :mod:`chela.share_proxy` always sends the operator's
subscription OAuth token. It now gets a placeholder Claude.ai login (its own
``.credentials.json``) carrying only the subscription TYPE, so the banner reads
"Claude Max" / "Claude Pro". What must not change:

* the real token never reaches the guest — not in its env, not in the credentials file
  its entry command writes, not anywhere in its ``docker run`` argv;
* the proxy still strips every guest-supplied auth header and injects the operator's;
* a real model call through the proxy still succeeds (real sockets on loopback, a stub
  upstream — no docker, no network).
"""
from __future__ import annotations

import http.client
import json
import os
import shlex
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from chela import share_proxy as sp
from chela import share_sandbox as sb

UID, GID = os.getuid(), os.getgid()
SID = "0123456789ab"
WORKSPACE = "/tmp"
REAL_TOKEN = "sk-ant-oat01-REAL-OPERATOR-TOKEN-must-never-leak"
REAL_REFRESH = "sk-ant-ort01-REAL-REFRESH-must-never-leak"


def _creds(tmp_path, **extra) -> str:
    oauth = {"accessToken": REAL_TOKEN, "refreshToken": REAL_REFRESH,
             "expiresAt": 1, "scopes": ["user:inference"], **extra}
    p = tmp_path / ".credentials.json"
    p.write_text(json.dumps({"claudeAiOauth": oauth}))
    return str(p)


@pytest.fixture
def token_file(monkeypatch, tmp_path):
    monkeypatch.delenv("CHELA_SHARE_SANDBOX_SUBSCRIPTION", raising=False)

    def use(path: str) -> str:
        monkeypatch.setenv("CHELA_SHARE_SANDBOX_TOKEN_FILE", path)
        return path
    return use


def _envs(argv: list[str]) -> dict[str, str]:
    return dict(argv[i + 1].split("=", 1) for i, a in enumerate(argv) if a == "-e")


def _entry(argv: list[str]) -> str:
    assert argv[-3:-1] == ["sh", "-c"]
    return argv[-1]


def _written_credentials(entry: str) -> dict:
    """The JSON the entry command writes to the guest's ``.credentials.json``."""
    words = shlex.split(entry)
    i = words.index(f"{sb.GUEST_HOME}/.claude/.credentials.json")
    assert words[i - 1] == ">"
    return json.loads(words[i - 2])


# =====================================================================================
# the guest: a subscription login, never the real token
# =====================================================================================

@pytest.mark.parametrize("mode", ["none", "web"])
def test_a_known_subscription_gets_a_placeholder_login_and_no_auth_env(token_file, tmp_path, mode):
    token_file(_creds(tmp_path, subscriptionType="max"))
    argv = sb.guest_run_argv(SID, WORKSPACE, UID, GID, "/usr/bin/true", mode)
    envs = _envs(argv)
    for k in ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"):
        assert k not in envs, k
    assert envs["ANTHROPIC_BASE_URL"] == f"http://{sb.PROXY_ALIAS}:{sb.PROXY_PORT}"
    oauth = _written_credentials(_entry(argv))["claudeAiOauth"]
    assert oauth == {"accessToken": "placeholder", "refreshToken": None,
                     "expiresAt": oauth["expiresAt"], "scopes": ["user:inference"],
                     "subscriptionType": "max"}
    assert oauth["expiresAt"] > 4_000_000_000_000      # far future: Claude never refreshes
    assert _entry(argv).endswith("&& exec claude")


@pytest.mark.parametrize("sub", ["max", "pro"])
def test_the_real_token_appears_nowhere_in_the_guest_argv(token_file, tmp_path, sub):
    token_file(_creds(tmp_path, subscriptionType=sub))
    for mode in ("none", "web"):
        argv = sb.guest_run_argv(SID, WORKSPACE, UID, GID, "/usr/bin/true", mode)
        blob = "\0".join(argv)
        assert REAL_TOKEN not in blob and REAL_REFRESH not in blob
        assert "sk-ant" not in blob
        assert not [m for m in argv if str(tmp_path) in m]   # the token file isn't mounted


def test_the_subscription_type_comes_from_the_token_file(token_file, tmp_path):
    token_file(_creds(tmp_path, subscriptionType="pro"))
    assert sb.subscription_type() == "pro"


def test_the_env_knob_overrides_the_token_file(token_file, tmp_path, monkeypatch):
    token_file(_creds(tmp_path, subscriptionType="pro"))
    monkeypatch.setenv("CHELA_SHARE_SANDBOX_SUBSCRIPTION", " Max ")
    assert sb.subscription_type() == "max"
    argv = sb.guest_run_argv(SID, WORKSPACE, UID, GID, "/usr/bin/true", "none")
    assert _written_credentials(_entry(argv))["claudeAiOauth"]["subscriptionType"] == "max"


@pytest.mark.parametrize("content", [
    REAL_TOKEN,                                                    # a bare setup-token file
    json.dumps({"claudeAiOauth": {"accessToken": REAL_TOKEN}}),    # no subscription type
    json.dumps({"claudeAiOauth": {"accessToken": REAL_TOKEN, "subscriptionType": "max'; id #"}}),
    json.dumps(["not", "an", "object"]),
    "",
])
def test_an_unknown_subscription_keeps_the_placeholder_auth_env(token_file, tmp_path, content):
    p = tmp_path / "token"
    p.write_text(content)
    token_file(str(p))
    assert sb.subscription_type() is None
    argv = sb.guest_run_argv(SID, WORKSPACE, UID, GID, "/usr/bin/true", "none")
    assert _envs(argv)["ANTHROPIC_AUTH_TOKEN"] == "placeholder"
    assert ".credentials.json" not in _entry(argv)
    assert REAL_TOKEN not in "\0".join(argv)


def test_a_missing_token_file_is_an_unknown_subscription(token_file, tmp_path):
    token_file(str(tmp_path / "absent"))
    assert sb.subscription_type() is None


def test_the_entry_still_seeds_onboarding_and_execs_claude(token_file, tmp_path):
    token_file(_creds(tmp_path, subscriptionType="max"))
    words = shlex.split(sb.guest_entry("max"))
    seed = json.loads(words[words.index(f"{sb.GUEST_HOME}/.claude.json") - 2])
    assert seed["hasCompletedOnboarding"] is True
    assert seed["projects"][sb.GUEST_WORKDIR]["hasTrustDialogAccepted"] is True
    assert words[-2:] == ["exec", "claude"]


# =====================================================================================
# the proxy: strips the guest's auth, injects the operator's
# =====================================================================================

def test_upstream_headers_drop_every_guest_auth_header_and_add_the_operators():
    out = sp.upstream_headers([
        ("Authorization", "Bearer placeholder"), ("x-api-key", "guest-key"),
        ("Proxy-Authorization", "Basic Z3Vlc3Q="), ("Cookie", "s=1"),
        ("anthropic-beta", "claude-code-20250219, oauth-2025-04-20"),
        ("content-type", "application/json"),
    ], REAL_TOKEN)
    low = {k.lower(): v for k, v in out.items()}
    assert low["authorization"] == f"Bearer {REAL_TOKEN}"
    for k in ("x-api-key", "proxy-authorization", "cookie"):
        assert k not in low, k
    assert low["anthropic-beta"].split(",") == ["claude-code-20250219", sp.OAUTH_BETA]
    assert low["content-type"] == "application/json"


@pytest.mark.parametrize("name", ["authorization", "AUTHORIZATION", "x-api-key", "X-Api-Key"])
def test_a_guest_auth_header_in_any_case_never_reaches_upstream(name):
    out = sp.upstream_headers([(name, "Bearer placeholder")], REAL_TOKEN)
    assert "Bearer placeholder" not in out.values()
    assert [k for k in out if k.lower() in ("authorization", "x-api-key")] == ["Authorization"]
    assert out["Authorization"] == f"Bearer {REAL_TOKEN}"


def test_upstream_headers_add_the_oauth_beta_when_the_guest_sent_none():
    out = sp.upstream_headers([("anthropic-beta", "claude-code-20250219")], REAL_TOKEN)
    assert out["anthropic-beta"] == f"claude-code-20250219,{sp.OAUTH_BETA}"


# =====================================================================================
# ⭐ the ACCEPT case: a model call through the real proxy, to a stub upstream
# =====================================================================================

class _Upstream(BaseHTTPRequestHandler):
    seen: list[dict] = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        body = self.rfile.read(n)
        type(self).seen.append({"path": self.path, "headers": dict(self.headers.items()),
                                "body": json.loads(body)})
        out = json.dumps({"id": "msg_stub", "type": "message", "role": "assistant",
                          "content": [{"type": "text", "text": "PONG"}],
                          "stop_reason": "end_turn"}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(out)))
        self.end_headers()
        self.wfile.write(out)


def _serve(handler) -> tuple[ThreadingHTTPServer, int]:
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


@pytest.fixture
def proxy(tmp_path, monkeypatch):
    _Upstream.seen = []
    up, up_port = _serve(_Upstream)
    tok = tmp_path / ".credentials.json"
    tok.write_text(json.dumps({"claudeAiOauth": {"accessToken": REAL_TOKEN}}))

    class Handler(sp._Handler):
        token_file = str(tok)
        upstream = sp.urllib.parse.urlsplit(f"http://127.0.0.1:{up_port}")
    px, px_port = _serve(Handler)
    yield px_port
    px.shutdown()
    up.shutdown()


@pytest.mark.parametrize("guest_auth", [
    {"Authorization": "Bearer placeholder"},                        # the placeholder login
    {"Authorization": "Bearer placeholder", "x-api-key": "guest"},  # plus a stray api key
])
def test_a_model_call_through_the_proxy_succeeds_with_the_operators_token(proxy, guest_auth):
    conn = http.client.HTTPConnection("127.0.0.1", proxy, timeout=10)
    body = json.dumps({"model": "claude-x", "max_tokens": 8,
                       "messages": [{"role": "user", "content": "say PONG"}]})
    conn.request("POST", "/v1/messages?beta=true", body=body,
                 headers={"content-type": "application/json",
                          "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
                          **guest_auth})
    resp = conn.getresponse()
    reply = json.loads(resp.read())
    assert resp.status == 200 and reply["content"][0]["text"] == "PONG"
    assert REAL_TOKEN not in json.dumps(dict(resp.getheaders())) + json.dumps(reply)
    [seen] = _Upstream.seen
    hdrs = {k.lower(): v for k, v in seen["headers"].items()}
    assert seen["path"] == "/v1/messages?beta=true"
    assert seen["body"]["messages"][0]["content"] == "say PONG"
    assert hdrs["authorization"] == f"Bearer {REAL_TOKEN}"
    assert "x-api-key" not in hdrs
    assert hdrs["anthropic-beta"].split(",").count(sp.OAUTH_BETA) == 1


def test_a_non_v1_path_never_reaches_the_upstream(proxy):
    conn = http.client.HTTPConnection("127.0.0.1", proxy, timeout=10)
    conn.request("POST", "/api/oauth/profile", body=b"{}",
                 headers={"Authorization": "Bearer placeholder"})
    assert conn.getresponse().status == 403
    assert _Upstream.seen == []
