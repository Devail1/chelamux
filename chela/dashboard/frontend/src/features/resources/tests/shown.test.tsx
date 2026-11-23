// CMX-492: "Show resource stats", per device. Off: no strip in the DOM and no /api/resources
// request from any poll; on: CMX-172's strip and its 30 s poll, unchanged.
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { startScheduler } from '../../../shared/scheduler/index.ts';
import { Resources, resources, resourcesShown, setResourcesShown, tickResources } from '../index.ts';

const KEY = 'chela_hide_resource_stats';
const POLL_MS = 30000;
let urls: string[];
let answer: () => Promise<unknown>;
const reads = () => urls.filter((u) => u === '/api/resources').length;
const strip = () => document.getElementById('resources-strip');

beforeEach(() => {
  vi.useFakeTimers();
  localStorage.clear();
  resourcesShown.set(true);
  resources.set(null);
  urls = [];
  answer = () => Promise.resolve({ cpu_pct: 8, mem_pct: 13, disk_pct: 31 });
  vi.stubGlobal('fetch', (url: string) => {
    urls.push(url);
    return answer().then((body) => ({ json: () => Promise.resolve(body) }));
  });
  render(<Resources />);
});
afterEach(() => {
  cleanup();
  vi.clearAllTimers();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

const polls = (n: number) => act(() => vi.advanceTimersByTimeAsync(n * POLL_MS));
const flip = (on: boolean) => act(async () => {
  setResourcesShown(on);
  await vi.advanceTimersByTimeAsync(0);
});
/** What another tab's write looks like here: the stored value, then the event. */
const otherTab = (value: string | null, key: string | null = KEY) => act(async () => {
  if (value === null) localStorage.removeItem(KEY);
  else localStorage.setItem(KEY, value);
  window.dispatchEvent(new StorageEvent('storage', { key }));
  await vi.advanceTimersByTimeAsync(0);
});

test('ACCEPTANCE: off, five poll intervals make no /api/resources request and no strip is in the DOM', async () => {
  startScheduler(POLL_MS);
  await flip(false);
  await polls(5);
  expect(reads()).toBe(0);
  expect(strip()).toBeNull();
});

test('ACCEPTANCE (negative control): a strip merely hidden by CSS still polls on every interval', async () => {
  startScheduler(POLL_MS);
  const css = document.createElement('style');
  css.textContent = '#resources-strip { display: none; }';
  document.head.append(css);
  await polls(5);
  const el = strip() as HTMLElement;
  expect([getComputedStyle(el).display, reads()]).toEqual(['none', 5]);
  css.remove();
});

test('on by default: the strip renders and each interval reads once', async () => {
  startScheduler(POLL_MS);
  expect(resourcesShown.get()).toBe(true);
  await polls(1);
  expect(reads()).toBe(1);
  expect([strip()?.hidden, document.getElementById('res-cpu')?.textContent]).toEqual([false, 'CPU8%']);
  await polls(1);
  expect(reads()).toBe(2);
});

test('turning it back on reads at once, and shows nothing from before until that lands', async () => {
  await act(() => tickResources());
  await flip(false);
  expect(reads()).toBe(1);
  let land: (v: unknown) => void = () => {};
  answer = () => new Promise((r) => { land = r; });
  await flip(true);
  expect(reads()).toBe(2);
  expect(strip()?.hidden).toBe(true);
  await act(async () => {
    land({ cpu_pct: 44 });
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(document.getElementById('res-cpu')?.textContent).toBe('CPU44%');
});

test('a read still in flight when it is turned off is dropped', async () => {
  let land: (v: unknown) => void = () => {};
  answer = () => new Promise((r) => { land = r; });
  const tick = tickResources();
  await flip(false);
  land({ cpu_pct: 90 });
  await act(() => tick);
  expect(resources.get()).toBeNull();
});

test("the device's other tabs follow: a storage event hides or shows the strip; another key's does not", async () => {
  await otherTab('1', 'chela_theme');
  expect(strip()).not.toBeNull();
  await otherTab('1');
  expect([strip(), reads()]).toEqual([null, 0]);
  await otherTab(null);
  expect([strip() !== null, reads()]).toEqual([true, 1]);
  await flip(false);
  await otherTab(null, null); // localStorage.clear()
  expect(resourcesShown.get()).toBe(true);
});

// The reload tests go last: each leaves a second module copy listening for storage events.
async function reloaded(): Promise<boolean> {
  vi.resetModules();
  return (await import('../index.ts')).resourcesShown.get();
}

test('ACCEPTANCE: the choice survives a reload on this device', async () => {
  await flip(false);
  expect([localStorage.getItem(KEY), await reloaded()]).toEqual(['1', false]);
  await flip(true);
  expect([localStorage.getItem(KEY), await reloaded()]).toEqual([null, true]);
  localStorage.setItem(KEY, 'nonsense');
  expect(await reloaded()).toBe(true);
}, 20_000);

test('storage that throws: it reads as on, and a choice still holds for this page', async () => {
  const boom = () => { throw new DOMException('blocked', 'SecurityError'); };
  vi.spyOn(Storage.prototype, 'getItem').mockImplementation(boom);
  vi.spyOn(Storage.prototype, 'setItem').mockImplementation(boom);
  vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(boom);
  expect(await reloaded()).toBe(true);
  await flip(false);
  expect(strip()).toBeNull();
  await flip(true);
  expect(strip()).not.toBeNull();
}, 20_000);
