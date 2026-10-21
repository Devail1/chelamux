// CMX-172: the host resources strip and its display math (ported from
// tests/resources_model.test.mjs by CMX-219, plus the strip's render rules, which resources.js
// had no test for). The % NUMBER is the signal; the tint is decoration; a metric that comes back
// null hides itself; a failed read keeps the last sample.
import { act, cleanup, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import { Resources, resources, tickResources } from '../index.ts';
import { humanBytes, level, pct } from '../model.ts';

const GB = 1024 ** 3;
let reply: () => Promise<unknown>;
beforeEach(() => {
  resources.set(null);
  reply = () => Promise.resolve({});
  vi.stubGlobal('fetch', () => reply().then((body) => ({ json: () => Promise.resolve(body) })));
  render(<Resources />);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const strip = () => document.getElementById('resources-strip') as HTMLElement;
const item = (id: string) => document.getElementById(id) as HTMLElement;
async function sample(body: unknown) {
  reply = () => Promise.resolve(body);
  await act(() => tickResources());
}

test('pct: the ratio as a percentage, one decimal; a zero total is 0; clamped to 0-100', () => {
  expect(pct(25, 100)).toBe(25);
  expect(pct(1, 3)).toBe(33.3);
  expect(pct(0, 0)).toBe(0);
  expect(pct(5, 0)).toBe(0);
  expect(pct(150, 100)).toBe(100);
  expect(pct(-5, 100)).toBe(0);
});

test('level: below 75 ok, [75, 90) warn, 90+ bad', () => {
  expect([0, 74.9, 75, 89.9, 90, 100].map(level)).toEqual(['ok', 'ok', 'warn', 'warn', 'bad', 'bad']);
});

test('humanBytes: bytes, then KB/MB/GB with one decimal under 10; unknown is a dash', () => {
  expect([0, 512, 1024, 1536, 1024 * 1024 * 3, 42 * GB, 1024 ** 6].map(humanBytes)).toEqual(['0 B', '512 B', '1.0 KB', '1.5 KB', '3.0 MB', '42 GB', '1024 PB']);
  expect([null, undefined, NaN, Infinity].map(humanBytes)).toEqual(['-', '-', '-', '-']);
});

test('the strip is hidden until a sample lands, then each readout says its % and is tinted', async () => {
  expect(strip().hidden).toBe(true);
  await sample({ cpu_pct: 12.4, mem_pct: 80, mem_used: 8 * GB, mem_total: 10 * GB, disk_pct: 95.5, disk_used: 95 * GB, disk_total: 100 * GB });
  expect(strip().hidden).toBe(false);
  expect(['res-cpu', 'res-mem', 'res-disk'].map((id) => [item(id).className, item(id).textContent, item(id).title])).toEqual([
    ['res-item res-ok', 'CPU12%', 'CPU: 12%'],
    ['res-item res-warn', 'RAM80%', 'RAM: 8.0 GB / 10 GB'],
    ['res-item res-bad', 'Disk96%', 'Disk: 95 GB / 100 GB'],
  ]);
});

test('a null metric hides itself; one with no used/total says its % in the tooltip', async () => {
  await sample({ cpu_pct: null, mem_pct: 50, mem_used: null, mem_total: 10, disk_pct: 10 });
  expect(item('res-cpu').hidden).toBe(true);
  expect(item('res-mem').title).toBe('RAM: 50%');
  expect(item('res-disk').hidden).toBe(false);
});

test('a failed or empty read keeps the last sample, and a failure is logged', async () => {
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  await sample({ cpu_pct: 33 });
  reply = () => Promise.reject(new Error('down'));
  await act(() => tickResources());
  expect(item('res-cpu').textContent).toBe('CPU33%');
  expect(errors).toHaveBeenCalledWith('tickResources', expect.any(Error));
  await sample(null);
  expect(item('res-cpu').textContent).toBe('CPU33%');
  errors.mockRestore();
});

test('it reads /api/resources', async () => {
  const urls: string[] = [];
  vi.stubGlobal('fetch', (url: string) => {
    urls.push(url);
    return Promise.resolve({ json: () => Promise.resolve({}) });
  });
  await act(() => tickResources());
  expect(urls).toEqual(['/api/resources']);
});
