#!/usr/bin/env python3
"""Send a message or file to Telegram via the Bot API — dependency-free (stdlib only).

Configure via environment:
  TELEGRAM_BOT_TOKEN  bot token from @BotFather        (required)
  TELEGRAM_CHAT_ID    target chat/channel id           (required)
  TELEGRAM_TOPIC_ID   forum topic (message_thread_id)  (optional)

Usage:
  python send.py "your message"
  python send.py --file ./chart.png --caption "backtest result"

send_message() and send_file() are also importable for direct calls.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid

_API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4096


def _cfg() -> tuple[str, str, str | None]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        sys.exit("error: set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
    return token, chat, os.environ.get("TELEGRAM_TOPIC_ID")


def _multipart(fields: dict, files: dict, boundary: str) -> bytes:
    out: list[bytes] = []
    for k, v in fields.items():
        out.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        )
    for k, (fname, data) in files.items():
        ctype = mimetypes.guess_type(fname)[0] or "application/octet-stream"
        out.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"; '
            f'filename="{fname}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
        )
        out.append(data)
        out.append(b"\r\n")
    out.append(f"--{boundary}--\r\n".encode())
    return b"".join(out)


def _post(token: str, method: str, fields: dict, files: dict | None = None) -> dict:
    url = _API.format(token=token, method=method)
    if files:
        boundary = uuid.uuid4().hex
        body = _multipart(fields, files, boundary)
        headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    else:
        body = urllib.parse.urlencode(fields).encode()
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
    req = urllib.request.Request(url, data=body, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            return json.load(e)
        except (ValueError, OSError):
            return {"ok": False, "description": f"HTTP {e.code}"}


def _split(text: str, n: int = MAX_LEN) -> list[str]:
    return [text[i : i + n] for i in range(0, len(text), n)] or [""]


def send_message(text: str) -> None:
    """Send a text message (auto-split at Telegram's 4096-char limit)."""
    token, chat, topic = _cfg()
    for chunk in _split(text):
        fields = {"chat_id": chat, "text": chunk}
        if topic:
            fields["message_thread_id"] = topic
        r = _post(token, "sendMessage", fields)
        if not r.get("ok"):
            sys.exit(f"telegram error: {r.get('description', r)}")


def send_file(path: str, caption: str | None = None) -> None:
    """Send a file (any type) as a document, with an optional caption."""
    token, chat, topic = _cfg()
    with open(path, "rb") as f:
        data = f.read()
    fields = {"chat_id": chat}
    if caption:
        fields["caption"] = caption
    if topic:
        fields["message_thread_id"] = topic
    r = _post(token, "sendDocument", fields, files={"document": (os.path.basename(path), data)})
    if not r.get("ok"):
        sys.exit(f"telegram error: {r.get('description', r)}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Send a message or file to Telegram.")
    ap.add_argument("text", nargs="?", help="message text (or caption when used with --file)")
    ap.add_argument("--file", help="path to a file to send")
    ap.add_argument("--caption", help="caption for --file")
    a = ap.parse_args()
    if a.file:
        send_file(a.file, a.caption or a.text)
    elif a.text:
        send_message(a.text)
    else:
        ap.error("provide a message or --file")


if __name__ == "__main__":
    main()
