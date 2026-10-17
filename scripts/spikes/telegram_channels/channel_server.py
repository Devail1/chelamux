#!/usr/bin/env python3
"""CMX-186 spike: a minimal Claude Code channel server, stdlib only.

Claude Code spawns this over stdio (MCP, newline-delimited JSON-RPC). It declares the
`claude/channel` capability and forwards every JSON line written to a unix socket
(`$SPIKE_CHANNEL_SOCK`) as a `notifications/claude/channel` event. One process per
session, so one socket per window: that is the "one window = one endpoint" mapping.

Spike code only. Nothing in `chela/` imports it. See docs/TELEGRAM_CHANNELS_SPIKE.md.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time

LEGACY_PROTOCOL = "2025-06-18"
_out_lock = threading.Lock()


def _write(msg: dict) -> None:
    with _out_lock:
        sys.stdout.write(json.dumps(msg) + "\n")
        sys.stdout.flush()


def _log(path: str | None, event: str, **fields) -> None:
    if not path:
        return
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"t": time.time(), "event": event, **fields}) + "\n")


def channel_notification(content: str, meta: dict | None = None) -> dict:
    """The one wire shape Claude Code accepts (docs: channels-reference#notification-format)."""
    params: dict = {"content": content}
    if meta:
        params["meta"] = {str(k): str(v) for k, v in meta.items()}
    return {"jsonrpc": "2.0", "method": "notifications/claude/channel", "params": params}


def handle_request(msg: dict) -> dict | None:
    """Answer one JSON-RPC message from Claude Code; None for notifications."""
    if "id" not in msg:
        return None
    method = msg.get("method")
    if method == "initialize":
        result = {
            # Answering with a legacy revision keeps the connection off the "modern"
            # era, where Claude Code 2.1.302 refuses to register a channel at all.
            "protocolVersion": LEGACY_PROTOCOL,
            "capabilities": {"experimental": {"claude/channel": {}}},
            "serverInfo": {"name": "tgchan", "version": "0.0.1"},
            "instructions": "Telegram text for this window arrives as <channel source=...>.",
        }
        return {"jsonrpc": "2.0", "id": msg["id"], "result": result}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": msg["id"], "result": {}}
    return {"jsonrpc": "2.0", "id": msg["id"],
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def _serve_socket(path: str, log: str | None) -> None:
    if os.path.exists(path):
        os.unlink(path)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(8)
    while True:
        conn, _ = srv.accept()
        with conn, conn.makefile("r", encoding="utf-8") as lines:
            for line in lines:
                item = json.loads(line)
                _write(channel_notification(item["content"], item.get("meta")))
                _log(log, "pushed", id=item.get("meta", {}).get("probe_id"))


def main() -> None:
    log = os.environ.get("SPIKE_CHANNEL_LOG")
    sock = os.environ["SPIKE_CHANNEL_SOCK"]
    threading.Thread(target=_serve_socket, args=(sock, log), daemon=True).start()
    for line in sys.stdin:
        msg = json.loads(line)
        _log(log, "rpc", method=msg.get("method"))
        reply = handle_request(msg)
        if reply is not None:
            _write(reply)


if __name__ == "__main__":
    main()
