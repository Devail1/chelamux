// Structural guard against the §5.1 shrink spiral. computeFit must be a pure,
// idempotent fixed point: fit(fit(x)) === fit(x). If a future change reintroduces
// a dependency on the element being centered, the idempotency test fails here
// instead of silently spiraling the font to 6px on a real screen.
//
//   node --test chela/dashboard/static/collab/
import test from 'node:test';
import assert from 'node:assert/strict';
import { computeFit } from './fit.js';

test('computeFit: fixed input → known value', () => {
  // 120x30 grid, cells ~0.6x / ~1.214x fontSize, viewport 1200x700:
  //   byWidth  = 1200 / (120 * 0.6)      = 16.666…
  //   byHeight = 700  / (30  * 1.214286) = 19.216…  → min 16.666 → floor 16
  assert.equal(computeFit(1200, 700, 120, 30, 0.6, 1.2142857), 16);
});

test('computeFit: idempotent — fit(fit(x)) === fit(x) (no spiral)', () => {
  const cols = 120, rows = 30, W = 1000, H = 640, rW = 0.6, rH = 1.2;
  const px1 = computeFit(W, H, cols, rows, rW, rH);
  // Re-derive the cell ratio from the px1 render, exactly as fitFont does live:
  // natW = cols * rW * px1, so ratio = natW / (cols * px1) === rW (invariant).
  const natW = cols * rW * px1, natH = rows * rH * px1;
  const px2 = computeFit(W, H, cols, rows, natW / (cols * px1), natH / (rows * px1));
  assert.equal(px2, px1, 'second fit must equal first');
  const px3 = computeFit(W, H, cols, rows, natW / (cols * px1), natH / (rows * px1));
  assert.equal(px3, px1, 'must be a fixed point across repeated passes');
});

test('computeFit: floors to an integer and clamps to >= 6', () => {
  const v = computeFit(1000, 640, 120, 30, 0.6, 1.2);
  assert.equal(v, Math.floor(v));
  assert.equal(computeFit(10, 10, 120, 30, 0.6, 1.2), 6); // tiny viewport clamps
});

test('computeFit: guards non-positive / missing inputs → null', () => {
  assert.equal(computeFit(0, 700, 120, 30, 0.6, 1.2), null);
  assert.equal(computeFit(1200, 700, 0, 30, 0.6, 1.2), null);
  assert.equal(computeFit(1200, 700, 120, 30, 0, 1.2), null);
  assert.equal(computeFit(NaN, 700, 120, 30, 0.6, 1.2), null);
});
