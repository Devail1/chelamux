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
import secrets
import threading
import time
import zlib

from chela import agent_manager, config, discovery

log = logging.getLogger(__name__)

# Presence heartbeat. Matches presence.js so agent and human pills converge on
# the same ~4s cadence for late joiners.
HEARTBEAT_SECONDS = 4.0

# We keep ONE socket open per room (see below) instead of connect-send-close per
# beat: that removes the flush race (no close to lose the frame to) and the
# per-agent handshake latency, so every room publishes within one fast pass and
# presence converges at the true 4s cadence with no pill flicker.
PING_INTERVAL = 20.0    # client-side keepalive so dead sockets are detected
DRAIN_TIMEOUT = 0.02    # non-blocking-ish poll when emptying the inbound queue

# Pill colour by live session status. Green = actively working, amber = blocked
# on input (needs attention), grey = idle at the prompt. Kept in sync with the
# dashboard's own _liveness() semantics.
_STATUS_COLOR = {"busy": "#3fb950", "waiting": "#d29922", "idle": "#7d8590"}
_DEFAULT_COLOR = "#7d8590"

# Relay route regex is [\w@.\-]+ and the DO keys rooms by this raw path segment,
# so the room id must avoid percent-encoding. presence.js applies the identical
# transform (see roomId() there) so both sides land in the same room.
_UNSAFE_ROOM_CHARS = re.compile(r"[^\w@.\-]")

# Per-instance room-namespace prefix, persisted so it's stable across restarts.
_INSTANCE_FILE = config.CHELA_DIR / "collab_id"
_instance_cache: str | None = None


def instance_id() -> str:
    """Stable, unguessable per-instance room-namespace prefix.

    Rooms would otherwise be keyed by the guessable tmux wid (``@9``), so on a
    shared default relay every chelamux instance would collide on the same rooms
    — and anyone could guess them. Prefixing with a random per-instance secret
    (persisted in ``~/.chela/collab_id``) namespaces them. This is namespacing,
    NOT a security boundary: presence frames are still unencrypted on the relay.
    Real end-to-end / capability tokens are a deliberate later step.
    """
    global _instance_cache
    if _instance_cache:
        return _instance_cache
    try:
        sid = _INSTANCE_FILE.read_text().strip()
    except (FileNotFoundError, OSError):
        sid = ""
    if not sid:
        config.CHELA_DIR.mkdir(parents=True, exist_ok=True)
        sid = secrets.token_hex(8)  # 16 hex chars — all relay-safe (\w)
        try:
            _INSTANCE_FILE.write_text(sid)
        except OSError:
            log.warning("collab: could not persist instance id to %s", _INSTANCE_FILE)
    _instance_cache = sid
    return sid


def room_id(wid: str) -> str:
    """Relay-safe, instance-namespaced room id for a tmux window id, e.g.
    ``a1b2c3d4e5f6a7b8-@11``. MUST match presence.js, which builds the identical
    string from the injected prefix — both sanitize ``<prefix>-<wid>`` to the
    relay's allowed charset ``[\\w@.\\-]``."""
    return _UNSAFE_ROOM_CHARS.sub("_", f"{instance_id()}-{wid or 'default'}")


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
# wid -> live simple_websocket.Client. Only the single publisher thread touches
# this map, so no lock is needed.
_sockets: dict[str, object] = {}


def _socket_for(wid: str):
    """Return a live publish socket for a room, opening one on first use."""
    client = _sockets.get(wid)
    if client is not None:
        return client
    from simple_websocket import Client
    url = f"{config.COLLAB_RELAY}/room/{room_id(wid)}"
    client = Client.connect(url, ping_interval=PING_INTERVAL)
    _sockets[wid] = client
    return client


def _drop_socket(wid: str) -> None:
    client = _sockets.pop(wid, None)
    if client is not None:
        try:
            client.close()
        except Exception:
            pass


def _drain(client) -> None:
    """Discard queued inbound frames. The relay echoes every browser's awareness
    and (25fps) cursor updates back to our socket; simple_websocket's reader
    thread queues them, so we empty that queue each beat to keep memory bounded.
    We only publish — inbound frames are not needed."""
    try:
        while client.receive(timeout=DRAIN_TIMEOUT) is not None:
            pass
    except Exception:
        pass  # socket trouble surfaces on the next send(), which drops it


def _send(wid: str, state: dict | None) -> None:
    """Publish one awareness frame over the room's persistent socket. On any
    socket error, drop it so the next beat transparently reconnects."""
    clock = _clocks.get(wid, 0) + 1
    _clocks[wid] = clock
    frame = encode_awareness_update(_client_id(wid), clock, state)
    try:
        client = _socket_for(wid)
        _drain(client)
        client.send(frame)
    except Exception:
        log.debug("collab: publish failed for room %s (will reconnect)", wid, exc_info=True)
        _drop_socket(wid)


def publish_once(prev_wids: set[str]) -> set[str]:
    """One heartbeat: publish presence for every current agent, and a removal
    for any that disappeared since last time. Returns the current agent wid set."""
    rooms = _agent_rooms()
    current = set(rooms)
    for wid in prev_wids - current:  # agents that went away
        _send(wid, None)             # removal frame over the still-open socket
        _drop_socket(wid)            # then close it
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
