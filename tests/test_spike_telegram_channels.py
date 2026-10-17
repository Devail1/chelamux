"""CMX-186 spike: the channel server's wire contract and the driver's isolation guards."""

import importlib.util
import io
import json
import subprocess
import sys
import socket
import threading
import time
from pathlib import Path

import pytest


SPIKE = Path(__file__).resolve().parent.parent / "scripts" / "spikes" / "telegram_channels"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"cmx186_{name}", SPIKE / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


srv = _load("channel_server")
drv = _load("run_spike")


def test_initialize_declares_channel_on_a_legacy_revision():
    reply = srv.handle_request({"jsonrpc": "2.0", "id": 7, "method": "initialize"})
    assert reply["id"] == 7
    # 2.1.302 never registers a channel on the 2026-07-28 ("modern") revision.
    assert reply["result"]["protocolVersion"] == "2025-06-18"
    assert reply["result"]["capabilities"] == {"experimental": {"claude/channel": {}}}


def test_ping_and_unknown_methods():
    assert srv.handle_request({"id": 1, "method": "ping"}) == {"jsonrpc": "2.0", "id": 1, "result": {}}
    err = srv.handle_request({"id": 2, "method": "server/discover"})
    assert err["error"]["code"] == -32601 and "result" not in err
    assert srv.handle_request({"method": "notifications/initialized"}) is None


def test_channel_notification_shape():
    assert srv.channel_notification("hi") == {
        "jsonrpc": "2.0", "method": "notifications/claude/channel", "params": {"content": "hi"}}
    note = srv.channel_notification("x", {"probe_id": 3})
    assert note["params"]["meta"] == {"probe_id": "3"}


def test_log_writes_only_with_a_path(tmp_path):
    srv._log(None, "rpc")  # no path: must not raise or write anywhere
    path = tmp_path / "chan.log"
    srv._log(str(path), "pushed", id="P1")
    row = json.loads(path.read_text())
    assert (row["event"], row["id"]) == ("pushed", "P1")


def test_socket_line_becomes_a_notification(tmp_path, monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(srv.sys, "stdout", out)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "s.sock").write_text("stale")  # a stale path is replaced, not fatal
    threading.Thread(target=srv._serve_socket, args=("s.sock", "log"), daemon=True).start()
    assert drv.wait_for(lambda: (tmp_path / "s.sock").is_socket(), 5)
    with socket.socket(socket.AF_UNIX) as s:
        s.connect("s.sock")
        s.sendall(b'{"content": "hello", "meta": {"probe_id": "P9"}}\n')
    assert drv.wait_for(lambda: out.getvalue(), 5)
    assert json.loads(out.getvalue())["params"] == {"content": "hello", "meta": {"probe_id": "P9"}}


def test_tmux_always_names_the_private_server(monkeypatch):
    calls = []
    monkeypatch.setenv("TMUX", "/tmp/live,1,0")
    monkeypatch.setattr(drv.subprocess, "run", lambda argv, **kw: calls.append((argv, kw)))
    drv.tmux("ls")
    argv, kw = calls[0]
    assert argv == ["tmux", "-L", "cmx186spike", "ls"]
    assert "TMUX" not in kw["env"] and kw["check"] is True


def test_send_tmux_mirrors_send_tmux(monkeypatch):
    sent = []
    monkeypatch.setattr(drv, "tmux", lambda *a, **kw: sent.append(a))
    monkeypatch.setattr(drv, "pane", lambda w: "❯ [Pasted text #1 +3 lines]")
    monkeypatch.setattr(drv.time, "sleep", lambda s: None)
    drv.send_tmux("w0", "one line")
    assert sent == [("send-keys", "-t", "spike:w0", "-l", "one line"),
                    ("send-keys", "-t", "spike:w0", "Enter")]
    sent.clear()
    drv.send_tmux("w0", "a\nb")
    assert sent == [("load-buffer", "-"), ("paste-buffer", "-p", "-t", "spike:w0"),
                    ("send-keys", "-t", "spike:w0", "Enter"), ("send-keys", "-t", "spike:w0", "Enter")]
    sent.clear()
    monkeypatch.setattr(drv, "pane", lambda w: "❯ ")
    drv.send_tmux("w0", "a\nb")
    assert len(sent) == 3  # submitted: no second Enter


def test_first_row_skips_blobs_and_filters_kind(monkeypatch):
    rows = [{"type": "api-request-blob", "x": "P1"}, {"type": "queue-operation", "x": "P1"},
            {"type": "user", "x": "P1"}, {"type": "assistant", "x": "ACK P1"}]
    monkeypatch.setattr(drv, "transcript_rows", lambda cwd: rows)
    assert drv.first_row("c", "P1") == rows[2]
    assert drv.first_row("c", "P1", "assistant") == rows[3]
    assert drv.first_row("c", "P2") is None


def test_wait_for_times_out():
    t0 = time.time()
    assert drv.wait_for(lambda: None, 0.3, step=0.05) is None
    assert time.time() - t0 >= 0.3
    assert drv.wait_for(lambda: "ok", 1) == "ok"


def test_png_is_decodable():
    data = drv.png((0, 0, 255), size=2)
    assert data.startswith(b"\x89PNG\r\n\x1a\n") and data.endswith(b"IEND\xaeB`\x82")


def test_wait_for_returns_none_not_the_last_falsy_value():
    assert drv.wait_for(lambda: [], 0.1, step=0.05) is None


def test_server_main_answers_over_stdio(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "SPIKE_CHANNEL_SOCK": "s.sock"}
    proc = subprocess.run([sys.executable, "-I", str(SPIKE / "channel_server.py")], cwd=tmp_path,
                          env=env, input='{"id": 1, "method": "ping"}\n{"method": "n"}\n',
                          capture_output=True, text=True, timeout=20)
    assert proc.stdout.splitlines() == ['{"jsonrpc": "2.0", "id": 1, "result": {}}']


def test_driver_entry_point_parses_args():
    proc = subprocess.run([sys.executable, str(SPIKE / "run_spike.py"), "--help"],
                          capture_output=True, text=True, timeout=20)
    assert "--windows" in proc.stdout


def test_launch_names_session_then_windows_and_fails_closed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(drv, "tmux", lambda *a, **kw: calls.append(a))
    monkeypatch.setattr(drv, "wait_for", lambda pred, timeout, step=0.25: True)
    drv.launch(str(tmp_path), "w0", "haiku")
    drv.launch(str(tmp_path), "w1", "haiku")
    assert calls[0][:4] == ("new-session", "-d", "-s", "spike")
    assert calls[2][:3] == ("new-window", "-t", "spike")
    cfg = json.loads((tmp_path / "w1" / "mcp.json").read_text())
    assert cfg["mcpServers"]["tgchan"]["env"]["SPIKE_CHANNEL_SOCK"] == "chan.sock"
    answers = iter([None])
    monkeypatch.setattr(drv, "wait_for", lambda pred, timeout, step=0.25: next(answers))
    with pytest.raises(RuntimeError, match="dialog never appeared"):
        drv.launch(str(tmp_path), "w2", "haiku")
    answers = iter([True, None])
    with pytest.raises(RuntimeError, match="prompt never became ready"):
        drv.launch(str(tmp_path), "w3", "haiku")


@pytest.mark.parametrize("transport", ["channel", "tmux"])
def test_probe_routes_by_transport(monkeypatch, transport):
    used = []
    monkeypatch.setattr(drv, "send_channel", lambda cwd, body, pid: used.append("channel"))
    monkeypatch.setattr(drv, "send_tmux", lambda win, body: used.append("tmux"))
    monkeypatch.setattr(drv, "first_row", lambda cwd, needle, kind=None: {"type": "user"})
    monkeypatch.setattr(drv, "pane", lambda win: "❯ ")
    row = drv.probe("c", "w0", transport, "idle_text", "body", "P1", timeout=1)
    assert used == [transport] and row["delivered"] and row["acked"]


def test_main_reports_files_busy_probes_and_cross_talk(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(drv, "launch", lambda root, w, model: str(tmp_path))
    monkeypatch.setattr(drv, "send_tmux", lambda win, body: None)
    monkeypatch.setattr(drv, "pane", lambda win: "sleep 20")
    monkeypatch.setattr(drv.time, "sleep", lambda s: None)
    monkeypatch.setattr(drv, "wait_for", lambda pred, timeout, step=0.25: pred() or None)
    monkeypatch.setattr(drv.subprocess, "run", lambda *a, **kw: None)
    monkeypatch.setattr(drv, "probe", lambda cwd, w, tr, sc, body, pid: {"probe": pid})
    (tmp_path / "made-W0t4.txt").write_text("yes")
    # w0's transcript (same fake cwd for both windows) holds w1's ACK: that is cross-talk.
    monkeypatch.setattr(drv, "first_row", lambda cwd, needle, kind=None: needle == "ACK W1c1" or None)
    monkeypatch.setattr(drv.sys, "argv", ["x", "--root", str(tmp_path), "--windows", "2", "--busy"])
    drv.main()
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    made = {r["probe"]: r.get("file_made") for r in rows if "probe" in r}
    assert made["W0t4"] is True and made["W0c4"] is False and made["W0t1"] is None
    assert {"BUSYt", "BUSYc"} <= set(made) and made["BUSYc"] is False
    assert {r["window"]: r["cross_talk"] for r in rows if "cross_talk" in r} == {"w0": ["W1c1"], "w1": []}
