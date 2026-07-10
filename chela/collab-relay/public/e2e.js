// End-to-end crypto for the collaborative terminal share (auth-plane CHUNK 2) —
// the browser/WebCrypto mirror of chela/e2e.py. This is the EXACT module the
// joiner SPA runs AND the module the Python↔JS interop vector suite imports under
// Node (globalThis.crypto.subtle exists in both), so there is one implementation,
// not two that can drift. Every constant and byte layout matches e2e.py; the
// vectors in tests/test_e2e_vectors.py lock the agreement.
//
// Contract (see e2e.py for the authoritative prose):
//   HKDF-SHA256(secret, salt=SALT, info="chela-v1|<room>|{h2j,j2h}") → two AES-256 keys
//   nonce  = 4 reserved zero bytes ∥ 8-byte little-endian seq
//   envelope = ver(1) ∥ type(1) ∥ seq(8 LE) ∥ ciphertext(with 128-bit GCM tag)
//   AAD    = ver(1) ∥ type(1) ∥ seq(8 LE) ∥ utf8(room)
//   strictly-increasing seq per direction; a wrong code fails the first GCM tag.

export const VER = 1;
export const T_OUTPUT = 1, T_INPUT = 2, T_META = 3, T_PRESENCE = 4, T_CTL = 5;
const SALT = new TextEncoder().encode('chela-collab-e2e-v1');
const KEY_LEN = 32, HEADER_LEN = 10, SECRET_LEN = 16;
const te = new TextEncoder();

export class E2EError extends Error {}
export class AuthError extends E2EError {}   // GCM tag failed — wrong code / tamper
export class ReplayError extends E2EError {} // seq did not strictly increase
export class BadFrame extends E2EError {}    // malformed envelope

// --- base32 (RFC4648), mirroring Python base64.b32{encode,decode} -------------
const B32 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
export function b32encode(bytes) {
  let bits = 0, val = 0, out = '';
  for (const b of bytes) {
    val = (val << 8) | b; bits += 8;
    while (bits >= 5) { out += B32[(val >>> (bits - 5)) & 31]; bits -= 5; }
  }
  if (bits > 0) out += B32[(val << (5 - bits)) & 31];
  return out; // no padding (matches pairing_code's rstrip('='))
}
export function secretFromCode(code) {
  const s = code.replace(/\s+/g, '').toUpperCase().replace(/=+$/, '');
  let bits = 0, val = 0; const out = [];
  for (const ch of s) {
    const idx = B32.indexOf(ch);
    if (idx < 0) throw new BadFrame('invalid base32 char in pairing code');
    val = (val << 5) | idx; bits += 5;
    if (bits >= 8) { out.push((val >>> (bits - 8)) & 0xff); bits -= 8; }
  }
  const secret = new Uint8Array(out);
  if (secret.length !== SECRET_LEN) throw new BadFrame(`pairing code decodes to ${secret.length} bytes, expected ${SECRET_LEN}`);
  return secret;
}

// --- key derivation -----------------------------------------------------------
async function hkdfKey(secret, info) {
  const base = await crypto.subtle.importKey('raw', secret, 'HKDF', false, ['deriveBits']);
  const bits = await crypto.subtle.deriveBits(
    { name: 'HKDF', hash: 'SHA-256', salt: SALT, info: te.encode(info) }, base, KEY_LEN * 8);
  return crypto.subtle.importKey('raw', bits, { name: 'AES-GCM' }, false, ['encrypt', 'decrypt']);
}
export async function deriveKeys(secret, room) {
  return {
    h2j: await hkdfKey(secret, `chela-v1|${room}|h2j`),
    j2h: await hkdfKey(secret, `chela-v1|${room}|j2h`),
  };
}

// Diagnostic: the raw HKDF output as hex, for the interop vector suite to assert
// byte-identical key derivation against Python. Not used by the SPA (its keys are
// non-extractable). Same salt/info as hkdfKey — this exercises the real KDF path.
export async function keyHex(secret, room) {
  const hex = async (info) => {
    const base = await crypto.subtle.importKey('raw', secret, 'HKDF', false, ['deriveBits']);
    const bits = await crypto.subtle.deriveBits(
      { name: 'HKDF', hash: 'SHA-256', salt: SALT, info: te.encode(info) }, base, KEY_LEN * 8);
    return [...new Uint8Array(bits)].map((b) => b.toString(16).padStart(2, '0')).join('');
  };
  return { h2j: await hex(`chela-v1|${room}|h2j`), j2h: await hex(`chela-v1|${room}|j2h`) };
}

// seq is a BigInt so the 64-bit counter is exact regardless of magnitude.
function seqBytes(seq) {
  const b = new Uint8Array(8);
  let v = BigInt(seq);
  for (let i = 0; i < 8; i++) { b[i] = Number(v & 0xffn); v >>= 8n; } // little-endian
  return b;
}
function readSeq(bytes) { // bytes[2..10] LE → BigInt
  let v = 0n;
  for (let i = 7; i >= 0; i--) v = (v << 8n) | BigInt(bytes[2 + i]);
  return v;
}
function nonce(seq) { const n = new Uint8Array(12); n.set(seqBytes(seq), 4); return n; } // 4 reserved zeros ∥ seq
function concat(...arrs) {
  const len = arrs.reduce((a, x) => a + x.length, 0);
  const out = new Uint8Array(len); let o = 0;
  for (const x of arrs) { out.set(x, o); o += x.length; }
  return out;
}

// --- session ------------------------------------------------------------------
export class Session {
  // async because WebCrypto key import is async: `await Session.create(...)`.
  static async create(secret, room, role) {
    if (role !== 'host' && role !== 'joiner') throw new Error("role must be 'host' or 'joiner'");
    const keys = await deriveKeys(secret, room);
    const s = new Session();
    s.room = room;
    s._roomAad = te.encode(room);
    s._sendKey = role === 'host' ? keys.h2j : keys.j2h;
    s._recvKey = role === 'host' ? keys.j2h : keys.h2j;
    s._sendSeq = 0n;
    s._recvLast = -1n;
    return s;
  }

  async seal(typ, plaintext) {
    const seq = this._sendSeq; this._sendSeq += 1n;
    const header = concat(new Uint8Array([VER, typ]), seqBytes(seq));
    const aad = concat(header, this._roomAad);
    const ct = new Uint8Array(await crypto.subtle.encrypt(
      { name: 'AES-GCM', iv: nonce(seq), additionalData: aad, tagLength: 128 }, this._sendKey, plaintext));
    return concat(header, ct);
  }

  async open(envelope) {
    if (envelope.length < HEADER_LEN) throw new BadFrame('envelope shorter than header');
    if (envelope[0] !== VER) throw new BadFrame(`unknown version ${envelope[0]}`);
    const typ = envelope[1];
    const seq = readSeq(envelope);
    if (seq <= this._recvLast) throw new ReplayError(`seq ${seq} <= last ${this._recvLast}`);
    const header = envelope.subarray(0, HEADER_LEN);
    const aad = concat(header, this._roomAad);
    let pt;
    try {
      pt = await crypto.subtle.decrypt(
        { name: 'AES-GCM', iv: nonce(seq), additionalData: aad, tagLength: 128 },
        this._recvKey, envelope.subarray(HEADER_LEN));
    } catch (_) {
      throw new AuthError('GCM tag failed — wrong pairing code or tampered frame');
    }
    this._recvLast = seq;
    return [typ, new Uint8Array(pt)];
  }
}
