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

Nonce (96-bit, deterministic — never random, never reused for a key): 4 reserved
  zero bytes ∥ 8-byte little-endian per-key monotonic `seq`. A new share = new
  secret = new keys, so the counters reset safely.

Envelope (binary, little-endian): ver(1) ∥ type(1) ∥ seq(8) ∥ ciphertext, where
  ciphertext is AES-256-GCM output with the 128-bit tag appended (the library
  default). GCM AAD = ver(1) ∥ type(1) ∥ seq(8) ∥ utf8(room) — so header
  tampering, cross-room replay, and type confusion ALL fail the tag.

Receiver enforces strictly-increasing `seq` per direction: replays and reorders
  are dropped (gaps are allowed — a late joiner just starts from the current seq).
  A wrong pairing code derives wrong keys → the first frame fails the GCM tag →
  we raise AuthError ("wrong code"), never emit garbage.
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
VER = 1
# Frame types. Non-zero so a zero-initialised buffer never looks like a valid type.
T_OUTPUT = 1     # terminal output bytes  (host→joiner)
T_INPUT = 2      # terminal input bytes   (joiner→host; P1.5 write-grant)
T_META = 3       # JSON grid/title        (host→joiner)
T_PRESENCE = 4   # JSON presence          (either; P2)
T_CTL = 5        # JSON control, e.g. hello (joiner→host)

SALT = b"chela-collab-e2e-v1"   # fixed HKDF salt, identical both sides
KEY_LEN = 32                    # AES-256
NONCE_LEN = 12                  # 96-bit GCM nonce
_RESERVED = b"\x00\x00\x00\x00"  # 4 reserved nonce bytes
HEADER_LEN = 10                 # ver(1)+type(1)+seq(8)
SECRET_LEN = 16                 # pairing secret bytes


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


def _nonce(seq: int) -> bytes:
    return _RESERVED + seq.to_bytes(8, "little")


# --- session (one per peer): send on one key, receive on the other ------------
class Session:
    """Seals/opens envelopes for one endpoint. `role` picks which direction key is
    used for sending vs receiving:
        host   → send h2j, recv j2h
        joiner → send j2h, recv h2j
    Not thread-safe for concurrent seal() (the send seq is mutable state); the
    caller serialises seal+send so wire order matches seq order (the receiver
    rejects out-of-order seqs)."""

    def __init__(self, secret: bytes, room: str, *, role: str) -> None:
        if role not in ("host", "joiner"):
            raise ValueError("role must be 'host' or 'joiner'")
        k_h2j, k_j2h = derive_keys(secret, room)
        send_key, recv_key = (k_h2j, k_j2h) if role == "host" else (k_j2h, k_h2j)
        self.room = room
        self._room_aad = room.encode("utf-8")
        self._send = AESGCM(send_key)
        self._recv = AESGCM(recv_key)
        self._send_seq = 0
        self._recv_last = -1   # strictly-increasing gate; first accepted seq is >= 0

    def seal(self, typ: int, plaintext: bytes) -> bytes:
        seq = self._send_seq
        self._send_seq += 1
        header = bytes([VER, typ]) + seq.to_bytes(8, "little")
        ct = self._send.encrypt(_nonce(seq), plaintext, header + self._room_aad)
        return header + ct

    def open(self, envelope: bytes) -> tuple[int, bytes]:
        if len(envelope) < HEADER_LEN:
            raise BadFrame("envelope shorter than header")
        if envelope[0] != VER:
            raise BadFrame(f"unknown version {envelope[0]}")
        typ = envelope[1]
        seq = int.from_bytes(envelope[2:HEADER_LEN], "little")
        # Reject replays/reorders BEFORE the (costly) AEAD open. seq is not yet
        # authenticated, but a forged seq can't advance our window: if the tag
        # fails we raise without touching _recv_last, and a genuine replay (seq
        # <= last) is dropped here cheaply.
        if seq <= self._recv_last:
            raise ReplayError(f"seq {seq} <= last {self._recv_last}")
        header = envelope[:HEADER_LEN]
        try:
            pt = self._recv.decrypt(_nonce(seq), envelope[HEADER_LEN:], header + self._room_aad)
        except InvalidTag as e:
            raise AuthError("GCM tag failed — wrong pairing code or tampered frame") from e
        self._recv_last = seq
        return typ, pt
