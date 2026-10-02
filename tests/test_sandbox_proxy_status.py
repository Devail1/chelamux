"""🚦🧱 CMX-436 — a sandboxed window's working/idle, derived from its share proxy's traffic.

A sandboxed session's Claude runs in a container: it is not in ``claude agents --json``,
its hooks cannot reach the host and its transcript is in the container's tmpfs, so its
status pill read "unknown" forever. Its credential proxy sees every model request and
records them in the session's activity file. These tests cover each link: the two
transitions (in flight ⇒ busy; completion + grace ⇒ idle), the proxy's forward path
actually driving them, the file staying OFF the guest container, the resolver used by
``peek`` and ``/api/agents`` — and the case that must keep working: an ordinary window's
status is untouched.

No docker or network: the upstream connection is a fake object and the sandbox check is
stubbed.
"""
from __future__ import annotations

import io
import json
import os
from contextlib import contextmanager
from email.message import Message as HeaderBlock
from unittest.mock import patch

import pytest

from chela import agent_manager, discovery, orchestrator, sandbox_status, sessions, share_proxy, share_sandbox

SID = "0123456789ab"
UID, GID = os.getuid(), os.getgid()
TOOLS = [{"name": "Bash"}]
GRACE = share_proxy.IDLE_GRACE_S


@pytest.fixture(autouse=True)
def _fresh_verdicts():
    sandbox_status._cache.clear()
    yield
    sandbox_status._cache.clear()


# --- 1. the two transitions -------------------------------------------------------------

def test_a_turn_in_flight_is_busy():
    assert share_proxy.activity_status({"inflight": 1, "last_done": None, "updated": 100.0},
                                       now=101.0) == "busy"


def test_completion_reads_busy_within_the_grace_then_idle_after_it():
    state = {"inflight": 0, "last_done": 100.0, "updated": 100.0}
    assert share_proxy.activity_status(state, now=100.0 + GRACE / 2) == "busy"   # tool gap
    assert share_proxy.activity_status(state, now=100.0 + GRACE + 0.5) == "idle"
    assert share_proxy.activity_status(state, now=100.0 + GRACE) == "idle"     # the grace is exclusive


def test_a_fresh_proxy_with_nothing_yet_is_idle():
    assert share_proxy.activity_status({"inflight": 0, "last_done": None, "updated": 1.0},
                                       now=2.0) == "idle"


def test_in_flight_on_a_proxy_that_stopped_writing_is_not_claimed_as_busy():
    state = {"inflight": 1, "last_done": None, "updated": 100.0}
    assert share_proxy.activity_status(state, now=100.0 + share_proxy.ACTIVITY_STALE_S + 1) is None


@pytest.mark.parametrize("state", [
    None, [], "busy", {"inflight": "x", "updated": 1.0},
    {"inflight": 1, "last_done": None, "updated": None},   # in flight, never stamped: unknown
])
def test_an_unreadable_activity_claims_nothing(state):
    assert share_proxy.activity_status(state, now=2.0) is None


def test_activity_file_round_trip(tmp_path):
    a = share_proxy.Activity(str(tmp_path))
    a.open()
    read = lambda: json.loads((tmp_path / share_proxy.ACTIVITY_NAME).read_text())  # noqa: E731
    assert read()["inflight"] == 0
    a.begin()
    assert read()["inflight"] == 1
    assert share_proxy.activity_status(read()) == "busy"
    a.end()
    st = read()
    assert st["inflight"] == 0 and st["last_done"] is not None
    assert share_proxy.activity_status(st, now=st["last_done"] + GRACE + 1) == "idle"


def test_an_unmatched_end_never_drives_inflight_negative(tmp_path):
    # A negative count would make the NEXT turn's begin() read 0 — idle mid-answer.
    a = share_proxy.Activity(str(tmp_path))
    a.open()
    a.end()
    a.begin()
    st = json.loads((tmp_path / share_proxy.ACTIVITY_NAME).read_text())
    assert st["inflight"] == 1
    assert share_proxy.activity_status(st) == "busy"


# --- 2. the proxy's forward path drives them -------------------------------------------

TURN = (b'event: message_stop\ndata: {"type": "message_stop"}\n\n')


def _forward(tmp_path, monkeypatch, body: dict, path="/v1/messages"):
    """Forward one request through ``_Handler._forward`` with a fake upstream that
    snapshots the activity file WHILE the response is in flight."""
    (tmp_path / "token").write_text("tok")
    sdir = tmp_path / "session"
    sdir.mkdir(exist_ok=True)
    seen: dict = {}

    class Resp:
        status = 200

        def __init__(self):
            self._chunks = [TURN]

        def getheader(self, name, default=None):
            return "text/event-stream" if name.lower() == "content-type" else default

        def getheaders(self):
            return [("content-type", "text/event-stream")]

        def read1(self, n):
            if "during" not in seen:
                seen["during"] = json.loads((sdir / share_proxy.ACTIVITY_NAME).read_text())
            return self._chunks.pop(0) if self._chunks else b""

    class Conn:
        def __init__(self, *a, **kw):
            pass

        def request(self, *a, **kw):
            pass

        def getresponse(self):
            return Resp()

        def close(self):
            pass

    monkeypatch.setattr(share_proxy.http.client, "HTTPSConnection", Conn)
    raw = json.dumps(body).encode()
    h = share_proxy._Handler.__new__(share_proxy._Handler)
    h.command, h.path, h.request_version = "POST", path, "HTTP/1.1"
    h.headers = HeaderBlock()
    h.headers["content-length"] = str(len(raw))
    h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
    h.send_response = lambda *a, **kw: None
    h.send_header = lambda *a, **kw: None
    h.end_headers = lambda: None
    h.token_file = str(tmp_path / "token")
    h.upstream = share_proxy.urllib.parse.urlsplit("https://upstream.invalid")
    h.outbox = None
    h.activity = share_proxy.Activity(str(sdir))
    h.activity.open()
    h._forward()
    assert h.wfile.getvalue() == TURN                     # the guest is served regardless
    seen["after"] = json.loads((sdir / share_proxy.ACTIVITY_NAME).read_text())
    return seen


def test_a_forwarded_turn_is_busy_while_in_flight_and_idle_after_the_grace(tmp_path, monkeypatch):
    seen = _forward(tmp_path, monkeypatch, {"stream": True, "tools": TOOLS})
    assert seen["during"]["inflight"] == 1
    assert share_proxy.activity_status(seen["during"]) == "busy"
    after = seen["after"]
    assert after["inflight"] == 0 and after["last_done"] is not None
    assert share_proxy.activity_status(after, now=after["last_done"] + GRACE + 1) == "idle"


@pytest.mark.parametrize("label, body, path", [
    ("side call: no tools (a title / summary)", {"stream": True}, "/v1/messages"),
    ("count_tokens", {"stream": True, "tools": TOOLS}, "/v1/messages/count_tokens"),
])
def test_side_calls_never_mark_the_session_working(tmp_path, monkeypatch, label, body, path):
    seen = _forward(tmp_path, monkeypatch, body, path)
    assert seen["during"]["inflight"] == 0, label
    assert seen["after"]["last_done"] is None, label


def test_a_failing_activity_write_never_breaks_the_guests_response(tmp_path, monkeypatch):
    class Broken(share_proxy.Activity):
        def begin(self):
            raise RuntimeError("disk on fire")

        def end(self):
            raise RuntimeError("disk on fire")

    monkeypatch.setattr(share_proxy, "Activity", Broken)
    _forward(tmp_path, monkeypatch, {"stream": True, "tools": TOOLS})   # asserts the guest is served


def test_an_upstream_failure_still_ends_the_turn(tmp_path, monkeypatch):
    (tmp_path / "token").write_text("tok")
    sdir = tmp_path / "session"
    sdir.mkdir()

    class Conn:
        def __init__(self, *a, **kw):
            pass

        def request(self, *a, **kw):
            raise ConnectionRefusedError

        def close(self):
            pass

    monkeypatch.setattr(share_proxy.http.client, "HTTPSConnection", Conn)
    raw = json.dumps({"stream": True, "tools": TOOLS}).encode()
    h = share_proxy._Handler.__new__(share_proxy._Handler)
    h.command, h.path, h.request_version = "POST", "/v1/messages", "HTTP/1.1"
    h.headers = HeaderBlock()
    h.headers["content-length"] = str(len(raw))
    h.rfile, h.wfile = io.BytesIO(raw), io.BytesIO()
    h.send_response = lambda *a, **kw: None
    h.send_header = lambda *a, **kw: None
    h.end_headers = lambda: None
    h.token_file = str(tmp_path / "token")
    h.upstream = share_proxy.urllib.parse.urlsplit("https://upstream.invalid")
    h.outbox = None
    h.activity = share_proxy.Activity(str(sdir))
    h.activity.open()
    h._forward()
    assert json.loads((sdir / share_proxy.ACTIVITY_NAME).read_text())["inflight"] == 0


class _FakeServer:
    served: list = []

    def __init__(self, addr, handler):
        pass

    def serve_forever(self):
        _FakeServer.served.append(1)


def test_the_proxy_opens_its_activity_file_only_with_a_session_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(share_proxy, "ThreadingHTTPServer", _FakeServer)
    for attr in ("outbox", "activity", "token_file", "upstream"):
        monkeypatch.setattr(share_proxy._Handler, attr, getattr(share_proxy._Handler, attr))
    monkeypatch.setenv("CHELA_PROXY_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CHELA_PROXY_SESSION_DIR", raising=False)
    share_proxy._Handler.activity = None
    share_proxy.main()
    assert share_proxy._Handler.activity is None
    assert not list(tmp_path.rglob(share_proxy.ACTIVITY_NAME))
    d = tmp_path / "session"
    d.mkdir()
    monkeypatch.setenv("CHELA_PROXY_SESSION_DIR", str(d))
    share_proxy.main()
    act = share_proxy._Handler.activity
    assert isinstance(act, share_proxy.Activity) and act.path == str(d / share_proxy.ACTIVITY_NAME)
    assert json.loads((d / share_proxy.ACTIVITY_NAME).read_text())["inflight"] == 0
    # An unwritable session dir: no activity at all (status unknown), never a dead writer.
    monkeypatch.setenv("CHELA_PROXY_SESSION_DIR", str(tmp_path / "missing"))
    share_proxy.main()
    assert share_proxy._Handler.activity is None


# --- 3. the status file is never the guest's ------------------------------------------

def test_the_activity_file_is_in_the_proxy_only_session_dir_and_off_the_guest(monkeypatch, tmp_path):
    monkeypatch.setattr(share_sandbox, "token_file", lambda: tmp_path / "tok")
    apath = share_sandbox.activity_path(SID)
    assert apath.parent == share_sandbox.session_dir(SID)
    proxy = share_sandbox.proxy_run_argv(SID, UID, GID)
    assert f"{apath.parent}:{share_sandbox.PROXY_SESSION_MOUNT}" in proxy   # the proxy writes it
    guest = " ".join(share_sandbox.guest_run_argv(SID, str(tmp_path), UID, GID, "/usr/bin/true"))
    assert str(apath) not in guest
    assert str(apath.parent) not in guest
    assert share_proxy.ACTIVITY_NAME not in guest


# --- 4. the resolver (peek + /api/agents) ---------------------------------------------

def _activity(state: dict) -> None:
    d = share_sandbox.session_dir(SID)
    d.mkdir(parents=True, exist_ok=True)
    (d / share_proxy.ACTIVITY_NAME).write_text(json.dumps(state))


def test_a_sandboxed_window_takes_its_status_from_the_proxy():
    import time
    _activity({"inflight": 1, "last_done": None, "updated": time.time()})
    assert sandbox_status.resolve("@5", None, check=lambda w: SID) == ("busy", sandbox_status.PROXY_SOURCE)
    _activity({"inflight": 0, "last_done": time.time() - GRACE - 5, "updated": time.time()})
    assert sandbox_status.resolve("@5", None, check=lambda w: SID) == ("idle", sandbox_status.PROXY_SOURCE)


def test_a_sandboxed_window_with_no_activity_file_stays_unresolved():
    assert sandbox_status.resolve("@5", None, check=lambda w: SID) == (None, None)


def test_a_corrupt_activity_file_claims_nothing():
    d = share_sandbox.session_dir(SID)
    d.mkdir(parents=True, exist_ok=True)
    (d / share_proxy.ACTIVITY_NAME).write_text("{not json")
    assert share_sandbox.proxy_activity_status(SID) is None
    assert sandbox_status.resolve("@5", None, check=lambda w: SID) == (None, None)


def test_proxy_activity_status_reads_at_the_given_time():
    _activity({"inflight": 0, "last_done": 100.0, "updated": 100.0})
    assert share_sandbox.proxy_activity_status(SID, now=100.0 + GRACE / 2) == "busy"
    assert share_sandbox.proxy_activity_status(SID, now=100.0 + GRACE + 1) == "idle"


def test_an_ordinary_window_is_unaffected():
    """⭐ The case that must be ACCEPTED: a non-sandboxed window keeps exactly what Claude
    reported — or nothing — even while some sandboxed session's proxy says busy."""
    import time
    _activity({"inflight": 1, "last_done": None, "updated": time.time()})
    for native in ("busy", "idle", "waiting"):
        assert sandbox_status.resolve("@2", native, check=lambda w: None) == (native, None)
    assert sandbox_status.resolve("@2", None, check=lambda w: None) == (None, None)


def test_claudes_own_status_wins_and_skips_the_sandbox_check():
    calls = []
    assert sandbox_status.resolve("@5", "idle", check=lambda w: calls.append(w) or SID) == ("idle", None)
    assert calls == []


def test_the_sandbox_verdict_is_cached_then_rechecked():
    calls, t = [], [0.0]
    check = lambda w: calls.append(w) or None  # noqa: E731
    clock = lambda: t[0]  # noqa: E731
    sandbox_status.resolve("@5", None, check=check, clock=clock)
    sandbox_status.resolve("@5", None, check=check, clock=clock)
    assert calls == ["@5"]
    t[0] = sandbox_status.CHECK_TTL_S + 1
    sandbox_status.resolve("@5", None, check=check, clock=clock)
    assert calls == ["@5", "@5"]


@contextmanager
def _peek_env(monkeypatch, sandboxed: set[str]):
    monkeypatch.setattr(discovery, "get_windows_by_id", lambda: {"@5": "sandbox-aaron", "@2": "worker"})
    monkeypatch.setattr(discovery, "get_window_cwd_by_id", lambda wid: "/proj")
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid, base=None: None)
    monkeypatch.setattr(agent_manager, "session_status_map",
                        lambda force=False: {"by_pid": {}, "cwd_by_pid": {}, "by_cwd": {}})
    monkeypatch.setattr(agent_manager, "claude_pid", lambda wid: 123)
    monkeypatch.setattr(agent_manager, "window_type", lambda wid, running=None: "claude")
    monkeypatch.setattr(share_sandbox, "share_session_id", lambda wid: SID if wid in sandboxed else None)
    yield


def test_peek_reads_a_sandboxed_windows_status_from_its_proxy(monkeypatch):
    import time
    _activity({"inflight": 1, "last_done": None, "updated": time.time()})
    with _peek_env(monkeypatch, {"@5"}):
        p = orchestrator.peek("@5")
        q = orchestrator.peek("@2")
    assert p["session_status"] == "busy" and p["status_source"] == sandbox_status.PROXY_SOURCE
    assert "from the sandbox proxy" in orchestrator.format_peek(p)
    assert q["session_status"] is None and q["status_source"] is None   # ordinary: unchanged
    assert "sandbox proxy" not in orchestrator.format_peek(q)
    # ⭐ An ordinary window Claude itself reports as busy: no proxy label.
    assert "sandbox proxy" not in orchestrator.format_peek({**q, "session_status": "busy"})


def test_api_agents_reads_a_sandboxed_windows_status_from_its_proxy():
    import time

    from chela.dashboard import app as dash
    _activity({"inflight": 1, "last_done": None, "updated": time.time()})
    live = {"sandbox-aaron": "@5", "worker": "@2", "other": "@3"}
    pids = {"@5": 1000, "@2": 1001, "@3": 1002}
    with (
        patch("chela.discovery.get_all_windows", return_value=dict(live)),
        patch("chela.dispatcher.list_runs", return_value=[]),
        patch("chela.agent_manager.session_status_map",
              return_value={"by_pid": {1001: "idle"}, "cwd_by_pid": {}}),
        patch("chela.agent_manager.claude_pid", side_effect=lambda wid: pids.get(wid)),
        patch("chela.agent_manager.window_type", return_value="claude"),
        patch("chela.scheduler.list_tasks", return_value=[]),
        patch("chela.transcripts.agent_transcript_summary",
              return_value={"recap": None, "recap_ts": None, "pr": None, "ai_title": None}),
        patch("chela.messenger.capture_pane", return_value=""),
        patch("chela.inbox.is_done", return_value=False),
        patch("chela.share_sandbox.share_session_id", side_effect=lambda w: SID if w == "@5" else None),
        patch("chela.share_sandbox.window_net_mode", return_value=None),
    ):
        rows = {a["window_id"]: a for a in dash.app.test_client().get("/api/agents").get_json()}
    assert rows["@5"]["session_status"] == "busy"
    assert rows["@5"]["status_source"] == sandbox_status.PROXY_SOURCE
    assert rows["@5"]["thinking"] is True
    # Ordinary windows: exactly what Claude reported (or nothing), no source.
    assert rows["@2"]["session_status"] == "idle" and rows["@2"]["status_source"] is None
    assert rows["@3"]["session_status"] is None and rows["@3"]["status_source"] is None
