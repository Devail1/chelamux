// Python↔JS interop harness for the E2E terminal-share crypto. Imports the EXACT
// module the browser SPA runs (chela/collab-relay/public/e2e.js) under Node's
// WebCrypto, so the vector suite proves the browser code — not a reimplementation
// — agrees with chela/e2e.py. Driven by tests/test_e2e_vectors.py:
//   stdin  : {secret_hex, room, wrong_secret_hex, py_frames:[{envelope_hex}],
//             js_plaintexts:[{type, hex}]}
//   stdout : {base32, keys:{h2j,j2h}, py_to_js:[hex|error],
//             js_to_py:[{type,seq,envelope_hex}], wrong_code, replay}
import { Session, keyHex, b32encode, AuthError, ReplayError } from '../chela/collab-relay/public/e2e.js';

const hex = (u8) => [...u8].map((b) => b.toString(16).padStart(2, '0')).join('');
const unhex = (s) => new Uint8Array((s.match(/../g) || []).map((h) => parseInt(h, 16)));

const read = () => new Promise((res) => { let d = ''; process.stdin.on('data', (c) => (d += c)); process.stdin.on('end', () => res(d)); });

(async () => {
  const job = JSON.parse(await read());
  const secret = unhex(job.secret_hex);
  const room = job.room;
  const out = { base32: b32encode(secret), keys: await keyHex(secret, room), py_to_js: [], js_to_py: [] };

  // Python→JS: a joiner opens the host's frames (recv key = h2j), in order.
  const joiner = await Session.create(secret, room, 'joiner');
  for (const f of job.py_frames) {
    try {
      const [, pt] = await joiner.open(unhex(f.envelope_hex));
      out.py_to_js.push(hex(pt));
    } catch (e) { out.py_to_js.push('ERR:' + e.constructor.name); }
  }

  // JS→Python: a host seals frames (send key = h2j) for Python to open.
  const host = await Session.create(secret, room, 'host');
  for (const p of job.js_plaintexts) {
    const env = await host.seal(p.type, unhex(p.hex));
    out.js_to_py.push({ type: p.type, envelope_hex: hex(env) });
  }

  // Wrong pairing code → the first host frame must fail the GCM tag as AuthError.
  try {
    const bad = await Session.create(unhex(job.wrong_secret_hex), room, 'joiner');
    await bad.open(unhex(job.py_frames[0].envelope_hex));
    out.wrong_code = 'NO_ERROR';
  } catch (e) { out.wrong_code = e.constructor.name; }

  // Replay: re-open an already-seen frame → ReplayError (fresh joiner, open twice).
  try {
    const j2 = await Session.create(secret, room, 'joiner');
    await j2.open(unhex(job.py_frames[0].envelope_hex));
    await j2.open(unhex(job.py_frames[0].envelope_hex));
    out.replay = 'NO_ERROR';
  } catch (e) { out.replay = e.constructor.name; }

  process.stdout.write(JSON.stringify(out));
})().catch((e) => { process.stderr.write('HARNESS ERROR: ' + e.stack); process.exit(1); });
