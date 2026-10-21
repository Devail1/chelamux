// The dispatcher run-state toasts' edge-trigger (ported from tests/runtoast.test.mjs by CMX-219;
// the toasts on the page are features/work's, driven by tests/sse_owners.test.mjs). A toast
// fires only when a run's status changes INTO a toasted state.
import { expect, test } from 'vitest';
import { MUTE_KEY, RUN_TOAST_KINDS, runToastKind } from '../index.ts';

test('fires exactly on the transition INTO a toasted state, never on an unchanged one', () => {
  expect(runToastKind('running', 'awaiting_review')).toBe('awaiting_review');
  expect(runToastKind('awaiting_review', 'awaiting_review')).toBeNull();
  expect(runToastKind(undefined, 'awaiting_review')).toBe('awaiting_review');
  expect(runToastKind('awaiting_review', 'done')).toBe('done');
  expect(runToastKind('running', 'failed')).toBe('failed');
  expect(runToastKind('claimed', 'running')).toBeNull();
  expect(runToastKind(undefined, 'running')).toBeNull();
  expect(runToastKind('running', undefined)).toBeNull();
  expect(runToastKind('running', 'hasOwnProperty')).toBeNull();
});

test('the rework loop toasts both of its ends, and never the same state twice', () => {
  expect(runToastKind('awaiting_review', 'changes_requested')).toBe('changes_requested');
  expect(runToastKind('changes_requested', 'needs_human')).toBe('needs_human');
  expect(runToastKind('changes_requested', 'running')).toBeNull();
  expect(runToastKind('needs_human', 'needs_human')).toBeNull();
});

test("each kind says its state IN WORDS, and a closed PR never wears done's icon (CMX-265)", () => {
  expect(Object.fromEntries(Object.entries(RUN_TOAST_KINDS).map(([k, v]) => [k, v.text]))).toEqual({
    awaiting_review: 'awaiting review',
    changes_requested: 'changes requested, reworking',
    needs_human: 'NEEDS A HUMAN: rework cap reached',
    done: 'done',
    failed: 'failed',
    closed: 'closed, not merged',
  });
  expect(RUN_TOAST_KINDS.closed?.icon).not.toBe(RUN_TOAST_KINDS.done?.icon);
  expect(MUTE_KEY).toBe('chela_mute_run_toasts');
});
