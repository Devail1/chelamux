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
    network = {"Internal": True, "Options": {"com.docker.network.bridge.inhibit_ipv4": "true"}}
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
