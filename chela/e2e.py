"""End-to-end crypto for the collaborative terminal share (auth-plane CHUNK 2).

The relay is a dumb public forwarder; it must never see plaintext. This module is
the SINGLE SOURCE OF TRUTH for the on-the-wire crypto contract — every constant
and byte layout here is mirrored exactly by the browser SPA's WebCrypto code
(chela/collab-relay/public/index.html) and locked by the Python↔JS interop vector
suite (tests/test_e2e_vectors.py). Change a byte here → regenerate vectors → update
the SPA, or the two sides silently stop agreeing.

Grounded in docs/AUTH_PLANE_SCOPE.md §5 (Crypto). We roll NOTHING custom: HKDF and
AES-GCM come from `cryptography` (Python) and WebCrypto SubtleCrypto (browser),
both vetted, natively interoperable.

CONTRACT
--------
Pairing secret: 16 random bytes (owner mints via `mint_secret`), shown to the
  joiner as base32 (`pairing_code`) and pasted back (`secret_from_code`).

Key derivation: HKDF-SHA256 with a fixed salt and a room-bound info string yields
  TWO independent AES-256 keys, one per direction, so the two senders can never
  collide on a nonce:
      k_h2j = HKDF(secret, salt=SALT, info="chela-v1|<room>|h2j", L=32)   host→joiner
      k_j2h = HKDF(secret, salt=SALT, info="chela-v1|<room>|j2h", L=32)   joiner→host

Stream id (P1.5 multi-joiner): a 4-byte per-sender id. The host uses all-zero
  (HOST_STREAM_ID); each joiner picks a random 4 bytes at session start. It rides
  in the envelope header AND is the top of the nonce (see below), so two joiners
  that both start their counters at 0 can never collide on a k_j2h nonce, and the
  host tracks seq PER stream id (a 2nd joiner's low seqs aren't rejected as the
  first's replays). It is NOT an identity/auth token — everyone paired shares the
  same keys (per-joiner keys are a parked P2/P3 item); it only makes concurrent
  senders on one direction key nonce-safe and independently seq-tracked.

Nonce (96-bit, deterministic — never random, never reused for a key): 4-byte
  stream id ∥ 8-byte little-endian per-(key,stream) monotonic `seq`. Equivalently
  the 12 bytes of the header AFTER ver+type. Unique per (key,stream,seq); a new
  share = new secret = new keys, so counters reset safely.

Envelope (binary, little-endian): ver(1) ∥ type(1) ∥ stream_id(4) ∥ seq(8) ∥
  ciphertext, where ciphertext is AES-256-GCM output with the 128-bit tag appended
  (the library default). GCM AAD = the full 14-byte header ∥ utf8(room) — so header
  tampering (including stream-id swaps), cross-room replay, and type confusion ALL
  fail the tag.

Receiver enforces strictly-increasing `seq` PER (direction key, stream id):
  replays and reorders are dropped (gaps are allowed — a late joiner just starts
  from the current seq; a fresh stream id starts from 0). A wrong pairing code
  derives wrong keys → the first frame fails the GCM tag → we raise AuthError
  ("wrong code"), never emit garbage.

Wire version 2 (P1.5). v1 had a 10-byte header (4 reserved zero nonce bytes, no
  stream id) and single-sender seq; it is not interoperable — a v1 frame hitting a
  v2 parser fails cleanly on the version byte. Both sides deploy together.
"""

from __future__ import annotations

import base64
import re
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# --- wire constants (mirrored byte-for-byte in the SPA) -----------------------
VER = 2
# Frame types. Non-zero so a zero-initialised buffer never looks like a valid type.
T_OUTPUT = 1     # terminal output bytes  (host→joiner)
T_INPUT = 2      # terminal input bytes   (joiner→host; P1.5 write-grant)
T_META = 3       # JSON grid/title        (host→joiner)
T_PRESENCE = 4   # JSON presence          (either; P2)
T_CTL = 5        # JSON control: hello (j→h), grant (h→j)

SALT = b"chela-collab-e2e-v1"   # fixed HKDF salt, identical both sides
KEY_LEN = 32                    # AES-256
NONCE_LEN = 12                  # 96-bit GCM nonce = stream_id(4) + seq(8)
STREAM_ID_LEN = 4               # per-sender nonce-prefix / seq-tracking id
HOST_STREAM_ID = b"\x00\x00\x00\x00"  # the single host sender; joiners are random
HEADER_LEN = 14                 # ver(1)+type(1)+stream_id(4)+seq(8)
SECRET_LEN = 16                 # pairing secret bytes
MAX_RECV_STREAMS = 64           # cap on tracked (stream_id → last_seq) entries


class E2EError(Exception):
    """Base for all envelope-processing failures."""


class AuthError(E2EError):
    """GCM tag verification failed — wrong key (wrong pairing code) or tampering."""


class ReplayError(E2EError):
    """seq did not strictly increase — a replay or reorder; dropped."""


class BadFrame(E2EError):
    """Malformed envelope (too short / unknown version)."""


# --- pairing code (base32) ----------------------------------------------------
def mint_secret() -> bytes:
    """A fresh 16-byte pairing secret (owner-side, per share)."""
    return secrets.token_bytes(SECRET_LEN)


def pairing_code(secret: bytes) -> str:
    """Display form: RFC4648 base32, uppercase, padding stripped (16 bytes → 26
    chars). The SPA's base32 decoder mirrors this exactly."""
    return base64.b32encode(secret).decode("ascii").rstrip("=")


def secret_from_code(code: str) -> bytes:
    """Parse a pasted pairing code back to the 16-byte secret. Tolerant of
    whitespace, case, and missing padding (re-padded to a multiple of 8)."""
    s = re.sub(r"\s+", "", code).upper().rstrip("=")
    s += "=" * ((-len(s)) % 8)
    try:
        secret = base64.b32decode(s)
    except Exception as e:  # noqa: BLE001 - normalise to our error type
        raise BadFrame(f"invalid pairing code: {e}") from e
    if len(secret) != SECRET_LEN:
        raise BadFrame(f"pairing code decodes to {len(secret)} bytes, expected {SECRET_LEN}")
    return secret


# --- key derivation -----------------------------------------------------------
def _hkdf(secret: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=SHA256(), length=KEY_LEN, salt=SALT, info=info).derive(secret)


def derive_keys(secret: bytes, room: str) -> tuple[bytes, bytes]:
    """(k_h2j, k_j2h) for a room. info is room-bound so a secret leaked from one
    room can't decrypt another."""
    return (
        _hkdf(secret, f"chela-v1|{room}|h2j".encode()),
        _hkdf(secret, f"chela-v1|{room}|j2h".encode()),
    )


def _nonce(stream_id: bytes, seq: int) -> bytes:
    return stream_id + seq.to_bytes(8, "little")


# --- session (one per peer): send on one key, receive on the other ------------
class Session:
    """Seals/opens envelopes for one endpoint. `role` picks which direction key is
    used for sending vs receiving:
        host   → send h2j, recv j2h
        joiner → send j2h, recv h2j
    `stream_id` is this sender's 4-byte nonce-prefix (host = all-zero; a joiner
    defaults to a random 4 bytes so concurrent joiners never collide on a k_j2h
    nonce). recv seq is tracked PER stream id, so multiple senders on the recv key
    (many joiners → the host) are each gated independently.

    Not thread-safe for concurrent seal() (the send seq is mutable state); the
    caller serialises seal+send so wire order matches seq order for THIS stream."""

    def __init__(self, secret: bytes, room: str, *, role: str,
                 stream_id: bytes | None = None) -> None:
        if role not in ("host", "joiner"):
            raise ValueError("role must be 'host' or 'joiner'")
        if stream_id is None:
            stream_id = HOST_STREAM_ID if role == "host" else secrets.token_bytes(STREAM_ID_LEN)
        if len(stream_id) != STREAM_ID_LEN:
            raise ValueError(f"stream_id must be {STREAM_ID_LEN} bytes")
        k_h2j, k_j2h = derive_keys(secret, room)
        send_key, recv_key = (k_h2j, k_j2h) if role == "host" else (k_j2h, k_h2j)
        self.room = room
        self.stream_id = stream_id
        self._room_aad = room.encode("utf-8")
        self._send = AESGCM(send_key)
        self._recv = AESGCM(recv_key)
        self._send_seq = 0
        # stream_id -> last accepted seq. First accepted seq for a stream is >= 0;
        # bounded so a churn of reconnecting joiners can't grow it without limit.
        self._recv_last: dict[bytes, int] = {}

    def seal(self, typ: int, plaintext: bytes) -> bytes:
        seq = self._send_seq
        self._send_seq += 1
        header = bytes([VER, typ]) + self.stream_id + seq.to_bytes(8, "little")
        ct = self._send.encrypt(header[2:HEADER_LEN], plaintext, header + self._room_aad)
        return header + ct

    def open(self, envelope: bytes) -> tuple[int, bytes]:
        if len(envelope) < HEADER_LEN:
            raise BadFrame("envelope shorter than header")
        if envelope[0] != VER:
            raise BadFrame(f"unknown version {envelope[0]}")
        typ = envelope[1]
        sid = bytes(envelope[2:2 + STREAM_ID_LEN])
        seq = int.from_bytes(envelope[2 + STREAM_ID_LEN:HEADER_LEN], "little")
        # Reject replays/reorders per stream BEFORE the (costly) AEAD open. seq/sid
        # aren't yet authenticated, but a forged pair can't advance our window: if
        # the tag fails we raise without touching _recv_last, and a genuine replay
        # (seq <= last for that stream) is dropped here cheaply.
        if seq <= self._recv_last.get(sid, -1):
            raise ReplayError(f"seq {seq} <= last for stream {sid.hex()}")
        header = envelope[:HEADER_LEN]
        try:
            pt = self._recv.decrypt(header[2:HEADER_LEN], envelope[HEADER_LEN:], header + self._room_aad)
        except InvalidTag as e:
            raise AuthError("GCM tag failed — wrong pairing code or tampered frame") from e
        if sid not in self._recv_last and len(self._recv_last) >= MAX_RECV_STREAMS:
            self._recv_last.pop(next(iter(self._recv_last)))  # evict oldest-inserted
        self._recv_last[sid] = seq
        return typ, pt
