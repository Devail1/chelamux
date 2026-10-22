// CMX-246: every pane's terminal is one React root portaled into the slots terminals.js draws.
// A ready pane draws its iframe; a pending one a spinner that polls until the port lands, then
// swaps in place without touching a sibling's live iframe.
import { act } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { agents } from '../../../shared/store/agents.ts';
import { dispatch } from '../../../shared/events/index.ts';
import { view } from '../../../shared/store/view.ts';
import { forgetReady, markReady, mountPaneFrames, READY_MAX_TRIES, READY_POLL_MS, refreshPaneFrames, resolveReady } from '../index.ts';
import { frame, host, must, pending, stage, stubFetch } from './setup.ts';

const readyOf = (ready: Set<string>) => (path: string) => {
  const wid = decodeURIComponent(path.split('agent=')[1] ?? '');
  return path.startsWith('/api/term/ready') ? { ready: ready.has(wid) } : {};
};

test('a pane already in the port map draws its iframe at once: src with a cid, label, clipboard delegated', async () => {
  stubFetch(readyOf(new Set(['@1'])));
  agents.set([{ window_id: '@1', name: 'api' }]);
  await resolveReady(['@1']);
  stage('@1');
  mountPaneFrames(document);
  const f = must(frame('@1'));
  expect(f.getAttribute('src')).toMatch(/^\/term\/%401\/\?cid=[^&]+$/);
  expect(f.title).toBe('api');
  expect(f.getAttribute('allow')).toBe('clipboard-read; clipboard-write');
  expect(pending('@1')).toBeNull();
});

test('a repaint (a rename) keeps the same src, so the pane never reconnects', async () => {
  stubFetch(readyOf(new Set(['@1'])));
  agents.set([{ window_id: '@1', name: 'old' }]);
  await resolveReady(['@1']);
  stage('@1');
  mountPaneFrames(document);
  const f = must(frame('@1'));
  const src = f.getAttribute('src');
  agents.set([{ window_id: '@1', name: 'new' }]);
  expect(frame('@1')).toBe(f);
  expect(f.getAttribute('src')).toBe(src);
  expect(f.title).toBe('new');
});

test('a pane not yet ready draws the spinner with its label, polls, and swaps in its iframe beside a live sibling', async () => {
  vi.useFakeTimers();
  const up = new Set(['@1']);
  const calls = stubFetch(readyOf(up));
  agents.set([{ window_id: '@2', name: 'fresh' }]);
  await resolveReady(['@1', '@2']);
  stage('@1', '@2');
  mountPaneFrames(document);
  const sibling = must(frame('@1'));
  expect(must(pending('@2')).dataset.pending).toBe('@2');
  expect(must(pending('@2')).textContent).toContain('starting fresh…');
  expect(frame('@2')).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(READY_POLL_MS));
  expect(frame('@2')).toBeNull(); // still not up
  up.add('@2');
  await act(() => vi.advanceTimersByTimeAsync(READY_POLL_MS));
  expect(must(frame('@2')).getAttribute('src')).toMatch(/^\/term\/%402\/\?cid=/);
  expect(pending('@2')).toBeNull();
  expect(frame('@1')).toBe(sibling);
  const polls = calls.filter((c) => c.path.includes('agent=%402')).length;
  await act(() => vi.advanceTimersByTimeAsync(READY_POLL_MS * 3));
  expect(calls.filter((c) => c.path.includes('agent=%402')).length).toBe(polls); // the poll stopped
});

test('after the cap the spinner offers a retry; the retry polls again until the pane is up', async () => {
  vi.useFakeTimers();
  const up = new Set<string>();
  const calls = stubFetch(readyOf(up));
  stage('@3');
  mountPaneFrames(document);
  await act(() => vi.advanceTimersByTimeAsync(READY_POLL_MS * READY_MAX_TRIES));
  expect(calls.filter((c) => c.path.startsWith('/api/term/ready')).length).toBe(READY_MAX_TRIES);
  const retry = must(pending('@3')?.querySelector<HTMLButtonElement>('button.term-retry'));
  expect(retry.textContent).toBe('click to retry');
  expect(pending('@3')?.querySelector('.term-spinner')).toBeNull();
  await act(() => vi.advanceTimersByTimeAsync(READY_POLL_MS * 2));
  expect(calls.filter((c) => c.path.startsWith('/api/term/ready')).length).toBe(READY_MAX_TRIES); // gave up
  act(() => retry.click());
  expect(pending('@3')?.querySelector('.term-spinner')).not.toBeNull();
  up.add('@3');
  await act(() => vi.advanceTimersByTimeAsync(READY_POLL_MS));
  expect(frame('@3')).not.toBeNull();
});

test('a slot that leaves the page stops its poll on the next paint', async () => {
  vi.useFakeTimers();
  const calls = stubFetch(readyOf(new Set()));
  const el = stage('@4');
  mountPaneFrames(document);
  await act(() => vi.advanceTimersByTimeAsync(READY_POLL_MS));
  el.remove();
  refreshPaneFrames();
  const n = calls.length;
  await act(() => vi.advanceTimersByTimeAsync(READY_POLL_MS * 3));
  expect(calls.length).toBe(n);
});

test('forgetReady makes a re-spawn under the same wid probe its port again', async () => {
  const up = new Set(['@5']);
  const calls = stubFetch(readyOf(up));
  await resolveReady(['@5']);
  await resolveReady(['@5']);
  expect(calls.length).toBe(1); // known ready: no second probe
  forgetReady('@5');
  up.delete('@5');
  await resolveReady(['@5']);
  expect(calls.length).toBe(2);
  stage('@5');
  mountPaneFrames(document);
  expect(pending('@5')).not.toBeNull();
});

test('a failed readiness probe reads as not ready, never as ready', async () => {
  vi.stubGlobal('fetch', () => Promise.reject(new Error('down')));
  await resolveReady(['@6']);
  stage('@6');
  mountPaneFrames(document);
  expect(pending('@6')).not.toBeNull();
  expect(frame('@6')).toBeNull();
});

test('the term-ready push swaps a shown placeholder in at once, only while the Wall is open', () => {
  stubFetch(readyOf(new Set()));
  stage('@7');
  mountPaneFrames(document);
  view.set('work');
  act(() => dispatch('term-ready', JSON.stringify({ ready: ['@7'] })));
  expect(frame('@7')).toBeNull();
  view.set('terminals');
  act(() => dispatch('term-ready', JSON.stringify({ ready: ['@8', 7, '@7'] })));
  expect(frame('@7')).not.toBeNull();
});

test('the term-ready push ignores a wid with no placeholder on the page', async () => {
  const calls = stubFetch(readyOf(new Set()));
  act(() => dispatch('term-ready', JSON.stringify({ ready: ['@9'] })));
  await resolveReady(['@9']);
  expect(calls.length).toBe(1); // still probed: the push did not mark it ready
});

test('a loaded frame wires the pane keys, reports its watch state and re-fits the keybar', async () => {
  const calls = stubFetch();
  markReady('@1');
  stage('@1');
  mountPaneFrames(document);
  const f = must(frame('@1'));
  calls.length = 0;
  f.dispatchEvent(new Event('load'));
  await Promise.resolve();
  expect(host.frameLoaded).toHaveBeenCalledTimes(1);
  const cid = f.getAttribute('src')?.split('cid=')[1];
  expect(calls).toEqual([{ path: '/api/term/%401/watch', method: 'POST', body: { cid, watching: true } }]);
});
