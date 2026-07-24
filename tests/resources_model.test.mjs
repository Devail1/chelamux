// HOST RESOURCES MODEL — pure pct/level/humanBytes math (resourcesmodel.js).
// No DOM, no fetch: every property here is a straight function-of-inputs
// check, each written to go RED under one specific corruption of the real
// logic (a guard that survives its own corruption is decoration, not a
// guard).
//
// Run: node --test tests/resources_model.test.mjs (tests/test_js_suites.py
// runs every .test.mjs inside pytest, by discovery).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { humanBytes, level, pct } from '../chela/dashboard/static/js/resourcesmodel.js';

// --- pct ----------------------------------------------------------------

test('pct computes the ratio as a percentage', () => {
    assert.equal(pct(25, 100), 25);
    assert.equal(pct(1, 3), 33.3);
});

test('pct with a zero total is 0, not NaN/Infinity', () => {
    // 🔴 GUARD: dropping this zero-guard divides by zero -> NaN, which would
    // render as "NaN%" in the header strip.
    assert.equal(pct(0, 0), 0);
    assert.equal(pct(5, 0), 0);
});

test('pct clamps to the 0-100 range', () => {
    assert.equal(pct(150, 100), 100);
    assert.equal(pct(-5, 100), 0);
});

// --- level ----------------------------------------------------------------

test('level classifies below 75 as ok', () => {
    assert.equal(level(0), 'ok');
    assert.equal(level(74.9), 'ok');
});

test('level classifies [75, 90) as warn', () => {
    // 🔴 GUARD: moving this boundary (e.g. to >75) mis-tints exactly-75%.
    assert.equal(level(75), 'warn');
    assert.equal(level(89.9), 'warn');
});

test('level classifies 90+ as bad', () => {
    // 🔴 GUARD: inverting the comparison (e.g. <=) would classify 100% as warn.
    assert.equal(level(90), 'bad');
    assert.equal(level(100), 'bad');
});

// --- humanBytes -------------------------------------------------------------

test('humanBytes renders sub-1024 values as bytes', () => {
    assert.equal(humanBytes(0), '0 B');
    assert.equal(humanBytes(512), '512 B');
});

test('humanBytes scales up through KB/MB/GB', () => {
    assert.equal(humanBytes(1024), '1.0 KB');
    assert.equal(humanBytes(1536), '1.5 KB');
    assert.equal(humanBytes(1024 * 1024 * 3), '3.0 MB');
    assert.equal(humanBytes(1024 * 1024 * 1024 * 42), '42 GB');
});

test('humanBytes handles null/non-finite input without throwing', () => {
    assert.equal(humanBytes(null), '—');
    assert.equal(humanBytes(undefined), '—');
    assert.equal(humanBytes(NaN), '—');
});
