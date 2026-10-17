#!/usr/bin/env python3
"""CMX-186 spike driver: channel delivery vs the tmux paste path, on ISOLATED sessions.

Every tmux call names its own server with ``-L`` (never the live ``chela`` server, never
TMUX_TMPDIR) and every session runs in a throwaway directory under ``--root``. Results are
JSON lines on stdout; docs/TELEGRAM_CHANNELS_SPIKE.md is written from them.

    python3 scripts/spikes/telegram_channels/run_spike.py --root <empty dir> --windows 3
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import socket
import struct
import subprocess
import sys
import time
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
TMUX_SOCKET = "cmx186spike"
# One colour per transport, so a right answer proves the model LOOKED at that image.
PHOTO_RGB = {"tmux": (255, 0, 0), "channel": (0, 0, 255)}


def png(rgb: tuple[int, int, int], size: int = 8) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\0" + bytes(rgb) * size for _ in range(size))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def tmux(*args: str, **kw) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("TMUX", "TMUX_PANE")}
    return subprocess.run(["tmux", "-L", TMUX_SOCKET, *args], env=env, check=True,
                          capture_output=True, **kw)


def pane(win: str) -> str:
    return tmux("capture-pane", "-p", "-t", f"spike:{win}").stdout.decode()


def wait_for(pred, timeout: float, step: float = 0.25):
    end = time.time() + timeout
    while time.time() < end:
        got = pred()
        if got:
            return got
        time.sleep(step)
    return None


def launch(root: str, win: str, model: str) -> str:
    cwd = os.path.join(root, win)
    os.makedirs(cwd, exist_ok=True)
    cfg = {"mcpServers": {"tgchan": {
        "command": sys.executable,
        "args": ["-I", os.path.join(HERE, "channel_server.py")],
        # Relative: the server inherits the session cwd, which keeps the socket path
        # under the AF_UNIX 108-byte limit however deep --root is.
        "env": {"SPIKE_CHANNEL_SOCK": "chan.sock", "SPIKE_CHANNEL_LOG": "chan.log"}}}}
    with open(os.path.join(cwd, "mcp.json"), "w") as fh:
        json.dump(cfg, fh)
    cmd = (f"env -u CHELA_WID -u CHELA_ACTOR claude --model {model} --strict-mcp-config "
           f"--mcp-config mcp.json --dangerously-load-development-channels server:tgchan "
           f"--debug-file debug.txt")
    verb = (["new-session", "-d", "-s", "spike", "-x", "200", "-y", "50"] if win == "w0"
            else ["new-window", "-t", "spike"])
    tmux(*verb, "-n", win, "-c", cwd, cmd)
    if not wait_for(lambda: "local development" in pane(win), 30):
        raise RuntimeError(f"{win}: dev-channel dialog never appeared")
    tmux("send-keys", "-t", f"spike:{win}", "Enter")
    if not wait_for(lambda: "auto mode" in pane(win) or "for shortcuts" in pane(win), 30):
        raise RuntimeError(f"{win}: prompt never became ready")
    wait_for(lambda: os.path.exists(os.path.join(cwd, "chan.sock")), 15)
    return cwd


def send_channel(cwd: str, content: str, probe_id: str) -> None:
    here = os.getcwd()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        os.chdir(cwd)  # a relative connect, for the same AF_UNIX length limit
        try:
            s.connect("chan.sock")
        finally:
            os.chdir(here)
        s.sendall((json.dumps({"content": content, "meta": {"probe_id": probe_id}}) + "\n").encode())


def send_tmux(win: str, text: str) -> None:
    """chela.messenger.send_tmux's algorithm, aimed at the isolated server."""
    target = f"spike:{win}"
    if "\n" in text:
        tmux("load-buffer", "-", input=text.encode())
        tmux("paste-buffer", "-p", "-t", target)
        time.sleep(0.5)
        tmux("send-keys", "-t", target, "Enter")
        time.sleep(0.3)
        if "[Pasted text" in pane(win).split("❯")[-1]:
            tmux("send-keys", "-t", target, "Enter")
    else:
        tmux("send-keys", "-t", target, "-l", text)
        time.sleep(0.5)
        tmux("send-keys", "-t", target, "Enter")


def transcript_rows(cwd: str) -> list[dict]:
    enc = re.sub(r"[^A-Za-z0-9]", "-", os.path.realpath(cwd))
    rows = []
    for path in glob.glob(os.path.expanduser(f"~/.claude/projects/{enc}*/*.jsonl")):
        with open(path, encoding="utf-8") as fh:
            rows += [json.loads(line) for line in fh if line.strip()]
    return rows


def first_row(cwd: str, needle: str, kind: str | None = None) -> dict | None:
    for row in transcript_rows(cwd):
        if row.get("type") in ("api-request-blob", "queue-operation"):
            continue
        if (kind is None or row.get("type") == kind) and needle in json.dumps(row):
            return row
    return None


def probe(cwd: str, win: str, transport: str, scenario: str, body: str, pid: str,
          timeout: float = 120) -> dict:
    t0 = time.time()
    if transport == "channel":
        send_channel(cwd, body, pid)
    else:
        send_tmux(win, body)
    seen = wait_for(lambda: first_row(cwd, pid), timeout)
    t_seen = time.time()
    ack = wait_for(lambda: first_row(cwd, f"ACK {pid}", "assistant"), timeout)
    t_ack = time.time()
    att = (seen or {}).get("attachment") or {}
    reply = json.dumps((ack or {}).get("message", {}).get("content", ""))
    return {"probe": pid, "transport": transport, "scenario": scenario,
            "delivered": seen is not None, "acked": ack is not None,
            "seen_s": round(t_seen - t0, 2) if seen else None,
            "ack_s": round(t_ack - t0, 2) if ack else None,
            "row_type": (seen or {}).get("type"), "is_meta": (seen or {}).get("isMeta"),
            "attachment_type": att.get("type"), "origin": att.get("origin"),
            "stranded_on_prompt": pid in pane(win).split("❯")[-1],
            "said_red": "red" in reply.lower(), "said_blue": "blue" in reply.lower(),
            "image_block": '"type": "image"' in json.dumps(seen or {})}


def scenarios(cwd: str, win: str, tag: str):
    """(transport, scenario, body, probe_id) for one window; both transports per case."""
    for tr in ("tmux", "channel"):
        p = f"{tag}{tr[0]}"
        with open(os.path.join(cwd, f"photo-{tr}.png"), "wb") as fh:
            fh.write(png(PHOTO_RGB[tr]))
        yield tr, "idle_text", f"Reply with exactly: ACK {p}1", f"{p}1"
        yield tr, "multiline", (f"line one of a multi-line note\nline two\n\nline four.\n"
                                f"Reply with exactly: ACK {p}2"), f"{p}2"
        yield tr, "photo_caption", (f"What colour is this image? Then reply ACK {p}3"
                                    f"\n\n📎 image: {os.path.join(cwd, f'photo-{tr}.png')}"), f"{p}3"
        yield tr, "imperative", (f"Create a file named made-{p}4.txt in the current directory "
                                 f"containing the word yes, then reply with exactly: ACK {p}4"), f"{p}4"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--windows", type=int, default=1)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--busy", action="store_true", help="also run the mid-tool-call probes")
    a = ap.parse_args()
    wins = [f"w{i}" for i in range(a.windows)]
    try:
        cwds = {w: launch(a.root, w, a.model) for w in wins}
        for w in wins:
            for tr, sc, body, pid in scenarios(cwds[w], w, f"{w.upper()}"):
                row = probe(cwds[w], w, tr, sc, body, pid)
                row["window"] = w
                if sc == "imperative":
                    row["file_made"] = os.path.exists(os.path.join(cwds[w], f"made-{pid}.txt"))
                print(json.dumps(row), flush=True)
        if a.busy:
            w = wins[0]
            for tr in ("tmux", "channel"):
                pid = f"BUSY{tr[0]}"
                send_tmux(w, "Use the Bash tool to run `sleep 20`, then say SLEPT.")
                wait_for(lambda: "sleep 20" in pane(w), 30)
                time.sleep(3)
                body = (f"Create a file named made-{pid}.txt in the current directory "
                        f"containing the word yes, then reply with exactly: ACK {pid}")
                row = probe(cwds[w], w, tr, "mid_tool_imperative", body, pid)
                row["file_made"] = wait_for(
                    lambda: os.path.exists(os.path.join(cwds[w], f"made-{pid}.txt")), 30) is not None
                print(json.dumps({**row, "window": w}), flush=True)
        # Cross-talk: did any window's probe land in ANOTHER window's transcript?
        for w in wins:
            others = [pid for o in wins if o != w for pid in (f"{o.upper()}c1", f"{o.upper()}t1")]
            leaked = [pid for pid in others if first_row(cwds[w], f"ACK {pid}", "assistant")]
            print(json.dumps({"window": w, "cross_talk": leaked}), flush=True)
    finally:
        subprocess.run(["tmux", "-L", TMUX_SOCKET, "kill-server"], capture_output=True)


if __name__ == "__main__":
    main()
