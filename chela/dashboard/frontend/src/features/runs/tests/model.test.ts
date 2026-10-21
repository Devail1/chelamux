// The run-status badge table (features/runs/model.ts, was static/js/runstate.js) — the
// presentation half of the review loop, and the Runs tables' other pure helpers (CMX-231).
//
// 🔴 Seen to go red: with the old inline ternary in dispatcher.js, `needs_human` and
// `changes_requested` fell through to `badge-priority-low` — the SAME grey an unknown status
// gets. The one run state that means "a human must look at this" was styled as the least
// interesting thing on the board.
import { expect, test } from 'vitest';
import { linearGroups, prLabel, riskChip, runBuckets, runCardNote, runCloseAction, runDisplayId, runStallNote, runStatusBadgeClass, RUN_STATUSES, taskProgress, trackerHref, UNKNOWN_BADGE } from '../model.ts';
import { runPrCellHtml, taskProgressChipHtml } from '../legacy.ts';

test('needs_human is the LOUDEST badge — never the unknown-status grey', () => {
  const cls = runStatusBadgeClass('needs_human');
  expect(cls).not.toBe(UNKNOWN_BADGE);
  expect(cls).toBe('badge-needs-human');
});

test('changes_requested reads as live work, not as parked', () => {
  const cls = runStatusBadgeClass('changes_requested');
  expect(cls).not.toBe(UNKNOWN_BADGE);
  expect(cls).toBe('badge-rework');
});

test('EVERY run state the dispatcher can write has its own badge', () => {
  // The guard against the next one: a status with no entry falls to UNKNOWN_BADGE, and a
  // state the dispatcher writes is never "unknown".
  for (const status of RUN_STATUSES) {
    expect(runStatusBadgeClass(status), `${status} has no badge of its own`).not.toBe(UNKNOWN_BADGE);
  }
});

test('the review loop is visually DISTINCT — the three states never collide', () => {
  const seen = ['awaiting_review', 'changes_requested', 'needs_human'].map(runStatusBadgeClass);
  expect(new Set(seen).size).toBe(3);
});

test('closed (CMX-265, a PR closed without merging) is NOT badge-done', () => {
  // 🔴 GUARD: reusing `badge-done` for a rejected PR would recreate the exact "shipped"
  // vs "rejected" conflation the board's Archived lane exists to prevent, just here.
  expect(runStatusBadgeClass('closed')).not.toBe('badge-done');
  expect(runStatusBadgeClass('closed')).toBe('badge-closed');
});

test('an unknown status still renders (grey), and never throws', () => {
  expect(runStatusBadgeClass('who_knows')).toBe(UNKNOWN_BADGE);
  expect(runStatusBadgeClass(undefined)).toBe(UNKNOWN_BADGE);
});

// --- 🗂️✖️ CMX-406 — runCardNote: a hand-closed run's card reads "Closed — <reason>" ---

test('runCardNote: a closed run with a reason shows the reason, not its last_error', () => {
  expect(runCardNote({ status: 'closed', close_reason: 'superseded by cmx-403',
                  last_error: 'tmux window disappeared' })).toEqual({ text: 'Closed: superseded by cmx-403', closed: true });
});

test('runCardNote: a failed run (or a reason on a non-closed row) keeps its error', () => {
  expect(runCardNote({ status: 'failed', last_error: 'boom' })).toEqual({ text: 'boom', closed: false });
  expect(runCardNote({ status: 'failed', close_reason: 'x', last_error: 'boom' })).toEqual({ text: 'boom', closed: false });
});

test('runCardNote: a reconcile-closed run (no reason) falls back; nothing → null', () => {
  expect(runCardNote({ status: 'closed', close_reason: null, last_error: 'e' })).toEqual({ text: 'e', closed: false });
  expect(runCardNote({ status: 'done' })).toBe(null);
});

// --- 🗂️🔁 CMX-68 — a closed run silently holding a READY task is flagged, with a way out ---

test('runStallNote: a stalled card says the closed run blocks it; an unstalled one says nothing', () => {
  const msg = 'closed run blocks this task: requeue or refile';
  expect(runStallNote({ status: 'closed', closed_run_stall: ` ${msg} ` })).toBe(msg);
  expect(runStallNote({ status: 'open', closed_run_stall: msg })).toBe(msg);
  expect(runStallNote({ status: 'open', closed_run_stall: '  ' })).toBe(null);
  expect(runStallNote({ status: 'closed', closed_run_stall: null })).toBe(null);
  expect(runStallNote(null)).toBe(null);
});

test('runCloseAction: Requeue only where a closed run is stalling; never on a merged PR', () => {
  const stall = 'closed run blocks this task: requeue or refile';
  expect(runCloseAction({ task_id: 'X', status: 'closed', closed_run_stall: stall })).toEqual({ label: 'Requeue' });
  expect(runCloseAction({ task_id: 'X', status: 'open', closed_run_stall: stall })).toEqual({ label: 'Requeue' });
  expect(runCloseAction({ task_id: 'X', status: 'closed', closed_run_stall: null })).toBe(null);
  expect(runCloseAction({ task_id: 'X', status: 'open' })).toBe(null);
  expect(runCloseAction({ task_id: 'X', status: 'running' })).toEqual({ label: 'Close & requeue' });
  expect(runCloseAction({ task_id: 'X', status: 'awaiting_review', pr_state: 'merged' })).toBe(null);
  expect(runCloseAction({ task_id: 'X', status: 'done' })).toBe(null);
});

test('runCloseAction: every walk-away-able status offers Close & requeue; done/unknown never do', () => {
  for (const status of ['claimed', 'running', 'awaiting_review', 'changes_requested',
                        'needs_human', 'failed']) {
    expect(runCloseAction({ task_id: 'X', status }), status).toEqual({ label: 'Close & requeue' });
    // a merged PR shipped — whatever the run status says, nothing to requeue
    expect(runCloseAction({ task_id: 'X', status, pr_state: 'merged' }), status).toBe(null);
  }
  for (const status of ['done', 'queued', 'bogus', '', undefined]) {
    expect(runCloseAction({ task_id: 'X', status }), String(status)).toBe(null);
  }
  expect(runCloseAction({ status: 'running' }), 'no task_id ⇒ nothing to post').toBe(null);
  expect(runCloseAction({ task_id: 'X', status: 'closed', closed_run_stall: 'x',
                                pr_state: 'merged' })).toBe(null);
});

// --- the Runs tables' row helpers (CMX-231, from dispatcher.js) ---------------------------

test('runDisplayId: PROJECT-N when numbered, else the legacy hash, else empty', () => {
  expect(runDisplayId({ project_key: 'CMX', task_number: 7, task_id: 'abc' })).toBe('CMX-7');
  expect(runDisplayId({ project_key: 'CMX', task_number: 0, task_id: 'abc' })).toBe('CMX-0');
  expect(runDisplayId({ project_key: 'CMX', task_number: null, task_id: 'abc' })).toBe('abc');
  expect(runDisplayId({ task_number: 3, task_id: 'abc' })).toBe('abc');
  expect(runDisplayId({})).toBe('');
});

test('prLabel: #N off a github pull URL, anchored; anything else is PR', () => {
  expect(prLabel('https://github.com/o/r/pull/42')).toBe('#42');
  expect(prLabel('https://github.com/o/r/pull/42/files')).toBe('#42');
  expect(prLabel('https://github.com/o/r/pull/42#x')).toBe('#42');
  expect(prLabel('https://github.com/o/r/pull/42abc')).toBe('PR');
  expect(prLabel('https://gitlab.example/merge_requests/9')).toBe('PR');
});

test('taskProgress: null with no task data or zero total; else label + tooltip lines', () => {
  expect(taskProgress(null)).toBe(null);
  expect(taskProgress({ total: 0, done: 0 })).toBe(null);
  expect(taskProgress({ total: 4, done: 3 })).toEqual({ label: '3/4', title: '3 of 4 tasks done' });
  expect(taskProgress({ total: 2, done: 1, in_progress: { subject: 'x' },
    blocked: [{ subject: 'a', blocked_by: ['t1', 't2'] }, { subject: 'b', blocked_by: [] }] })).toEqual({ label: '1/2', title: '1 of 2 tasks done\nIn progress: x\n"a" blocked by t1, t2\n"b" blocked' });
});

test('trackerHref links https only; riskChip knows three levels and never guesses', () => {
  expect(trackerHref({ tracker_url: 'https://linear.app/a/issue/X-1' })).toBe('https://linear.app/a/issue/X-1');
  expect(trackerHref({ tracker_url: 'http://linear.app/a' })).toBe(null);
  expect(trackerHref({ tracker_url: 'javascript:alert(1)' })).toBe(null);
  expect(trackerHref(null)).toBe(null);
  expect(riskChip({ risk: 'HIGH', risk_reason: 'label' })).toEqual({ label: 'risk: high', cls: 'risk-high', reason: 'label' });
  expect(riskChip({ risk: 'low' })).toEqual({ label: 'risk: low', cls: 'risk-low', reason: '' });
  expect(riskChip({ risk: 'extreme' })).toBe(null);
  expect(riskChip({ risk: 3 })).toBe(null);
  expect(riskChip({ risk: 'constructor' })).toBe(null); // a prototype key is not a level (CMX-228)
});

test('runBuckets: needs_human leaves every bucket for its own; linearGroups splits by group', () => {
  const r = (task_id: string, status: string) => ({ task_id, status });
  const b = runBuckets({ path: 'p', active_runs: [r('1', 'running'), r('2', 'needs_human')],
    awaiting_review_runs: [r('3', 'needs_human'), r('4', 'awaiting_review')], recent_runs: [r('5', 'done'), r('6', 'needs_human')] });
  const ids = (rs: { task_id?: string | null }[]) => rs.map((x) => x.task_id);
  expect(ids(b.needsHuman)).toEqual(['2', '3', '6']);
  expect([ids(b.active), ids(b.awaiting), ids(b.recent)]).toEqual([['1'], ['4'], ['5']]);
  const g = linearGroups([{ id: 'a', group: 'backlog' }, { id: 'b', group: 'ready' }, { id: 'c', group: 'in_progress' }, { id: 'd' }]);
  expect([g.ready, g.started, g.backlog].map((x) => x.map((t) => t.id))).toEqual([['b'], ['c'], ['a', 'd']]);
});

test('legacy string forms escape what they splice and render nothing without data', () => {
  expect(runPrCellHtml(null)).toBe('');
  expect(runPrCellHtml('https://github.com/o/r/pull/9')).toBe('<a class="pr-badge" href="https://github.com/o/r/pull/9" target="_blank" rel="noopener noreferrer" title="https://github.com/o/r/pull/9">#9</a>');
  expect(runPrCellHtml('https://x/"><img>')).toMatch(/href="https:\/\/x\/&quot;&gt;&lt;img&gt;"/);
  expect(taskProgressChipHtml(null)).toBe('');
  expect(taskProgressChipHtml({ total: 2, done: 1, in_progress: { subject: '<b>' } }, '<svg/>')).toBe('<span class="task-progress-chip" title="1 of 2 tasks done\nIn progress: &lt;b&gt;"><svg/> 1/2</span>');
});
