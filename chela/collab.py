"""Server-side collab presence — publish a running agent as a first-class peer.

P3 "agent-as-peer": make a running Claude agent show up in the collaborative
terminal's presence overlay. For every tmux window that has a live ``claude``
process, we publish a Yjs *awareness* frame — on the agent's behalf — into that
window's relay room (room == window id), so a browser viewing
``/term/<wid>/?collab=1`` sees a "claude" pill next to the human pills, coloured
by the agent's live busy / idle / waiting status.

An agent isn't a browser, so there is deliberately no Yjs here. A y-protocols
awareness update is just::

    varUint(count) · varUint(clientID) · varUint(clock) · varString(JSON(state))

i.e. LEB128 varints plus a JSON string — and the relay never parses frames, so a
hand-encoded Python frame is byte-indistinguishable from a browser's on the wire.
We connect, send one frame, and close, once per heartbeat (~4s). The browser is
the *persistent* peer; the relay fans our ephemeral send out to it. Agents carry
no cursor (they have no mouse) — presence is pill-only.

Two details that make it stick on the receiver (see y-protocols/awareness):
  * The receiver only accepts an update when its stored clock < ours, and only
    then refreshes ``lastUpdated``. Awareness times a peer out after 30s without
    a refresh — so we must *increment the clock every heartbeat*, otherwise the
    pill would vanish after 30s even while we keep sending.
  * When an agent's window goes away we send one removal frame (state ``null``);
    if we can't, the receiver's 30s timeout reaps it anyway.

Spike quality: ephemeral connect-per-heartbeat (fine at this cadence/scale),
best-effort, fully gated behind ``CHELA_COLLAB`` and the browser's own ``?collab``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import zlib

from chela import agent_manager, config, discovery

log = logging.getLogger(__name__)

# Presence heartbeat. Matches presence.js so agent and human pills converge on
# the same ~4s cadence for late joiners.
HEARTBEAT_SECONDS = 4.0

# Connect-send-close drops the frame if we close before it drains through the
# TLS / CF edge, so we hold the socket briefly after send. (Verified live: 0.4s
# was flaky, 0.7s reliable.) Spike shortcut — a persistent per-room socket would
# avoid both the flush wait and repeated handshakes, and is the productionization.
FLUSH_SECONDS = 0.7

# Pill colour by live session status. Green = actively working, amber = blocked
# on input (needs attention), grey = idle at the prompt. Kept in sync with the
# dashboard's own _liveness() semantics.
_STATUS_COLOR = {"busy": "#3fb950", "waiting": "#d29922", "idle": "#7d8590"}
_DEFAULT_COLOR = "#7d8590"

# Relay route regex is [\w@.\-]+ and the DO keys rooms by this raw path segment,
# so the room id must avoid percent-encoding. presence.js applies the identical
# transform (see roomId() there) so both sides land in the same room.
_UNSAFE_ROOM_CHARS = re.compile(r"[^\w@.\-]")


def room_id(wid: str) -> str:
    """Relay-safe room id for a tmux window id (e.g. ``@11`` stays ``@11``)."""
    return _UNSAFE_ROOM_CHARS.sub("_", wid or "default")


# --- Yjs awareness wire encoding (LEB128 + JSON) ---------------------------

def _write_varuint(buf: bytearray, n: int) -> None:
    while True:
        if n < 0x80:
            buf.append(n)
            return
        buf.append((n & 0x7F) | 0x80)
        n >>= 7


def _write_varstring(buf: bytearray, s: str) -> None:
    data = s.encode("utf-8")
    _write_varuint(buf, len(data))
    buf.extend(data)


def encode_awareness_update(client_id: int, clock: int, state: dict | None) -> bytes:
    """One-client awareness update, byte-identical to y-protocols'
    ``encodeAwarenessUpdate``. ``state=None`` encodes a removal (JSON ``null``)."""
    buf = bytearray()
    _write_varuint(buf, 1)            # one client in this update
    _write_varuint(buf, client_id)
    _write_varuint(buf, clock)
    _write_varstring(buf, json.dumps(state, separators=(",", ":")))  # None -> "null"
    return bytes(buf)


def _client_id(wid: str) -> int:
    """Stable uint32 client id for an agent window (namespaced to avoid ever
    colliding with a browser's random Yjs clientID)."""
    return zlib.crc32(("chela-agent:" + wid).encode()) & 0xFFFFFFFF


# --- discovery: which windows are agents right now -------------------------

def _agent_rooms() -> dict[str, dict]:
    """``{wid: {"name", "status"}}`` for every window running a claude process.

    Plain-shell / dev-server windows (no claude pid) are not agents and get no
    pill — matching how the wall itself decides an agent is "alive".
    """
    out: dict[str, dict] = {}
    try:
        windows = discovery.get_all_windows()  # {name: wid}
        status_map = agent_manager.session_status_map()
    except Exception:
        log.debug("collab: discovery/status query failed", exc_info=True)
        return out
    for name, wid in windows.items():
        try:
            cpid = agent_manager.claude_pid(wid)
        except Exception:
            cpid = None
        if cpid is None:
            continue  # not an agent
        out[wid] = {"name": name, "status": status_map["by_pid"].get(cpid)}
    return out


def _agent_state(name: str, status: str | None) -> dict:
    return {
        "user": {
            "name": f"claude · {name}",
            "color": _STATUS_COLOR.get(status or "", _DEFAULT_COLOR),
            "bot": True,
            "status": status or "idle",
        },
        "cursor": None,  # agents have no mouse — pill only
    }


# --- publishing ------------------------------------------------------------

_clocks: dict[str, int] = {}


def _send(wid: str, state: dict | None) -> None:
    """Open → send one awareness frame → close, for one room. Best-effort."""
    from simple_websocket import Client

    clock = _clocks.get(wid, 0) + 1
    _clocks[wid] = clock
    frame = encode_awareness_update(_client_id(wid), clock, state)
    url = f"{config.COLLAB_RELAY}/room/{room_id(wid)}"
    client = None
    try:
        client = Client.connect(url)
        client.send(frame)
        time.sleep(FLUSH_SECONDS)  # let the frame drain before we close
    except Exception:
        log.debug("collab: publish failed for room %s", wid, exc_info=True)
    finally:
        if client is not None:
            try:
                client.close()
            except Exception:
                pass


def publish_once(prev_wids: set[str]) -> set[str]:
    """One heartbeat: publish presence for every current agent, and a removal
    for any that disappeared since last time. Returns the current agent wid set."""
    rooms = _agent_rooms()
    current = set(rooms)
    for wid in prev_wids - current:  # agents that went away
        _send(wid, None)
        _clocks.pop(wid, None)
    for wid, info in rooms.items():
        _send(wid, _agent_state(info["name"], info["status"]))
    return current


def start() -> None:
    """Launch the presence publisher in a daemon thread (no-op when disabled)."""
    if not (config.COLLAB_PRESENCE and config.TERMINALS_ENABLED):
        return

    def _loop() -> None:
        prev: set[str] = set()
        while True:
            try:
                prev = publish_once(prev)
            except Exception:
                log.exception("collab: publish_once failed")
            time.sleep(HEARTBEAT_SECONDS)

    threading.Thread(target=_loop, name="chela-collab", daemon=True).start()
    log.info("Collab presence publisher enabled (relay=%s, every %.0fs)",
             config.COLLAB_RELAY, HEARTBEAT_SECONDS)
