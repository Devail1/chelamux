"""Python↔JS interop vector suite for the E2E terminal-share crypto (auth-plane
CHUNK 2 — THE key deliverable). Browser↔Python is the seam where interop bugs
hide, so we pin it: fixed fixtures encrypted by Python are decrypted by the real
browser module (chela/collab-relay/public/e2e.js, run under Node's WebCrypto) and
vice versa, and we assert byte-identical base32 + HKDF on both sides.

The Node half runs via tests/e2e_interop.mjs. If Node is unavailable the interop
tests skip (the pure-Python tests still run and are meaningful on their own).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from chela import e2e

# Synthetic room + secret — NEVER a real instance id (room ids used to embed the
# per-instance secret; they no longer do, but keep test fixtures obviously fake).
ROOM = "test-instance-@1-tty"
SECRET = bytes.fromhex("000102030405060708090a0b0c0d0e0f")  # fixed → reproducible vectors
WRONG_SECRET = bytes.fromhex("0f0e0d0c0b0a09080706050403020100")

# P1.5 multi-joiner: two distinct (fixed, reproducible) joiner stream ids, each
# sending T_INPUT from seq 0 — the collision case v1 could not represent.
STREAM_A = bytes.fromhex("aa0000a1")
STREAM_B = bytes.fromhex("bb0000b2")
INPUT_PLAINTEXTS = [
    [b"ls -la\r", b"\x03"],        # stream A: a command, then Ctrl-C
    [b"echo hi\r", b"q"],          # stream B
]

# P2 presence: symmetric k_pres channel, one peer's stream, cursor + off-grid null.
PRES_STREAM = bytes.fromhex("cc0000c3")
PRESENCE_PLAINTEXTS = [
    json.dumps({"id": "ab12", "name": "Fox", "x": 0.5, "y": 0.25}).encode(),
    json.dumps({"id": "ab12", "name": "Fox", "x": None, "y": None}).encode(),   # off-grid
]

# Representative plaintexts across frame types and sizes, including empty, binary,
# UTF-8 (box-drawing / emoji), and a large payload (multi-block GCM).
PLAINTEXTS = [
    (e2e.T_META, json.dumps({"cols": 137, "rows": 39, "title": "shell"}).encode()),
    (e2e.T_OUTPUT, b""),
    (e2e.T_OUTPUT, b"\x1b[2J\x1b[H$ ls -la\r\n"),
    (e2e.T_OUTPUT, "┌─ chela ─┐ ✓ agent · 火".encode("utf-8")),
    (e2e.T_OUTPUT, bytes(range(256)) * 20),          # 5120 bytes, all byte values
    (e2e.T_CTL, json.dumps({"t": "hello", "cols": 137, "rows": 39}).encode()),
]

def test_wire_type_tags_are_pinned_to_the_literals_e2e_js_hardcodes():
    # DEFEAT_SHAPES #5: every `typ == e2e.T_*` assertion below reads the SAME symbol on
    # both sides once rendered — a mutation to the tag's VALUE (not just to the
    # seal/open wiring) moves the constructed fixture and the expectation together and
    # stays green. `chela/collab-relay/public/e2e.js` hardcodes these five values
    # independently (`T_OUTPUT=1, T_INPUT=2, T_META=3, T_PRESENCE=4, T_CTL=5`) for wire
    # compatibility; pin the Python side to the same literals so a drift is caught here
    # too, not only by the (Node-optional, skippable) interop vectors.
    assert e2e.T_OUTPUT == 1, e2e.T_OUTPUT
    assert e2e.T_INPUT == 2, e2e.T_INPUT
    assert e2e.T_META == 3, e2e.T_META
    assert e2e.T_PRESENCE == 4, e2e.T_PRESENCE
    assert e2e.T_CTL == 5, e2e.T_CTL


_HERE = Path(__file__).resolve().parent
_INTEROP = _HERE / "e2e_interop.mjs"


def _host_frames() -> list[dict]:
    """Python host seals the plaintexts in seq order → wire fixtures."""
    host = e2e.Session(SECRET, ROOM, role="host")
    frames = []
    for typ, pt in PLAINTEXTS:
        env = host.seal(typ, pt)
        frames.append({"type": typ, "plaintext": pt, "envelope": env})
    return frames


def _pres_frames() -> list[bytes]:
    """One presence peer (PRES_STREAM) seals its cursor updates on k_pres."""
    peer = e2e.PresenceSession(SECRET, ROOM, stream_id=PRES_STREAM)
    return [peer.seal(e2e.T_PRESENCE, pt) for pt in PRESENCE_PLAINTEXTS]


# --- pure-Python correctness --------------------------------------------------
def test_python_roundtrip_and_tag_length():
    host = e2e.Session(SECRET, ROOM, role="host")
    joiner = e2e.Session(SECRET, ROOM, role="joiner")
    for typ, pt in PLAINTEXTS:
        env = host.seal(typ, pt)
        # 128-bit (16-byte) GCM tag: envelope = header(10) + plaintext + 16.
        assert len(env) == e2e.HEADER_LEN + len(pt) + 16
        gtyp, gpt = joiner.open(env)
        assert gtyp == typ
        assert gpt == pt


def test_wrong_code_fails_first_tag_cleanly():
    env = e2e.Session(SECRET, ROOM, role="host").seal(e2e.T_OUTPUT, b"secret output")
    joiner = e2e.Session(WRONG_SECRET, ROOM, role="joiner")
    with pytest.raises(e2e.AuthError):
        joiner.open(env)


def test_replay_and_reorder_rejected():
    host = e2e.Session(SECRET, ROOM, role="host")
    e0 = host.seal(e2e.T_OUTPUT, b"first")
    e1 = host.seal(e2e.T_OUTPUT, b"second")
    joiner = e2e.Session(SECRET, ROOM, role="joiner")
    joiner.open(e1)                       # accept seq 1
    with pytest.raises(e2e.ReplayError):  # seq 0 now stale (reorder)
        joiner.open(e0)
    with pytest.raises(e2e.ReplayError):  # exact replay of seq 1
        joiner.open(e1)


def test_cross_room_aad_isolation():
    """A frame sealed for one room must not open under another room's session,
    even with the same secret (room is in both the HKDF info and the GCM AAD)."""
    env = e2e.Session(SECRET, ROOM, role="host").seal(e2e.T_OUTPUT, b"x")
    other = e2e.Session(SECRET, ROOM + "-other", role="joiner")
    with pytest.raises(e2e.AuthError):
        other.open(env)


def test_joiner_default_stream_id_is_random_and_host_is_zero():
    host = e2e.Session(SECRET, ROOM, role="host")
    a = e2e.Session(SECRET, ROOM, role="joiner")
    b = e2e.Session(SECRET, ROOM, role="joiner")
    assert host.stream_id == e2e.HOST_STREAM_ID
    assert a.stream_id != e2e.HOST_STREAM_ID
    assert a.stream_id != b.stream_id            # random → practically never equal


def test_multi_joiner_streams_gated_independently():
    """Two joiners on distinct stream ids each seal from seq 0; the host opens all
    (per-stream seq gating). In v1 the 2nd joiner's seq 0 was a fatal replay/nonce
    collision — this is the exact P1.5 concurrency fix."""
    host = e2e.Session(SECRET, ROOM, role="host")
    jA = e2e.Session(SECRET, ROOM, role="joiner", stream_id=STREAM_A)
    jB = e2e.Session(SECRET, ROOM, role="joiner", stream_id=STREAM_B)
    a0, a1 = jA.seal(e2e.T_INPUT, b"a0"), jA.seal(e2e.T_INPUT, b"a1")
    b0, b1 = jB.seal(e2e.T_INPUT, b"b0"), jB.seal(e2e.T_INPUT, b"b1")
    # Interleaved; both streams' seq 0 accepted, both advance independently.
    assert host.open(a0) == (e2e.T_INPUT, b"a0")
    assert host.open(b0) == (e2e.T_INPUT, b"b0")
    assert host.open(a1) == (e2e.T_INPUT, b"a1")
    assert host.open(b1) == (e2e.T_INPUT, b"b1")
    with pytest.raises(e2e.ReplayError):         # per-stream replay still rejected
        host.open(a0)


def test_stream_id_is_authenticated():
    """The stream id rides in the header (GCM AAD) — flipping it fails the tag, so
    an attacker can't dodge a stream's seq gate by forging a fresh id."""
    env = bytearray(e2e.Session(SECRET, ROOM, role="joiner", stream_id=STREAM_A).seal(e2e.T_INPUT, b"x"))
    env[2] ^= 0xff                               # corrupt a stream_id byte
    with pytest.raises(e2e.AuthError):
        e2e.Session(SECRET, ROOM, role="host").open(bytes(env))


def test_input_roundtrip_joiner_to_host():
    joiner = e2e.Session(SECRET, ROOM, role="joiner", stream_id=STREAM_A)
    host = e2e.Session(SECRET, ROOM, role="host")
    env = joiner.seal(e2e.T_INPUT, b"whoami\r")
    assert host.open(env) == (e2e.T_INPUT, b"whoami\r")


def test_presence_symmetric_group_channel():
    """k_pres is symmetric: two peers on distinct stream ids each seal from seq 0,
    and any third peer opens BOTH — everyone sees everyone (Figma-style presence)."""
    p1 = e2e.PresenceSession(SECRET, ROOM, stream_id=bytes.fromhex("11111111"))
    p2 = e2e.PresenceSession(SECRET, ROOM, stream_id=bytes.fromhex("22222222"))
    rx = e2e.PresenceSession(SECRET, ROOM)                 # a third peer receiving
    assert rx.open(p1.seal(e2e.T_PRESENCE, b"a0")) == (e2e.T_PRESENCE, b"a0")
    assert rx.open(p2.seal(e2e.T_PRESENCE, b"b0")) == (e2e.T_PRESENCE, b"b0")  # seq 0, not a replay
    # A terminal-key session must NOT open a presence frame (different key).
    with pytest.raises(e2e.AuthError):
        e2e.Session(SECRET, ROOM, role="host").open(p1.seal(e2e.T_PRESENCE, b"x"))


def test_presence_key_identical_both_sides(interop):
    assert interop["result"]["presence_key"] == e2e.presence_key(SECRET, ROOM).hex()


def test_presence_python_encrypted_js_decrypted(interop):
    got = interop["result"]["pres_py_to_js"]
    assert [bytes.fromhex(h) for h in got] == PRESENCE_PLAINTEXTS


def test_presence_js_encrypted_python_decrypted(interop):
    rx = e2e.PresenceSession(SECRET, ROOM)
    for sealed, pt in zip(interop["result"]["pres_js_to_py"], PRESENCE_PLAINTEXTS):
        typ, gpt = rx.open(bytes.fromhex(sealed))
        assert typ == e2e.T_PRESENCE and gpt == pt


def test_base32_pairing_code_roundtrip():
    code = e2e.pairing_code(SECRET)
    assert len(code) == 26  # 16 bytes → 26 base32 chars, padding stripped
    assert e2e.secret_from_code(code) == SECRET
    # tolerant of whitespace / lowercase / padding
    assert e2e.secret_from_code(f"  {code.lower()}  ======") == SECRET


# --- Python↔JS interop (skips if Node is absent) ------------------------------
def _run_node(job: dict) -> dict:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available for JS interop vectors")
    proc = subprocess.run(
        [node, str(_INTEROP)], input=json.dumps(job).encode(),
        capture_output=True, timeout=60,
    )
    if proc.returncode != 0:
        pytest.fail(f"interop harness failed: {proc.stderr.decode()[:2000]}")
    return json.loads(proc.stdout.decode())


@pytest.fixture(scope="module")
def interop() -> dict:
    frames = _host_frames()
    job = {
        "secret_hex": SECRET.hex(),
        "wrong_secret_hex": WRONG_SECRET.hex(),
        "room": ROOM,
        "py_frames": [{"envelope_hex": f["envelope"].hex()} for f in frames],
        "js_plaintexts": [{"type": typ, "hex": pt.hex()} for typ, pt in PLAINTEXTS],
        "input_streams": [
            {"stream_id_hex": STREAM_A.hex(), "plaintexts": [p.hex() for p in INPUT_PLAINTEXTS[0]]},
            {"stream_id_hex": STREAM_B.hex(), "plaintexts": [p.hex() for p in INPUT_PLAINTEXTS[1]]},
        ],
        "pres_frames": [{"envelope_hex": env.hex()} for env in _pres_frames()],
        "pres_js_plaintexts": [pt.hex() for pt in PRESENCE_PLAINTEXTS],
        "pres_stream_id_hex": PRES_STREAM.hex(),
    }
    return {"frames": frames, "result": _run_node(job)}


def test_hkdf_identical_both_sides(interop):
    """The two derived keys must be byte-identical in Python and JS/WebCrypto."""
    k_h2j, k_j2h = e2e.derive_keys(SECRET, ROOM)
    keys = interop["result"]["keys"]
    assert keys["h2j"] == k_h2j.hex()
    assert keys["j2h"] == k_j2h.hex()


def test_base32_identical_both_sides(interop):
    assert interop["result"]["base32"] == e2e.pairing_code(SECRET)


def test_python_encrypted_js_decrypted(interop):
    """Python→JS: every Python-sealed frame decrypts to the original in the browser module."""
    got = interop["result"]["py_to_js"]
    assert len(got) == len(PLAINTEXTS)
    for recovered_hex, (_typ, pt) in zip(got, PLAINTEXTS):
        assert recovered_hex == pt.hex(), f"JS decrypt mismatch (got {recovered_hex[:40]}…)"


def test_js_encrypted_python_decrypted(interop):
    """JS→Python: every JS-sealed frame decrypts to the original in Python, in order."""
    joiner = e2e.Session(SECRET, ROOM, role="joiner")
    for sealed, (typ, pt) in zip(interop["result"]["js_to_py"], PLAINTEXTS):
        env = bytes.fromhex(sealed["envelope_hex"])
        gtyp, gpt = joiner.open(env)
        assert gtyp == typ
        assert gpt == pt


def test_js_reports_wrong_code_not_garbage(interop):
    assert interop["result"]["wrong_code"] == "AuthError"


def test_js_rejects_replay(interop):
    assert interop["result"]["replay"] == "ReplayError"


def test_js_input_streams_open_on_host(interop):
    """JS→Python write path: two browser joiners on distinct stream ids each seal
    T_INPUT from seq 0; the Python host opens ALL of them, interleaved, no
    cross-rejection — the concurrent-writer case proven across the language seam."""
    js = interop["result"]["js_input"]
    assert [s["stream_id_hex"] for s in js] == [STREAM_A.hex(), STREAM_B.hex()]
    host = e2e.Session(SECRET, ROOM, role="host")   # opens j2h (joiner→host input)
    # Interleave the two streams (stream0#0, stream1#0, stream0#1, stream1#1).
    for fi in range(len(INPUT_PLAINTEXTS[0])):
        for si in range(len(js)):
            typ, pt = host.open(bytes.fromhex(js[si]["envelopes"][fi]))
            assert typ == e2e.T_INPUT
            assert pt == INPUT_PLAINTEXTS[si][fi]
