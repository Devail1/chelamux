// The run-status badge table (runstate.js) — the presentation half of the review loop.
//
// 🔴 Seen to go red: with the old inline ternary in dispatcher.js, `needs_human` and
// `changes_requested` fell through to `badge-priority-low` — the SAME grey an unknown status
// gets. The one run state that means "a human must look at this" was styled as the least
// interesting thing on the board. Run: node --test tests/  (pytest runs these too — see
// tests/test_js_suites.py)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runStatusBadgeClass, RUN_STATUSES, UNKNOWN_BADGE } from '../chela/dashboard/static/js/runstate.js';

test('needs_human is the LOUDEST badge — never the unknown-status grey', () => {
  const cls = runStatusBadgeClass('needs_human');
  assert.notEqual(cls, UNKNOWN_BADGE);
  assert.equal(cls, 'badge-needs-human');
});

test('changes_requested reads as live work, not as parked', () => {
  const cls = runStatusBadgeClass('changes_requested');
  assert.notEqual(cls, UNKNOWN_BADGE);
  assert.equal(cls, 'badge-rework');
});

test('EVERY run state the dispatcher can write has its own badge', () => {
  // The guard against the next one: a status with no entry falls to UNKNOWN_BADGE, and a
  // state the dispatcher writes is never "unknown".
  for (const status of RUN_STATUSES) {
    assert.notEqual(runStatusBadgeClass(status), UNKNOWN_BADGE,
      `${status} has no badge of its own`);
  }
});

test('the review loop is visually DISTINCT — the three states never collide', () => {
  const seen = ['awaiting_review', 'changes_requested', 'needs_human'].map(runStatusBadgeClass);
  assert.equal(new Set(seen).size, 3);
});

test('an unknown status still renders (grey), and never throws', () => {
  assert.equal(runStatusBadgeClass('who_knows'), UNKNOWN_BADGE);
  assert.equal(runStatusBadgeClass(undefined), UNKNOWN_BADGE);
});
