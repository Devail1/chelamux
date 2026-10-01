"""📲🧱 CMX-420 — Telegram outbound for SANDBOXED sessions, via the share proxy's outbox.

A sandboxed session's transcript is inside its container, so the relay reads the outbox
the credential proxy writes instead. These tests cover each link of that chain: the SSE
parse → exactly one outbox record per turn, the proxy's forward path actually feeding it,
the outbox directory staying OFF the guest container, the relay posting an outbox entry
once, the doctor's verdict, and — the case that must keep working — an ordinary window
relaying from its transcript exactly as before.

No docker, network or Telegram: the upstream connection is a fake object, the sandbox
check is stubbed, and the Telegram sender is a list.
"""
from __future__ import annotations

import io
import json
import os
from email.message import Message as HeaderBlock
from pathlib import Path

import pytest

from chela import config, runtime_truth, sessions, share_proxy, share_sandbox
from chela.telegram.bindings import BindingRegistry
from chela.telegram.monitor import TranscriptMonitor
from chela.telegram.relay import RegistryRelay
from chela.telegram.sandboxrelay import SandboxOutboxes

SID = "0123456789ab"
UID, GID = os.getuid(), os.getgid()


def _sse(*events: dict) -> bytes:
    return b"".join(
        f"event: {e['type']}\ndata: {json.dumps(e)}\n\n".encode() for e in events)


# One realistic streamed turn: thinking (dropped), text in three deltas, a tool call with
# its input streamed as JSON (reduced to its name), a ping, then the stop.
TURN = _sse(
    {"type": "message_start", "message": {"id": "msg_1", "role": "assistant", "content": []}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "ping"},
    {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "Your CV "}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "looks "}},
    {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "great."}},
    {"type": "content_block_stop", "index": 1},
    {"type": "content_block_start", "index": 2,
     "content_block": {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}}},
    {"type": "content_block_delta", "index": 2,
     "delta": {"type": "input_json_delta", "partial_json": "{\"command\": \"cat secret\"}"}},
    {"type": "content_block_stop", "index": 2},
    {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
    {"type": "message_stop"},
)


def _chunks(data: bytes, n: int = 7):
    """Split mid-frame, mid-line and mid-UTF-8 — the way a socket actually delivers."""
    return [data[i:i + n] for i in range(0, len(data), n)]


def _outbox_lines(d: Path) -> list[dict]:
    return [json.loads(x) for x in (d / share_proxy.OUTBOX_NAME).read_text().splitlines() if x]


# --- 1. the SSE parse → exactly one outbox entry --------------------------------------

def test_a_streamed_turn_becomes_exactly_one_outbox_entry(tmp_path):
    box = share_proxy.Outbox(str(tmp_path))
    box.open()
    c = share_proxy.TurnCollector()
    for chunk in _chunks(TURN.replace(b"\n", b"\r\n")):   # CRLF framing too
        c.feed(chunk)
    box.turn(c)
    lines = _outbox_lines(tmp_path)
    assert len(lines) == 1
    rec = lines[0]
    assert rec["type"] == "assistant"
    assert rec["message"]["content"] == [
        {"type": "text", "text": "Your CV looks great."},
        {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {}},
    ]
    assert "cat secret" not in json.dumps(rec) and "hmm" not in json.dumps(rec)
    status = json.loads((tmp_path / share_proxy.STATUS_NAME).read_text())
    assert status["turns"] == 1 and status["last_turn"]


def test_a_stream_cut_before_message_stop_is_not_relayed(tmp_path):
    box = share_proxy.Outbox(str(tmp_path))
    box.open()
    c = share_proxy.TurnCollector()
    c.feed(TURN.split(b"event: message_stop")[0])
    box.turn(c)
    assert _outbox_lines(tmp_path) == []


@pytest.mark.parametrize("method, path, body, want", [
    ("POST", "/v1/messages", {"stream": True, "tools": [{"name": "Bash"}]}, True),
    ("POST", "/v1/messages?beta=true", {"stream": True, "tools": [{"name": "Bash"}]}, True),
    ("POST", "/v1/messages", {"stream": True}, False),            # a title / summary call
    ("POST", "/v1/messages", {"stream": False, "tools": [{}]}, False),
    ("POST", "/v1/messages/count_tokens", {"stream": True, "tools": [{}]}, False),
    ("GET", "/v1/messages", {"stream": True, "tools": [{}]}, False),
])
def test_only_main_loop_turns_are_recorded(method, path, body, want):
    assert share_proxy.records_turn(method, path, json.dumps(body).encode()) is want


# --- 2. the proxy's forward path actually feeds the outbox ----------------------------

class _FakeResp:
    status = 200

    def __init__(self, body: bytes):
        self._chunks = _chunks(body, 50)

    def getheader(self, name, default=None):
        return "text/event-stream" if name.lower() == "content-type" else default

    def getheaders(self):
        return [("content-type", "text/event-stream")]

    def read1(self, n):
        return self._chunks.pop(0) if self._chunks else b""


class _FakeConn:
    def __init__(self, *a, **kw):
        pass

    def request(self, *a, **kw):
        pass

    def getresponse(self):
        return _FakeResp(TURN)

    def close(self):
        pass


def _handler(tmp_path, body: dict):
    raw = json.dumps(body).encode()
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
    h.outbox = share_proxy.Outbox(str(tmp_path / "session"))
    return h


def test_the_proxy_forwards_the_stream_unchanged_and_records_the_turn(tmp_path, monkeypatch):
    (tmp_path / "token").write_text("tok")
    (tmp_path / "session").mkdir()
    monkeypatch.setattr(share_proxy.http.client, "HTTPSConnection", _FakeConn)
    h = _handler(tmp_path, {"stream": True, "tools": [{"name": "Bash"}]})
    h.outbox.open()
    h._forward()
    assert h.wfile.getvalue() == TURN                     # the guest gets every byte
    lines = _outbox_lines(tmp_path / "session")
    assert len(lines) == 1
    assert lines[0]["message"]["content"][0]["text"] == "Your CV looks great."


# --- 3. the outbox directory is never the guest's -------------------------------------

def test_the_session_dir_is_mounted_into_the_proxy_only(monkeypatch, tmp_path):
    monkeypatch.setattr(share_sandbox, "token_file", lambda: tmp_path / "tok")
    sdir = str(share_sandbox.session_dir(SID))
    proxy = share_sandbox.proxy_run_argv(SID, UID, GID)
    assert f"{sdir}:{share_sandbox.PROXY_SESSION_MOUNT}" in proxy          # read-write
    assert f"CHELA_PROXY_SESSION_DIR={share_sandbox.PROXY_SESSION_MOUNT}" in proxy
    guest = share_sandbox.guest_run_argv(SID, str(tmp_path), UID, GID, "/usr/bin/true")
    joined = " ".join(guest)
    assert sdir not in joined
    assert str(share_sandbox.session_root()) not in joined
    assert share_sandbox.PROXY_SESSION_MOUNT not in joined


def test_verify_container_refuses_the_outbox_dir_mounted_into_the_guest(tmp_path):
    real = os.path.realpath(str(tmp_path))
    net = share_sandbox.network_name(SID)
    info = {
        "State": {"Running": True},
        "Config": {"Labels": {share_sandbox.LABEL: SID}, "User": f"{UID}:{GID}"},
        "HostConfig": {"CapDrop": ["ALL"], "ReadonlyRootfs": True,
                       "SecurityOpt": ["no-new-privileges"], "Memory": 1, "PidsLimit": 1,
                       "NetworkMode": net},
        "NetworkSettings": {"Networks": {net: {}}},
        "Mounts": [
            {"Type": "bind", "Source": real, "Destination": share_sandbox.GUEST_WORKDIR, "RW": True},
            {"Type": "bind", "Source": "/usr/bin/true", "Destination": share_sandbox.CLAUDE_MOUNT, "RW": False},
        ],
    }
    # CMX-418: the session network must hold exactly the guest and its token proxy.
    network = {"Internal": True, "Options": {"com.docker.network.bridge.inhibit_ipv4": "true"},
               "Containers": {"a": {"Name": share_sandbox.container_name(SID)},
                              "b": {"Name": share_sandbox.proxy_name(SID)}}}
    assert share_sandbox.verify_container(info, network, SID, real, UID, GID) is None  # control
    info["Mounts"].append({"Type": "bind", "Source": str(share_sandbox.session_dir(SID)),
                           "Destination": "/home/guest/outbox", "RW": False})
    why = share_sandbox.verify_container(info, network, SID, real, UID, GID)
    assert why and "unexpected mount" in why


def test_a_workspace_containing_the_outbox_root_is_refused(tmp_path, monkeypatch):
    project = tmp_path / "project"
    (project / "state").mkdir(parents=True)
    monkeypatch.setattr(config, "CHELA_DIR", project / "state")
    assert "sandboxed sessions' replies" in (share_sandbox.workspace_refusal(str(project)) or "")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert share_sandbox.workspace_refusal(str(elsewhere)) is None


# --- 4. the relay picks up a sandboxed window's outbox, once --------------------------

def _relay(windows=("@7",)):
    reg = BindingRegistry(chat_id="-100")
    for i, w in enumerate(windows):
        reg.bind(w, 40 + i)
    sent: list[tuple[str, object]] = []
    relay = RegistryRelay(lambda text, mode, thread, **kw: sent.append((text, thread)) or True,
                          reg, show_tool_calls=False)
    return relay, sent


def _host_transcript(tmp_path) -> Path:
    p = tmp_path / "host.jsonl"
    p.write_text("")
    return p


def _append(path: Path, text: str) -> None:
    rec = {"type": "assistant", "timestamp": "2026-10-01T00:00:00Z",
           "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}
    with path.open("a") as f:
        f.write(json.dumps(rec) + "\n")


def test_relay_posts_a_sandboxed_windows_outbox_entry_exactly_once(tmp_path):
    share_sandbox.session_dir(SID).mkdir(parents=True)
    outbox = share_sandbox.outbox_path(SID)
    outbox.write_text("")
    host = _host_transcript(tmp_path)          # what the cwd fallback would have guessed
    boxes = SandboxOutboxes(check=lambda wid: SID if wid == "@7" else None)
    relay, sent = _relay()
    mon = TranscriptMonitor(on_message=relay.on_message, resolver=boxes.resolver(lambda w: host))
    mon.poll(["@7"])                           # attach (empty outbox)
    _append(outbox, "hello from the sandbox")
    _append(host, "SOMEONE ELSE'S TRANSCRIPT")
    mon.poll(["@7"])
    mon.poll(["@7"])                           # dedup: nothing new, nothing re-posted
    assert len(sent) == 1
    assert "hello from the sandbox" in sent[0][0] and sent[0][1] == "40"


def test_an_ordinary_window_relays_from_its_transcript_exactly_as_before(tmp_path):
    host = _host_transcript(tmp_path)
    calls = []
    boxes = SandboxOutboxes(check=lambda wid: calls.append(wid) and None)
    relay, sent = _relay()
    mon = TranscriptMonitor(on_message=relay.on_message, resolver=boxes.resolver(lambda w: host))
    mon.poll(["@7"])
    _append(host, "an ordinary agent's reply")
    mon.poll(["@7"])
    mon.poll(["@7"])
    assert [t for t, _ in sent] and len(sent) == 1
    assert "an ordinary agent's reply" in sent[0][0]
    assert calls == ["@7"]                     # verdict cached, not re-probed every poll


def test_the_sandbox_verdict_is_cached_then_rechecked():
    now = [0.0]
    calls = []
    boxes = SandboxOutboxes(check=lambda w: calls.append(w) or SID, clock=lambda: now[0], ttl=15)
    assert boxes.sid("@7") == SID and boxes.sid("@7") == SID
    now[0] = 16.0
    assert boxes.sid("@7") == SID
    assert calls == ["@7", "@7"]


def test_the_opt_out_silences_only_that_sandboxed_session():
    from chela.main import _RelayedWindows

    boxes = SandboxOutboxes(check=lambda wid: SID if wid == "@7" else None)
    reg = BindingRegistry(chat_id="-100")
    reg.bind("@7", 40)
    reg.bind("@8", 41)
    assert boxes.allows("@7") and boxes.allows("@8")            # default ON
    share_sandbox.set_relay_enabled(SID, False)
    assert not boxes.allows("@7") and boxes.allows("@8")
    assert _RelayedWindows(reg, boxes).windows() == ["@8"]
    share_sandbox.set_relay_enabled(SID, True)
    assert boxes.allows("@7")


# --- 5. the doctor ---------------------------------------------------------------------

@pytest.fixture
def doctor_env(monkeypatch, tmp_path):
    state = {"sandboxed": {"@7": SID}, "bound": {"@7": "guest", "@8": "agent"}}
    monkeypatch.setattr(runtime_truth, "_tmux_or_unverifiable", lambda: "/usr/bin/tmux")
    monkeypatch.setattr(runtime_truth, "_bound_windows", lambda: state["bound"])
    monkeypatch.setattr(runtime_truth.discovery, "get_windows_by_id",
                        lambda *a, **kw: {w: {} for w in state["bound"]})
    monkeypatch.setattr(share_sandbox, "share_session_id", lambda wid: state["sandboxed"].get(wid))
    monkeypatch.setattr(sessions, "resolve_window",
                        lambda wid, **kw: sessions.Resolution(wid=wid, detail="no session named"))
    return state


def _doctor(state):
    return runtime_truth._relay_report(state["bound"], runtime_truth._relay_read())


def _for(findings, wid):
    return [f for f in findings if f.title.startswith(wid)]


def _fresh_outbox(last_turn_lag: float = 0.0):
    d = share_sandbox.session_dir(SID)
    d.mkdir(parents=True, exist_ok=True)
    share_sandbox.outbox_path(SID).write_text("")
    mtime = share_sandbox.outbox_path(SID).stat().st_mtime
    share_sandbox.proxy_status_path(SID).write_text(json.dumps({"last_turn": mtime + last_turn_lag}))


def test_doctor_is_green_for_a_sandboxed_window_with_a_fresh_outbox(doctor_env):
    _fresh_outbox()
    found = _doctor(doctor_env)
    [sb] = _for(found, "@7")
    assert sb.level == runtime_truth.OK and "outbox" in sb.title
    # …and the ordinary window with no transcript is STILL dead outbound.
    [plain] = _for(found, "@8")
    assert plain.level == runtime_truth.ERROR and "NO transcript" in plain.title


def test_doctor_flags_a_sandboxed_window_with_no_outbox(doctor_env):
    [sb] = _for(_doctor(doctor_env), "@7")
    assert sb.level == runtime_truth.ERROR and "NO outbox" in sb.title


def test_doctor_flags_a_stale_outbox_while_the_proxy_is_active(doctor_env):
    _fresh_outbox(last_turn_lag=runtime_truth.SANDBOX_OUTBOX_STALE_S + 60)
    [sb] = _for(_doctor(doctor_env), "@7")
    assert sb.level == runtime_truth.ERROR and "STALE" in sb.title


def test_doctor_reports_an_opted_out_sandboxed_window_as_ok(doctor_env):
    share_sandbox.set_relay_enabled(SID, False)
    [sb] = _for(_doctor(doctor_env), "@7")
    assert sb.level == runtime_truth.OK and "OFF" in sb.title


# --- 6. the proxy's outbox switches and branches (CMX-420 rework) ----------------------

class _FakeServer:
    served: list = []

    def __init__(self, addr, handler):
        self.handler = handler

    def serve_forever(self):
        _FakeServer.served.append(self.handler)


@pytest.fixture
def proxy_main(monkeypatch, tmp_path):
    """Run the sidecar's ``main()`` up to ``serve_forever`` — and serve nothing."""
    _FakeServer.served = []
    monkeypatch.setattr(share_proxy, "ThreadingHTTPServer", _FakeServer)
    for attr in ("outbox", "token_file", "upstream"):      # main() sets these class-wide
        monkeypatch.setattr(share_proxy._Handler, attr, getattr(share_proxy._Handler, attr))
    share_proxy._Handler.outbox = None
    monkeypatch.setenv("CHELA_PROXY_TOKEN_FILE", str(tmp_path / "token"))
    monkeypatch.delenv("CHELA_PROXY_SESSION_DIR", raising=False)
    # An outbox opened on an EMPTY dir lands in the cwd ("outbox.jsonl") — so run here,
    # where that would be seen, and never in the repo.
    monkeypatch.chdir(tmp_path)
    return monkeypatch


def test_the_proxy_opens_its_outbox_when_given_a_session_dir(proxy_main, tmp_path):
    d = tmp_path / "session"
    d.mkdir()
    proxy_main.setenv("CHELA_PROXY_SESSION_DIR", str(d))
    share_proxy.main()
    assert _FakeServer.served, "main() never reached the server"
    box = share_proxy._Handler.outbox
    assert isinstance(box, share_proxy.Outbox) and box.path == str(d / share_proxy.OUTBOX_NAME)
    # Created at STARTUP, empty — so a missing outbox means "no directory", not "quiet".
    assert (d / share_proxy.OUTBOX_NAME).read_text() == ""
    status = json.loads((d / share_proxy.STATUS_NAME).read_text())
    assert status["turns"] == 0 and status["last_turn"] is None


@pytest.mark.parametrize("value", [None, ""])
def test_the_proxy_has_no_outbox_without_a_session_dir(proxy_main, tmp_path, value):
    if value is not None:
        proxy_main.setenv("CHELA_PROXY_SESSION_DIR", value)
    share_proxy.main()
    assert _FakeServer.served and share_proxy._Handler.outbox is None
    assert not list(tmp_path.rglob(share_proxy.OUTBOX_NAME))
    assert not list(tmp_path.rglob(share_proxy.STATUS_NAME))


def test_the_proxy_runs_without_an_outbox_when_its_dir_is_unwritable(proxy_main, tmp_path):
    proxy_main.setenv("CHELA_PROXY_SESSION_DIR", str(tmp_path / "does-not-exist"))
    share_proxy.main()
    assert _FakeServer.served and share_proxy._Handler.outbox is None


def _old(path: Path, age: float = 3600.0) -> float:
    t = path.stat().st_mtime - age
    os.utime(path, (t, t))
    return t


def test_a_completed_turn_with_nothing_visible_still_touches_the_outbox(tmp_path):
    box = share_proxy.Outbox(str(tmp_path))
    box.open()
    outbox = tmp_path / share_proxy.OUTBOX_NAME
    before = _old(outbox)
    c = share_proxy.TurnCollector()
    c.feed(_sse(   # thinking only — complete, but no visible content
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "x"}},
        {"type": "message_stop"}))
    assert c.complete and c.record() is None
    box.turn(c)
    assert _outbox_lines(tmp_path) == []
    assert outbox.stat().st_mtime > before + 1800        # touched: not mistaken for STALE
    status = json.loads((tmp_path / share_proxy.STATUS_NAME).read_text())
    assert status["turns"] == 1 and status["last_turn"]


def test_a_cut_stream_neither_writes_nor_stamps(tmp_path):
    box = share_proxy.Outbox(str(tmp_path))
    box.open()
    c = share_proxy.TurnCollector()
    c.feed(TURN.split(b"event: message_stop")[0])
    box.turn(c)
    status = json.loads((tmp_path / share_proxy.STATUS_NAME).read_text())
    assert status["turns"] == 0 and status["last_turn"] is None


class _Resp(_FakeResp):
    def __init__(self, body: bytes, status: int = 200, ctype: str = "text/event-stream"):
        super().__init__(body)
        self.status, self._ctype = status, ctype

    def getheader(self, name, default=None):
        return self._ctype if name.lower() == "content-type" else default

    def getheaders(self):
        return [("content-type", self._ctype)]


def _forward(tmp_path, monkeypatch, body: dict, *, path="/v1/messages", status=200,
             ctype="text/event-stream", outbox=True):
    (tmp_path / "token").write_text("tok")
    (tmp_path / "session").mkdir(exist_ok=True)
    resp = _Resp(TURN, status, ctype)

    class Conn(_FakeConn):
        def getresponse(self):
            return resp

    monkeypatch.setattr(share_proxy.http.client, "HTTPSConnection", Conn)
    h = _handler(tmp_path, body)
    h.path = path
    if outbox:
        h.outbox.open()
    else:
        h.outbox = None
    h._forward()
    assert h.wfile.getvalue() == TURN                   # the guest is served regardless
    out = tmp_path / "session" / share_proxy.OUTBOX_NAME
    return [json.loads(x) for x in out.read_text().splitlines()] if out.exists() else None


TOOLS = [{"name": "Bash"}]


def test_forward_control_a_main_loop_turn_is_recorded(tmp_path, monkeypatch):
    assert len(_forward(tmp_path, monkeypatch, {"stream": True, "tools": TOOLS})) == 1


@pytest.mark.parametrize("label, kw", [
    ("side call: no tools (a title / summary)", dict(body={"stream": True})),
    ("side call: empty tool list", dict(body={"stream": True, "tools": []})),
    ("not streamed", dict(body={"stream": False, "tools": TOOLS})),
    ("count_tokens", dict(body={"stream": True, "tools": TOOLS}, path="/v1/messages/count_tokens")),
    ("an upstream error", dict(body={"stream": True, "tools": TOOLS}, status=529)),
    ("not an event stream", dict(body={"stream": True, "tools": TOOLS}, ctype="application/json")),
])
def test_forward_never_records_anything_but_a_main_loop_turn(tmp_path, monkeypatch, label, kw):
    assert _forward(tmp_path, monkeypatch, **kw) == [], label
    status = json.loads((tmp_path / "session" / share_proxy.STATUS_NAME).read_text())
    assert status["turns"] == 0, label


def test_forward_without_an_outbox_records_nothing_and_still_serves(tmp_path, monkeypatch):
    assert _forward(tmp_path, monkeypatch, {"stream": True, "tools": TOOLS}, outbox=False) is None


def test_a_failing_outbox_never_breaks_the_guests_response(tmp_path, monkeypatch):
    def boom(self, c):
        raise OSError("disk full")

    monkeypatch.setattr(share_proxy.Outbox, "turn", boom)
    _forward(tmp_path, monkeypatch, {"stream": True, "tools": TOOLS})   # asserts bytes served


# --- 7. session dirs: pruned, private ---------------------------------------------------

def _session(name: str, age: float, now: float, inner_age: float | None = None) -> Path:
    d = share_sandbox.session_root() / name
    d.mkdir(parents=True)
    f = d / share_proxy.OUTBOX_NAME
    f.write_text("{}\n")
    t = now - (age if inner_age is None else inner_age)
    os.utime(f, (t, t))
    os.utime(d, (now - age, now - age))
    return d


def test_prune_removes_only_session_dirs_past_their_age():
    now = 2_000_000_000.0
    max_age = share_sandbox.SESSION_DIR_MAX_AGE_S
    old = _session("aaaaaaaaaaaa", max_age + 60, now)
    fresh = _session("bbbbbbbbbbbb", max_age - 60, now)
    # An old dir whose outbox was written recently is still in use.
    live = _session("cccccccccccc", max_age + 60, now, inner_age=10)
    stranger = _session("not-a-session", max_age + 60, now)        # not ours to delete
    target = _session("dddddddddddd", max_age - 60, now)
    link = share_sandbox.session_root() / "eeeeeeeeeeee"
    link.symlink_to(target)
    os.utime(target, (now - max_age - 60,) * 2)
    os.utime(target / share_proxy.OUTBOX_NAME, (now - max_age - 60,) * 2)
    share_sandbox.prune_session_dirs(now=now)
    assert not old.exists()
    assert fresh.exists() and live.exists() and stranger.exists()
    assert link.is_symlink()                    # a symlink is never followed into rmtree


def test_prune_tolerates_a_missing_root():
    assert not share_sandbox.session_root().exists()
    share_sandbox.prune_session_dirs()


def test_run_prunes_and_creates_a_private_session_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(share_sandbox, "preflight", lambda cwd, net="none": None)
    monkeypatch.setattr(share_sandbox, "claude_binary", lambda: "/usr/bin/true")
    monkeypatch.setattr(share_sandbox, "host_deny_nets", lambda: [])
    monkeypatch.setattr(share_sandbox.signal, "signal", lambda *a: None)
    monkeypatch.setattr(share_sandbox, "cleanup", lambda sid: None)

    class P:
        returncode, stdout, stderr = 0, "", ""

    monkeypatch.setattr(share_sandbox.subprocess, "run", lambda argv, **k: P())
    monkeypatch.setattr(share_sandbox.subprocess, "call", lambda argv: 0)
    pruned = []
    monkeypatch.setattr(share_sandbox, "prune_session_dirs", lambda: pruned.append(1))
    old = os.umask(0o022)
    try:
        assert share_sandbox.run(SID, str(tmp_path)) == 0
    finally:
        os.umask(old)
    assert pruned == [1]
    assert share_sandbox.session_dir(SID).stat().st_mode & 0o777 == 0o700
    assert share_sandbox.session_root().stat().st_mode & 0o777 == 0o700


# --- 8. `chela telegram --sandbox-relay` --------------------------------------------------

def _sandbox_relay(monkeypatch, spec, sandboxed=("@7",)):
    import argparse

    from chela import main as cli

    monkeypatch.setattr(share_sandbox, "share_session_id",
                        lambda wid: SID if wid in sandboxed else None)
    monkeypatch.setattr(share_sandbox, "check_share_session",
                        lambda wid: (False, "no such container"))
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)   # must exit BEFORE the bridge
    cli.cmd_telegram(argparse.Namespace(sandbox_relay=spec))


@pytest.mark.parametrize("spec", ["@7=off", "7=off", " @7 = OFF "])
def test_sandbox_relay_off_stores_the_opt_out(monkeypatch, capsys, spec):
    _sandbox_relay(monkeypatch, spec)
    assert not share_sandbox.relay_enabled(SID)
    assert "NOT relayed" in capsys.readouterr().out
    _sandbox_relay(monkeypatch, "@7=on")
    assert share_sandbox.relay_enabled(SID)


@pytest.mark.parametrize("spec, code", [("@7=maybe", 2), ("@7", 2), ("@8=off", 1)])
def test_sandbox_relay_refuses_without_storing(monkeypatch, spec, code):
    with pytest.raises(SystemExit) as e:
        _sandbox_relay(monkeypatch, spec)
    assert e.value.code == code
    assert share_sandbox.relay_enabled(SID)
    assert not (config.CHELA_DIR / "share-relay-optout.json").exists()


def test_sandbox_relay_is_wired_into_the_cli(monkeypatch):
    from chela import main as cli

    got = []
    monkeypatch.setattr(cli, "cmd_telegram", lambda args: got.append(args.sandbox_relay))
    monkeypatch.setattr(cli.sys, "argv", ["chela", "telegram", "--sandbox-relay", "@7=off"])
    cli.main()
    assert got == ["@7=off"]


# --- 9. the daemon itself: cmd_telegram's wiring -------------------------------------------

class _Bot:
    made: list = []

    def __init__(self, *a, **kw):
        self.sent: list = []
        _Bot.made.append(self)

    def send(self, text, parse_mode=None, message_thread_id=None, reply_markup=None, **kw):
        self.sent.append((text, message_thread_id))
        return True

    def send_photos(self, *a, **kw):
        return True

    def post(self, *a, **kw):
        return 1

    def edit(self, *a, **kw):
        return True

    def delete(self, *a, **kw):
        return True

    def chat_action(self, *a, **kw):
        return True


class _Thread:
    started: list = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target, self.args = target, args

    def start(self):
        _Thread.started.append(self)


@pytest.fixture(params=[True, False], ids=["no-inbound", "inbound"])
def daemon(request, monkeypatch, tmp_path):
    """Run the REAL ``cmd_telegram`` (both branches) far enough to hold the transcript
    monitor and the pane watch's window set it built — starting nothing."""
    import argparse
    import threading
    from types import SimpleNamespace

    import chela.telegram as tg
    from chela import main as cli

    _Bot.made, _Thread.started = [], []
    monkeypatch.setattr(threading, "Thread", _Thread)
    monkeypatch.setattr(tg, "BotSender", _Bot)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "0:test")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100")
    monkeypatch.delenv("TELEGRAM_TOPIC_ID", raising=False)
    reg = BindingRegistry(chat_id="-100")
    reg.bind("@7", 40)                                   # sandboxed
    reg.bind("@8", 41)                                   # ordinary
    monkeypatch.setattr(cli, "_build_bindings_registry", lambda args, chat: reg)
    monkeypatch.setattr(cli.agent_manager, "start_background_refresh", lambda *a, **kw: None)
    monkeypatch.setattr(share_sandbox, "share_session_id",
                        lambda wid: SID if wid == "@7" else None)
    host = _host_transcript(tmp_path)
    monkeypatch.setattr(sessions, "transcript_for_window", lambda wid: host)
    share_sandbox.session_dir(SID).mkdir(parents=True)
    share_sandbox.outbox_path(SID).write_text("")
    foreground = []
    monkeypatch.setattr(cli, "_outbound_loop", lambda *a: foreground.append(a))
    monkeypatch.setattr(tg, "build_application",
                        lambda *a, **kw: SimpleNamespace(run_polling=lambda: None))

    def run():
        cli.cmd_telegram(argparse.Namespace(
            wid=None, bind=["@7:40"], interval=2, auto_topics=False, reconcile_interval=15,
            no_inbound=request.param, sandbox_relay=None))
        relays = foreground + [t.args for t in _Thread.started if t.target is cli._outbound_loop]
        panes = [t.args for t in _Thread.started if t.target is cli._pane_loop]
        assert len(relays) == 1 and len(panes) == 1
        return SimpleNamespace(monitor=relays[0][0], panes=panes[0][1], bot=_Bot.made[0],
                               host=host, outbox=share_sandbox.outbox_path(SID))

    return run


def _texts(bot, thread):
    return [t for t, th in bot.sent if str(th) == str(thread)]


def test_the_daemon_relays_a_sandboxed_window_from_its_outbox(daemon):
    d = daemon()
    d.monitor.poll(["@7", "@8"])
    _append(d.outbox, "from the sandbox")
    _append(d.host, "an ordinary reply")
    d.monitor.poll(["@7", "@8"])
    d.monitor.poll(["@7", "@8"])
    assert [t for t in _texts(d.bot, 40) if "from the sandbox" in t] and \
        len(_texts(d.bot, 40)) == 1
    assert not [t for t in _texts(d.bot, 40) if "ordinary" in t]   # never the cwd guess
    assert len(_texts(d.bot, 41)) == 1 and "an ordinary reply" in _texts(d.bot, 41)[0]
    assert d.panes.windows() == ["@7", "@8"]                      # default ON: both watched


def test_the_daemon_silences_an_opted_out_sandboxed_session(daemon):
    share_sandbox.set_relay_enabled(SID, False)
    d = daemon()
    d.monitor.poll(["@7", "@8"])
    _append(d.outbox, "a guest's CV")
    _append(d.host, "an ordinary reply")
    d.monitor.poll(["@7", "@8"])
    assert _texts(d.bot, 40) == []                                # nothing of the guest
    assert len(_texts(d.bot, 41)) == 1                            # the rest untouched
    assert d.panes.windows() == ["@8"]                            # nor its pane mirror
    share_sandbox.set_relay_enabled(SID, True)                    # re-read, not latched
    assert d.panes.windows() == ["@7", "@8"]
