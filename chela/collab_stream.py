"""Owner-side ttyd→relay terminal bridge (auth-plane spike — CHUNK 1: PLAINTEXT).

Connects to a wid's local ttyd as a second ``tty``-subprotocol client (the same
upstream hop app.py ``term_ws`` opens), but — unlike term_ws, which pumps raw
bytes because the *browser* owns the protocol — this bridge PARSES ttyd frames
itself and pumps only terminal OUTPUT into a per-share relay room, so a browser
at ``/j/<room>`` (served by the relay Worker) can watch the live session
read-only. On a joiner's CTL *hello* it CYCLES its ttyd connection so tmux
re-emits its own full-state attach repaint (the keyframe), so a late or
reconnecting join sees a correctly-initialised screen even though the relay holds
NO history. (A ``tmux capture-pane`` snapshot was tried first but captures cell
content only — no alt-screen/scroll-region/cursor state — so an alt-screen TUI's
absolute-cursor updates smeared onto it; the real attach repaint carries the state.)

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
  begins ``\\x1b[?1049h\\x1b[2J`` (alt-screen enter + clear + full redraw). This IS
  the keyframe — on a cold join we re-attach ttyd to make tmux emit it again, and
  relay it. The owner's separate ttyd client and the window size are untouched.

Relay frames are END-TO-END ENCRYPTED (CHUNK 2). The relay is a dumb opaque
forwarder that rebroadcasts each frame to the OTHER sockets in the room — it
never sees plaintext. Every frame is an e2e envelope (see chela/e2e.py):
``ver ∥ type ∥ seq ∥ AES-256-GCM ciphertext``. The owner mints a 16-byte pairing
secret (shown as base32); the joiner pastes it; both HKDF to two per-direction
keys. The bridge is the HOST peer:
    host→joiner (key h2j): T_OUTPUT = terminal bytes; T_META = {"cols","rows"} JSON;
                           T_CTL = {"t":"ended"} (share stopped)
    joiner→host (key j2h): T_CTL = {"t":"hello",…} (cold-join); T_INPUT = keystrokes
  On a hello we send T_META (dims), then cycle ttyd so its attach repaint (the
  keyframe) reaches the joiner via the OUTPUT pump. A wrong pairing code fails the
  first GCM tag → we log and drop, never emit garbage.

🔐 Typing is GATED on the host (CMX-403, docs/SHARE_SANDBOX.md) — never in the relay,
which cannot read a frame. A share is VIEW ONLY unless all of these hold, checked on
every decrypted T_INPUT (``Bridge._input_refusal``):
  * the ``share_typing`` setting is on (``config.share_typing_enabled``, read per frame);
  * the share was created with typing allowed; and
  * the window verifies LIVE as a sandboxed session (``share_sandbox.check_share_session``,
    re-run at least every SANDBOX_RECHECK_INTERVAL — never trusted from share creation).
The one exception is the trusted-peer UNSANDBOXED override: an explicit, per-share,
time-boxed grant bound to ONE joiner stream id, audited in the event log, that skips only
the sandbox check (the setting still gates it). Refused input is dropped and the guest
gets one rate-limited T_CTL notice.

🕶️ A typing share's OUTPUT is gated too (CMX-416, ``Bridge._output_refusal``): it claimed
to show a sandbox, so every ttyd OUTPUT frame is held until the pane still verifies — the
pane's identity (tmux pane pid + launcher shape, read per frame) must be the one first
seen, and the full container check must pass (re-run every SANDBOX_RECHECK_INTERVAL). The
first failed or UNKNOWN verdict drops that frame and ends the share with a reason the guest
sees. View-only shares never claimed a sandbox and stream unchanged.

Allowed input is still capped by a token bucket. A
flood of hellos can't spam ttyd reattaches (rate-limited by REATTACH_DEBOUNCE).

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

from chela import collab, config, e2e, event_log, share_sandbox

log = logging.getLogger(__name__)

# ttyd command bytes (see module docstring).
OUTPUT = 0x30            # '0'  server→client: terminal output
SET_WINDOW_TITLE = 0x31  # '1'
SET_PREFERENCES = 0x32   # '2'

RESIZE_POLL_INTERVAL = 0.5    # s — floor between source-window size polls (resize watch)
RECONNECT_DELAY = 1.5         # s — reconnect backoff on a ttyd/relay error
TTYD_RECV_TIMEOUT = 0.4       # s — ttyd read poll; also bounds cold-join reattach latency
# Cold-join keyframe = tmux's OWN full-state attach repaint. A joiner hello asks the
# ttyd pump to cycle its connection so tmux re-emits `\x1b[?1049h…` (alt-screen enter +
# scroll-region + cursor + content) — the correct frame for an alt-screen TUI, which a
# capture-pane snapshot (cell content only, no terminal state) cannot reconstruct and
# which smeared Claude Code's absolute-cursor updates. Honored at most once per
# REATTACH_DEBOUNCE, so a burst of joins coalesces into a single repaint.
REATTACH_DEBOUNCE = 1.5       # s
# Input token bucket: a joiner may burst up to INPUT_BURST_BYTES, refilling at
# INPUT_RATE_BPS bytes/s — enough for fast typing and reasonable pastes, a ceiling
# against a flood. Oversized single frames are dropped outright.
INPUT_RATE_BPS = 4096
INPUT_BURST_BYTES = 8192
INPUT_MAX_FRAME = 4096
# Fail-closed invariant "no share outlives its session": if the wid vanishes from
# the ttyd port map (window died / supervisor reaped ttyd) for longer than this,
# the bridge STOPS and revokes rather than reconnect-looping into a zombie that
# keeps a dead — or worse, recycled — session nominally "shared". A brief absence
# (transient discovery hiccup, respawn) under the grace is tolerated as a blip.
DEATH_GRACE = 8.0             # s the wid may be absent before we fail closed
# 🔐 Typing gate (CMX-403). The sandbox verdict is re-read from the LIVE process tree +
# `docker inspect` at least this often while a guest types, so a window whose process is
# swapped after the share was created stops accepting input within this interval.
SANDBOX_RECHECK_INTERVAL = 2.0  # s
# At most one "view only" notice per this interval, however fast the guest types.
VIEW_ONLY_NOTICE_INTERVAL = 10.0  # s

# What the guest is told when a typing share ends because its pane stopped verifying.
SANDBOX_LOST_REASON = "the session stopped being a verified sandbox"

# Share access modes, as reported to the dashboard (share pill 👁 / ⌨ / UNSANDBOXED).
MODE_VIEW = "view"
MODE_TYPING = "typing"            # typing allowed into a verified sandboxed session
MODE_UNSANDBOXED = "unsandboxed"  # trusted-peer override, time-boxed, one joiner


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


class Bridge:
    """One ttyd↔relay pump for a single wid. Two sockets: the local ttyd (we read
    its OUTPUT) and the relay room (we publish DATA, and read CTL hellos). Sends
    to the relay come from both the pump thread (DATA) and the control thread
    (keyframes), so relay writes are serialised under a lock."""

    def __init__(self, wid: str, secret: bytes | None = None, on_revoke=None, *,
                 allow_typing: bool = False, clock=time.monotonic, wallclock=time.time) -> None:
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
        # Cold-join keyframe: a hello sets _reattach_req; the ttyd pump cycles its
        # connection (rate-limited by REATTACH_DEBOUNCE via _last_reattach) so tmux
        # re-emits a full-state attach repaint. _last_reattach=0 lets the first join
        # reattach immediately; a burst then coalesces into one repaint.
        self._reattach_req = threading.Event()
        self._last_reattach = 0.0
        # Last source grid we told joiners about, for the resize watch (below). None
        # until the first T_META; _maybe_resize resends on any change so the SPA's
        # xterm grid stays in lockstep with the live, reflowed OUTPUT stream.
        self._last_dims: tuple[int, int] | None = None
        self._threads: list[threading.Thread] = []
        # Called once when the bridge fails closed on session death, so the caller
        # can revoke the share (app.py: pop _SHARED[wid]). None in standalone use.
        self._on_revoke = on_revoke
        # The live ttyd socket (owned by the output-pump thread, read by the control
        # thread to forward INPUT that passed the typing gate). A token bucket caps
        # forwarded input bytes/sec so no joiner can flood the pty.
        self._ttyd = None
        self._ttyd_lock = threading.Lock()
        self._input_tokens = float(INPUT_BURST_BYTES)
        self._input_tokens_ts = time.monotonic()
        # 🔐 Typing gate (see module docstring). `allow_typing` is what the share was
        # created with; it is necessary, never sufficient. `_clock` is injectable so the
        # expiry/re-check guards run on a fake clock.
        self.allow_typing = bool(allow_typing)
        self._clock = clock
        self._wallclock = wallclock
        self._policy_lock = threading.Lock()
        self._sandbox_verdict: tuple[bool, str] = (False, "not checked yet")
        self._sandbox_checked_at: float | None = None
        self._last_notice = float("-inf")
        # 🕶️ Output gate (CMX-416): the pane identity bound on the first verified frame,
        # and why the session stopped verifying (set once; the share then ends).
        self._output_identity: tuple | None = None
        self._sandbox_lost: str | None = None
        # Trusted-peer UNSANDBOXED override: None, or {"until": monotonic deadline,
        # "joiner": bound stream id (None until the first hello/input), "audit": the
        # granted-event payload}. Guarded by _policy_lock.
        self._override: dict | None = None

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

    # --- typing gate (CMX-403) ----------------------------------------------
    def grant_unsandboxed(self, *, granted_by: str, window: str, ttl_s: float) -> dict:
        """Arm the trusted-peer UNSANDBOXED override for ``ttl_s`` seconds, bound to the
        first joiner that says hello (or types) after this, and audit the grant. The
        caller (app.py) has already checked the typed window-name confirmation."""
        started = self._wallclock()
        audit = {"wid": self.wid, "window": window, "granted_by": granted_by,
                 "started_at": started, "expires_at": started + ttl_s}
        with self._policy_lock:
            self._override = {"until": self._clock() + ttl_s, "joiner": None, "audit": audit}
        event_log.append("share.unsandboxed_granted",
                         f"UNSANDBOXED typing granted on {window} ({self.wid}) by {granted_by}",
                         audit, wid=self.wid)
        return audit

    def revoke_unsandboxed(self, reason: str, *, event: str = "share.unsandboxed_revoked") -> bool:
        """End the override now (kill switch / share stop / expiry). Audited; True if one
        was active."""
        with self._policy_lock:
            ov, self._override = self._override, None
        if ov is None:
            return False
        payload = dict(ov["audit"], revoked_at=self._wallclock(), reason=reason,
                       joiner=ov["joiner"].hex() if ov["joiner"] else None)
        event_log.append(event, f"UNSANDBOXED typing ended on {payload['window']} "
                         f"({self.wid}): {reason}", payload, wid=self.wid)
        return True

    def _expire_override_if_due(self) -> None:
        with self._policy_lock:
            due = self._override is not None and self._clock() >= self._override["until"]
        if due and self.revoke_unsandboxed("expired", event="share.unsandboxed_expired"):
            self._notice("Full access expired — this share is view only now.", force=True)

    def mode(self) -> str:
        self._expire_override_if_due()
        with self._policy_lock:
            if self._override is not None:
                return MODE_UNSANDBOXED
        return MODE_TYPING if self.allow_typing else MODE_VIEW

    def state(self) -> dict:
        """What the dashboard shows: the mode, and the override's wall-clock expiry."""
        m = self.mode()
        with self._policy_lock:
            exp = self._override["audit"]["expires_at"] if self._override else None
        return {"mode": m, "expires_at": exp}

    def set_mode(self, new: str, *, changed_by: str, window: str | None = None,
                 ttl_s: float | None = None) -> dict:
        """Change a LIVE share's access in place (CMX-421) — same secret, room, link and
        pairing code, so a guest already joined keeps the connection and the next input
        frame is judged under the new mode. The caller (app.py) has already applied the
        creation gates for an upgrade; a downgrade needs none. Audited as
        ``share.mode_changed``; an upgrade to UNSANDBOXED also arms the override (which
        writes ``share.unsandboxed_granted`` and binds the first guest to type)."""
        if new not in (MODE_VIEW, MODE_TYPING, MODE_UNSANDBOXED):
            raise ValueError(f"unknown share mode: {new}")
        old = self.mode()
        if new == old:
            return {"from": old, "to": new, "changed": False}
        if old == MODE_UNSANDBOXED:
            self.revoke_unsandboxed("mode changed to " + new)
        with self._policy_lock:
            self.allow_typing = new == MODE_TYPING
            self._sandbox_checked_at = None   # an upgrade to typing re-verifies on the next frame
        if new == MODE_UNSANDBOXED:
            self.grant_unsandboxed(granted_by=changed_by, window=window or self.wid,
                                   ttl_s=float(ttl_s or 0))
        event_log.append("share.mode_changed",
                         f"share mode {old} → {new} on {window or self.wid} ({self.wid}) by {changed_by}",
                         {"wid": self.wid, "window": window, "from": old, "to": new, "by": changed_by},
                         wid=self.wid)
        self._notice("View only now." if new == MODE_VIEW else "You can type now.", force=True)
        return {"from": old, "to": new, "changed": True}

    def _bind_joiner(self, stream_id: bytes) -> None:
        with self._policy_lock:
            if self._override is not None and self._override["joiner"] is None:
                self._override["joiner"] = stream_id

    def _sandbox_ok(self) -> tuple[bool, str]:
        """The LIVE sandbox verdict, re-checked at least every SANDBOX_RECHECK_INTERVAL."""
        now = self._clock()
        if self._sandbox_checked_at is None or now - self._sandbox_checked_at >= SANDBOX_RECHECK_INTERVAL:
            try:
                self._sandbox_verdict = share_sandbox.check_share_session(self.wid)
            except Exception:  # noqa: BLE001 — fail closed
                self._sandbox_verdict = (False, "the sandbox could not be verified")
            self._sandbox_checked_at = now
        return self._sandbox_verdict

    def _output_refusal(self) -> str | None:
        """None when the next OUTPUT frame may reach the guest; otherwise why not. Only a
        share created with typing allowed is gated — it claimed to show a verified sandbox.
        Every call re-reads the pane's identity (cheap: one tmux query + /proc), so a
        process swapped into the pane is caught before its first frame is relayed: tmux
        records the new pane pid before the new process can write anything. The full
        container check rides ``_sandbox_ok`` (every SANDBOX_RECHECK_INTERVAL). Any
        exception or unreadable state is a refusal — unknown never reads as OK."""
        if not self.allow_typing:
            return None
        if self._sandbox_lost is not None:
            return self._sandbox_lost
        try:
            ident = share_sandbox.pane_identity(self.wid)
        except Exception:  # noqa: BLE001 — fail closed
            ident = "the sandbox could not be verified"
        if isinstance(ident, str):
            return ident
        if self._output_identity is None:
            self._output_identity = ident
        elif ident != self._output_identity:
            return "the pane's process changed"
        ok, why = self._sandbox_ok()
        return None if ok else (why or "the sandbox could not be verified")

    def _relay_output(self, payload: bytes) -> bool:
        """Relay one ttyd OUTPUT payload, unless the output gate refuses — then the frame
        is DROPPED and the share ends. False means the bridge has stopped."""
        why = self._output_refusal()
        if why is not None:
            self._end_unverified(why)
            return False
        self._seal_send(e2e.T_OUTPUT, payload)
        return True

    def _end_unverified(self, why: str) -> None:
        """A typing share's pane stopped verifying as a sandbox: no further frame, then
        end the share with a reason the guest sees. Idempotent."""
        if self._sandbox_lost is not None and self._stop.is_set():
            return
        self._sandbox_lost = why
        log.warning("collab_stream: %s stopped verifying as a sandbox (%s) — ending the share",
                    self.wid, why)
        event_log.append("share.sandbox_lost",
                         f"share of {self.wid} ended: the session stopped being a verified "
                         f"sandbox ({why})", {"wid": self.wid, "reason": why}, wid=self.wid)
        self._fail_closed(f"sandbox lost: {why}", guest_reason=SANDBOX_LOST_REASON)

    def _input_refusal(self, stream_id: bytes) -> str | None:
        """None when this joiner's keystrokes may reach the pane; otherwise the notice to
        send them. Order matters: the setting gates EVERYTHING, the override included."""
        if not config.share_typing_enabled():
            return "View only — typing is turned off on the host."
        self._expire_override_if_due()
        with self._policy_lock:
            ov = self._override
            if ov is not None:
                if ov["joiner"] is None:
                    ov["joiner"] = stream_id
                if ov["joiner"] == stream_id:
                    return None
                return "View only — typing is limited to one paired guest."
        if not self.allow_typing:
            return "View only — this share does not allow typing."
        if self._sandbox_lost is not None:
            return "View only — " + SANDBOX_LOST_REASON + "."
        ok, _why = self._sandbox_ok()
        return None if ok else "View only — the host could not verify the sandboxed session."

    def _notice(self, msg: str, *, force: bool = False) -> None:
        """One encrypted T_CTL notice to the guest, rate-limited to one per
        VIEW_ONLY_NOTICE_INTERVAL (``force`` for one-off state changes like expiry)."""
        now = self._clock()
        if not force and now - self._last_notice < VIEW_ONLY_NOTICE_INTERVAL:
            return
        self._last_notice = now
        self._seal_send(e2e.T_CTL, json.dumps({"t": "notice", "msg": msg}).encode("utf-8"))

    # --- input forwarding --------------------------------------------------
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
        (b'0' + bytes). Drops oversized or rate-exceeding frames; never blocks the
        control loop."""
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

    def _send_meta(self) -> None:
        """Send the current grid dims (T_META) so the joiner sizes its xterm. The
        visual keyframe rides the OUTPUT stream — either the live app output or, on a
        cold join, a fresh tmux attach repaint (_request_reattach)."""
        cols, rows = _window_dims(self.wid)
        self._last_dims = (cols, rows)   # keep the resize watch in lockstep
        self._seal_send(e2e.T_META, json.dumps({"cols": cols, "rows": rows}).encode("utf-8"))

    def _request_reattach(self) -> None:
        """Ask the ttyd pump to cycle its connection so tmux re-emits a full-state
        attach repaint — the cold-join keyframe. The pump enforces REATTACH_DEBOUNCE
        (coalescing a burst of joins), so this setter is a cheap idempotent signal."""
        self._reattach_req.set()

    def _maybe_resize(self) -> None:
        """Poll the source window size; on a change, resend T_META so the joiner's
        xterm grid follows the live, reflowed OUTPUT stream (the app repaints itself
        on SIGWINCH; the joiner just needs the new dims). ttyd reflows output to the
        live PTY but sends no structured size, and the joiner learns the grid ONLY
        from T_META — so this is the sole grid-lockstep mechanism (no window pin)."""
        dims = _window_dims(self.wid)
        if self._last_dims is None:
            self._last_dims = dims
            return
        if dims != self._last_dims:
            log.info("collab_stream: %s source grid %s -> %s — resending T_META",
                     self.wid, self._last_dims, dims)
            self._send_meta()   # sets _last_dims

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
            reattach = False   # was this disconnect an intentional cold-join reattach?
            try:
                while not self._stop.is_set():
                    msg = up.receive(timeout=TTYD_RECV_TIMEOUT)
                    now = time.monotonic()
                    # Cold-join keyframe: a hello asked us to cycle the connection so
                    # tmux re-emits its attach repaint. Rate-limited so a burst of
                    # joins coalesces into one repaint.
                    if self._reattach_req.is_set() and (now - self._last_reattach) >= REATTACH_DEBOUNCE:
                        self._reattach_req.clear()
                        self._last_reattach = now
                        reattach = True
                        break   # → finally closes up → reconnect → fresh attach repaint
                    # Watch for a source-window resize (cheap tmux size poll, floored
                    # at RESIZE_POLL_INTERVAL) even mid-output, so a joiner mid-session
                    # isn't left rendering new-size bytes into a stale grid.
                    if now >= next_resize_check:
                        next_resize_check = now + RESIZE_POLL_INTERVAL
                        self._maybe_resize()
                    if msg is None:
                        # idle receive timeout, NOT a close — stay connected (a real
                        # ttyd close raises → caught below → reconnect). A typing share
                        # still re-verifies while idle, so a swapped pane ends the share
                        # even before it prints anything.
                        why = self._output_refusal()
                        if why is not None:
                            self._end_unverified(why)
                            return
                        continue
                    if isinstance(msg, str):
                        msg = msg.encode("utf-8", "replace")
                    if not msg:
                        continue
                    if msg[0] == OUTPUT and not self._relay_output(bytes(msg[1:])):
                        return   # 🕶️ gate refused: frame dropped, share ended
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
            if not reattach:
                time.sleep(RECONNECT_DELAY)   # error backoff; skip it for a wanted reattach

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
            # On (re)connect, resync any already-open joiner with the current grid.
            # (Genuine reconnects only now — idle no longer drops the socket.)
            self._send_meta()
            try:
                while not self._stop.is_set():
                    msg = self._relay.receive(timeout=1.0)
                    self._expire_override_if_due()   # revert to view-only on time, idle or not
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
        # Presence rides the SAME room but a different (symmetric k_pres) key, so the
        # bridge can't decrypt it — skip by the cleartext type byte BEFORE attempting
        # a decrypt, or every peer cursor would look like a GCM-tag failure and spam
        # the auth-warn log. Presence is strictly peer-to-peer; the bridge ignores it.
        if len(msg) >= 2 and msg[1] == e2e.T_PRESENCE:
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
        # The sender's stream id — authenticated: the header is GCM additional data.
        sender = bytes(msg[2:2 + e2e.STREAM_ID_LEN])
        if typ == e2e.T_INPUT:
            # 🔐 The typing gate — HOST-side, per frame (see _input_refusal).
            refusal = self._input_refusal(sender)
            if refusal is not None:
                self._notice(refusal)
                return
            self._forward_input(bytes(pt))
            return
        if typ == e2e.T_CTL:
            try:
                obj = json.loads(pt.decode("utf-8", "replace"))
            except Exception:
                return
            if obj.get("t") == "hello":
                self._bind_joiner(sender)   # an armed override binds to the first joiner
                # Cold join: size the joiner (T_META), then cycle ttyd so tmux paints
                # it a correct full-state repaint.
                self._send_meta()
                self._request_reattach()

    # --- lifecycle ---------------------------------------------------------
    def start(self) -> "Bridge":
        for target in (self._pump_relay_control, self._pump_ttyd_to_relay):
            t = threading.Thread(target=target, name=f"collab-stream-{self.wid}", daemon=True)
            t.start()
            self._threads.append(t)
        log.info("collab_stream: bridge up for %s → room %s", self.wid, self.room)
        return self

    def _fail_closed(self, reason: str, *, guest_reason: str | None = None) -> None:
        """Session died — stop the bridge and fire the revoke hook exactly once,
        so the share can't outlive the terminal it points at."""
        log.info("collab_stream: bridge for %s failing closed (%s) — revoking", self.wid, reason)
        self.stop(guest_reason=guest_reason)
        cb, self._on_revoke = self._on_revoke, None
        if cb:
            try:
                cb(self.wid)
            except Exception:
                log.exception("collab_stream: on_revoke hook failed for %s", self.wid)

    def stop(self, *, guest_reason: str | None = None) -> None:
        # The share is going away, so an armed UNSANDBOXED override ends with it — audited
        # (the #btn-shares kill switch lands here via app.py _revoke_share).
        self.revoke_unsandboxed("share stopped")
        # Proactively tell connected joiners the share is over (encrypted, so only
        # paired joiners read it) BEFORE tearing the socket down — they show a clean
        # "ended" state instead of hanging. Best-effort: a joiner that misses it
        # (relay mid-reconnect) falls back to the SPA's pairing timeout. The relay
        # holds no history, so nothing lingers for a later joiner to decrypt.
        try:
            ended = {"t": "ended", "reason": guest_reason} if guest_reason else {"t": "ended"}
            self._seal_send(e2e.T_CTL, json.dumps(ended).encode("utf-8"))
            time.sleep(0.15)   # let the frame flush to the relay before we tear the socket down
        except Exception:
            pass
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


def start_bridge(wid: str, secret: bytes | None = None, on_revoke=None, *,
                 allow_typing: bool = False, unsandboxed: dict | None = None) -> str | None:
    """Start a bridge for a shared wid and return its base32 pairing code (or the
    existing bridge's code if already running). on_revoke(wid) fires if the bridge
    fails closed on session death — app.py passes a hook that pops _SHARED[wid] so
    the share is revoked automatically (the dashboard reaper is the belt-and-braces
    complement: reconcile _SHARED against the live agent/port map each poll).

    ``allow_typing`` / ``unsandboxed`` (``{"granted_by", "window", "ttl_s"}``) set the
    share's access policy (CMX-403); both only take effect on a NEW bridge — a running
    share changes mode through ``set_share_mode`` (CMX-421), never by re-minting."""
    if not config.COLLAB_RELAY:
        return None
    with _bridges_lock:
        if wid in _bridges:
            return _bridges[wid].pairing_code
        b = Bridge(wid, secret=secret, on_revoke=on_revoke, allow_typing=allow_typing)
        if unsandboxed:
            b.grant_unsandboxed(**unsandboxed)
        b.start()
        _bridges[wid] = b
        return b.pairing_code


def share_state(wid: str) -> dict | None:
    """``{"mode", "expires_at"}`` of a running share, or None."""
    b = _bridges.get(wid)
    return b.state() if b else None


def set_share_mode(wid: str, mode: str, **kw) -> dict | None:
    """Switch a running share's mode in place (``Bridge.set_mode``), or None if there is
    no bridge for ``wid``."""
    b = _bridges.get(wid)
    return b.set_mode(mode, **kw) if b else None


def stop_bridge(wid: str) -> None:
    with _bridges_lock:
        b = _bridges.pop(wid, None)
    if b:
        b.stop()


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
