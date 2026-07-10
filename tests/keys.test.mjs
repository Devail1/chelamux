// Unit tests for the joiner keys-line pure logic (chela/collab-relay/public/
// keys.js) — the exact escape-byte sequences the on-screen keys emit + the
// swipe→wheel sign/threshold. Pins the bytes so a typo can't silently ship a
// key that does the wrong thing on a real terminal. Run: node --test tests/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { KEY_SEQ, ctrlChar, keySeq, swipeWheelDelta, SWIPE_MIN_PX }
  from '../chela/collab-relay/public/keys.js';

test('special keys map to their VT/xterm escape sequences', () => {
  assert.equal(KEY_SEQ.Escape, '\x1b');
  assert.equal(KEY_SEQ.Tab, '\t');
  assert.equal(KEY_SEQ.BTab, '\x1b[Z');
  assert.equal(KEY_SEQ.Enter, '\r');
  assert.equal(KEY_SEQ.Up, '\x1b[A');
  assert.equal(KEY_SEQ.Down, '\x1b[B');
  assert.equal(KEY_SEQ.Right, '\x1b[C');
  assert.equal(KEY_SEQ.Left, '\x1b[D');
  assert.equal(KEY_SEQ.Home, '\x1b[H');
  assert.equal(KEY_SEQ.End, '\x1b[F');
  assert.equal(KEY_SEQ.PageUp, '\x1b[5~');
  assert.equal(KEY_SEQ.PageDown, '\x1b[6~');
});

test('ctrlChar produces the C0 control byte for a letter', () => {
  assert.equal(ctrlChar('c'), '\x03');   // ^C = ETX (interrupt)
  assert.equal(ctrlChar('C'), '\x03');   // case-insensitive
  assert.equal(ctrlChar('a'), '\x01');
  assert.equal(ctrlChar('d'), '\x04');   // ^D = EOF
  assert.equal(ctrlChar('z'), '\x1a');   // ^Z = SUSP
  assert.equal(ctrlChar('l'), '\x0c');   // ^L = clear
  assert.equal(ctrlChar(''), '\x01');    // empty defaults to 'a' (harmless)
});

test('keySeq resolves names, C- prefixes, and unknowns', () => {
  assert.equal(keySeq('Enter'), '\r');
  assert.equal(keySeq('PageDown'), '\x1b[6~');
  assert.equal(keySeq('C-c'), '\x03');   // keybar ^C button
  assert.equal(keySeq('C-u'), '\x15');
  assert.equal(keySeq('Nope'), null);    // unknown → nothing sent
  assert.equal(keySeq(''), null);
  assert.equal(keySeq(null), null);
});

test('swipeWheelDelta: natural sign + sub-threshold de-jitter', () => {
  // Downward drag (dy>0) scrolls toward older content → wheel up (negative).
  assert.equal(swipeWheelDelta(20), -20);
  assert.equal(swipeWheelDelta(-14), 14);
  // Below the threshold → no wheel.
  assert.equal(swipeWheelDelta(0), 0);
  assert.equal(swipeWheelDelta(SWIPE_MIN_PX - 0.5), 0);
  assert.ok(swipeWheelDelta(SWIPE_MIN_PX) !== 0);
});
