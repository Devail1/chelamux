// CMX-434 — a host restart (a deploy) is not the end of a share. The host sends
// {"t":"restarting"}, keeps the share on disk and comes back with the same link and code.
// This proves the guest half (chela/collab-relay/public/restart.js): "host restarting…",
// hellos with backoff, back to live on the host's first frame, and only a long silence
// ends the share. It also checks that the relay page actually routes those frames into it.
//
// Run: node --test tests/relay_restart.test.mjs (pytest runs it via tests/test_js_suites.py).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  HostRestart, helloMessage, resumeNonceOf, RESTARTING_STATUS, GAVE_UP_MESSAGE,
  HELLO_BACKOFF_MIN, HELLO_BACKOFF_MAX, RESTART_GIVE_UP_MS,
} from '../chela/collab-relay/public/restart.js';

const ROOT = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const RELAY_HTML = fs.readFileSync(path.join(ROOT, 'chela/collab-relay/public/index.html'), 'utf8');

// A fake clock: timers fire only when the test advances time.
function clock() {
  let now = 0, id = 0;
  const timers = new Map();
  return {
    set: (fn, ms) => { timers.set(++id, { at: now + ms, fn }); return id; },
    clear: (t) => { timers.delete(t); },
    advance(ms) {
      const end = now + ms;
      for (;;) {
        const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
        if (!due) break;
        timers.delete(due[0]); now = due[1].at; due[1].fn();
      }
      now = end;
    },
    pending: () => timers.size,
  };
}

function rig() {
  const c = clock(), log = { hellos: 0, status: [], ended: [] };
  const r = new HostRestart({
    hello: () => { log.hellos++; }, status: (m) => log.status.push(m), giveUp: (m) => log.ended.push(m),
    setTimer: c.set, clearTimer: c.clear,
  });
  return { c, r, log };
}

test('restarting shows "host restarting…" and never "This share has ended"', () => {
  const { r, log } = rig();
  r.start();
  assert.equal(r.active, true);
  assert.deepEqual(log.status, [RESTARTING_STATUS]);
  assert.deepEqual(log.ended, []);
});

test('hellos are re-sent with backoff, doubling and capped', () => {
  const { c, r, log } = rig();
  r.start();
  c.advance(HELLO_BACKOFF_MIN);       // 500
  assert.equal(log.hellos, 1);
  c.advance(1000);                    // +1000
  assert.equal(log.hellos, 2);
  c.advance(2000 + 4000 + 8000);      // 2000, 4000, then the 8000 cap
  assert.equal(log.hellos, 5);
  c.advance(HELLO_BACKOFF_MAX);       // still 8000 apart
  assert.equal(log.hellos, 6);
});

test("the host's first frame ends the restart and stops the hellos", () => {
  const { c, r, log } = rig();
  r.start();
  assert.equal(r.frame(), true);
  assert.equal(r.active, false);
  assert.equal(c.pending(), 0);
  c.advance(RESTART_GIVE_UP_MS * 2);
  assert.equal(log.hellos, 0);
  assert.deepEqual(log.ended, []);
  assert.equal(r.frame(), false, 'a frame outside a restart changes nothing');
});

test('a host that never comes back ends the share only after the give-up window', () => {
  const { c, r, log } = rig();
  r.start();
  c.advance(RESTART_GIVE_UP_MS - 1);
  assert.deepEqual(log.ended, []);
  c.advance(1);
  assert.deepEqual(log.ended, [GAVE_UP_MESSAGE]);
  assert.equal(r.active, false);
});

test('a hello carries the restored host\'s resume nonce once one was issued', () => {
  assert.deepEqual(helloMessage(80, 24, null), { t: 'hello', cols: 80, rows: 24 });
  assert.deepEqual(helloMessage(80, 24, 'ab12cd34'), { t: 'hello', cols: 80, rows: 24, resume: 'ab12cd34' });
  assert.equal(resumeNonceOf({ t: 'resume', n: 'ab12cd34' }), 'ab12cd34');
  assert.equal(resumeNonceOf({ t: 'resume', n: '<script>' }), null);
  assert.equal(resumeNonceOf({ t: 'resume', n: 7 }), null);
  assert.equal(resumeNonceOf({ t: 'notice', n: 'ab12cd34' }), null);
});

test('the relay page routes restarting / resume frames and uses the nonce in its hello', () => {
  assert.match(RELAY_HTML, /from '\/restart\.js'/);
  assert.match(RELAY_HTML, /else if \(m\.t === 'restarting'\) hostRestart\.start\(\);/);
  assert.match(RELAY_HTML, /resumeNonce = resumeNonceOf\(m\); hello\(\);/);
  assert.match(RELAY_HTML, /helloMessage\(grid\.cols, grid\.rows, resumeNonce\)/);
  assert.match(RELAY_HTML, /if \(hostRestart\.frame\(\)\)/);
});
