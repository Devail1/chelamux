// The run-status badge table (runstate.js) — the presentation half of the review loop.
//
// 🔴 Seen to go red: with the old inline ternary in dispatcher.js, `needs_human` and
// `changes_requested` fell through to `badge-priority-low` — the SAME grey an unknown status
// gets. The one run state that means "a human must look at this" was styled as the least
// interesting thing on the board. Run: node --test tests/  (pytest runs these too — see
// tests/test_js_suites.py)
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { runCardNote, runCloseAction, runStallNote, runStatusBadgeClass, RUN_STATUSES, UNKNOWN_BADGE } from '../chela/dashboard/static/js/runstate.js';

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

test('closed (CMX-265, a PR closed without merging) is NOT badge-done', () => {
  // 🔴 GUARD: reusing `badge-done` for a rejected PR would recreate the exact "shipped"
  // vs "rejected" conflation the board's Archived lane exists to prevent, just here.
  assert.notEqual(runStatusBadgeClass('closed'), 'badge-done');
  assert.equal(runStatusBadgeClass('closed'), 'badge-closed');
});

test('an unknown status still renders (grey), and never throws', () => {
  assert.equal(runStatusBadgeClass('who_knows'), UNKNOWN_BADGE);
  assert.equal(runStatusBadgeClass(undefined), UNKNOWN_BADGE);
});

// --- 🗂️✖️ CMX-406 — runCardNote: a hand-closed run's card reads "Closed — <reason>" ---

test('runCardNote: a closed run with a reason shows the reason, not its last_error', () => {
  assert.deepEqual(
    runCardNote({ status: 'closed', close_reason: 'superseded by cmx-403',
                  last_error: 'tmux window disappeared' }),
    { text: 'Closed — superseded by cmx-403', closed: true });
});

test('runCardNote: a failed run (or a reason on a non-closed row) keeps its error', () => {
  assert.deepEqual(runCardNote({ status: 'failed', last_error: 'boom' }),
                   { text: 'boom', closed: false });
  assert.deepEqual(runCardNote({ status: 'failed', close_reason: 'x', last_error: 'boom' }),
                   { text: 'boom', closed: false });
});

test('runCardNote: a reconcile-closed run (no reason) falls back; nothing → null', () => {
  assert.deepEqual(runCardNote({ status: 'closed', close_reason: null, last_error: 'e' }),
                   { text: 'e', closed: false });
  assert.equal(runCardNote({ status: 'done' }), null);
});

// --- 🗂️🔁 CMX-68 — a closed run silently holding a READY task is flagged, with a way out ---

test('runStallNote: a stalled card says the closed run blocks it; an unstalled one says nothing', () => {
  const msg = 'closed run blocks this task: requeue or refile';
  assert.equal(runStallNote({ status: 'closed', closed_run_stall: msg }), `⚠ ${msg}`);
  assert.equal(runStallNote({ status: 'open', closed_run_stall: msg }), `⚠ ${msg}`);
  assert.equal(runStallNote({ status: 'closed', closed_run_stall: null }), null);
  assert.equal(runStallNote(null), null);
});

test('runCloseAction: Requeue only where a closed run is stalling; never on a merged PR', () => {
  const stall = 'closed run blocks this task: requeue or refile';
  assert.deepEqual(runCloseAction({ task_id: 'X', status: 'closed', closed_run_stall: stall }),
    { label: 'Requeue' });
  assert.deepEqual(runCloseAction({ task_id: 'X', status: 'open', closed_run_stall: stall }),
    { label: 'Requeue' });
  assert.equal(runCloseAction({ task_id: 'X', status: 'closed', closed_run_stall: null }), null);
  assert.equal(runCloseAction({ task_id: 'X', status: 'open' }), null);
  assert.deepEqual(runCloseAction({ task_id: 'X', status: 'running' }), { label: 'Close & requeue' });
  assert.equal(runCloseAction({ task_id: 'X', status: 'awaiting_review', pr_state: 'merged' }), null);
  assert.equal(runCloseAction({ task_id: 'X', status: 'done' }), null);
});

test('runCloseAction: every walk-away-able status offers Close & requeue; done/unknown never do', () => {
  for (const status of ['claimed', 'running', 'awaiting_review', 'changes_requested',
                        'needs_human', 'failed']) {
    assert.deepEqual(runCloseAction({ task_id: 'X', status }), { label: 'Close & requeue' }, status);
    // a merged PR shipped — whatever the run status says, nothing to requeue
    assert.equal(runCloseAction({ task_id: 'X', status, pr_state: 'merged' }), null, status);
  }
  for (const status of ['done', 'queued', 'bogus', '', undefined]) {
    assert.equal(runCloseAction({ task_id: 'X', status }), null, String(status));
  }
  assert.equal(runCloseAction({ status: 'running' }), null, 'no task_id ⇒ nothing to post');
  assert.equal(runCloseAction({ task_id: 'X', status: 'closed', closed_run_stall: 'x',
                                pr_state: 'merged' }), null);
});
