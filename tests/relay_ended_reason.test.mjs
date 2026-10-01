// CMX-416 — when a typing share's pane stops verifying as a sandbox, the host ends the
// share with {"t":"ended","reason":SANDBOX_LOST_REASON} (tests/test_share_typing_gate.py
// proves the host SENDS it). This proves the other half: the guest relay page SHOWS it.
//
// The page's module script needs WebCrypto + a live relay socket, so instead of booting
// it we lift the REAL `if (m.t === 'ended') showEnded(…);` statement out of
// chela/collab-relay/public/index.html and run it against a stub showEnded — the text
// asserted is the text the shipped page would render, not a copy of it.
//
// Run: node --test tests/relay_ended_reason.test.mjs (pytest runs it via tests/test_js_suites.py).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const read = p => fs.readFileSync(path.join(ROOT, p), 'utf8');
const RELAY_HTML = read('chela/collab-relay/public/index.html');
// The reason the host sends, read from the host's own constant (not retyped here).
const SANDBOX_LOST_REASON = read('chela/collab_stream.py')
  .match(/^SANDBOX_LOST_REASON = "([^"]+)"$/m)[1];

const ended = (() => {
  const src = RELAY_HTML.match(/if \(m\.t === 'ended'\) showEnded\([\s\S]*?\);/);
  assert.ok(src, "the relay page's 'ended' handler was not found");
  const run = new Function('m', 'showEnded', src[0]);
  return (m) => { const shown = []; run(m, msg => shown.push(msg)); return shown; };
})();

test('a share ended because its sandbox was lost tells the guest WHY', () => {
  const [msg] = ended({ t: 'ended', reason: SANDBOX_LOST_REASON });
  assert.equal(msg, `This share has ended — ${SANDBOX_LOST_REASON}.`);
});

test('a long reason is capped at 120 characters', () => {
  const [msg] = ended({ t: 'ended', reason: 'x'.repeat(500) });
  assert.equal(msg, `This share has ended — ${'x'.repeat(120)}.`);
});

test('an ordinary end (no reason, or a non-string one) keeps the plain message', () => {
  assert.deepEqual(ended({ t: 'ended' }), ['This share has ended.']);
  assert.deepEqual(ended({ t: 'ended', reason: '' }), ['This share has ended.']);
  assert.deepEqual(ended({ t: 'ended', reason: { evil: 1 } }), ['This share has ended.']);
});

test('a non-ended control frame does not end the share', () => {
  assert.deepEqual(ended({ t: 'notice', msg: 'View only' }), []);
});
