// CMX-426 + CMX-129: the "chela was updated, Reload" banner and its "N services run old code,
// Restart" half (ported from tests/update_banner.test.mjs, update_banner_stale.test.mjs and
// update_banner_close_size.test.mjs by CMX-219). It offers a reload only when the page's and
// the server's versions are both known and differ; unknown hides the stale half; dismiss is
// per version and per stale set; Restart is confirm-gated and POSTs /api/update/apply.
import { act, cleanup, fireEvent, render } from '@testing-library/react';
import { afterEach, beforeEach, expect, test, vi } from 'vitest';
import css from '../../../../../static/style.css?raw';
import { UpdateBanner, applyStale, applyVersion, banner, checkForUpdate, reloadOffered, restartStale } from '../index.ts';
import type { UpdateStatus } from '../flow.ts';

const PAGE = 'aaaaaaaaaaaa';
let versionPayload: unknown;
let statusPayload: unknown;
let applyPayload: unknown;
let calls: { url: string; method: string }[] = [];
let asked = '';
let answer = true;
const reload = vi.fn();

beforeEach(() => {
  banner.set({ reload: '', dismissed: '', stale: null, dismissedStale: '', progress: null });
  versionPayload = { version: PAGE };
  statusPayload = null;
  applyPayload = {};
  calls = [];
  asked = '';
  answer = true;
  reload.mockClear();
  vi.stubGlobal('fetch', (url: string, opts?: RequestInit) => {
    calls.push({ url, method: opts?.method ?? 'GET' });
    const body = url.endsWith('/api/version') ? versionPayload : url.endsWith('/api/update/status') ? statusPayload : url.endsWith('/api/update/apply') ? applyPayload : new Error(`unexpected ${url}`);
    if (body instanceof Error) return Promise.reject(body);
    return Promise.resolve({ ok: true, json: () => Promise.resolve(body) });
  });
  vi.stubGlobal('confirm', (q: string) => {
    asked = q;
    return answer;
  });
  vi.stubGlobal('location', { ...window.location, reload });
  render(<UpdateBanner />);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const $ = (id: string) => document.getElementById(id);
const shown = () => !!$('update-banner');
const msg = () => $('update-banner-msg')?.textContent;
function sync<T>(fn: () => T): T {
  let out: T | undefined;
  act(() => {
    out = fn();
  });
  return out as T;
}
const ver = (v: string) => sync(() => applyVersion(v, PAGE));
const stale = (st: UpdateStatus | null) => sync(() => applyStale(st));
const click = (id: string) => act(() => {
  fireEvent.click($(id) as HTMLElement);
});
const STALE: UpdateStatus = { ok: true, behind: 0, stale_ok: true, boot: 'b1', applying: false, stale_services: ['chela-collab', 'chela-telegram'], last_result: null };
const instant = (statuses: UpdateStatus[]) => {
  let i = 0;
  let clock = 0;
  return { sleep: async (ms: number) => void (clock += ms), now: () => clock, reload: () => {}, fetchStatus: async () => statuses[Math.min(i++, statuses.length - 1)] };
};
const posts = () => calls.filter((c) => c.url.endsWith('/api/update/apply'));

test('reloadOffered: differs → offer; match or unknown → silent', () => {
  expect(reloadOffered('a1', 'b2')).toBe(true);
  expect(reloadOffered('a1', 'a1')).toBe(false);
  expect(reloadOffered('', 'b2')).toBe(false);
  expect(reloadOffered('a1', '')).toBe(false);
});

test('same version on every poll: no banner (the stable-deploy case)', () => {
  for (let i = 0; i < 3; i++) expect(ver(PAGE)).toBe(false);
  expect(shown()).toBe(false);
});

test('a new deploy shows the banner with Reload; back to the same version hides it', () => {
  expect(ver('bbbbbbbbbbbb')).toBe(true);
  expect(msg()).toBe('chela was updated');
  expect($('update-banner')?.getAttribute('role')).toBe('status');
  expect($('update-banner-reload')).not.toBeNull();
  expect($('update-banner-restart')).toBeNull();
  expect(ver(PAGE)).toBe(false);
  expect(shown()).toBe(false);
});

test('dismiss hides it for that version only', () => {
  expect(ver('cccccccccccc')).toBe(true);
  click('update-banner-close');
  expect(shown()).toBe(false);
  expect(ver('cccccccccccc')).toBe(false);
  expect(ver('dddddddddddd')).toBe(true);
});

test('Reload reloads the page; nothing reloads on its own', () => {
  ver('ffffffffffff');
  expect(reload).not.toHaveBeenCalled();
  click('update-banner-reload');
  expect(reload).toHaveBeenCalledTimes(1);
});

test('checkForUpdate asks /api/version and /api/update/status; the two reads are independent', async () => {
  versionPayload = { version: 'eeeeeeeeeeee' };
  // the page's own version is its boot block's (none in this test page): never offers
  expect(await act(() => checkForUpdate())).toBe(false);
  expect(calls.map((c) => c.url).sort()).toEqual(['/api/update/status', '/api/version']);
  statusPayload = STALE;
  versionPayload = new Error('version route down');
  expect(await act(() => checkForUpdate())).toBe(false);
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(msg()).toBe('2 services run old code');
  versionPayload = { version: '' };
  statusPayload = new Error('status route down');
  expect(await act(() => checkForUpdate())).toBe(false);
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(shown()).toBe(false);
});

test('stale: one banner naming the count, a Restart button, no Reload', () => {
  expect(stale(STALE)).toBe(true);
  expect(msg()).toBe('2 services run old code');
  expect([$('update-banner-restart')?.textContent, $('update-banner-reload')]).toEqual(['Restart', null]);
  stale({ ...STALE, behind: 1 });
  expect($('update-banner-restart')?.textContent).toBe('Update & restart');
  stale({ ...STALE, stale_ok: false });
  expect(shown()).toBe(false);
});

test('a deploy AND stale services: ONE banner, both facts, each button', () => {
  stale(STALE);
  expect(ver('bbbbbbbbbbbb')).toBe(true);
  expect(document.querySelectorAll('.update-banner')).toHaveLength(1);
  expect(msg()).toBe('chela was updated · 2 services run old code');
  expect([!!$('update-banner-restart'), !!$('update-banner-reload')]).toEqual([true, true]);
  stale({ ...STALE, stale_services: [] });
  expect([msg(), !!$('update-banner-restart')]).toEqual(['chela was updated', false]);
});

test('dismiss hides THIS stale set only; a new set shows again; a dismissed reload does not swallow one', () => {
  stale({ ...STALE, stale_services: ['chela-dismissed'] });
  click('update-banner-close');
  expect(stale({ ...STALE, stale_services: ['chela-dismissed'] })).toBe(false);
  expect(stale({ ...STALE, stale_services: ['chela-daemon'] })).toBe(true);
  ver('dddddddddddd');
  click('update-banner-close');
  expect(stale({ ...STALE, stale_services: ['chela-late'] })).toBe(true);
  expect(msg()).toBe('1 service runs old code');
});

test('Restart: confirm names the services, POSTs the existing route, ends in the outcome', async () => {
  stale(STALE);
  applyPayload = { ok: true, started: true, run: 2, boot: 'b1', detail: 'restarting chela-collab' };
  const res = await act(() => restartStale(instant([{ ...STALE, applying: true }, { ...STALE, last_result: { run: 2, ok: true, behind_before: 0, restarted: ['chela-telegram', 'chela-collab'] } }])));
  expect(asked).toBe('Restart: chela-collab, chela-telegram? Running agents and judges keep running; the daemon restarts between ticks.');
  expect(posts().map((c) => c.method)).toEqual(['POST']);
  expect([res?.text, msg()]).toEqual(['Restarted chela-collab, chela-telegram', 'Restarted chela-collab, chela-telegram']);
  expect($('update-banner-msg')?.className).toBe('s-savemsg ok');
  expect($('update-banner-restart')).toBeNull();
  click('update-banner-close');
  stale({ ...STALE, stale_services: ['chela-next'] });
  expect(msg()).toBe('1 service runs old code');
});

test('the banner button runs it; a declined confirm POSTs nothing', async () => {
  stale({ ...STALE, behind: 3 });
  answer = false;
  click('update-banner-restart');
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(asked).toMatch(/^Update & restart: chela-collab, chela-telegram\?/);
  expect(posts()).toHaveLength(0);
});

test('nothing stale, or one already in flight: Restart neither asks nor POSTs', async () => {
  expect(await act(() => restartStale(instant([])))).toBeNull();
  expect(asked).toBe('');
  stale(STALE);
  applyPayload = { ok: true, started: true, run: 4, boot: 'b1', detail: 'restarting chela-collab' };
  let release = () => {};
  const gate = new Promise<void>((r) => (release = r));
  const deps = { ...instant([]), fetchStatus: async () => {
    await gate;
    return { ...STALE, last_result: { run: 4, ok: true, restarted: ['chela-collab'] } };
  } };
  const first = restartStale(deps);
  await act(() => new Promise((r) => setTimeout(r, 0)));
  expect(msg()).toBe('Starting…');
  expect($('update-banner-restart')).toBeNull(); // nothing to restart twice while it runs
  const second = await restartStale(deps);
  release();
  await act(() => first);
  expect(second).toBeNull();
  expect(posts()).toHaveLength(1);
});

test('refused, not started, failed request and never confirmed each end in their own outcome', async () => {
  stale(STALE);
  applyPayload = { ok: false, error: 'an update is already running (4s so far)' };
  expect((await act(() => restartStale(instant([]))))?.text).toBe('an update is already running (4s so far)');
  expect($('update-banner-msg')?.className).toBe('s-savemsg err');
  expect($('update-banner-restart')).not.toBeNull();
  applyPayload = { ok: true, error: 'odd' };
  expect((await act(() => restartStale(instant([]))))?.text).toBe('odd');
  applyPayload = { ok: false };
  expect((await act(() => restartStale(instant([]))))?.text).toBe('Restart refused.');
  applyPayload = new Error('dashboard down');
  expect((await act(() => restartStale(instant([]))))?.text).toBe('request failed, the dashboard may be restarting');
  applyPayload = { ok: true, started: false, detail: 'already up to date' };
  expect((await act(() => restartStale(instant([]))))?.text).toBe('already up to date');
  applyPayload = { ok: true, started: false };
  expect((await act(() => restartStale(instant([]))))?.text).toBe('Nothing to restart.');
  expect($('update-banner-msg')?.className).toBe('s-savemsg ok');
  applyPayload = { ok: true, started: true, run: 3, boot: 'b1', detail: 'restarting chela-collab' };
  const res = await act(() => restartStale(instant([{ ...STALE, applying: true }])));
  expect(res?.text).toBe("Couldn't confirm the restart. Check `pm2 ls`.");
  expect($('update-banner-msg')?.className).toBe('s-savemsg err');
  expect($('update-banner-restart')).not.toBeNull(); // a failed restart keeps the offer to retry
});

test('the dismiss x is 24px high with a 24x24 minimum target and a 14px glyph; a toolbar .icon-btn keeps 38x38', () => {
  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);
  ver('bbbbbbbbbbbb');
  const close = $('update-banner-close') as HTMLElement;
  expect(close.closest('.update-banner')).not.toBeNull();
  const cs = getComputedStyle(close);
  expect([cs.height, cs.minWidth, cs.minHeight, cs.fontSize, cs.width]).toEqual(['24px', '24px', '24px', '14px', 'auto']);
  const other = document.createElement('button');
  other.className = 'icon-btn';
  document.body.appendChild(other);
  const ocs = getComputedStyle(other);
  expect([ocs.width, ocs.height, ocs.fontSize]).toEqual(['38px', '38px', '17px']);
  other.remove();
  style.remove();
});

test('a restart that restarts THIS dashboard ends as success, not an error', async () => {
  stale(STALE);
  applyPayload = { ok: true, started: true, run: 5, boot: 'b1', detail: 'restarting chela-dashboard' };
  const res = await act(() => restartStale(instant([{ ...STALE, boot: 'b2' }])));
  expect([res?.text, res?.cls]).toEqual(['The dashboard restarted, reloading…', 'ok']);
});
