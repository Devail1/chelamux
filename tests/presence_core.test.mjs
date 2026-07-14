// Deterministic unit tests for presence-core.js — the pure logic behind cursors +
// facepile. The cross-viewer coordinate invariant (a cursor lands on the SAME grid
// cell for differently sized viewers) is proven here mathematically; the full
// two-browser render is validated separately (MCP). Run: node --test tests/  (or `uv run pytest -q` — tests/test_js_suites.py runs every .test.mjs)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  OKABE_ITO, colorForId, textOn, autoName, initials,
  clamp01, toNorm, fromNorm, PeerStore,
} from '../chela/collab-relay/public/presence-core.js';

test('coordinate invariant: same grid cell across DIFFERENT viewer rects', () => {
  const rectA = { left: 100, top: 50, width: 800, height: 400 };   // one viewer
  const rectB = { left: 0, top: 0, width: 300, height: 200 };      // a smaller, offset viewer
  // Sender A maps a mouse at 60%/30% of its grid → normalized.
  const mouse = { x: rectA.left + rectA.width * 0.6, y: rectA.top + rectA.height * 0.3 };
  const norm = toNorm(mouse.x, mouse.y, rectA);
  assert.ok(Math.abs(norm.x - 0.6) < 1e-9 && Math.abs(norm.y - 0.3) < 1e-9);
  // Receiver B maps that normalized point to ITS px, then back to normalized.
  const pxB = fromNorm(norm.x, norm.y, rectB);
  const normB = toNorm(pxB.x, pxB.y, rectB);
  assert.ok(Math.abs(normB.x - norm.x) < 1e-9 && Math.abs(normB.y - norm.y) < 1e-9,
    'normalized position must be identical regardless of viewer size');
});

test('off-grid mouse → null (pointer hidden)', () => {
  const rect = { left: 0, top: 0, width: 100, height: 100 };
  assert.equal(toNorm(-5, 50, rect), null);       // left of grid
  assert.equal(toNorm(50, 150, rect), null);      // below grid
  assert.deepEqual(toNorm(50, 50, rect), { x: 0.5, y: 0.5 });
  assert.equal(toNorm(0, 0, { left: 0, top: 0, width: 0, height: 0 }), null); // no grid yet
  assert.equal(clamp01(-1), 0);
  assert.equal(clamp01(2), 1);
});

test('color is deterministic, palette-bound, colorblind-safe set', () => {
  assert.equal(colorForId('abc'), colorForId('abc'));            // stable per id
  assert.ok(OKABE_ITO.includes(colorForId('anything')));        // always in-palette
  assert.equal(OKABE_ITO[0], '#0072B2');                        // blue leads (red-weak order)
  assert.ok(!OKABE_ITO.includes('#000000'));                    // black dropped (dark bg)
  assert.equal(textOn('#F0E442'), '#0a0c11');                   // dark text on bright yellow
  assert.equal(textOn('#0072B2'), '#ffffff');                   // light text on blue
});

test('names + initials are the non-hue cue', () => {
  assert.equal(autoName('id1'), autoName('id1'));               // stable auto-name
  assert.match(autoName('id1'), /^\w+ \w+$/);                   // adjective animal
  assert.equal(initials('Ada Lovelace'), 'AL');
  assert.equal(initials('fox'), 'F');
  assert.equal(initials(''), '?');
});

test('PeerStore: update, off-grid, and staleness pruning', () => {
  const s = new PeerStore();
  s.update({ id: 'p1', name: 'Fox', x: 0.5, y: 0.2 }, 1000);
  s.update({ id: 'p2', x: null, y: null }, 1000);              // no name → auto
  assert.equal(s.list().length, 2);
  assert.equal(s.peers.get('p2').x, null);                     // off-grid preserved
  assert.match(s.peers.get('p2').name, /^\w+ \w+$/);           // auto-named
  assert.ok(OKABE_ITO.includes(s.peers.get('p1').color));
  // p1 refreshed at 4000, p2 last seen 1000 → prune at 6001 drops only p2 (>5s).
  s.update({ id: 'p1', x: 0.6, y: 0.3 }, 4000);
  assert.equal(s.prune(6001, 5000), true);
  assert.deepEqual(s.list().map((p) => p.id), ['p1']);
  assert.equal(s.prune(6001, 5000), false);                    // nothing more to prune
});
