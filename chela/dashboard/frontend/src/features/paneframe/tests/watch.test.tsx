// CMX-175 ignore-size reporting and the background-tab teardown, driven by the same events a
// real browser fires (visibilitychange) and the dock's store.
import { act } from '@testing-library/react';
import { expect, test, vi } from 'vitest';
import { setMinimized } from '../../../shared/store/wall.ts';
import { framesOf, isSuspended, isWatched, markReady, mountPaneFrames, startTermTimer, stopTermTimer, TERM_HIDE_GRACE_MS, TERM_REFRESH_MS, widOfFrame } from '../index.ts';
import { frame, host, must, setVisible, stage, stubFetch, type Call } from './setup.ts';

function wall(...wids: string[]) {
  for (const w of wids) markReady(w);
  stage(...wids);
  mountPaneFrames(document);
}
const watches = (calls: Call[]) => calls.filter((c) => c.path.endsWith('/watch')).map((c) => [decodeURIComponent(c.path.split('/')[3] ?? ''), (c.body as { watching: boolean }).watching]);

test('hiding the tab reports watching:false for every frame and showing it reports true, never reloading one', async () => {
  const calls = stubFetch();
  wall('@1', '@2');
  const f1 = must(frame('@1'));
  const src = f1.getAttribute('src');
  setVisible(false);
  await Promise.resolve();
  expect(watches(calls)).toEqual([['@1', false], ['@2', false]]);
  calls.length = 0;
  setVisible(true);
  expect(watches(calls)).toEqual([['@1', true], ['@2', true]]);
  expect(frame('@1')).toBe(f1);
  expect(f1.getAttribute('src')).toBe(src);
});

test('docking a pane reports watching:false for THAT pane only; restoring it reports true', () => {
  const calls = stubFetch();
  wall('@1', '@2');
  setMinimized('@1', true);
  expect(watches(calls)).toEqual([['@1', false]]);
  expect(isWatched('@1')).toBe(false);
  expect(isWatched('@2')).toBe(true);
  calls.length = 0;
  setMinimized('@1', false);
  expect(watches(calls)).toEqual([['@1', true]]);
});

test('hidden past the grace, only the frames a second viewer shares are blanked; showing the tab reconnects them and re-ticks', async () => {
  vi.useFakeTimers();
  stubFetch((p) => (p === '/api/term/clients' ? { '@1': 2, '@2': 1 } : {}));
  wall('@1', '@2');
  const src1 = must(frame('@1')).getAttribute('src');
  const src2 = must(frame('@2')).getAttribute('src');
  setVisible(false);
  await act(() => vi.advanceTimersByTimeAsync(TERM_HIDE_GRACE_MS - 1));
  expect(isSuspended()).toBe(false); // a quick tab flip never reloads
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(isSuspended()).toBe(true);
  const f1 = must(frame('@1'));
  expect(f1.getAttribute('src')).toBe('about:blank');
  expect(f1.dataset.suspendedSrc).toBe(src1);
  expect(widOfFrame(f1)).toBe('@1'); // still named by its stashed src
  expect(must(frame('@2')).getAttribute('src')).toBe(src2); // sole viewer: stays connected
  expect(must(frame('@2')).hasAttribute('data-suspended-src')).toBe(false);
  expect(host.tick).not.toHaveBeenCalled();
  setVisible(true);
  expect(frame('@1')).toBe(f1);
  expect(f1.getAttribute('src')).toBe(src1);
  expect(f1.hasAttribute('data-suspended-src')).toBe(false);
  expect(isSuspended()).toBe(false);
  expect(host.tick).toHaveBeenCalledTimes(1);
});

test('showing the tab before the grace cancels the teardown', async () => {
  vi.useFakeTimers();
  const calls = stubFetch((p) => (p === '/api/term/clients' ? { '@1': 3 } : {}));
  wall('@1');
  setVisible(false);
  setVisible(true);
  await act(() => vi.advanceTimersByTimeAsync(TERM_HIDE_GRACE_MS * 2));
  expect(calls.some((c) => c.path === '/api/term/clients')).toBe(false);
  expect(isSuspended()).toBe(false);
  expect(host.tick).not.toHaveBeenCalled(); // nothing was suspended, nothing to reconcile
});

test('an unreadable client count disturbs nothing', async () => {
  vi.useFakeTimers();
  stubFetch();
  wall('@1');
  vi.stubGlobal('fetch', () => Promise.reject(new Error('down')));
  setVisible(false);
  await act(() => vi.advanceTimersByTimeAsync(TERM_HIDE_GRACE_MS));
  expect(isSuspended()).toBe(false);
  expect(must(frame('@1')).getAttribute('src')).not.toBe('about:blank');
});

test('a tab shown again while the count was in flight blanks nothing', async () => {
  vi.useFakeTimers();
  let answer: (v: unknown) => void = () => {};
  stubFetch();
  wall('@1');
  vi.stubGlobal('fetch', (url: string) =>
    url === '/api/term/clients' ? new Promise((r) => { answer = r; }) : Promise.resolve({ json: () => Promise.resolve({}) }),
  );
  setVisible(false);
  await act(() => vi.advanceTimersByTimeAsync(TERM_HIDE_GRACE_MS));
  setVisible(true);
  await act(async () => { answer({ json: () => Promise.resolve({ '@1': 5 }) }); await vi.advanceTimersByTimeAsync(0); });
  expect(isSuspended()).toBe(false);
});

test('a blanked frame reports no watch state (it has no connection to flag)', async () => {
  vi.useFakeTimers();
  const calls = stubFetch((p) => (p === '/api/term/clients' ? { '@1': 2 } : {}));
  wall('@1');
  setVisible(false);
  await act(() => vi.advanceTimersByTimeAsync(TERM_HIDE_GRACE_MS));
  calls.length = 0;
  setMinimized('@1', true);
  expect(watches(calls)).toEqual([]);
});

test('framesOf lists the stage\'s frames, or one window\'s', () => {
  stubFetch();
  wall('@1', '@2');
  expect(framesOf().length).toBe(2);
  expect(framesOf('@2')).toEqual([frame('@2')]);
  expect(framesOf('@9')).toEqual([]);
});

test('the 4 s tick runs the host\'s reconcile while started, never while a teardown holds frames blank', async () => {
  vi.useFakeTimers();
  stubFetch((p) => (p === '/api/term/clients' ? { '@1': 2 } : {}));
  wall('@1');
  startTermTimer();
  await act(() => vi.advanceTimersByTimeAsync(TERM_REFRESH_MS - 1));
  expect(host.tick).not.toHaveBeenCalled();
  await act(() => vi.advanceTimersByTimeAsync(1));
  expect(host.tick).toHaveBeenCalledTimes(1);
  setVisible(false);
  await act(() => vi.advanceTimersByTimeAsync(TERM_HIDE_GRACE_MS));
  host.tick.mockClear();
  await act(() => vi.advanceTimersByTimeAsync(TERM_REFRESH_MS * 2));
  expect(host.tick).not.toHaveBeenCalled();
  setVisible(true);
  host.tick.mockClear();
  stopTermTimer();
  await act(() => vi.advanceTimersByTimeAsync(TERM_REFRESH_MS * 2));
  expect(host.tick).not.toHaveBeenCalled();
});

test('starting the tick twice runs one timer', async () => {
  vi.useFakeTimers();
  startTermTimer();
  startTermTimer();
  await act(() => vi.advanceTimersByTimeAsync(TERM_REFRESH_MS));
  expect(host.tick).toHaveBeenCalledTimes(1);
});
