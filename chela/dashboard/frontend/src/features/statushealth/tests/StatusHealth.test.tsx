// CMX-179: the native status feed's health marker (ported from tests/status_health_mobile
// .test.mjs by CMX-219, plus the show/hide rules statushealth.js had no test for). Shown on any
// answered health that is not `ok: true` (a missing or malformed answer fails CLOSED); hidden
// until the first answer; a failed read keeps the last answer. The word is its own .shw-text
// span so it ellipsizes on a phone while the glyph stays.
import { act, cleanup, render } from '@testing-library/react';
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, test, vi } from 'vitest';
import css from '../../../../../static/style.css?raw';
import { StatusHealth, statusHealth, tickStatusHealth } from '../index.ts';

let reply: () => Promise<unknown>;
beforeEach(() => {
  statusHealth.set(undefined);
  reply = () => Promise.resolve({ ok: true });
  vi.stubGlobal('fetch', () => reply().then((body) => ({ json: () => Promise.resolve(body) })));
  render(<StatusHealth />);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const pill = () => document.getElementById('status-health-warn') as HTMLElement;
async function answer(body: unknown) {
  reply = () => Promise.resolve(body);
  await act(() => tickStatusHealth());
}

test('hidden until the feed is known, shown on not-ok, hidden again on ok', async () => {
  expect(pill().hidden).toBe(true);
  await answer({ ok: false, error: 'timeout' });
  expect(pill().hidden).toBe(false);
  await answer({ ok: true });
  expect(pill().hidden).toBe(true);
});

test.each([[null], [{}], [{ ok: 'true' }], [{ ok: 1 }], ['ok'], [[]]])('a malformed answer %j fails closed: shown', async (body) => {
  await answer(body);
  expect(pill().hidden).toBe(false);
});

test('a failed read keeps the last answer, and is logged', async () => {
  const errors = vi.spyOn(console, 'error').mockImplementation(() => {});
  await answer({ ok: false });
  reply = () => Promise.reject(new Error('dashboard down'));
  await act(() => tickStatusHealth());
  expect(pill().hidden).toBe(false);
  expect(errors).toHaveBeenCalledWith('tickStatusHealth', expect.any(Error));
  await answer({ ok: true });
  reply = () => Promise.reject(new Error('dashboard down'));
  await act(() => tickStatusHealth());
  expect(pill().hidden).toBe(true);
  errors.mockRestore();
});

test('it reads /api/agents/status_health', async () => {
  const urls: string[] = [];
  vi.stubGlobal('fetch', (url: string) => {
    urls.push(url);
    return Promise.resolve({ json: () => Promise.resolve({ ok: true }) });
  });
  await act(() => tickStatusHealth());
  expect(urls).toEqual(['/api/agents/status_health']);
});

test('glyph + word, never colour alone; the word is its own span, with a narrow form', () => {
  expect(pill().querySelector('svg')).not.toBeNull();
  expect(pill().querySelector('.shw-text')?.textContent).toBe('agent status unavailable');
  expect(pill().querySelector('.shw-text-narrow')?.textContent).toBe('status down');
  expect(pill().title).toBe("claude agents --json is not answering. Every pane's busy/idle status is stale fleet-wide, not a calm fleet");
});

// CMX-311: jsdom parses the 240 KB style.css on insertion, ~2 s on a loaded machine: once, in
// beforeAll (the hook's own 10 s budget), not inside a test's 5 s, and only around these tests.
describe('under style.css', () => {
  let style: HTMLStyleElement;
  beforeAll(() => {
    style = document.createElement('style');
    style.textContent = css;
    document.head.append(style);
  });
  afterAll(() => style.remove());

  test('the pill may shrink (min-width 0, non-zero flex-shrink) and its word ellipsizes', () => {
    const cs = getComputedStyle(pill());
    expect(cs.minWidth).toBe('0px');
    expect(cs.flexShrink).not.toBe('0');
    const word = getComputedStyle(pill().querySelector('.shw-text') as HTMLElement);
    expect([word.overflow, word.textOverflow, word.whiteSpace]).toEqual(['hidden', 'ellipsis', 'nowrap']);
  });
});
