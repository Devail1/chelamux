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
  for (const kind of ['awaiting_review', 'changes_requested', 'needs_human', 'done', 'failed']) {
    const meta = RUN_TOAST_KINDS[kind];
    assert.ok(meta && meta.icon && meta.text, `metadata for ${kind}`);
  }
});

// The rework loop (CMX-68). The reviewer sends a PR back and the dispatcher re-spawns its
// agent; the loop is bounded, and when it gives up the run stops dead in needs_human. Both
// ends ride the SAME edge-trigger — no second poller — and both must be visible.
test('the rework loop toasts both of its ends', () => {
  assert.equal(runToastKind('awaiting_review', 'changes_requested'), 'changes_requested');
  assert.equal(runToastKind('changes_requested', 'needs_human'), 'needs_human');
});

test('a run going back round does not toast the same state twice', () => {
  // changes_requested → running (re-spawned) → awaiting_review (pushed again): the last
  // step toasts, the middle one does not, and a poll that changes nothing never does.
  assert.equal(runToastKind('changes_requested', 'running'), null);
  assert.equal(runToastKind('running', 'awaiting_review'), 'awaiting_review');
  assert.equal(runToastKind('changes_requested', 'changes_requested'), null);
  assert.equal(runToastKind('needs_human', 'needs_human'), null);
});

test('the toast says which state it is IN WORDS, not by colour alone', () => {
  // Hue is never the only signal (Liav is red-weak) — a toast whose text did not name the
  // state would be unreadable to the person it is for.
  assert.match(RUN_TOAST_KINDS.changes_requested.text, /changes requested/i);
  assert.match(RUN_TOAST_KINDS.needs_human.text, /human/i);
});
