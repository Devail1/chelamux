// The dispatcher run-state toasts (ported from tests/runtoast.test.mjs by CMX-219; the page's
// SSE wiring is tests/sse_owners.test.mjs). Edge-triggered: a toast fires only when a run's
// status changes INTO a toasted state, never on a frame that changed only its PR state; the
// `hello` baseline primes; the Notifications mute silences them.
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { RunToasts, TOAST_MS, primeRunStatus, resetRunStatus, runStateToasts } from '../index.ts';
import { MUTE_KEY, RUN_TOAST_KINDS, runToastKind } from '../model.ts';

beforeEach(() => {
  resetRunStatus();
  localStorage.removeItem(MUTE_KEY);
  render(<RunToasts />);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

const toasts = () => [...document.querySelectorAll('.run-toast-stack .run-toast')] as HTMLElement[];
const frame = (runs: object[]) => act(() => {
  runStateToasts({ runs });
});

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

test('each kind says its state IN WORDS, and a closed PR never wears done\'s icon (CMX-265)', () => {
  expect(Object.fromEntries(Object.entries(RUN_TOAST_KINDS).map(([k, v]) => [k, v.text]))).toEqual({
    awaiting_review: 'awaiting review',
    changes_requested: 'changes requested, reworking',
    needs_human: 'NEEDS A HUMAN: rework cap reached',
    done: 'done',
    failed: 'failed',
    closed: 'closed, not merged',
  });
  expect(RUN_TOAST_KINDS.closed?.icon).not.toBe(RUN_TOAST_KINDS.done?.icon);
  expect(new Set(Object.values(RUN_TOAST_KINDS).map((k) => k.icon)).size).toBe(6);
});

test('a run moving INTO review toasts once, with its label, state and PR link', () => {
  const run = { task_id: 'CMX-901', label: 'CMX-901 · port', pr_url: 'https://example.test/pull/9' };
  frame([{ ...run, status: 'running' }]);
  frame([{ ...run, status: 'awaiting_review' }]);
  frame([{ ...run, status: 'awaiting_review', pr_state: 'MERGEABLE' }]);
  expect(toasts()).toHaveLength(1);
  const [t] = toasts();
  expect(t?.textContent).toBe(' CMX-901 · port → awaiting review · PR');
  expect(t?.querySelector('svg')).not.toBeNull();
  expect(t?.querySelector('a')?.getAttribute('href')).toBe('https://example.test/pull/9');
  expect(t?.querySelector('a')?.getAttribute('target')).toBe('_blank');
});

test('a toast with no label names the task, and with no PR has no link', () => {
  frame([{ task_id: 'CMX-905', status: 'failed' }]);
  expect(toasts()[0]?.textContent).toBe(' CMX-905 → failed');
  expect(toasts()[0]?.querySelector('a')).toBeNull();
});

test('a click dismisses a toast; a click on its PR link does not', () => {
  frame([{ task_id: 'CMX-906', status: 'done', pr_url: 'https://example.test/pull/6' }]);
  act(() => {
    fireEvent.click(toasts()[0]?.querySelector('a') as HTMLElement);
  });
  expect(toasts()).toHaveLength(1);
  act(() => {
    fireEvent.click(toasts()[0] as HTMLElement);
  });
  expect(toasts()).toHaveLength(0);
});

test('a toast closes itself after TOAST_MS', () => {
  vi.useFakeTimers();
  frame([{ task_id: 'CMX-907', status: 'done' }]);
  expect(TOAST_MS).toBe(15000);
  act(() => {
    vi.advanceTimersByTime(TOAST_MS - 100);
  });
  expect(toasts()).toHaveLength(1);
  act(() => {
    vi.advanceTimersByTime(200);
  });
  expect(toasts()).toHaveLength(0);
});

test('the hello baseline primes: a run already in review never toasts, a later move still does', () => {
  act(() => primeRunStatus({ runs: [{ task_id: 'CMX-902', status: 'awaiting_review' }, { status: 'done' }, null] }));
  frame([{ task_id: 'CMX-902', status: 'awaiting_review' }]);
  expect(toasts()).toHaveLength(0);
  frame([{ task_id: 'CMX-902', status: 'done' }]);
  expect(toasts()).toHaveLength(1);
});

test('muted toasts stay silent, but still record the status', () => {
  localStorage.setItem(MUTE_KEY, '1');
  frame([{ task_id: 'CMX-903', status: 'failed' }]);
  expect(toasts()).toHaveLength(0);
  localStorage.removeItem(MUTE_KEY);
  frame([{ task_id: 'CMX-903', status: 'failed' }]);
  expect(toasts()).toHaveLength(0);
});

test('a frame with no runs, or rows with no task id, fires nothing', () => {
  expect(runStateToasts(null)).toEqual([]);
  expect(runStateToasts({ runs: 'x' })).toEqual([]);
  expect(runStateToasts({ runs: [{ status: 'done' }, null] })).toEqual([]);
});
