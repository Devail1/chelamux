"""Owner-side ttyd→relay terminal bridge (auth-plane spike — CHUNK 1: PLAINTEXT).

Connects to a wid's local ttyd as a second ``tty``-subprotocol client (the same
upstream hop app.py ``term_ws`` opens), but — unlike term_ws, which pumps raw
bytes because the *browser* owns the protocol — this bridge PARSES ttyd frames
itself and pumps only terminal OUTPUT into a per-share relay room, so a browser
at ``/j/<room>`` (served by the relay Worker) can watch the live session
read-only. On a joiner's CTL *hello* it replies with a ``capture-pane`` keyframe,
so a late or reconnecting join sees the current screen even though the relay
holds NO history.

NO crypto yet — frames are plaintext. AES-GCM + Python/JS interop vectors are
CHUNK 2, added once this pipe is proven.

ttyd 1.7.7 wire protocol — verified empirically against ~/bin/ttyd's served JS
and a live handshake probe (see the spike report), NOT assumed:

  init (client→server, first message on open): raw UTF-8 JSON bytes, with NO
    command-byte prefix::
        {"AuthToken": "<token>", "columns": C, "rows": R}
    token is "" here — the chela ttyds run with no --credential.
  client→server commands (first byte, an ASCII digit):
    INPUT '0'+bytes · RESIZE_TERMINAL '1'+JSON · PAUSE '2' · RESUME '3'
  server→client commands (first byte, an ASCII digit):
    OUTPUT '0'+bytes · SET_WINDOW_TITLE '1'+str · SET_PREFERENCES '2'+JSON

  A fresh client connection triggers a FULL tmux repaint: the first OUTPUT frame
  begins ``\\x1b[?1049h\\x1b[2J`` (alt-screen enter + clear + full redraw). We rely
  on that for the bridge's own first paint; joiners get a capture-pane keyframe.

Relay frames are END-TO-END ENCRYPTED (CHUNK 2). The relay is a dumb opaque
forwarder that rebroadcasts each frame to the OTHER sockets in the room — it
never sees plaintext. Every frame is an e2e envelope (see chela/e2e.py):
``ver ∥ type ∥ seq ∥ AES-256-GCM ciphertext``. The owner mints a 16-byte pairing
secret (shown as base32); the joiner pastes it; both HKDF to two per-direction
keys. The bridge is the HOST peer:
    host→joiner (key h2j): T_OUTPUT = terminal bytes; T_META = {"cols","rows"} JSON
    joiner→host (key j2h): T_CTL   = {"t":"hello","cols","rows"} (request a keyframe)
  A keyframe = a T_META frame + a T_OUTPUT frame of
  ``\\x1b[2J\\x1b[3J\\x1b[H`` + ``tmux capture-pane -ep`` output. A wrong pairing
  code fails the first GCM tag → we log and drop, never emit garbage.

Read-only: the bridge NEVER sends INPUT to the pty. It DOES decrypt inbound CTL,
rate-limited so a flood of hellos can't spam capture-pane.

Runs standalone for the spike::  python -m chela.collab_stream <wid>
and exposes start_bridge(wid) / stop_bridge(wid) for app.py to call from the
share toggle (CHUNK 2 integration).
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time

from chela import collab, config, e2e

log = logging.getLogger(__name__)

# ttyd command bytes (see module docstring).
OUTPUT = 0x30            # '0'  server→client: terminal output
SET_WINDOW_TITLE = 0x31  # '1'
SET_PREFERENCES = 0x32   # '2'

# capture-pane keyframe: clear screen + clear scrollback + cursor home, so the
# joiner's xterm starts from a known blank state before we paint the snapshot.
_CLEAR = b"\x1b[2J\x1b[3J\x1b[H"

SNAPSHOT_MIN_INTERVAL = 0.5   # s — floor between capture-pane calls (rate limit)
RESIZE_POLL_INTERVAL = 0.5    # s — floor between source-window size polls (resize watch)
RECONNECT_DELAY = 1.5         # s — naive reconnect backoff (spike)
# Input token bucket (P1.5): a granted joiner may burst up to INPUT_BURST_BYTES,
# refilling at INPUT_RATE_BPS bytes/s — enough for fast typing and reasonable
# pastes, a ceiling against a flood. Oversized single frames are dropped outright.
INPUT_RATE_BPS = 4096
INPUT_BURST_BYTES = 8192
INPUT_MAX_FRAME = 4096
# Fail-closed invariant "no share outlives its session": if the wid vanishes from
# the ttyd port map (window died / supervisor reaped ttyd) for longer than this,
# the bridge STOPS and revokes rather than reconnect-looping into a zombie that
# keeps a dead — or worse, recycled — session nominally "shared". A brief absence
# (transient discovery hiccup, respawn) under the grace is tolerated as a blip.
DEATH_GRACE = 8.0             # s the wid may be absent before we fail closed


def _port_map() -> dict:
    """wid → local ttyd port, from the map agent-terminals.sh writes."""
    try:
        with open(config.CHELA_DIR / "agent_terminals.json", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _window_dims(wid: str) -> tuple[int, int]:
    """The wid tmux window's current size. We send this as the ttyd init size so
    a `window-size largest` session is NOT grown by our attaching (we never send
    anything bigger than what's already there), and it is also the grid the
    joiner must render at for the escape stream to line up."""
    try:
        out = subprocess.run(
            ["tmux", "display-message", "-p", "-t", wid, "#{window_width} #{window_height}"],
            capture_output=True, text=True, timeout=5,
        )
        c, r = out.stdout.split()
        return int(c), int(r)
    except Exception:
        return config.TERM_COLS, config.TERM_ROWS


def _snapshot(wid: str) -> bytes:
    """A current-screen keyframe: `tmux capture-pane -e` (SGR colours preserved,
    UTF-8 intact) of the visible pane — works on the alternate screen too (TUIs
    like Claude Code), which is exactly the cold-join case. Prefixed with a clear
    so it overwrites whatever the joiner had. Live frames heal any residual
    alt-screen/cursor drift on the next repaint."""
    try:
        out = subprocess.run(
            ["tmux", "capture-pane", "-ep", "-t", wid],
            capture_output=True, timeout=5,
        )
        return _CLEAR + out.stdout
    except Exception:
        return _CLEAR


class Bridge:
    """One ttyd↔relay pump for a single wid. Two sockets: the local ttyd (we read
    its OUTPUT) and the relay room (we publish DATA, and read CTL hellos). Sends
    to the relay come from both the pump thread (DATA) and the control thread
    (keyframes), so relay writes are serialised under a lock."""

    def __init__(self, wid: str, secret: bytes | None = None, on_revoke=None) -> None:
        self.wid = wid
        self.room = collab.room_id(wid) + "-tty"   # isolated from the presence room
        # E2E: owner-minted pairing secret → host session. The joiner pastes the
        # base32 code and derives the same keys for this same room string.
        self.secret = secret or e2e.mint_secret()
        self.pairing_code = e2e.pairing_code(self.secret)
        self._session = e2e.Session(self.secret, self.room, role="host")
        self._stop = threading.Event()
        self._relay = None
        self._relay_lock = threading.Lock()
        self._last_snapshot = 0.0
        # Cache the PLAINTEXT snapshot (the expensive capture-pane result), never
        # the sealed bytes — each send must re-seal with a fresh monotonic seq or
        # the joiner would reject the resend as a replay.
        self._cached_snapshot: tuple[int, int, bytes] | None = None  # (cols, rows, bytes)
        # Last source grid we told joiners about, for the resize watch (below). None
        # until the first keyframe; _maybe_resize resends on any change so the SPA's
        # xterm grid stays in lockstep with the live, reflowed OUTPUT stream.
        self._last_dims: tuple[int, int] | None = None
        self._threads: list[threading.Thread] = []
        # Called once when the bridge fails closed on session death, so the caller
        # can revoke the share (app.py: pop _SHARED[wid]). None in standalone use.
        self._on_revoke = on_revoke
        # P1.5 writable: the live ttyd socket (owned by the output-pump thread, read
        # by the control thread to forward INPUT), and the owner's write grant. The
        # grant defaults OFF — read-only by construction until an owner-only Flask
        # call flips it (set_write). A token bucket caps forwarded input bytes/sec so
        # a paired joiner can't flood the pty.
        self._ttyd = None
        self._ttyd_lock = threading.Lock()
        self._write = threading.Event()
        self._input_tokens = float(INPUT_BURST_BYTES)
        self._input_tokens_ts = time.monotonic()

    # --- relay send helpers ------------------------------------------------
    def _seal_send(self, typ: int, plaintext: bytes) -> None:
        """Seal + send under one lock so wire order == seq order (the receiver
        rejects out-of-order seqs). Both pump threads funnel through here."""
        with self._relay_lock:
            if self._relay is None:
                return
            try:
                self._relay.send(self._session.seal(typ, plaintext))
            except Exception:
                pass

    # --- P1.5 write grant --------------------------------------------------
    def _send_grant_state(self) -> None:
        """Tell joiners the current write state (h2j T_CTL). Sent on every grant
        flip AND after a hello, so a joiner that connects mid-share learns whether
        it may type without waiting for the next toggle."""
        self._seal_send(e2e.T_CTL, json.dumps({"t": "grant", "write": self._write.is_set()}).encode("utf-8"))

    def set_write(self, granted: bool) -> bool:
        """Owner grant/revoke of write access (per-share, tailnet-side). Flips the
        gate that _handle_relay checks before forwarding any INPUT to the pty, and
        broadcasts the new state to joiners. Returns the effective state."""
        if granted:
            self._write.set()
        else:
            self._write.clear()
        log.info("collab_stream: %s write %s", self.wid, "GRANTED" if granted else "revoked")
        self._send_grant_state()
        return self._write.is_set()

    def _allow_input(self, n: int) -> bool:
        """Token-bucket admission for n input bytes (called under no lock; only the
        control thread touches the bucket)."""
        now = time.monotonic()
        self._input_tokens = min(
            float(INPUT_BURST_BYTES),
            self._input_tokens + (now - self._input_tokens_ts) * INPUT_RATE_BPS,
        )
        self._input_tokens_ts = now
        if n > self._input_tokens:
            return False
        self._input_tokens -= n
        return True

    def _forward_input(self, data: bytes) -> None:
        """Forward joiner keystrokes to the local ttyd as a client INPUT frame
        (b'0' + bytes) — ONLY reached while write is granted. Drops oversized or
        rate-exceeding frames; never blocks the control loop."""
        if not data or len(data) > INPUT_MAX_FRAME or not self._allow_input(len(data)):
            return
        with self._ttyd_lock:
            ttyd = self._ttyd
        if ttyd is None:
            return
        try:
            ttyd.send(b"0" + data)   # ttyd client→server INPUT
        except Exception:
            pass

    def _send_keyframe(self, force: bool = False) -> None:
        """A T_META frame (grid dims) + a T_OUTPUT capture-pane snapshot, both
        freshly sealed. Rate-limited: capture-pane runs at most every
        SNAPSHOT_MIN_INTERVAL; within that window we re-seal the cached plaintext.
        `force` bypasses the cache — used on a source resize, where the cached
        snapshot is at the OLD grid and would paint garbled into the new one."""
        now = time.monotonic()
        if force or self._cached_snapshot is None or (now - self._last_snapshot) >= SNAPSHOT_MIN_INTERVAL:
            cols, rows = _window_dims(self.wid)
            self._cached_snapshot = (cols, rows, _snapshot(self.wid))
            self._last_snapshot = now
            self._last_dims = (cols, rows)   # keep the resize watch in lockstep
        cols, rows, snap = self._cached_snapshot
        self._seal_send(e2e.T_META, json.dumps({"cols": cols, "rows": rows}).encode("utf-8"))
        self._seal_send(e2e.T_OUTPUT, snap)

    def _maybe_resize(self) -> None:
        """Poll the source window size; if it changed, push a fresh keyframe so the
        joiner's xterm grid follows the reflowed OUTPUT stream. ttyd reflows output
        to the live PTY on resize but sends no structured size, and the joiner learns
        the grid ONLY from T_META — so on any change we resend T_META + a full
        repaint. The share also pins the window (app.py), so in practice this catches
        the pin's own initial resize and any transient before the grid settles."""
        dims = _window_dims(self.wid)
        if self._last_dims is None:
            self._last_dims = dims
            return
        if dims != self._last_dims:
            log.info("collab_stream: %s source grid %s -> %s — resending keyframe",
                     self.wid, self._last_dims, dims)
            self._send_keyframe(force=True)   # sets _last_dims

    # --- the two pumps -----------------------------------------------------
    def _pump_ttyd_to_relay(self) -> None:
        """Read ttyd frames, forward OUTPUT payloads to the relay as DATA. Title/
        prefs frames are dropped (the joiner SPA owns its own theme). Reconnects
        to ttyd until stopped; each fresh connect yields a full repaint we relay
        so already-present joiners refresh."""
        import simple_websocket
        gone_since = None
        while not self._stop.is_set():
            port = _port_map().get(self.wid)
            if not port:
                # Fail closed if the window has been gone past the grace window —
                # a dead/reaped session must not linger as a zombie share.
                now = time.monotonic()
                gone_since = gone_since or now
                if now - gone_since > DEATH_GRACE:
                    self._fail_closed("window gone from port map")
                    return
                time.sleep(RECONNECT_DELAY)
                continue
            gone_since = None
            try:
                cols, rows = _window_dims(self.wid)
                up = simple_websocket.Client(
                    f"ws://127.0.0.1:{port}/term/{self.wid}/ws",
                    subprotocols=["tty"], ping_interval=25,
                )
                # Synthesize the ttyd init handshake (raw JSON, no prefix).
                up.send(json.dumps({"AuthToken": "", "columns": cols, "rows": rows}).encode())
            except Exception:
                time.sleep(RECONNECT_DELAY)
                continue
            with self._ttyd_lock:
                self._ttyd = up   # publish for the control thread's INPUT forwarding
            next_resize_check = time.monotonic()
            try:
                while not self._stop.is_set():
                    msg = up.receive(timeout=1.0)
                    # Watch for a source-window resize (cheap tmux size poll, floored
                    # at RESIZE_POLL_INTERVAL) even mid-output, so a joiner mid-session
                    # isn't left rendering new-size bytes into a stale grid.
                    now = time.monotonic()
                    if now >= next_resize_check:
                        next_resize_check = now + RESIZE_POLL_INTERVAL
                        self._maybe_resize()
                    if msg is None:
                        continue   # idle receive timeout, NOT a close — stay connected
                                   # (a real ttyd close raises → caught below → reconnect)
                    if isinstance(msg, str):
                        msg = msg.encode("utf-8", "replace")
                    if not msg:
                        continue
                    if msg[0] == OUTPUT:
                        self._seal_send(e2e.T_OUTPUT, bytes(msg[1:]))
                    # SET_WINDOW_TITLE / SET_PREFERENCES intentionally ignored.
            except Exception:
                pass
            finally:
                with self._ttyd_lock:
                    self._ttyd = None   # no INPUT forwarding while disconnected
                try:
                    up.close()
                except Exception:
                    pass
            time.sleep(RECONNECT_DELAY)

    def _pump_relay_control(self) -> None:
        """Own the relay socket: (re)connect, read CTL hellos, answer with a
        keyframe. DATA frames from ourselves are never echoed back (the relay
        only forwards to OTHER sockets); other joiners' CTL is ignored unless it's
        a hello aimed at us."""
        import simple_websocket
        relay_base = config.COLLAB_RELAY
        while not self._stop.is_set():
            if not relay_base:
                log.warning("collab_stream: no CHELA_COLLAB_RELAY set; bridge idle")
                time.sleep(RECONNECT_DELAY)
                continue
            try:
                with self._relay_lock:
                    self._relay = simple_websocket.Client(
                        f"{relay_base}/room/{self.room}", ping_interval=25,
                    )
            except Exception:
                time.sleep(RECONNECT_DELAY)
                continue
            # On (re)connect, resync any already-open joiner: current grid + write
            # grant. (Genuine reconnects only now — idle no longer drops the socket.)
            self._send_keyframe()
            self._send_grant_state()
            try:
                while not self._stop.is_set():
                    msg = self._relay.receive(timeout=1.0)
                    if msg is None:
                        continue   # idle receive timeout, NOT a close — stay connected
                                   # (a real relay close raises → caught below → reconnect)
                    self._handle_relay(msg)
            except Exception:
                pass
            finally:
                with self._relay_lock:
                    try:
                        if self._relay:
                            self._relay.close()
                    except Exception:
                        pass
                    self._relay = None
            time.sleep(RECONNECT_DELAY)

    def _handle_relay(self, msg) -> None:
        if isinstance(msg, str):
            msg = msg.encode("utf-8", "replace")
        if not msg:
            return
        try:
            typ, pt = self._session.open(bytes(msg))
        except e2e.ReplayError:
            return  # dropped duplicate/reorder
        except e2e.AuthError:
            # Wrong pairing code or tampered frame — never emit garbage; log at a
            # low rate so a bad joiner can't flood the logs.
            now = time.monotonic()
            if now - getattr(self, "_last_auth_warn", 0) > 5:
                self._last_auth_warn = now
                log.warning("collab_stream: %s dropped a frame failing the GCM tag "
                            "(wrong pairing code or tampering)", self.wid)
            return
        except e2e.E2EError:
            return
        if typ == e2e.T_INPUT:
            # Read-only by construction: forward to the pty ONLY while the owner has
            # granted write. Ungranted input is silently dropped at this choke point
            # (never reaches ttyd/tmux) — the single server-side enforcement point.
            if self._write.is_set():
                self._forward_input(bytes(pt))
            return
        if typ == e2e.T_CTL:
            try:
                obj = json.loads(pt.decode("utf-8", "replace"))
            except Exception:
                return
            if obj.get("t") == "hello":
                self._send_keyframe()
                self._send_grant_state()   # late joiner learns the current write state

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> "Bridge":
        for target in (self._pump_relay_control, self._pump_ttyd_to_relay):
            t = threading.Thread(target=target, name=f"collab-stream-{self.wid}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("collab_stream: bridge up for %s → room %s", self.wid, self.room)
        return self

    def _fail_closed(self, reason: str) -> None:
        """Session died — stop the bridge and fire the revoke hook exactly once,
        so the share can't outlive the terminal it points at."""
        log.info("collab_stream: bridge for %s failing closed (%s) — revoking", self.wid, reason)
        self.stop()
        cb, self._on_revoke = self._on_revoke, None
        if cb:
            try:
                cb(self.wid)
            except Exception:
                log.exception("collab_stream: on_revoke hook failed for %s", self.wid)

    def stop(self) -> None:
        self._stop.set()
        with self._relay_lock:
            try:
                if self._relay:
                    self._relay.close()
            except Exception:
                pass
            self._relay = None
        _bridges.pop(self.wid, None)   # keep the registry consistent on any stop


# --- registry so app.py can start/stop bridges per shared wid (CHUNK 2) -------
_bridges: dict[str, Bridge] = {}
_bridges_lock = threading.Lock()


def start_bridge(wid: str, secret: bytes | None = None, on_revoke=None) -> str | None:
    """Start a bridge for a shared wid and return its base32 pairing code (or the
    existing bridge's code if already running). on_revoke(wid) fires if the bridge
    fails closed on session death — app.py passes a hook that pops _SHARED[wid] so
    the share is revoked automatically (the dashboard reaper is the belt-and-braces
    complement: reconcile _SHARED against the live agent/port map each poll)."""
    if not config.COLLAB_RELAY:
        return None
    with _bridges_lock:
        if wid in _bridges:
            return _bridges[wid].pairing_code
        b = Bridge(wid, secret=secret, on_revoke=on_revoke).start()
        _bridges[wid] = b
        return b.pairing_code


def stop_bridge(wid: str) -> None:
    with _bridges_lock:
        b = _bridges.pop(wid, None)
    if b:
        b.stop()


def set_write(wid: str, granted: bool) -> bool | None:
    """Owner grant/revoke of write for a shared wid (P1.5). Returns the effective
    state, or None if no bridge is running for the wid (nothing to grant)."""
    with _bridges_lock:
        b = _bridges.get(wid)
    return b.set_write(granted) if b else None


def join_url(wid: str) -> str:
    """The shareable read-only link, e.g. https://<relay>/j/<room>. Derived from
    the relay's wss:// URL (the SPA is served by the same Worker)."""
    base = config.COLLAB_RELAY.replace("wss://", "https://").replace("ws://", "http://")
    return f"{base}/j/{collab.room_id(wid) + '-tty'}"


def main() -> None:
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("usage: python -m chela.collab_stream <wid>", file=sys.stderr)
        raise SystemExit(2)
    wid = sys.argv[1]
    if not config.COLLAB_RELAY:
        print("CHELA_COLLAB_RELAY is empty — set it to your relay wss:// URL", file=sys.stderr)
        raise SystemExit(1)
    b = Bridge(wid).start()
    print(f"bridge up for {wid} → {config.COLLAB_RELAY}/room/{b.room}", flush=True)
    print(f"join link:    {join_url(wid)}", flush=True)
    print(f"pairing code: {b.pairing_code}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        b.stop()


if __name__ == "__main__":
    main()
