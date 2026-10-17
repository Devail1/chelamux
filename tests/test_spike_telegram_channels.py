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


def test_server_main_turns_a_socket_line_into_a_channel_notification(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "SPIKE_CHANNEL_SOCK": "s.sock", "SPIKE_CHANNEL_LOG": "chan.log"}
    proc = subprocess.Popen([sys.executable, "-I", str(SPIKE / "channel_server.py")], cwd=tmp_path,
                            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert drv.wait_for(lambda: (tmp_path / "s.sock").is_socket(), 10)
        with socket.socket(socket.AF_UNIX) as s:
            s.connect(str(tmp_path / "s.sock"))
            s.sendall(b'{"content": "hello", "meta": {"probe_id": "P9"}}\n')
        log = tmp_path / "chan.log"
        assert drv.wait_for(lambda: log.exists() and '"pushed"' in log.read_text(), 10)
        out, _ = proc.communicate("", timeout=10)
    finally:
        proc.kill()
    assert [json.loads(line) for line in out.splitlines()] == [
        {"jsonrpc": "2.0", "method": "notifications/claude/channel",
         "params": {"content": "hello", "meta": {"probe_id": "P9"}}}]
    assert json.loads(log.read_text().splitlines()[-1])["id"] == "P9"


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
    assert calls[1] == ("send-keys", "-t", "spike:w0", "Enter")  # accepts the dev-channel dialog
    assert calls[2][:3] == ("new-window", "-t", "spike")
    for call, win in ((calls[0], "w0"), (calls[2], "w1")):
        assert call[-5:-1] == ("-n", win, "-c", str(tmp_path / win))
        assert call[-1] == ("env -u CHELA_WID -u CHELA_ACTOR claude --model haiku --strict-mcp-config "
                            "--mcp-config mcp.json --dangerously-load-development-channels server:tgchan "
                            "--debug-file debug.txt")
    cfg = json.loads((tmp_path / "w1" / "mcp.json").read_text())
    assert cfg == {"mcpServers": {"tgchan": {
        "command": sys.executable, "args": ["-I", str(SPIKE / "channel_server.py")],
        "env": {"SPIKE_CHANNEL_SOCK": "chan.sock", "SPIKE_CHANNEL_LOG": "chan.log"}}}}
    answers = iter([None])
    monkeypatch.setattr(drv, "wait_for", lambda pred, timeout, step=0.25: next(answers))
    with pytest.raises(RuntimeError, match="dialog never appeared"):
        drv.launch(str(tmp_path), "w2", "haiku")
    answers = iter([True, None])
    with pytest.raises(RuntimeError, match="prompt never became ready"):
        drv.launch(str(tmp_path), "w3", "haiku")


def _fake_probe_world(monkeypatch, pane_text, seen, ack):
    used, timeouts = [], []
    clock = iter([100.0, 101.5, 103.25])
    monkeypatch.setattr(drv.time, "time", lambda: next(clock))
    monkeypatch.setattr(drv, "send_channel", lambda cwd, body, pid: used.append(("channel", cwd, body, pid)))
    monkeypatch.setattr(drv, "send_tmux", lambda win, body: used.append(("tmux", win, body)))
    monkeypatch.setattr(drv, "wait_for", lambda pred, timeout, step=0.25: timeouts.append(timeout) or pred())
    monkeypatch.setattr(drv, "first_row", lambda cwd, needle, kind=None:
                        ack if (needle, kind) == ("ACK P1", "assistant") else seen if needle == "P1" else None)
    monkeypatch.setattr(drv, "pane", lambda win: pane_text)
    return used, timeouts


SEEN = {"type": "user", "isMeta": True,
        "attachment": {"type": "queued_command", "origin": {"kind": "channel", "server": "tgchan"}},
        "message": {"content": [{"type": "image"}, {"type": "text", "text": "P1"}]}}


@pytest.mark.parametrize("transport", ["channel", "tmux"])
def test_probe_reports_every_field(monkeypatch, transport):
    # P1 sits ABOVE the last prompt too: only the text after the last ❯ counts as stranded.
    used, timeouts = _fake_probe_world(monkeypatch, "> P1 sent\n❯ P1 still typed",
                                       SEEN, {"message": {"content": "It is RED. ACK P1"}})
    row = drv.probe("c", "w0", transport, "photo_caption", "body", "P1", timeout=7)
    assert used == ([("channel", "c", "body", "P1")] if transport == "channel" else [("tmux", "w0", "body")])
    assert timeouts == [7, 7]
    assert row == {"probe": "P1", "transport": transport, "scenario": "photo_caption",
                   "delivered": True, "acked": True, "seen_s": 1.5, "ack_s": 3.25,
                   "row_type": "user", "is_meta": True, "attachment_type": "queued_command",
                   "origin": {"kind": "channel", "server": "tgchan"}, "stranded_on_prompt": True,
                   "said_red": True, "said_blue": False, "image_block": True}


def test_probe_reports_a_lost_message_and_a_clean_prompt(monkeypatch):
    _fake_probe_world(monkeypatch, "> P1 sent\n❯ ", None, None)
    row = drv.probe("c", "w0", "tmux", "idle_text", "body", "P1", timeout=1)
    assert row == {"probe": "P1", "transport": "tmux", "scenario": "idle_text",
                   "delivered": False, "acked": False, "seen_s": None, "ack_s": None,
                   "row_type": None, "is_meta": None, "attachment_type": None, "origin": None,
                   "stranded_on_prompt": False, "said_red": False, "said_blue": False,
                   "image_block": False}


def test_probe_text_row_without_image_says_blue(monkeypatch):
    seen = {"type": "user", "message": {"content": "P1"}}
    _fake_probe_world(monkeypatch, "❯ ", seen, {"message": {"content": "Blue. ACK P1"}})
    row = drv.probe("c", "w0", "channel", "photo_caption", "body", "P1", timeout=1)
    assert (row["said_blue"], row["said_red"], row["image_block"], row["is_meta"],
            row["attachment_type"]) == (True, False, False, None, None)


def test_send_tmux_second_enter_only_for_a_stranded_paste_after_the_last_prompt(monkeypatch):
    sent = []
    monkeypatch.setattr(drv, "tmux", lambda *a, **kw: sent.append(a))
    monkeypatch.setattr(drv, "pane", lambda w: "[Pasted text #1 +3 lines] earlier\n❯ ")
    monkeypatch.setattr(drv.time, "sleep", lambda s: None)
    drv.send_tmux("w0", "a\nb")
    assert sent[-1] == ("send-keys", "-t", "spike:w0", "Enter") and len(sent) == 3


def test_send_channel_writes_one_json_line_and_restores_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    got = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as lsn:
        lsn.bind("chan.sock")
        lsn.listen(1)

        def accept():
            conn, _ = lsn.accept()
            with conn, conn.makefile("r") as fh:
                got.extend(fh)
        t = threading.Thread(target=accept, daemon=True)
        t.start()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        drv.send_channel(str(tmp_path), "hi\nthere", "P7")
        t.join(5)
    assert Path.cwd() == elsewhere
    assert [json.loads(line) for line in got] == [{"content": "hi\nthere", "meta": {"probe_id": "P7"}}]


def test_transcript_rows_reads_only_this_cwds_project(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    cwd = tmp_path / "w0.x"
    cwd.mkdir()
    # Claude Code's project-dir encoding: every non-alphanumeric becomes "-".
    enc = "".join(c if c.isascii() and c.isalnum() else "-" for c in str(cwd.resolve()))
    assert "_" in str(cwd.resolve()) and "." in cwd.name  # fixture exercises both
    proj = tmp_path / ".claude" / "projects" / enc
    proj.mkdir(parents=True)
    (proj / "a.jsonl").write_text('{"n": 1}\n\n{"n": 2}\n')
    other = tmp_path / ".claude" / "projects" / "-elsewhere"
    other.mkdir()
    (other / "b.jsonl").write_text('{"n": 3}\n')
    assert drv.transcript_rows(str(cwd)) == [{"n": 1}, {"n": 2}]


def test_scenarios_write_each_transports_own_photo_and_tag_every_probe(tmp_path):
    rows = list(drv.scenarios(str(tmp_path), "w0", "W0"))
    assert [(tr, sc, pid) for tr, sc, _, pid in rows] == [
        (tr, sc, f"W0{tr[0]}{i}") for tr in ("tmux", "channel")
        for i, sc in enumerate(("idle_text", "multiline", "photo_caption", "imperative"), 1)]
    for tr, sc, body, pid in rows:
        assert f"ACK {pid}" in body
        if sc == "photo_caption":
            assert f"📎 image: {tmp_path / f'photo-{tr}.png'}" in body
        if sc == "multiline":
            assert body.count("\n") == 4
    assert (tmp_path / "photo-tmux.png").read_bytes() == drv.png((255, 0, 0))
    assert (tmp_path / "photo-channel.png").read_bytes() == drv.png((0, 0, 255))


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
