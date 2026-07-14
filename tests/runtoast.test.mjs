// Deterministic unit tests for runtoast.js — the pure edge-trigger behind the
// dispatcher run-state toasts. The DOM render + mute wiring live in sse.js/nav.js
// and are validated in-browser; the transition logic that keeps the toast from
// spamming every SSE poll is proven here. Run: node --test tests/  (or `uv run pytest -q` — tests/test_js_suites.py runs every .test.mjs)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runToastKind, RUN_TOAST_KINDS } from '../chela/dashboard/static/js/runtoast.js';

test('fires exactly on the transition INTO awaiting_review', () => {
  assert.equal(runToastKind('running', 'awaiting_review'), 'awaiting_review');
});

test('does NOT re-fire while status stays awaiting_review (pr_state-only poll)', () => {
  assert.equal(runToastKind('awaiting_review', 'awaiting_review'), null);
});

test('done and failed transitions fire (secondary)', () => {
  assert.equal(runToastKind('awaiting_review', 'done'), 'done');
  assert.equal(runToastKind('running', 'failed'), 'failed');
});

test('non-review transitions never toast', () => {
  assert.equal(runToastKind('claimed', 'running'), null);
  assert.equal(runToastKind(undefined, 'running'), null);
});

test('first-seen (undefined prev) into a review state still fires', () => {
  assert.equal(runToastKind(undefined, 'awaiting_review'), 'awaiting_review');
});

test('every returned kind has display metadata', () => {
  for (const kind of ['awaiting_review', 'done', 'failed']) {
    const meta = RUN_TOAST_KINDS[kind];
    assert.ok(meta && meta.icon && meta.text, `metadata for ${kind}`);
  }
});
